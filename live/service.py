"""Single-flight polling, atomic last-good cache, and a local read-only dashboard."""
from __future__ import annotations

from copy import deepcopy
import gzip
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import threading
import time
from urllib.parse import parse_qs, urlsplit
import pandas as pd

from live.decisions import (
    build_rebalance_plan,
    create_weekly_decision_ticket,
    evaluate_account_data_gate,
    execute_decision_ticket,
    sync_account_intraday_stops,
)
from live.engine import build_research_indicators, monitor, recompute
from live.importer import commit_broker_csv, preview_broker_csv
from live.market import MarketClock, RISK_SYMBOLS, SYMBOLS, utc_now
from live.notifications import evaluate_and_emit_notifications
from live.review import build_review_report, render_review_markdown
from strategy_core.api import calibrate_from_store, describe, simulate_multi_asset, USPortfolioConfig
from live.store import INSTRUMENT_META, TradingStore, clean_float, market_today, now_iso

NO_FALLBACK_VERSIONS = frozenset({'rp40'})

ROOT = Path(__file__).resolve().parents[1]


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')


def load_research(root=ROOT):
    return json.loads((root/'research_outputs'/'V22_dashboard_snapshot.json').read_text(encoding='utf-8'))


def render_dashboard(root=ROOT):
    html = (root/'web'/'template.html').read_text(encoding='utf-8')
    payload = json_bytes(load_research(root)).decode('utf-8').replace('</', '<\\/')
    rendered = html.replace('/* V21_STYLES */', (root/'web'/'v21.css').read_text(encoding='utf-8')).replace(
        '/* V21_CODE */', (root/'web'/'v21.js').read_text(encoding='utf-8')).replace('V21_DATA_JSON', payload)
    rendered = rendered.replace('</head>', '  <link rel="stylesheet" href="/assets/ptms.css" />\n</head>')
    rendered = rendered.replace('</body>', '  <script src="/assets/ptms.js"></script>\n</body>')
    return rendered.encode('utf-8')


