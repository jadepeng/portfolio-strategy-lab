"""Asymmetric weekly + intraday decision arbitration, persisted stops, and lot-aware order generation."""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any

import pandas as pd

from live.market import ZONES, utc_now, validate_frame
from live.store import (
    INSTRUMENT_META,
    TradingStore,
    bucket_to_symbol,
    cash_etf_symbol,
    clean_float,
    market_symbols,
    market_today,
    normalize_trade_date,
    risk_monitored_symbols,
)
from strategy_core.api import clip_weekly_risk_additions, IntradayStopEngine, StopConfig, normalize_bars, stop_line


def add_trading_sessions(day_str: str, sessions: int, market: str, clock=None) -> str:
    """Advance N regular trading sessions forward from day_str."""
    td = normalize_trade_date(day_str)
    if sessions <= 0:
        return td
    cal = getattr(clock, 'calendars', {}).get(market) if clock is not None else None
    start = pd.Timestamp(td) + pd.Timedelta(days=1)
    if cal is not None:
        try:
            end = start + pd.Timedelta(days=max(30, sessions * 6))
            future = cal.sessions_in_range(start, end)
            if len(future) >= sessions:
                return str(future[sessions - 1].date())
        except Exception:
            pass
    rng = pd.bdate_range(start=start, periods=sessions)
    return str(rng[-1].date())


def is_cooldown_active(cooldown_until_date: str | None, today_local_date: str) -> bool:
    if not cooldown_until_date:
        return False
    return str(today_local_date) <= str(cooldown_until_date)


def cooldown_remaining_days(
    cooldown_until_date: str | None,
    today_local_date: str,
    market: str,
    clock=None,
) -> int:
    if not is_cooldown_active(cooldown_until_date, today_local_date):
        return 0
    cal = getattr(clock, 'calendars', {}).get(market) if clock is not None else None
    start = pd.Timestamp(today_local_date)
    end = pd.Timestamp(cooldown_until_date)
    if cal is not None:
        try:
            return int(len(cal.sessions_in_range(start, end)))
        except Exception:
            pass
    return int(len(pd.bdate_range(start=start, end=end)))


def _extract_raw_df(entry: Any) -> pd.DataFrame | None:
    if entry is None:
        return None
    if isinstance(entry, dict):
        return entry.get('raw')
    if isinstance(entry, pd.DataFrame):
        return entry
    return None


def _normalize_daily_context(raw_df: pd.DataFrame, market: str, clock, now: pd.Timestamp) -> pd.DataFrame:
    df = validate_frame(raw_df, intraday=False).copy()
    if df.index.tz is not None:
        df.index = df.index.tz_convert(ZONES[market]).tz_localize(None).normalize()
    else:
        df.index = pd.DatetimeIndex(df.index).normalize()
    return clock.complete_daily(df, market, now)


def _round_sell_shares(raw_qty: float, total_shares: float, sellable_shares: float, lot_size: int) -> tuple[float, bool]:
    if raw_qty <= 0 or total_shares <= 0:
        return 0.0, False
    if lot_size > 1:
        qty = float(math.floor(raw_qty / lot_size) * lot_size)
        if qty == 0.0 and total_shares < lot_size and raw_qty >= 0.5 * total_shares:
            qty = float(total_shares)
        max_sell = float(math.floor(sellable_shares / lot_size) * lot_size)
        if sellable_shares == total_shares and total_shares < lot_size:
            max_sell = float(total_shares)
    else:
        qty = round(raw_qty, 4)
        max_sell = round(float(sellable_shares), 4)

    blocked = False
    if qty > max_sell + 1e-6:
        blocked = sellable_shares < total_shares - 1e-6
        qty = max_sell
    return max(0.0, qty), blocked