class LiveService:
    def __init__(self, provider, *, root=ROOT, quote_interval=30, history_interval=900, clock=None):
        self.provider, self.root = provider, root
        self.quote_interval, self.history_interval = max(15, quote_interval), max(300, history_interval)
        self.clock = clock or MarketClock()
        self.lock, self.flight = threading.Lock(), threading.Lock()
        self.wake, self.stopped = threading.Event(), threading.Event()
        self.worker = None
        self.research = load_research(root)
        self.sim_inputs = {}  # market -> recompute inputs for fee what-if (memory only)
        self.research_indicators = build_research_indicators(root)
        self.store = TradingStore(root/'market_data'/'personal_trading.db')
        self.cache = root/'market_data'/'live_cache'/'last_good.json'
        self.daily, self.tape, self.bars = {}, {}, {}
        self.last_history = 0
        self.force_history = False
        self.live = {'quotes': {}, 'models': {}, 'signals': {}, 'indicators': {}, 'intraday': {}, 'strategy_status': {},
                     'quality': [], 'frozen_signals': [], 'liquidity': []}
        self.status = {'state': 'starting', 'provider': provider.name, 'refreshing': False,
                       'last_attempt': None, 'last_success': None, 'errors': {}, 'cache_restored': False,
                       'quote_interval_seconds': self.quote_interval, 'history_interval_seconds': self.history_interval}
        try:
            cached = json.loads(self.cache.read_text(encoding='utf-8'))
            if cached.get('schema') == 1 and isinstance(cached.get('live'), dict):
                self.live.update(cached['live'])
                self.live.setdefault('indicators', {})
                self.status.update(state='cached', cache_restored=True, last_success=cached.get('saved_at'))
        except (OSError, ValueError, TypeError):
            pass

    def payload(self):
        now = utc_now()
        with self.lock:
            live, status = deepcopy(self.live), deepcopy(self.status)
        market_status = {m: self.clock.status(m, now) for m in ('CN', 'US')}
        for quote in live['quotes'].values():
            stamp = pd.Timestamp(quote['timestamp'])
            quote['age_seconds'] = max(0, int((now-stamp).total_seconds()))
            quote['stale'] = quote['age_seconds'] > 180
            quote['refresh_failed'] = any(k in status['errors'] for k in ('quotes', 'quote:'+quote.get('symbol', '')))
            quote['display_state'] = 'stale' if quote['stale'] else 'recent'
            if market_status[quote['market']]['state'] == 'closed':
                quote['display_state'] = 'last_close'
        # A cache read or an API poll never refreshes the source's data timestamp.
        for market, value in live.get('strategy_status', {}).items():
            if self._market_connection_degraded(market, status):
                value['connection_degraded'] = True
        for symbol, observation in live.get('intraday', {}).items():
            age = (now-pd.Timestamp(observation['last_bar_end'])).total_seconds()
            if age > 2400:
                observation['data_state'] = 'last_session'
            observation['refresh_failed'] = any(k in status['errors'] for k in ('bars', 'risk:'+symbol, 'bars:'+symbol))
            if observation['refresh_failed']:
                observation['data_state'] = 'last_session'
        return {'schema': 1, 'server_time': now.isoformat(), 'status': status, 'market_status': market_status,
                'research': self.research, 'live': live}

    def refresh_once(self, *, history=False):
        if not self.flight.acquire(blocking=False):
            return False
        errors, any_success = {}, False
        with self.lock:
            self.status.update(refreshing=True, last_attempt=utc_now().isoformat())
        try:
            try:
                quotes, failures = self.provider.quotes(utc_now())
                errors.update({'quote:'+k: v for k, v in failures.items()})
                with self.lock:
                    self.live['quotes'].update(quotes)
                any_success |= bool(quotes)
            except Exception as error:
                errors['quotes'] = type(error).__name__ + '：行情请求失败，请检查网络及行情权限'
            if history or time.monotonic()-self.last_history >= self.history_interval:
                # Record attempt time too: an unavailable provider must not be hammered.
                self.last_history = time.monotonic()
                try:
                    daily, failures = self.provider.daily()
                    errors.update({'daily:'+k: v for k, v in failures.items()})
                    # Work only with this coherent refresh, never fill missing assets
                    # using previous downloads with a different adjustment basis.
                    self.daily = daily
                    self.tape = {}
                    try:
                        self.tape = self.provider.tape()
                    except Exception as error:
                        errors['tape'] = type(error).__name__ + '：指数成交额不可用，A股新信号暂停'
                    computed = recompute(self.daily, self.tape, self.clock, utc_now(), self.provider.name)
                    sim_inputs = computed.pop('_sim_inputs', {})
                    with self.lock:
                        self.sim_inputs.update(sim_inputs)
                        # Keep last successful model per market but label failed refresh.
                        self.live['models'].update(computed['models'])
                        self.live['signals'].update(computed['signals'])
                        self.live['indicators'] = computed.get('indicators', {})
                        self.live['strategy_status'] = computed['strategy_status']
                        for key in ('quality', 'frozen_signals', 'liquidity', 'computed_at', 'external_backtests'):
                            self.live[key] = computed[key]
                    any_success |= bool(computed['models'])
                except Exception as error:
                    errors['history'] = type(error).__name__ + '：策略数据更新失败，保留上次计算'
            try:
                bars, failures = self.provider.bars()
                errors.update({'bars:'+k: v for k, v in failures.items()})
                self.bars.update(bars or {})
                for symbol in RISK_SYMBOLS:
                    try:
                        observation = monitor(symbol, bars[symbol], self.daily[symbol]['raw'], self.clock, utc_now())
                        with self.lock:
                            self.live['intraday'][symbol] = observation
                        any_success = True
                    except (KeyError, ValueError, IndexError) as error:
                        errors['risk:'+symbol] = str(error)
            except Exception as error:
                errors['bars'] = type(error).__name__ + '：30分钟行情更新失败'
            try:
                self.sync_all_intraday_stops(utc_now())
            except Exception as error:
                errors['ptms_stops'] = type(error).__name__ + '：个人盘中风控同步失败'
            with self.lock:
                self.status.update(state=('degraded' if errors else 'connected') if any_success else 'offline',
                                   errors=errors, cache_restored=False)
                if any_success:
                    self.status['last_success'] = utc_now().isoformat()
                    # Validate JSON before replacing the last-good cache.
                    content = json_bytes({'schema': 1, 'saved_at': self.status['last_success'], 'live': self.live})
                    self.cache.parent.mkdir(parents=True, exist_ok=True)
                    temp = self.cache.with_suffix('.tmp')
                    temp.write_bytes(content)
                    os.replace(temp, self.cache)
            try:
                self.ptms_state()
            except Exception:
                pass
            return True
        except Exception as error:
            with self.lock:
                self.status.update(state='degraded', errors={**errors, 'refresh': type(error).__name__ + '：更新未完成'})
            return False
        finally:
            with self.lock:
                self.status['refreshing'] = False
            self.flight.release()

    def start(self):
        def loop():
            while not self.stopped.is_set():
                with self.lock:
                    force, self.force_history = self.force_history, False
                self.refresh_once(history=force)
                self.wake.wait(self.quote_interval)
                self.wake.clear()
        self.worker = threading.Thread(target=loop, name='market-refresh', daemon=True)
        self.worker.start()

    def request_refresh(self):
        # Requests coalesce; no additional threads or concurrent provider calls.
        with self.lock:
            if self.status['refreshing']:
                return False
            self.force_history = time.monotonic()-self.last_history >= 30
        self.wake.set()
        return True

    def stop(self):
        self.stopped.set()
        self.wake.set()

    def _current_prices_map(self, now):
        prices = {}
        for item in self.research.get('intraday', {}).get('next_session_candidates', []):
            sym = item.get('symbol')
            px = clean_float(item.get('prior_close'))
            if sym in INSTRUMENT_META and px and px > 0:
                prices[sym] = {'price': px, 'source': 'research_close', 'timestamp': None, 'stale': True}
        for sym, entry in (self.daily or {}).items():
            raw_df = entry.get('raw') if isinstance(entry, dict) else entry
            if isinstance(raw_df, pd.DataFrame) and not raw_df.empty and 'close' in raw_df:
                px = clean_float(raw_df['close'].iloc[-1])
                if px and px > 0:
                    prices[sym] = {'price': px, 'source': 'daily_raw', 'timestamp': str(raw_df.index[-1]), 'stale': True}
        with self.lock:
            quotes = deepcopy(self.live.get('quotes', {}))
            errors = dict(self.status.get('errors') or {})
        for sym, q in quotes.items():
            px = clean_float(q.get('price'))
            if px and px > 0:
                ts_raw = q.get('timestamp')
                age = (now - pd.Timestamp(ts_raw)).total_seconds() if ts_raw else 999999
                prices[sym] = {
                    'price': px,
                    'source': str(q.get('source') or 'quote'),
                    'timestamp': ts_raw,
                    'stale': age > 180,
                    'refresh_failed': any(k in errors for k in ('quotes', f'quote:{sym}')),
                }
        return prices

    def _market_connection_degraded(self, market, status):
        if status.get('cache_restored') or status.get('state') == 'offline':
            return True
        for err_key in status.get('errors') or {}:
            if err_key in ('quotes', 'history', 'refresh'):
                return True
            if err_key == 'tape' and market == 'CN':
                return True
            if err_key.startswith(('quote:', 'daily:')):
                sym = err_key.split(':', 1)[1]
                if INSTRUMENT_META.get(sym, {}).get('market', market) == market:
                    return True
        return False

    def _select_weekly_model(self, market, strategy_version):
        with self.lock:
            live_market = deepcopy(self.live.get('models', {}).get(market) or {})
        if strategy_version in live_market:
            return live_market[strategy_version]
        res_market = self.research.get('models', {}).get(market) or {}
        if strategy_version in res_market:
            return res_market[strategy_version]
        if strategy_version in NO_FALLBACK_VERSIONS:
            # rp40 has no frozen research snapshot. Returning {} makes the data gate
            # BLOCK the plan rather than silently executing the V18 baseline instead.
            return {}
        return live_market.get('baseline') or res_market.get('baseline') or {}

    def _indicators_with_fallback(self):
        with self.lock:
            live_ind = deepcopy(self.live.get('indicators') or {})
        out = {}
        for market in ('CN', 'US'):
            if live_ind.get(market):
                out[market] = {'source': 'online', **live_ind[market]}
            elif self.research_indicators.get(market):
                out[market] = {'source': 'research_fallback', **self.research_indicators[market]}
            else:
                out[market] = {'source': 'unavailable'}
        return out

    def _account_stop_view(self, account, portfolio, prices, now):
        market = account['market']
        has_live_inputs = any(sym in (self.daily or {}) or sym in (self.bars or {}) for sym in RISK_SYMBOLS if INSTRUMENT_META[sym]['market'] == market)
        if has_live_inputs:
            return sync_account_intraday_stops(
                self.store, account, portfolio, self.bars, self.daily, prices, self.clock, now
            )
        today_str = market_today(market, now)
        persisted = self.store.get_stop_states(account['account_id'])
        cands = {c['symbol']: c for c in self.research.get('intraday', {}).get('next_session_candidates', [])}
        pos_by_sym = {p['symbol']: p for p in portfolio['positions']}
        view = {}
        for sym, meta in INSTRUMENT_META.items():
            if meta['market'] != market or not meta['risk_monitored']:
                continue
            p_st = persisted.get(sym) or {}
            cand = cands.get(sym) or {}
            pos = pos_by_sym[sym]
            core_floor = float(account['core_floors'].get(sym, 0.0))
            active_line = clean_float(p_st.get('active_line')) or clean_float(cand.get('line'))
            last_px = clean_float((prices.get(sym) or {}).get('price')) or clean_float(cand.get('prior_close'))
            dist_pct = (
                round((last_px / active_line - 1.0) * 100.0, 2)
                if (last_px is not None and active_line is not None and active_line > 0)
                else None
            )
            cd_until = p_st.get('cooldown_until_date')
            cd_active = bool(cd_until and today_str <= str(cd_until))
            view[sym] = {
                'symbol': sym,
                'name': meta['name'],
                'bucket': meta['bucket'],
                'session_date': p_st.get('session_date') or today_str,
                'status': 'research_fallback',
                'status_note': '离线参考防线（联网后将按 30 分钟 K 线自动跟踪）',
                'active_line': active_line,
                'candidate_line': clean_float(cand.get('line')),
                'raw_trend_line': clean_float(cand.get('raw_trend_line')),
                'shock_line': clean_float(cand.get('shock_line')),
                'prior_close': clean_float(cand.get('prior_close')),
                'daily_sigma': clean_float(cand.get('daily_sigma')),
                'peak': clean_float(cand.get('peak')),
                'ma': clean_float(cand.get('ma')),
                'trend_prebroken': cand.get('trend_prebroken'),
                'last_price': last_px,
                'distance_pct': dist_pct,
                'below_count': int(p_st.get('below_count') or 0),
                'stage': int(p_st.get('stage') or 0),
                'pending_stage': int(p_st.get('pending_stage') or 0),
                'pending_issued_at': p_st.get('pending_issued_at'),
                'pending_reason': str(p_st.get('pending_reason') or ''),
                'cooldown_until_date': cd_until,
                'cooldown_active': cd_active,
                'cooldown_remaining_days': 0,
                'actual_weight': float(pos['actual_weight']),
                'core_floor': core_floor,
                'has_tactical': float(pos['actual_weight']) > core_floor + 1e-4,
                'observations': [],
            }
        return view

    def sync_all_intraday_stops(self, now=None):
        now_ts = now or utc_now()
        prices = self._current_prices_map(now_ts)
        for account in self.store.list_accounts():
            today_str = market_today(account['market'], now_ts)
            portfolio = self.store.get_portfolio(account['account_id'], prices, today_local_date=today_str)
            sync_account_intraday_stops(
                self.store, account, portfolio, self.bars, self.daily, prices, self.clock, now_ts
            )

    def ptms_state(self):
        now_ts = utc_now()
        with self.lock:
            status = deepcopy(self.status)
            strategy_status = deepcopy(self.live.get('strategy_status') or {})
        for market in ('CN', 'US'):
            if market in strategy_status and self._market_connection_degraded(market, status):
                strategy_status[market]['connection_degraded'] = True
        market_status = {m: self.clock.status(m, now_ts) for m in ('CN', 'US')}
        prices = self._current_prices_map(now_ts)
        indicators = self._indicators_with_fallback()
        accounts_out = {}
        for account in self.store.list_accounts():
            aid = account['account_id']
            market = account['market']
            today_str = market_today(market, now_ts)
            portfolio = self.store.get_portfolio(aid, prices, today_local_date=today_str)
            stop_states = self._account_stop_view(account, portfolio, prices, now_ts)
            tickets = self.store.list_tickets(aid, limit=50)
            pending = [t for t in tickets if t['status'] in ('PENDING', 'PARTIAL')]
            weekly_model = self._select_weekly_model(market, account['strategy_version'])
            data_gate = evaluate_account_data_gate(
                account,
                weekly_model,
                strategy_status.get(market),
                prices,
                self.clock,
                now_ts,
                market_status_entry=market_status[market],
            )
            plan = build_rebalance_plan(
                account,
                portfolio,
                weekly_model,
                stop_states,
                today_str,
                pending,
                data_gate=data_gate,
            )
            events = self.store.list_events(aid, limit=150)
            accounts_out[aid] = {
                'account': account,
                'portfolio': portfolio,
                'stop_states': stop_states,
                'plan': plan,
                'tickets': tickets,
                'events': events,
            }
        out = {
            'schema': 1,
            'server_time': now_ts.isoformat(),
            'status': status,
            'market_status': market_status,
            'accounts': accounts_out,
            'indicators': indicators,
            'quotes': prices,
            'instrument_meta': INSTRUMENT_META,
        }
        try:
            evaluate_and_emit_notifications(self.store, out, live_status=status)
        except Exception:
            pass
        notifications = self.store.list_notifications(limit=50)
        unread_count = len(self.store.list_notifications(limit=200, unread_only=True))
        out['notifications'] = notifications
        out['unread_notifications'] = unread_count
        return out

    def ptms_review(self, account_id='CN_MAIN'):
        now_ts = utc_now()
        account = self.store.get_account(account_id)
        prices = self._current_prices_map(now_ts)
        today_str = market_today(account['market'], now_ts)
        portfolio = self.store.get_portfolio(account_id, prices, today_local_date=today_str)
        weekly_model = self._select_weekly_model(account['market'], account['strategy_version'])
        report = build_review_report(
            self.store,
            account,
            portfolio,
            weekly_model.get('history') or [],
            self.daily,
            prices,
            today_str,
        )
        try:
            report['fee_calibration'] = self._fee_calibration(account)
        except Exception as error:  # diagnostics must never break the review page
            report['fee_calibration'] = {'state': 'failed', 'message': type(error).__name__ + '：费率校准失败'}
        return report

    def _fee_calibration(self, account):
        """Real-fill fee rates vs backtest assumptions + what-if NAV. Never alters live signals."""
        market, version = account['market'], account['strategy_version']
        with self.lock:
            sim = self.sim_inputs.get(market)
        if market == 'CN':
            if not sim:
                return {'state': 'waiting', 'message': '等待A股在线重算完成后生成费率对比'}
            base_cfg = sim['cfg']
        else:
            base_cfg = USPortfolioConfig()
        cal_cfg, buckets = calibrate_from_store(self.store, account['account_id'], base_cfg, INSTRUMENT_META, market)
        out = {'state': 'available', 'market': market, 'strategy_version': version,
               'buckets': buckets, 'calibrated_any': any(b['source'] == 'calibrated' for b in buckets),
               'note': '仅用于复盘对比：实盘信号与晋级门槛仍使用冻结的研究费率。'}
        signals = (sim or {}).get('signals', {}).get(version)
        if market == 'CN' and signals is not None:
            assumed = describe(simulate_multi_asset(sim['prices'], signals, base_cfg))
            calibrated = describe(simulate_multi_asset(sim['prices'], signals, cal_cfg))
            keys = ('cagr_pct', 'max_dd_pct', 'fee_initial_pct')
            out['what_if'] = {'cutoff': sim['cutoff'],
                              'assumed': {k: assumed[k] for k in keys},
                              'calibrated': {k: calibrated[k] for k in keys},
                              'cagr_delta_pp': round(calibrated['cagr_pct'] - assumed['cagr_pct'], 3)}
        return out

    def handle_ptms_post(self, path, body):
        if not isinstance(body, dict):
            raise ValueError('请求体必须为 JSON 对象')
        now_ts = utc_now()
        if path == '/api/ptms/account/config':
            aid = str(body.get('account_id') or '')
            acc = self.store.update_account_config(
                aid,
                strategy_version=body.get('strategy_version'),
                rebalance_deadband=body.get('rebalance_deadband'),
                core_floors=body.get('core_floors'),
                account_mode=body.get('account_mode'),
            )
            return {'account': acc, 'state': self.ptms_state()}

        if path == '/api/ptms/ledger/init':
            aid = str(body.get('account_id') or '')
            acc = self.store.get_account(aid)
            td = str(body.get('trade_date') or market_today(acc['market'], now_ts))
            res = self.store.init_account(
                aid,
                trade_date=td,
                event_time=body.get('event_time'),
                cash=body.get('cash', 0.0),
                positions=body.get('positions') or [],
                note=str(body.get('note') or '账户初始化建账'),
                account_mode=body.get('account_mode'),
            )
            return {'result': res, 'state': self.ptms_state()}

        if path == '/api/ptms/ledger/event':
            aid = str(body.get('account_id') or '')
            acc = self.store.get_account(aid)
            td = str(body.get('trade_date') or market_today(acc['market'], now_ts))
            ev = self.store.record_event(
                aid,
                event_type=str(body.get('event_type') or ''),
                trade_date=td,
                event_time=body.get('event_time'),
                symbol=body.get('symbol'),
                quantity=body.get('quantity', 0.0),
                price=body.get('price', 0.0),
                fee=body.get('fee', 0.0),
                cash_delta=body.get('cash_delta'),
                amount=body.get('amount'),
                decision_id=body.get('decision_id'),
                discipline_tag=str(body.get('discipline_tag') or 'MANUAL_OVERRIDE'),
                note=str(body.get('note') or ''),
                ignore_t_plus=bool(body.get('ignore_t_plus', False)),
                today_local_date=market_today(acc['market'], now_ts),
                client_request_id=body.get('client_request_id'),
                external_trade_id=body.get('external_trade_id'),
            )
            return {'event': ev, 'state': self.ptms_state()}

        if path == '/api/ptms/ledger/reverse':
            aid = str(body.get('account_id') or '')
            eid = int(body.get('event_id'))
            acc = self.store.get_account(aid)
            td = str(body.get('trade_date') or market_today(acc['market'], now_ts))
            rev = self.store.reverse_event(
                aid,
                eid,
                reason=str(body.get('reason') or ''),
                trade_date=td,
                event_time=body.get('event_time'),
                today_local_date=market_today(acc['market'], now_ts),
            )
            return {'reversal': rev, 'state': self.ptms_state()}

        if path == '/api/ptms/ledger/reconcile':
            aid = str(body.get('account_id') or '')
            acc = self.store.get_account(aid)
            td = str(body.get('trade_date') or market_today(acc['market'], now_ts))
            prices = self._current_prices_map(now_ts)
            res = self.store.reconcile_account(
                aid,
                trade_date=td,
                event_time=body.get('event_time'),
                target_cash=body.get('target_cash', 0.0),
                target_shares=body.get('target_shares') or {},
                prices=prices,
                note=str(body.get('note') or '券商持仓对账校准'),
                today_local_date=market_today(acc['market'], now_ts),
            )
            return {'result': res, 'state': self.ptms_state()}

        if path == '/api/ptms/decisions/create':
            aid = str(body.get('account_id') or '')
            st = self.ptms_state()
            if aid not in st['accounts']:
                raise KeyError(f'账户不存在: {aid}')
            live_plan = st['accounts'][aid]['plan']
            if not live_plan.get('can_execute_plan') and not body.get('allow_override'):
                reasons = (live_plan.get('data_gate') or {}).get('reasons') or [
                    live_plan.get('verdict_reason') or '当前数据或执行窗口未就绪'
                ]
                raise ValueError('当前不允许锁定计划调仓单：' + '；'.join(reasons))
            plan = body.get('snapshot') or live_plan
            ticket = create_weekly_decision_ticket(
                self.store,
                plan,
                source=str(body.get('source') or 'WEEKLY_REBALANCE'),
            )
            return {'ticket': ticket, 'state': self.ptms_state()}

        if path == '/api/ptms/decisions/execute':
            aid = str(body.get('account_id') or '')
            acc = self.store.get_account(aid)
            did = int(body.get('decision_id'))
            td = str(body.get('trade_date') or market_today(acc['market'], now_ts))
            res = execute_decision_ticket(
                self.store,
                aid,
                did,
                trade_date=td,
                event_time=body.get('event_time'),
                fills=body.get('fills') or [],
                discipline_tag=str(body.get('discipline_tag') or 'FOLLOW'),
                review_note=str(body.get('review_note') or ''),
                status=body.get('status'),
                ignore_t_plus=bool(body.get('ignore_t_plus', False)),
                client_request_id=body.get('client_request_id'),
                clock=self.clock,
            )
            return {'result': res, 'state': self.ptms_state()}

        if path == '/api/ptms/decisions/skip':
            aid = str(body.get('account_id') or '')
            did = int(body.get('decision_id'))
            ticket = self.store.skip_ticket(
                aid,
                did,
                status=str(body.get('status') or 'SKIPPED'),
                review_note=str(body.get('review_note') or ''),
            )
            return {'ticket': ticket, 'state': self.ptms_state()}

        if path == '/api/ptms/decisions/cancel-remainder':
            aid = str(body.get('account_id') or '')
            did = int(body.get('decision_id'))
            ticket = self.store.cancel_ticket_remainder(
                aid,
                did,
                review_note=str(body.get('review_note') or ''),
            )
            return {'ticket': ticket, 'state': self.ptms_state()}

        if path == '/api/ptms/restore/preview':
            backup_obj = body.get('backup') if 'backup' in body else body
            return {'preview': self.store.preview_full_backup(backup_obj)}

        if path == '/api/ptms/restore':
            backup_obj = body.get('backup') if 'backup' in body else body
            res = self.store.restore_full_backup(backup_obj)
            return {'result': res, 'state': self.ptms_state()}

        if path == '/api/ptms/import/preview':
            aid = str(body.get('account_id') or '')
            acc = self.store.get_account(aid)
            csv_text = str(body.get('csv_text') or '')
            prices = self._current_prices_map(now_ts)
            preview = preview_broker_csv(
                self.store,
                aid,
                csv_text,
                prices=prices,
                today_local_date=market_today(acc['market'], now_ts),
            )
            return {'preview': preview}

        if path == '/api/ptms/import/commit':
            aid = str(body.get('account_id') or '')
            acc = self.store.get_account(aid)
            csv_text = str(body.get('csv_text') or '')
            res = commit_broker_csv(
                self.store,
                aid,
                csv_text,
                ignore_t_plus=bool(body.get('ignore_t_plus', True)),
                today_local_date=market_today(acc['market'], now_ts),
            )
            return {'result': res, 'state': self.ptms_state()}

        if path == '/api/ptms/notifications/read':
            raw_ids = body.get('notification_ids')
            count = self.store.mark_notifications_read(raw_ids)
            notifications = self.store.list_notifications(limit=50)
            unread_count = len(self.store.list_notifications(limit=200, unread_only=True))
            return {
                'updated': count,
                'notifications': notifications,
                'unread_notifications': unread_count,
            }

        if path == '/api/ptms/stops/reset':
            aid = str(body.get('account_id') or '')
            sym = str(body.get('symbol') or '')
            stop_row = self.store.reset_stop_state(
                aid,
                sym,
                reason=str(body.get('reason') or '手动重置风控状态'),
                clear_cooldown=bool(body.get('clear_cooldown', True)),
            )
            return {'stop_state': stop_row, 'state': self.ptms_state()}

        if path == '/api/ptms/review/note':
            aid = str(body.get('account_id') or '')
            review_data = self.ptms_review(aid)
            summary = body.get('summary') or {
                'kpis': review_data['kpis'],
                'discipline': review_data['discipline'],
            }
            note_row = self.store.upsert_review_note(
                aid,
                period_type=str(body.get('period_type') or 'WEEKLY'),
                period_key=str(body.get('period_key') or ''),
                summary=summary,
                reflection=str(body.get('reflection') or ''),
            )
            return {'note': note_row, 'review': self.ptms_review(aid)}

        raise KeyError(f'未知接口路径: {path}')