def sync_account_intraday_stops(
    store: TradingStore,
    account: dict[str, Any],
    portfolio: dict[str, Any],
    bars_map: dict[str, pd.DataFrame] | None,
    raw_daily_map: dict[str, Any] | None,
    quotes: dict[str, dict[str, Any]] | None,
    clock,
    now: pd.Timestamp,
) -> dict[str, dict[str, Any]]:
    """Advance persistent intraday stop state machines using closed 30m bars and real holdings."""
    market = account['market']
    today_str = market_today(market, now)
    persisted_map = store.get_stop_states(account['account_id'])
    pos_by_sym = {p['symbol']: p for p in portfolio['positions']}
    out: dict[str, dict[str, Any]] = {}

    for symbol in risk_monitored_symbols(market):
        meta = INSTRUMENT_META[symbol]
        pos = pos_by_sym[symbol]
        core_floor = float(account['core_floors'].get(symbol, 0.0))
        actual_weight = float(pos['actual_weight'])
        persisted = dict(persisted_map.get(symbol) or {})
        cooldown_until = persisted.get('cooldown_until_date')
        cd_active = is_cooldown_active(cooldown_until, today_str)
        cd_days = cooldown_remaining_days(cooldown_until, today_str, market, clock)

        cfg = StopConfig(market=market, core_floor=core_floor)
        raw_df = _extract_raw_df((raw_daily_map or {}).get(symbol))
        bars_raw = (bars_map or {}).get(symbol)

        closed_bars = pd.DataFrame()
        bars_error: str | None = None
        if bars_raw is not None and not bars_raw.empty:
            try:
                norm = normalize_bars(validate_frame(bars_raw, intraday=True), cfg)
                closed_bars = norm.loc[norm.index + pd.Timedelta(minutes=30) + pd.Timedelta(seconds=5) <= now]
            except Exception as exc:
                bars_error = f'{type(exc).__name__}: {exc}'

        session_day = (
            closed_bars.index[-1].tz_convert(ZONES[market]).strftime('%Y-%m-%d')
            if not closed_bars.empty
            else today_str
        )

        line_info: dict[str, Any] | None = None
        daily_error: str | None = None
        if raw_df is not None and not raw_df.empty:
            try:
                completed_daily = _normalize_daily_context(raw_df, market, clock, now)
                context = completed_daily.loc[completed_daily.index < pd.Timestamp(session_day)]
                if context.empty:
                    daily_error = '前一日线历史为空'
                elif (pd.Timestamp(session_day) - context.index[-1]).days > 21:
                    daily_error = '前一日线数据过期超过21天'
                else:
                    line_info = stop_line(context['close'], cfg)
            except Exception as exc:
                daily_error = str(exc)
        else:
            daily_error = '缺少不复权日线历史'

        if line_info is None:
            active_line = clean_float(persisted.get('active_line'))
            q_px = clean_float((quotes or {}).get(symbol, {}).get('price')) or clean_float(pos.get('mark_price'))
            dist_pct = (
                round((q_px / active_line - 1.0) * 100.0, 2)
                if (q_px is not None and active_line is not None and active_line > 0)
                else None
            )
            out[symbol] = {
                'symbol': symbol,
                'name': meta['name'],
                'bucket': meta['bucket'],
                'session_date': persisted.get('session_date') or session_day,
                'status': 'data_unavailable',
                'status_note': daily_error or '保留已持久化风控状态',
                'active_line': active_line,
                'candidate_line': None,
                'raw_trend_line': None,
                'shock_line': None,
                'prior_close': None,
                'daily_sigma': None,
                'peak': None,
                'ma': None,
                'trend_prebroken': None,
                'last_price': q_px,
                'distance_pct': dist_pct,
                'below_count': int(persisted.get('below_count') or 0),
                'stage': int(persisted.get('stage') or 0),
                'pending_stage': int(persisted.get('pending_stage') or 0),
                'pending_issued_at': persisted.get('pending_issued_at'),
                'pending_reason': str(persisted.get('pending_reason') or ''),
                'cooldown_until_date': cooldown_until,
                'cooldown_active': cd_active,
                'cooldown_remaining_days': cd_days,
                'actual_weight': actual_weight,
                'core_floor': core_floor,
                'has_tactical': actual_weight > core_floor + 1e-4,
                'observations': [],
            }
            continue

        eng = IntradayStopEngine(symbol, cfg)
        has_tactical = actual_weight > core_floor + 1e-4
        persisted_stage = int(persisted.get('stage') or 0)
        persisted_pending = int(persisted.get('pending_stage') or 0)

        observations: list[dict[str, Any]] = []
        current_bars = (
            closed_bars.loc[closed_bars.index.tz_convert(ZONES[market]).strftime('%Y-%m-%d') == session_day]
            if not closed_bars.empty
            else pd.DataFrame()
        )

        if not has_tactical and persisted_stage == 0 and persisted_pending == 0:
            # Flat / at core floor with no pending exit: track fresh daily candidate without freezing stale high line
            eng.s.session = session_day
            eng.s.active_line = float(line_info['line'])
            eng.s.below_count = 0
            if not current_bars.empty:
                eng.s.last_completed = current_bars.index[-1]
                for t, row in current_bars.tail(16).iterrows():
                    observations.append(
                        {
                            'd': (t + pd.Timedelta(minutes=30)).isoformat(),
                            'close': round(float(row.close), 4),
                            'line': round(float(line_info['line']), 4),
                            'below_bars': 0,
                        }
                    )
        else:
            eng.s.active_line = clean_float(persisted.get('active_line'))
            eng.s.session = persisted.get('session_date')
            eng.s.below_count = int(persisted.get('below_count') or 0)
            eng.s.stage = persisted_stage
            eng.s.pending_stage = persisted_pending
            eng.s.pending_issued_at = (
                pd.Timestamp(persisted['pending_issued_at'])
                if persisted.get('pending_issued_at')
                else None
            )
            eng.s.pending_line = clean_float(persisted.get('pending_line'))
            eng.s.pending_reason = str(persisted.get('pending_reason') or '')
            eng.s.last_completed = (
                pd.Timestamp(persisted['last_bar_start'])
                if persisted.get('last_bar_start')
                else None
            )
            if eng.s.session == session_day and eng.s.last_completed is not None:
                eng.s.previous_bar = eng.s.last_completed

            eng.session_start(
                session_day,
                line_info['line'],
                line_info['prior_close'],
                line_info['daily_sigma'],
            )
            for t, row in current_bars.iterrows():
                if eng.s.last_completed is not None and t <= eng.s.last_completed:
                    observations.append(
                        {
                            'd': (t + pd.Timedelta(minutes=30)).isoformat(),
                            'close': round(float(row.close), 4),
                            'line': round(float(eng.s.active_line), 4),
                            'below_bars': eng.s.below_count,
                        }
                    )
                    continue
                obs = eng.on_closed_bar(
                    t,
                    float(row.open),
                    float(row.close),
                    prev_close=line_info['prior_close'],
                    sigma=line_info['daily_sigma'],
                    known_at=now,
                )
                observations.append(
                    {
                        'd': (t + pd.Timedelta(minutes=30)).isoformat(),
                        'close': round(float(row.close), 4),
                        'line': round(float(eng.s.active_line), 4),
                        'below_bars': int(obs['below_bars']),
                    }
                )

        # Save updated stop state atomically
        store.save_stop_state(
            account['account_id'],
            symbol,
            {
                'session_date': eng.s.session,
                'active_line': eng.s.active_line,
                'below_count': eng.s.below_count,
                'stage': eng.s.stage,
                'pending_stage': eng.s.pending_stage,
                'pending_issued_at': eng.s.pending_issued_at.isoformat() if eng.s.pending_issued_at is not None else None,
                'pending_line': eng.s.pending_line,
                'pending_reason': eng.s.pending_reason,
                'cooldown_until_date': cooldown_until,
                'last_bar_start': eng.s.last_completed.isoformat() if eng.s.last_completed is not None else None,
            },
        )

        last_px = (
            clean_float((quotes or {}).get(symbol, {}).get('price'))
            or (round(float(current_bars.iloc[-1].close), 4) if not current_bars.empty else None)
            or clean_float(line_info['prior_close'])
        )

        if eng.s.pending_stage in (1, 2) and has_tactical and portfolio['total_equity'] > 1e-6:
            excess = max(0.0, actual_weight - core_floor)
            frac = cfg.first_stage_fraction if eng.s.pending_stage == 1 else 1.0
            target_w = max(core_floor, actual_weight - excess * frac)
            cut_w = max(0.0, actual_weight - target_w)
            ref_px = last_px or float(line_info['prior_close'])
            raw_sell = (cut_w * float(portfolio['total_equity']) / ref_px) if ref_px > 0 else 0.0
            planned_qty, t_plus_blocked = _round_sell_shares(
                raw_sell,
                float(pos['shares']),
                float(pos['sellable_shares']),
                meta['lot_size'],
            )
            est_amt = round(planned_qty * ref_px, 2)
            est_fee = round(est_amt * meta['default_fee_rate'], 2)
            issued_str = (
                eng.s.pending_issued_at.isoformat()
                if eng.s.pending_issued_at is not None
                else f'{session_day}T15:00:00'
            )
            warning = (
                f'受 T+1 限制，今日可卖上限 {pos["sellable_shares"]:g} 股，待次一交易日解除锁定后继续执行'
                if t_plus_blocked
                else ''
            )
            ticket_snapshot = {
                'symbol': symbol,
                'name': meta['name'],
                'bucket': meta['bucket'],
                'stage': int(eng.s.pending_stage),
                'line': round(float(eng.s.pending_line or eng.s.active_line), 4),
                'current_weight': round(actual_weight, 6),
                'target_weight': round(target_w, 6),
                'core_floor': core_floor,
                'orders': [
                    {
                        'symbol': symbol,
                        'name': meta['name'],
                        'bucket': meta['bucket'],
                        'side': 'SELL',
                        'quantity': planned_qty,
                        'lot_size': meta['lot_size'],
                        'sellable_shares': float(pos['sellable_shares']),
                        'ref_price': round(ref_px, 4),
                        'est_amount': est_amt,
                        'est_fee': est_fee,
                        't_plus': meta['t_plus'],
                        'warning': warning,
                        'reason': f'盘中风控第{eng.s.pending_stage}档有效跌破减仓（{eng.s.pending_reason}）',
                    }
                ],
                't_plus_blocked': t_plus_blocked,
                'cooldown_sessions': cfg.cooldown_sessions,
            }
            store.upsert_intraday_ticket(
                account['account_id'],
                signal_cutoff=f'{symbol}:{issued_str}',
                reason=f'{meta["name"]}({symbol}) 触发第{eng.s.pending_stage}档盘中减仓：{eng.s.pending_reason}',
                snapshot=ticket_snapshot,
            )

        active_line = clean_float(eng.s.active_line, ndigits=4)
        dist_pct = (
            round((last_px / active_line - 1.0) * 100.0, 2)
            if (last_px is not None and active_line is not None and active_line > 0)
            else None
        )
        out[symbol] = {
            'symbol': symbol,
            'name': meta['name'],
            'bucket': meta['bucket'],
            'session_date': session_day,
            'status': 'bars_unavailable' if bars_error else 'ready',
            'status_note': bars_error or ('正在监控战术仓位' if has_tactical else '仅持长期底仓，暂无超出底仓的战术仓位'),
            'active_line': active_line,
            'candidate_line': clean_float(line_info['line'], ndigits=4),
            'raw_trend_line': clean_float(line_info['raw_trend_line'], ndigits=4),
            'shock_line': clean_float(line_info['shock_line'], ndigits=4),
            'prior_close': clean_float(line_info['prior_close'], ndigits=4),
            'daily_sigma': clean_float(line_info['daily_sigma'], ndigits=5),
            'peak': clean_float(line_info['peak'], ndigits=4),
            'ma': clean_float(line_info['ma'], ndigits=4),
            'trend_prebroken': bool(line_info['trend_prebroken']),
            'last_price': clean_float(last_px, ndigits=4),
            'distance_pct': dist_pct,
            'below_count': int(eng.s.below_count),
            'stage': int(eng.s.stage),
            'pending_stage': int(eng.s.pending_stage),
            'pending_issued_at': eng.s.pending_issued_at.isoformat() if eng.s.pending_issued_at is not None else None,
            'pending_reason': str(eng.s.pending_reason or ''),
            'cooldown_until_date': cooldown_until,
            'cooldown_active': cd_active,
            'cooldown_remaining_days': cd_days,
            'actual_weight': round(actual_weight, 6),
            'core_floor': core_floor,
            'has_tactical': has_tactical,
            'observations': observations[-16:],
        }
    return out


def _apply_core_floors(
    target: dict[str, float],
    account: dict[str, Any],
) -> dict[str, float]:
    """Ensure account core_floors are respected by drawing from the cash ETF bucket if needed."""
    market = account['market']
    cash_bucket = INSTRUMENT_META[cash_etf_symbol(market)]['bucket']
    out = {k: float(v) for k, v in target.items()}
    for sym, floor_val in (account.get('core_floors') or {}).items():
        if sym not in INSTRUMENT_META or INSTRUMENT_META[sym]['market'] != market:
            continue
        b = INSTRUMENT_META[sym]['bucket']
        floor = float(floor_val)
        if b == cash_bucket or out.get(b, 0.0) >= floor:
            continue
        need = floor - out[b]
        give = min(need, max(0.0, out.get(cash_bucket, 0.0)))
        out[b] += give
        out[cash_bucket] -= give
        rem = need - give
        if rem > 1e-8:
            others = [k for k in out if k not in (b, cash_bucket) and out[k] > 0]
            avail = sum(out[k] for k in others)
            if avail > 1e-9:
                for k in others:
                    take = min(out[k], rem * (out[k] / avail))
                    out[k] -= take
                    out[b] += take
    total = sum(out.values())
    if total > 0:
        out = {k: v / total for k, v in out.items()}
    return out