def make_server(service, host='127.0.0.1', port=8765):
    if host not in ('127.0.0.1', 'localhost'):
        raise ValueError('dashboard service binds to localhost only')

    MAX_POST_BYTES = 262144
    LARGE_POST_BYTES = 2097152
    LARGE_POST_PATHS = {
        '/api/ptms/restore',
        '/api/ptms/restore/preview',
        '/api/ptms/import/preview',
        '/api/ptms/import/commit',
    }
    ASSET_TYPES = {
        '/assets/ptms.css': ('ptms.css', 'text/css; charset=utf-8'),
        '/assets/ptms.js': ('ptms.js', 'application/javascript; charset=utf-8'),
    }

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def send_content(self, code, body, kind='application/json; charset=utf-8', extra_headers=None):
            if 'gzip' in self.headers.get('Accept-Encoding', '') and len(body) > 1024:
                body = gzip.compress(body)
                compressed = True
            else:
                compressed = False
            self.send_response(code)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'same-origin')
            self.send_header('Vary', 'Accept-Encoding')
            if extra_headers:
                for hk, hv in extra_headers.items():
                    self.send_header(hk, hv)
            if compressed:
                self.send_header('Content-Encoding', 'gzip')
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def local_request(self):
            allowed = {f'{h}:{self.server.server_port}' for h in ('localhost', '127.0.0.1')}
            return self.headers.get('Host', '') in allowed

        def _read_json_body(self, max_bytes=MAX_POST_BYTES):
            if self.headers.get('Transfer-Encoding'):
                raise ValueError('chunked encoding not supported')
            raw_len = self.headers.get('Content-Length', '0')
            try:
                length = int(raw_len)
            except ValueError as exc:
                raise ValueError('invalid Content-Length') from exc
            if length < 0 or length > max_bytes:
                raise ValueError('request body too large')
            raw = self.rfile.read(length) if length > 0 else b'{}'
            data = json.loads(raw.decode('utf-8'))
            if not isinstance(data, dict):
                raise ValueError('JSON body must be an object')
            return data

        def do_GET(self):
            if not self.local_request():
                return self.send_content(403, b'{"error":"invalid host"}')
            parsed = urlsplit(self.path)
            path = parsed.path
            if path in ('/', '/index.html'):
                return self.send_content(200, render_dashboard(service.root), 'text/html; charset=utf-8')
            if path in ASSET_TYPES:
                fname, mime = ASSET_TYPES[path]
                candidate = service.root / 'web' / fname
                if not candidate.exists():
                    candidate = ROOT / 'web' / fname
                if not candidate.exists():
                    return self.send_content(404, b'{"error":"asset not found"}')
                return self.send_content(200, candidate.read_bytes(), mime)
            if path in ('/api/dashboard', '/api/status'):
                payload = service.payload()
                if path == '/api/status':
                    payload = {k: payload[k] for k in ('server_time', 'status', 'market_status')}
                return self.send_content(200, json_bytes(payload))
            if path == '/api/ptms/state':
                return self.send_content(200, json_bytes(service.ptms_state()))
            if path == '/api/ptms/notifications':
                qs = parse_qs(parsed.query)
                unread_only = (qs.get('unread_only') or ['0'])[0] in ('1', 'true', 'yes')
                try:
                    limit = int((qs.get('limit') or ['50'])[0])
                except (TypeError, ValueError):
                    limit = 50
                notes = service.store.list_notifications(limit=limit, unread_only=unread_only)
                unread_count = len(service.store.list_notifications(limit=200, unread_only=True))
                return self.send_content(200, json_bytes({'notifications': notes, 'unread_notifications': unread_count}))
            if path == '/api/ptms/backup':
                backup = service.store.export_full_backup()
                return self.send_content(
                    200,
                    json_bytes(backup),
                    'application/json; charset=utf-8',
                    {'Content-Disposition': 'attachment; filename="ptms_full_backup.json"'},
                )
            if path == '/api/ptms/review':
                qs = parse_qs(parsed.query)
                aid = (qs.get('account_id') or ['CN_MAIN'])[0]
                try:
                    return self.send_content(200, json_bytes(service.ptms_review(aid)))
                except KeyError as error:
                    return self.send_content(404, json_bytes({'error': str(error)}))
            if path == '/api/ptms/export':
                qs = parse_qs(parsed.query)
                aid = (qs.get('account_id') or ['CN_MAIN'])[0]
                fmt = (qs.get('format') or ['markdown'])[0].lower()
                try:
                    review = service.ptms_review(aid)
                    acc = service.store.get_account(aid)
                except KeyError as error:
                    return self.send_content(404, json_bytes({'error': str(error)}))
                if fmt == 'json':
                    bundle = {
                        'exported_at': now_iso(),
                        'account': acc,
                        'review': review,
                        'events': service.store.list_events(aid, limit=None),
                    }
                    return self.send_content(
                        200,
                        json_bytes(bundle),
                        'application/json; charset=utf-8',
                        {'Content-Disposition': f'attachment; filename="ptms_{aid}_backup.json"'},
                    )
                md_bytes = render_review_markdown(review).encode('utf-8')
                return self.send_content(
                    200,
                    md_bytes,
                    'text/markdown; charset=utf-8',
                    {'Content-Disposition': f'attachment; filename="ptms_{aid}_review.md"'},
                )
            self.send_content(404, b'{"error":"not found"}')

        def do_POST(self):
            if not self.local_request():
                return self.send_content(403, b'{"error":"invalid host"}')
            origin = self.headers.get('Origin')
            if (origin and origin != 'http://'+self.headers.get('Host', '')) or self.headers.get('Sec-Fetch-Site') == 'cross-site':
                return self.send_content(403, b'{"error":"same origin required"}')
            path = urlsplit(self.path).path
            if path == '/api/refresh':
                if self.headers.get('Content-Length', '0') != '0' or self.headers.get('Transfer-Encoding'):
                    return self.send_content(400, b'{"error":"request body not accepted"}')
                accepted = service.request_refresh()
                return self.send_content(202, json_bytes({'accepted': accepted, 'message': '刷新已排队' if accepted else '刷新正在进行'}))
            if path.startswith('/api/ptms/'):
                try:
                    max_b = LARGE_POST_BYTES if path in LARGE_POST_PATHS else MAX_POST_BYTES
                    body = self._read_json_body(max_bytes=max_b)
                    res = service.handle_ptms_post(path, body)
                    return self.send_content(200, json_bytes(res))
                except KeyError as error:
                    return self.send_content(404, json_bytes({'error': str(error)}))
                except (ValueError, TypeError, json.JSONDecodeError) as error:
                    return self.send_content(400, json_bytes({'error': str(error)}))
            self.send_content(404, b'{"error":"not found"}')

        def log_message(self, fmt, *args):
            # Only paths/status; never credentials or upstream responses.
            print(fmt % args, flush=True)

    return ThreadingHTTPServer((host, port), Handler)