def _normalize_weights(weights: dict[str, float]) -> dict[str, float]:
    cleaned = {k: max(0.0, float(v)) for k, v in weights.items()}
    s = sum(cleaned.values())
    if s <= 0:
        raise ValueError('组合权重和不能为 0')
    return {k: v / s for k, v in cleaned.items()}


def generate_order_lines(
    account: dict[str, Any],
    portfolio: dict[str, Any],
    effective_target: dict[str, float],
    *,
    reason_map: dict[str, str] | None = None,
) -> tuple[list[dict[str, Any]], float, float]:
    """Convert target bucket weights into executable, lot-rounded, T+1-safe order lines."""
    equity = float(portfolio['total_equity'])
    available_cash = float(portfolio['cash'])
    if equity <= 1e-6:
        return [], available_cash, 0.0

    market = account['market']
    cash_sym = cash_etf_symbol(market)
    cash_meta = INSTRUMENT_META[cash_sym]
    cash_bucket = cash_meta['bucket']
    pos_by_sym = {p['symbol']: p for p in portfolio['positions']}
    core_floors = account.get('core_floors', {})

    orders: list[dict[str, Any]] = []
    total_fee = 0.0
    non_cash_syms = [s for s in market_symbols(market) if s != cash_sym]

    # 1. Sell overweight non-cash positions first to release cash
    for sym in non_cash_syms:
        meta = INSTRUMENT_META[sym]
        pos = pos_by_sym[sym]
        ref_px = clean_float(pos.get('mark_price'))
        if ref_px is None or ref_px <= 0:
            continue
        target_val = float(effective_target.get(meta['bucket'], 0.0)) * equity
        cur_val = float(pos['market_value'])
        if cur_val - target_val <= 1.0:
            continue

        floor_val = float(core_floors.get(sym, 0.0)) * equity
        reducible_val = max(0.0, cur_val - floor_val)
        sell_val = min(cur_val - target_val, reducible_val)
        if sell_val <= 1.0:
            continue

        raw_qty = sell_val / ref_px
        qty, t_plus_blocked = _round_sell_shares(
            raw_qty,
            float(pos['shares']),
            float(pos['sellable_shares']),
            meta['lot_size'],
        )
        if qty <= 0 and not t_plus_blocked:
            continue

        warning = (
            f'受 T+1 限制，今日可卖 {pos["sellable_shares"]:g} 股，未卖部分需次日处理'
            if t_plus_blocked
            else ''
        )
        amt = round(qty * ref_px, 2)
        fee = round(amt * meta['default_fee_rate'], 2)
        if qty > 0:
            available_cash += amt - fee
            total_fee += fee
        orders.append(
            {
                'symbol': sym,
                'name': meta['name'],
                'bucket': meta['bucket'],
                'side': 'SELL',
                'quantity': qty,
                'lot_size': meta['lot_size'],
                'sellable_shares': float(pos['sellable_shares']),
                'ref_price': round(ref_px, 4),
                'est_amount': amt,
                'est_fee': fee,
                't_plus': meta['t_plus'],
                'warning': warning,
                'reason': (reason_map or {}).get(sym)
                or f'目标 {effective_target[meta["bucket"]]*100:.1f}% < 实盘 {pos["actual_weight"]*100:.1f}%',
            }
        )

    # 2. Check non-cash buy demand; sell cash ETF if cash is short
    buy_candidates: list[tuple[float, str]] = []
    total_buy_cash_needed = 0.0
    for sym in non_cash_syms:
        meta = INSTRUMENT_META[sym]
        pos = pos_by_sym[sym]
        ref_px = clean_float(pos.get('mark_price'))
        if ref_px is None or ref_px <= 0:
            continue
        diff_val = float(effective_target.get(meta['bucket'], 0.0)) * equity - float(pos['market_value'])
        if diff_val > 1.0:
            buy_candidates.append((diff_val, sym))
            total_buy_cash_needed += diff_val * (1.0 + meta['default_fee_rate'])

    sold_cash_etf = False
    cash_pos = pos_by_sym[cash_sym]
    cash_px = clean_float(cash_pos.get('mark_price'))
    if available_cash < total_buy_cash_needed and cash_pos['sellable_shares'] > 0 and cash_px and cash_px > 0:
        shortfall = total_buy_cash_needed - available_cash
        lot = cash_meta['lot_size']
        raw_cash_sell = shortfall / (cash_px * (1.0 - cash_meta['default_fee_rate']))
        max_cash_sell = (
            float(math.floor(cash_pos['sellable_shares'] / lot) * lot)
            if lot > 1
            else float(cash_pos['sellable_shares'])
        )
        ceil_qty = (
            float(math.ceil(raw_cash_sell / lot) * lot)
            if lot > 1
            else round(raw_cash_sell, 4)
        )
        sell_cash_qty = min(ceil_qty, max_cash_sell)
        if sell_cash_qty > 0:
            sold_cash_etf = True
            amt = round(sell_cash_qty * cash_px, 2)
            fee = round(amt * cash_meta['default_fee_rate'], 2)
            available_cash += amt - fee
            total_fee += fee
            orders.append(
                {
                    'symbol': cash_sym,
                    'name': cash_meta['name'],
                    'bucket': cash_bucket,
                    'side': 'SELL',
                    'quantity': sell_cash_qty,
                    'lot_size': lot,
                    'sellable_shares': float(cash_pos['sellable_shares']),
                    'ref_price': round(cash_px, 4),
                    'est_amount': amt,
                    'est_fee': fee,
                    't_plus': cash_meta['t_plus'],
                    'warning': '',
                    'reason': '赎回现金管理工具以支付再平衡买入',
                }
            )

    # 3. Execute BUY orders on non-cash ETFs within available cash (zero leverage)
    buy_candidates.sort(key=lambda item: item[0], reverse=True)
    for diff_val, sym in buy_candidates:
        meta = INSTRUMENT_META[sym]
        pos = pos_by_sym[sym]
        ref_px = clean_float(pos.get('mark_price'))
        if ref_px is None or ref_px <= 0:
            continue
        lot = meta['lot_size']
        unit_cost = ref_px * (1.0 + meta['default_fee_rate'])
        desired_qty = (
            float(math.floor((diff_val / ref_px) / lot) * lot)
            if lot > 1
            else round(diff_val / ref_px, 4)
        )
        max_afford = (
            float(math.floor((available_cash / unit_cost) / lot) * lot)
            if lot > 1
            else round(available_cash / unit_cost, 4)
        )
        qty = min(desired_qty, max_afford)
        if qty <= 0:
            continue
        amt = round(qty * ref_px, 2)
        fee = round(amt * meta['default_fee_rate'], 2)
        if amt + fee > available_cash + 0.01:
            continue
        available_cash -= amt + fee
        total_fee += fee
        orders.append(
            {
                'symbol': sym,
                'name': meta['name'],
                'bucket': meta['bucket'],
                'side': 'BUY',
                'quantity': qty,
                'lot_size': lot,
                'sellable_shares': float(pos['sellable_shares']),
                'ref_price': round(ref_px, 4),
                'est_amount': amt,
                'est_fee': fee,
                't_plus': meta['t_plus'],
                'warning': '买入后当日锁定（T+1），下一交易日才可卖出' if meta['t_plus'] == 1 else '',
                'reason': (reason_map or {}).get(sym)
                or f'目标 {effective_target[meta["bucket"]]*100:.1f}% > 实盘 {pos["actual_weight"]*100:.1f}%',
            }
        )

    # 4. Sweep surplus cash into cash ETF (511880.SH / BIL.US) when targeted
    if not sold_cash_etf and cash_px and cash_px > 0:
        desired_cash_etf_val = max(
            0.0,
            float(effective_target.get(cash_bucket, 0.0)) * equity - float(cash_pos['market_value']),
        )
        if desired_cash_etf_val > 1.0:
            lot = cash_meta['lot_size']
            unit_cost = cash_px * (1.0 + cash_meta['default_fee_rate'])
            budget = min(available_cash, desired_cash_etf_val * (1.0 + cash_meta['default_fee_rate']))
            buy_cash_qty = (
                float(math.floor((budget / unit_cost) / lot) * lot)
                if lot > 1
                else round(budget / unit_cost, 4)
            )
            if buy_cash_qty > 0:
                amt = round(buy_cash_qty * cash_px, 2)
                fee = round(amt * cash_meta['default_fee_rate'], 2)
                if amt + fee <= available_cash + 0.01:
                    available_cash -= amt + fee
                    total_fee += fee
                    orders.append(
                        {
                            'symbol': cash_sym,
                            'name': cash_meta['name'],
                            'bucket': cash_bucket,
                            'side': 'BUY',
                            'quantity': buy_cash_qty,
                            'lot_size': lot,
                            'sellable_shares': float(cash_pos['sellable_shares']),
                            'ref_price': round(cash_px, 4),
                            'est_amount': amt,
                            'est_fee': fee,
                            't_plus': cash_meta['t_plus'],
                            'warning': '',
                            'reason': f'闲置现金配置至 {cash_meta["name"]}（留存零头现金）',
                        }
                    )

    return orders, round(max(0.0, available_cash), 2), round(total_fee, 2)


def compute_execution_window(
    market: str,
    signal_cutoff: str | None,
    clock=None,
    now: pd.Timestamp | None = None,
) -> dict[str, Any]:
    """Compute the earliest valid execution date/window after signal_cutoff."""
    now_ts = pd.Timestamp(now) if now is not None else utc_now()
    if now_ts.tzinfo is None:
        now_ts = now_ts.tz_localize('UTC')
    today_str = market_today(market, now_ts)
    if not signal_cutoff:
        return {
            'today_local_date': today_str,
            'earliest_execution_date': None,
            'window_open': False,
            'window_label': '暂无有效信号截点',
        }
    earliest = add_trading_sessions(str(signal_cutoff), 1, market, clock)
    local_now = now_ts.tz_convert(ZONES[market])
    window_open = (str(today_str) > str(earliest)) or (
        str(today_str) == str(earliest) and (local_now.hour, local_now.minute) >= (9, 30)
    )
    if market == 'CN':
        label = f'{earliest} 09:30 A股开盘起（周频基准为下周收盘）'
    else:
        label = f'{earliest} 09:30 美股开盘起 (美东时间)'
    return {
        'today_local_date': today_str,
        'earliest_execution_date': earliest,
        'window_open': window_open,
        'window_label': label,
    }


def evaluate_account_data_gate(
    account: dict[str, Any],
    weekly_model: dict[str, Any] | None,
    strategy_status_entry: dict[str, Any] | None,
    quotes_map: dict[str, dict[str, Any]] | None,
    clock,
    now: pd.Timestamp,
    *,
    market_status_entry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate independently per market whether data supports executing the plan."""
    market = account['market']
    model = weekly_model or {}
    origin = str(model.get('origin') or 'frozen_research_snapshot')
    cutoff = str(model.get('cutoff') or '')
    st_entry = strategy_status_entry or {}
    st_state = str(st_entry.get('state') or 'unavailable')
    st_msg = str(st_entry.get('message') or '')
    mkt_status = market_status_entry or clock.status(market, now)
    cal_state = str(mkt_status.get('state') or 'calendar_unverified')

    reasons: list[str] = []
    if origin != 'online_recomputed_simulation':
        reasons.append(st_msg or f'{market} 策略尚未完成在线重算（当前仅离线研究基线）')
    elif st_state == 'unavailable':
        reasons.append(st_msg or f'{market} 策略重算不可用')
    elif st_state == 'stale':
        reasons.append(st_msg or f'{market} 周线数据已过期，未覆盖最新完整交易周')
    elif st_state == 'unverified' or cal_state == 'calendar_unverified':
        reasons.append(st_msg or f'{market} 交易日历未核验，不能确认完整交易周与执行窗口')
    elif st_entry.get('connection_degraded'):
        reasons.append('行情服务处于降级或重启缓存状态，暂不按自动计划执行，请刷新确认后再处理')

    # Verify fresh live quotes for all required assets in this market
    q_map = quotes_map or {}
    for sym in market_symbols(market):
        q = q_map.get(sym)
        if not q or q.get('price') is None or q.get('source') in ('research_close', 'daily_raw'):
            reasons.append(f'{INSTRUMENT_META[sym]["name"]}({sym}) 缺少实时/收盘报价')
        elif q.get('refresh_failed'):
            reasons.append(f'{INSTRUMENT_META[sym]["name"]}({sym}) 行情刷新失败，报价可能过期')
        elif cal_state == 'open' and q.get('stale'):
            reasons.append(f'{INSTRUMENT_META[sym]["name"]}({sym}) 盘中报价已过期（超过3分钟未更新）')

    win = compute_execution_window(market, cutoff or None, clock, now)
    if reasons:
        status = 'BLOCKED'
        can_execute_plan = False
        actionable = False
    elif not win['window_open']:
        status = 'WAIT_WINDOW'
        can_execute_plan = False
        actionable = False
        reasons.append(f'周频信号形成于 {cutoff} 收盘，须等待 {win["window_label"]} 方可执行')
    else:
        status = 'READY'
        can_execute_plan = True
        actionable = True

    return {
        'status': status,
        'actionable': actionable,
        'can_execute_plan': can_execute_plan,
        'reasons': reasons,
        'signal_cutoff': cutoff or None,
        'signal_origin': origin,
        'signal_time_label': f'{cutoff} 收盘后' if cutoff else '暂无',
        'earliest_execution_date': win['earliest_execution_date'],
        'earliest_execution_window': win['window_label'],
        'window_open': win['window_open'],
        'market_state': cal_state,
        'market_state_label': str(mkt_status.get('label') or cal_state),
        'strategy_state': st_state,
    }


def build_rebalance_plan(
    account: dict[str, Any],
    portfolio: dict[str, Any],
    weekly_model: dict[str, Any],
    stop_states: dict[str, dict[str, Any]],
    today_local_date: str,
    pending_tickets: list[dict[str, Any]] | None = None,
    *,
    data_gate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Arbitrate weekly portfolio target against persistent intraday stops & cooldowns."""
    market = account['market']
    cash_sym = cash_etf_symbol(market)
    cash_bucket = INSTRUMENT_META[cash_sym]['bucket']
    raw_target = {k: float(v) for k, v in (weekly_model.get('target') or {}).items()}
    if not raw_target:
        raise ValueError(f'{market} 缺少周频策略目标权重')

    floored_target = _apply_core_floors(_normalize_weights(raw_target), account)

    if portfolio['total_equity'] > 1e-6:
        current_for_clip = dict(portfolio['bucket_weights'])
        current_for_clip[cash_bucket] = current_for_clip.get(cash_bucket, 0.0) + float(portfolio.get('cash_weight', 0.0))
        current_for_clip = _normalize_weights(current_for_clip)
    else:
        current_for_clip = {k: (1.0 if k == cash_bucket else 0.0) for k in floored_target}

    frozen_buckets: set[str] = set()
    cooldown_clips: list[dict[str, Any]] = []
    for sym, st in (stop_states or {}).items():
        if sym not in INSTRUMENT_META or INSTRUMENT_META[sym]['market'] != market:
            continue
        if st.get('cooldown_active') or int(st.get('pending_stage') or 0) > 0:
            b = INSTRUMENT_META[sym]['bucket']
            frozen_buckets.add(b)

    effective_target = clip_weekly_risk_additions(
        floored_target,
        current_for_clip,
        frozen_buckets,
        market=market,
    )
    for b in frozen_buckets:
        if floored_target.get(b, 0.0) > effective_target.get(b, 0.0) + 1e-4:
            sym = bucket_to_symbol(market, b)
            st = stop_states.get(sym, {})
            cooldown_clips.append(
                {
                    'symbol': sym,
                    'bucket': b,
                    'name': INSTRUMENT_META[sym]['name'],
                    'weekly_target': round(floored_target[b], 4),
                    'clipped_to': round(effective_target[b], 4),
                    'cooldown_until_date': st.get('cooldown_until_date'),
                    'pending_stage': int(st.get('pending_stage') or 0),
                    'reason': '日内有效破位减仓冷却期内，禁止周频加仓，拦截金额转向现金类工具',
                }
            )

    turnover = sum(abs(effective_target[b] - current_for_clip.get(b, 0.0)) for b in effective_target)
    if market == 'CN':
        tech_exit = effective_target.get('tech', 0.0) <= 1e-6 and current_for_clip.get('tech', 0.0) > 0.01
        eq_drop = (
            current_for_clip.get('tech', 0.0)
            + current_for_clip.get('red', 0.0)
            - effective_target.get('tech', 0.0)
            - effective_target.get('red', 0.0)
        )
        priority_exit = bool(tech_exit or eq_drop > 0.04 + 1e-9)
    else:
        priority_exit = bool(current_for_clip.get('qqq', 0.0) - effective_target.get('qqq', 0.0) > 0.04 + 1e-9)

    pending_intraday = [
        t
        for t in (pending_tickets or [])
        if t.get('source') == 'INTRADAY_STOP' and t.get('status') in ('PENDING', 'PARTIAL')
    ]
    orders, projected_cash, total_est_fee = generate_order_lines(account, portfolio, effective_target)

    deadband = float(account['rebalance_deadband'])
    gate_status = (data_gate or {}).get('status', 'READY')
    gate_reasons = (data_gate or {}).get('reasons') or []

    if gate_status == 'BLOCKED':
        verdict = 'DATA_BLOCKED'
        verdict_label = '数据暂不完整，暂停按计划操作'
        verdict_reason = '；'.join(gate_reasons) if gate_reasons else '行情或策略数据尚未就绪，暂不生成可执行计划；仍可手动补录已发生交易。'
    elif not portfolio['initialized'] or portfolio['total_equity'] <= 1e-6:
        verdict = 'NEEDS_INIT'
        verdict_label = '待初始化账户持仓'
        verdict_reason = '先录入当前账户现金与已有持仓，系统即可生成精确到股数的调仓建议'
    elif pending_intraday:
        verdict = 'ACTION_INTRADAY_STOP'
        verdict_label = '盘中破位防守优先'
        verdict_reason = (
            f'有 {len(pending_intraday)} 笔盘中防守减仓单待确认，优先降低风险敞口，暂缓本周风险加仓'
        )
    elif gate_status == 'WAIT_WINDOW':
        verdict = 'WAIT_EXECUTION_WINDOW'
        verdict_label = '信号已生成，等待执行窗口'
        verdict_reason = '；'.join(gate_reasons) if gate_reasons else '尚未到达最早可执行交易窗口，请于开盘后处理'
    elif priority_exit and orders:
        verdict = 'ACTION_PRIORITY_EXIT'
        verdict_label = '风控优先减仓'
        verdict_reason = '周频信号触发权益显著收缩或创业板清仓，按风控优先纪律绕过调仓阈值立即减仓'
    elif turnover >= deadband - 1e-9 and orders:
        verdict = 'ACTION_REBALANCE'
        verdict_label = '建议按周频调仓'
        verdict_reason = f'当前仓位与目标仓位双边偏离 {turnover*100:.1f}%，已达到 {deadband*100:.0f}% 调仓阈值'
    else:
        verdict = 'HOLD_WITHIN_DEADBAND'
        verdict_label = '继续持有，暂无需调仓'
        verdict_reason = (
            f'当前仓位偏离 {turnover*100:.1f}%，未达到 {deadband*100:.0f}% 的调仓阈值，继续持有以减少交易磨损'
            if turnover < deadband - 1e-9
            else '当前持仓已贴近目标，差额不足最小交易单位，无需操作'
        )

    can_execute_plan = (data_gate.get('can_execute_plan', True) if data_gate else True) and portfolio['initialized'] and portfolio['total_equity'] > 1e-6
    should_execute = bool(
        can_execute_plan
        and verdict in ('ACTION_INTRADAY_STOP', 'ACTION_PRIORITY_EXIT', 'ACTION_REBALANCE')
    )

    weights_comparison: list[dict[str, Any]] = []
    pos_by_sym = {p['symbol']: p for p in portfolio['positions']}
    for sym in market_symbols(market):
        meta = INSTRUMENT_META[sym]
        b = meta['bucket']
        pos = pos_by_sym[sym]
        st = (stop_states or {}).get(sym, {})
        actual_eff = current_for_clip.get(b, 0.0) if sym == cash_sym else float(pos['actual_weight'])
        eff_t = float(effective_target.get(b, 0.0))
        weights_comparison.append(
            {
                'symbol': sym,
                'bucket': b,
                'name': meta['name'],
                'actual_weight': round(float(pos['actual_weight']), 5),
                'effective_actual_weight': round(actual_eff, 5),
                'weekly_target': round(float(raw_target.get(b, 0.0)), 5),
                'effective_target': round(eff_t, 5),
                'drift': round(eff_t - actual_eff, 5),
                'core_floor': float(account['core_floors'].get(sym, 0.0)),
                'cooldown_active': bool(st.get('cooldown_active', False)),
                'cooldown_until_date': st.get('cooldown_until_date'),
                'pending_stage': int(st.get('pending_stage') or 0),
                't_plus': meta['t_plus'],
            }
        )

    sig_cutoff = str(weekly_model.get('cutoff') or '')
    sig_origin = str(weekly_model.get('origin') or 'frozen_research_snapshot')
    sig_sha = hashlib.sha256(
        json.dumps(
            {'market': market, 'cutoff': sig_cutoff, 'target': raw_target, 'version': account['strategy_version']},
            sort_keys=True,
        ).encode('utf-8')
    ).hexdigest()

    return {
        'account_id': account['account_id'],
        'market': market,
        'currency': account['currency'],
        'as_of_date': today_local_date,
        'strategy_version': account['strategy_version'],
        'strategy_label': str(weekly_model.get('label') or account['strategy_version']),
        'signal_cutoff': sig_cutoff,
        'signal_week': str(weekly_model.get('signalWeek') or ''),
        'signal_origin': sig_origin,
        'signal_sha256': sig_sha,
        'last_reason': str(weekly_model.get('lastReason') or ''),
        'verdict': verdict,
        'verdict_label': verdict_label,
        'verdict_reason': verdict_reason,
        'should_execute': should_execute,
        'can_execute_plan': can_execute_plan,
        'data_gate': data_gate,
        'turnover': round(turnover, 5),
        'deadband': deadband,
        'priority_exit': priority_exit,
        'cooldown_clips': cooldown_clips,
        'weights_comparison': weights_comparison,
        'orders': orders,
        'projected_cash_after': projected_cash,
        'total_est_fee': total_est_fee,
        'pending_intraday_count': len(pending_intraday),
    }


def create_weekly_decision_ticket(
    store: TradingStore,
    plan: dict[str, Any],
    *,
    source: str = 'WEEKLY_REBALANCE',
) -> dict[str, Any]:
    """Freeze a plan into an auditable decision ticket in SQLite."""
    return store.create_ticket(
        plan['account_id'],
        source=source,
        signal_cutoff=plan['signal_cutoff'] or plan['as_of_date'],
        signal_sha256=plan.get('signal_sha256'),
        reason=f'{plan["verdict_label"]}：{plan["verdict_reason"]}',
        snapshot=plan,
    )


def execute_decision_ticket(
    store: TradingStore,
    account_id: str,
    decision_id: int,
    *,
    trade_date: str,
    event_time: str | None = None,
    fills: list[dict[str, Any]],
    discipline_tag: str = 'FOLLOW',
    review_note: str = '',
    status: str | None = None,
    ignore_t_plus: bool = False,
    client_request_id: str | None = None,
    clock=None,
) -> dict[str, Any]:
    """Commit fills for a decision ticket and advance stop/cooldown state machines."""
    account = store.get_account(account_id)
    ticket = store.get_ticket(decision_id)
    market = account['market']
    td = normalize_trade_date(trade_date)

    resolved = store.resolve_ticket_with_fills(
        account_id,
        decision_id,
        trade_date=td,
        event_time=event_time,
        fills=fills,
        discipline_tag=discipline_tag,
        review_note=review_note,
        status=status,
        ignore_t_plus=ignore_t_plus,
        today_local_date=td,
        client_request_id=client_request_id,
    )

    if resolved.get('duplicate'):
        return {
            **resolved,
            'stop_states': store.get_stop_states(account_id),
        }

    monitored = set(risk_monitored_symbols(market))
    stop_states = store.get_stop_states(account_id)

    if ticket['source'] == 'INTRADAY_STOP':
        snap = ticket.get('snapshot') or {}
        ticket_stage = int(snap.get('stage') or 1)
        cooldown_until = add_trading_sessions(td, 3, market, clock)
        for fill in fills:
            sym = str(fill.get('symbol') or '').strip()
            if str(fill.get('side', '')).upper() == 'SELL' and sym in monitored:
                cur = dict(stop_states.get(sym) or {})
                cur['stage'] = max(int(cur.get('stage') or 0), ticket_stage)
                cur['pending_stage'] = 0
                cur['pending_issued_at'] = None
                cur['pending_line'] = None
                cur['pending_reason'] = ''
                cur['cooldown_until_date'] = cooldown_until
                store.save_stop_state(account_id, sym, cur)
    elif ticket['source'] == 'WEEKLY_REBALANCE':
        for fill in fills:
            sym = str(fill.get('symbol') or '').strip()
            if str(fill.get('side', '')).upper() == 'BUY' and sym in monitored:
                cur = stop_states.get(sym) or {}
                if not is_cooldown_active(cur.get('cooldown_until_date'), td):
                    store.reset_stop_state(
                        account_id,
                        sym,
                        reason=f'周频票据 #{decision_id} 买入建仓，重置新一轮移动止损线',
                        clear_cooldown=True,
                    )

    return {
        **resolved,
        'stop_states': store.get_stop_states(account_id),
    }
