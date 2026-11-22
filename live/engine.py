"""Recompute existing production portfolio and advisory engines using a separate, coherent live dataset."""
from __future__ import annotations

import hashlib
import pandas as pd

from live.market import SYMBOLS, ZONES, RISK_SYMBOLS, validate_frame
from live.backtest import run_bt_reference
from live.store import clean_bool, clean_float
from strategy_core.portfolio import build_china_portfolio_weekly, build_us_portfolio
from strategy_core.advisory import run_advisory_cn, run_advisory_us, evaluate_china_rule_blend, evaluate_us_rule_blend
from strategy_core.public_rules import PublicRuleConfig
from strategy_core.analytics import describe, equity_curve, yearly, latest_weights, frozen_signal_record, verify_market_history
from strategy_core.intraday import StopConfig, IntradayStopEngine, normalize_bars, stop_line


def extract_cn_indicators(p: pd.DataFrame, sig_df: pd.DataFrame, market_tape: pd.DataFrame) -> dict:
    """Extract rich decision-grade CN indicators from already-computed production portfolio signals."""
    s = sig_df.iloc[-1]
    t = market_tape.iloc[-1]
    ratio_series = market_tape.market_turn_100m.rolling(4).mean() / market_tape.market_turn_100m.rolling(13).mean()
    m_turn = float(t.market_turn_100m) if float(t.market_turn_100m) > 0 else 1.0
    tech_close = clean_float(s.get('tech'), ndigits=4)
    red_close = clean_float(s.get('red'), ndigits=4)
    bond_close = clean_float(s.get('bond_511260_adj'), ndigits=4)
    gold_close = clean_float(s.get('gold_518880_adj'), ndigits=4)
    money_close = clean_float(s.get('cash_511880_adj'), ndigits=4)
    return {
        'asof_week_start': str(sig_df.index[-1].date()),
        'leader': str(s.get('leader', '')),
        'tech': {
            'close': tech_close,
            'temperature': str(s.get('tech_temperature', '')),
            'stage': int(s.get('tech_stage', 0)),
            'mom_fast': clean_float(s.get('tech_mom_fast'), ndigits=5),
            'mom_slow': clean_float(s.get('tech_mom_slow'), ndigits=5),
            'momentum': clean_float(s.get('tech_momentum'), ndigits=5),
            'r1': clean_float(s.get('tech_r1'), ndigits=5),
            'ma_fast': clean_float(s.get('tech_ma_fast'), ndigits=4),
            'ma_slow': clean_float(s.get('tech_ma_slow'), ndigits=4),
            'ma_slope': clean_float(s.get('tech_ma_slope'), ndigits=6),
            'below_ma': clean_bool(s.get('tech_below_ma')),
            'breakout': clean_bool(s.get('tech_breakout')),
            'vol': clean_float(s.get('tech_vol'), ndigits=5),
            'dd': clean_float(s.get('tech_dd'), ndigits=5),
            'vs_red_6w': clean_float(s.get('tech_vs_dividend_6w'), ndigits=5),
            'relative_mom': clean_float(s.get('relative'), ndigits=5),
            'reentry_state': str(s.get('reentry_state', 'off')),
            'probe_signal': clean_bool(s.get('reentry_probe_signal')),
            'confirm_signal': clean_bool(s.get('reentry_confirm_signal')),
            'target_weight': clean_float(s.get('target_tech'), ndigits=5),
        },
        'red': {
            'close': red_close,
            'temperature': str(s.get('red_temperature', '')),
            'stage': int(s.get('red_stage', 0)),
            'mom_fast': clean_float(s.get('red_mom_fast'), ndigits=5),
            'mom_slow': clean_float(s.get('red_mom_slow'), ndigits=5),
            'momentum': clean_float(s.get('red_momentum'), ndigits=5),
            'r1': clean_float(s.get('red_r1'), ndigits=5),
            'ma_fast': clean_float(s.get('red_ma_fast'), ndigits=4),
            'ma_slow': clean_float(s.get('red_ma_slow'), ndigits=4),
            'ma_slope': clean_float(s.get('red_ma_slope'), ndigits=6),
            'below_ma': clean_bool(s.get('red_below_ma')),
            'breakout': clean_bool(s.get('red_breakout')),
            'vol': clean_float(s.get('red_vol'), ndigits=5),
            'dd': clean_float(s.get('red_dd'), ndigits=5),
            'eligible': clean_bool(s.get('dividend_eligible')),
            'target_weight': clean_float(s.get('target_red'), ndigits=5),
        },
        'defensive': {
            'bond_close': bond_close,
            'bond_eligible': clean_bool(s.get('bond_eligible')),
            'bond_trend': clean_bool(s.get('bond_trend')),
            'bond_mom_fast': clean_float(s.get('bond_momentum_fast'), ndigits=5),
            'bond_mom_slow': clean_float(s.get('bond_momentum_slow'), ndigits=5),
            'bond_vol': clean_float(s.get('bond_vol'), ndigits=5),
            'bond_target': clean_float(s.get('target_bond'), ndigits=5),
            'gold_close': gold_close,
            'gold_eligible': clean_bool(s.get('gold_eligible')),
            'gold_trend': clean_bool(s.get('gold_trend')),
            'gold_mom_fast': clean_float(s.get('gold_momentum_fast'), ndigits=5),
            'gold_mom_slow': clean_float(s.get('gold_momentum_slow'), ndigits=5),
            'gold_vol': clean_float(s.get('gold_vol'), ndigits=5),
            'gold_target': clean_float(s.get('target_gold'), ndigits=5),
            'money_close': money_close,
            'money_target': clean_float(s.get('target_money'), ndigits=5),
        },
        'portfolio': {
            'uncapped_vol': clean_float(s.get('uncapped_portfolio_vol'), ndigits=5),
            'estimated_vol': clean_float(s.get('estimated_portfolio_vol'), ndigits=5),
            'risk_multiplier': clean_float(s.get('portfolio_risk_multiplier'), ndigits=5),
            'allocation_reason': str(s.get('allocation_reason', '')),
            'regime_active': clean_bool(s.get('regime_active')),
            'regime_explanation': str(s.get('regime_explanation', '')),
            'market_warning': clean_bool(s.get('market_warning')),
            'market_reason': str(s.get('market_reason', '')),
            'market_weak': clean_bool(s.get('market_weak')),
            'crowding_proxy': clean_bool(s.get('crowding_proxy')),
            'growth_leader_weak': clean_bool(s.get('growth_leader_weak')),
            'market_turn_100m': clean_float(t.market_turn_100m, ndigits=2),
            'market_liquidity_ratio': clean_float(ratio_series.iloc[-1], ndigits=4),
            'tech_share_pct': clean_float(float(t.tech_turn_100m) / m_turn * 100.0, ndigits=2),
            'red_share_pct': clean_float(float(t.red_turn_100m) / m_turn * 100.0, ndigits=2),
            'us_risk_state_lagged': str(s.get('us_risk_state_lagged', '')),
            'us_source_week': str(pd.Timestamp(s['us_source_week']).date()) if pd.notna(s.get('us_source_week')) else None,
            'cross_market_reason': str(s.get('cross_market_reason', '')),
            'cross_market_tech_cut': clean_float(s.get('cross_market_tech_cut'), ndigits=5),
        },
    }


def extract_us_indicators(p: pd.DataFrame, sig_df: pd.DataFrame) -> dict:
    """Extract rich decision-grade US indicators from already-computed production portfolio signals."""
    s = sig_df.iloc[-1]
    qqq_close = float(p.QQQ.iloc[-1])
    qqq_10w = float(p.QQQ.tail(10).mean())
    qqq_20w = float(p.QQQ.tail(20).mean())
    qqq_30w = float(p.QQQ.tail(30).mean())
    return {
        'asof_week_start': str(sig_df.index[-1].date()),
        'risk_state': str(s.get('us_risk_state', '')),
        'trend_decision': str(s.get('us_trend_decision', '')),
        'trend_score': clean_float(s.get('us_trend_score'), ndigits=4),
        'realized_vol': clean_float(s.get('us_realized_vol'), ndigits=5),
        'vol_cap': 0.24,
        'vol_multiplier': clean_float(s.get('us_vol_mult'), ndigits=4),
        'qqq_close': clean_float(qqq_close, ndigits=3),
        'qqq_10w': clean_float(qqq_10w, ndigits=3),
        'qqq_20w': clean_float(qqq_20w, ndigits=3),
        'qqq_30w': clean_float(qqq_30w, ndigits=3),
        'qqq_dist_10w_pct': clean_float((qqq_close / qqq_10w - 1.0) * 100.0, ndigits=2) if qqq_10w > 0 else None,
        'qqq_dist_20w_pct': clean_float((qqq_close / qqq_20w - 1.0) * 100.0, ndigits=2) if qqq_20w > 0 else None,
        'qqq_dist_30w_pct': clean_float((qqq_close / qqq_30w - 1.0) * 100.0, ndigits=2) if qqq_30w > 0 else None,
        'qqq_1w': clean_float(s.get('qqq_1w'), ndigits=5),
        'qqq_4w': clean_float(s.get('qqq_4w'), ndigits=5),
        'above_30w': clean_bool(s.get('above_30w')),
        'spy_close': clean_float(p.SPY.iloc[-1], ndigits=3),
        'spy_13w': clean_float(s.get('spy_13w'), ndigits=3),
        'spy_30w': clean_float(s.get('spy_30w'), ndigits=3),
        'spy_4w': clean_float(s.get('spy_4w'), ndigits=5),
        'smh_relative_4w': clean_float(s.get('smh_relative_4w'), ndigits=5),
        'rsp_relative_4w': clean_float(s.get('rsp_relative_4w'), ndigits=5),
        'us_bear': clean_bool(s.get('us_bear')),
        'us_shock': clean_bool(s.get('us_shock')),
        'us_warning': clean_bool(s.get('us_warning')),
        'target_qqq': clean_float(s.get('target_qqq'), ndigits=5),
        'target_bil': clean_float(s.get('target_bil'), ndigits=5),
    }


def build_research_indicators(root) -> dict:
    """Compute baseline portfolio indicators from offline verified snapshots for fallback use."""
    try:
        from pathlib import Path
        from strategy_core.market_snapshot import load_weekly
        from strategy_core.us_snapshot import load_us_prices, load_us_open

        base_dir = Path(root)
        cn_path = base_dir / 'market_data' / 'verified_5asset_weekly_v13.csv'
        if not cn_path.exists():
            return {}
        cn = pd.read_csv(cn_path, parse_dates=['week_start']).set_index('week_start')
        us = load_us_prices()
        op = load_us_open()
        tape = load_weekly()
        _, cn_sig, _ = build_china_portfolio_weekly(cn, tape, us)
        us_sig, _ = build_us_portfolio(us, opens=op)
        return {
            'CN': extract_cn_indicators(cn, cn_sig, tape),
            'US': extract_us_indicators(us, us_sig),
        }
    except Exception:
        return {}


def weekly_prices(daily, market, clock, now):
    closes, opens, dates, completed = {}, {}, [], {}
    for symbol, (_, region, key, _) in SYMBOLS.items():
        if region != market:
            continue
        frame = clock.complete_daily(validate_frame(daily[symbol]['adjusted']), market, now)
        if frame.empty:
            raise ValueError('没有已完成日线：' + symbol)
        closes[key], opens[key] = frame.close, frame.open
        completed[symbol] = frame
        dates.append(str(frame.index[-1].date()))
    if len(set(dates)) != 1:
        raise ValueError('资产末日不一致，拒绝混合过期价格')
    start = max(s.first_valid_index() for s in closes.values())
    weekly_closes, weekly_opens = {}, {}
    for key in closes:
        c = closes[key].loc[start:]
        o = opens[key].loc[start:]
        if c.isna().any() or o.isna().any():
            raise ValueError('资产日线存在无效空值，未填充虚构价格')
        labels = c.index.to_period('W-SUN').start_time
        weekly_closes[key] = c.groupby(labels).last()
        weekly_opens[key] = o.groupby(labels).first()
    p, op = pd.DataFrame(weekly_closes), pd.DataFrame(weekly_opens)
    keep = [clock.week_complete(t, market, now) for t in p.index]
    p, op = p.loc[keep], op.loc[keep]
    if p.isna().any().any() or op.isna().any().any():
        raise ValueError('资产周线缺失，未填充虚构价格')
    p.index.name = op.index.name = 'week_start'
    verify_market_history(p, label=market)
    cutoff = max(f.index[f.index.to_period('W-SUN').start_time == p.index[-1]][-1] for f in completed.values())
    return p, op, str(cutoff.date())


def weekly_tape(tape, cn, clock, now):
    parts = {}
    for key in ('market', 'tech', 'star', 'red'):
        # complete_daily needs open/close; index turnover is never ETF volume.
        f = tape[key].copy()
        f['open'] = f.close
        f = clock.complete_daily(f, 'CN', now)
        parts[key] = f
    if len({f.index[-1] for f in parts.values()}) != 1:
        raise ValueError('指数成交额数据日期不一致')
    combined = pd.concat({k: f.turnover for k, f in parts.items()}, axis=1)
    combined = combined.loc[max(f.index[0] for f in parts.values()):]
    if combined.isna().any().any():
        raise ValueError('指数成交额缺失，不能生成拥挤度信号')
    labels = combined.index.to_period('W-SUN').start_time
    out = combined.groupby(labels).mean().rename(columns={k: k+'_turn_100m' for k in parts}) / 1e8
    out['market_close'] = parts['market'].close.groupby(parts['market'].index.to_period('W-SUN').start_time).last()
    out = out.loc[[clock.week_complete(t, 'CN', now) for t in out.index]]
    out = out.loc[out.index.isin(cn.index)]
    if len(out) < 52 or out.index[-1] != cn.index[-1]:
        raise ValueError('指数量能没有覆盖最新完整周或不足52周')
    return out


def pack_models(region, variants, cutoff):
    cols = tuple('held_'+x for x in (('tech', 'red', 'bond', 'gold', 'money') if region == 'CN' else ('qqq', 'bil')))
    output = {}
    for key, (label, signals, ledger) in variants.items():
        output[key] = {'key': key, 'label': label, 'metrics': describe(ledger),
                       'target': latest_weights(signals, tuple(c.replace('held_', 'target_') for c in cols)),
                       'held': {c.removeprefix('held_'): float(ledger.iloc[-1][c]) for c in cols},
                       'history': equity_curve(ledger, cols=cols), 'annual': yearly(ledger),
                       'cutoff': cutoff, 'signalWeek': str(signals.index[-1].date()),
                       'tradeAtNextWeek': True, 'lastReason': str(signals.iloc[-1].get('cross_market_reason', signals.iloc[-1].get('us_trend_decision', ''))),
                       'riskState': str(signals.iloc[-1].get('us_risk_state', '以本地趋势与量能决定')),
                       'origin': 'online_recomputed_simulation'}
    return output


def recompute(daily, tape, clock, now, source):
    """Each market fails independently. Never extend frozen Longbridge snapshots.

    Re-downloaded adjusted history can revise old simulations; these results are
    labeled online recomputations, not stitched onto the frozen research NAV.
    """
    result = {'models': {}, 'signals': {}, 'indicators': {}, 'quality': [], 'frozen_signals': [],
              'liquidity': [], 'strategy_status': {}, 'external_backtests': {}, 'computed_at': now.isoformat()}
    prices, opens, cutoffs = {}, {}, {}
    for market in ('CN', 'US'):
        try:
            prices[market], opens[market], cutoffs[market] = weekly_prices(daily, market, clock, now)
        except (ValueError, KeyError, IndexError) as error:
            result['strategy_status'][market] = {'state': 'unavailable', 'message': str(error)}
    if 'US' in prices:
        try:
            p, op = prices['US'], opens['US']
            base, hist = build_us_portfolio(p, opens=op)
            _, advisory, _, ahist = run_advisory_us(p, op)
            blend = evaluate_us_rule_blend(p, op, PublicRuleConfig(us_blend=.25))
            result['models']['US'] = pack_models('US', {
                'baseline': ('多周期趋势 · 在线重算', base, hist),
                'advisory': ('双重风险否决 · 在线研究', advisory, ahist),
                'blend25': ('25%公开规则融合 · 在线研究', blend['signal'], blend['ledger'])}, cutoffs['US'])
            sig = base.iloc[-1]
            try:
                result['external_backtests']['US'] = run_bt_reference(p, base, 'US')
            except Exception as error:
                result['external_backtests']['US'] = {'state': 'failed', 'message': type(error).__name__ + '：bt 对照失败'}
            result['signals']['US'] = {'risk': str(sig.get('us_risk_state', '')),
                'trend_score': float(sig.get('us_trend_score', 0)), 'qqq_close': float(p.QQQ.iloc[-1]),
                'qqq_ma30': float(p.QQQ.tail(30).mean()), 'spy_close': float(p.SPY.iloc[-1]),
                'vol_multiplier': float(sig.get('us_vol_mult', 1)), 'description': str(sig.us_trend_decision)}
            result['indicators']['US'] = extract_us_indicators(p, base)
        except Exception as error:
            result['strategy_status']['US'] = {'state': 'unavailable', 'message': type(error).__name__ + '：策略重算失败'}
    if 'CN' in prices and 'US' in prices:
        try:
            # The legacy cross-market mapper shifts on the U.S. index. If its
            # current-week row is absent, ffill would accidentally lag two weeks.
            # Wait for the U.S. completed week rather than insert a fake bar.
            if prices['US'].index[-1] < prices['CN'].index[-1]:
                raise ValueError('本周美股尚未完整收盘或数据滞后，等待跨市场完整周输入后重算A股')
            p = prices['CN']
            market_tape = weekly_tape(tape, p, clock, now)
            _, base, hist = build_china_portfolio_weekly(p, market_tape, prices['US'])
            _, advisory, _, ahist = run_advisory_cn(p, market_tape, prices['US'])
            blend = evaluate_china_rule_blend(p, market_tape, prices['US'], PublicRuleConfig(cn_blend=.25))
            result['models']['CN'] = pack_models('CN', {
                'baseline': ('五资产轮动 · 在线重算', base, hist),
                'advisory': ('双重风险否决 · 在线研究', advisory, ahist),
                'blend25': ('25%公开规则融合 · 在线研究', blend['signal'], blend['ledger'])}, cutoffs['CN'])
            sig = base.iloc[-1]
            try:
                result['external_backtests']['CN'] = run_bt_reference(p, base, 'CN')
            except Exception as error:
                result['external_backtests']['CN'] = {'state': 'failed', 'message': type(error).__name__ + '：bt 对照失败'}
            ratio = market_tape.market_turn_100m.rolling(4).mean() / market_tape.market_turn_100m.rolling(13).mean()
            result['signals']['CN'] = {'growth_stage': str(sig.get('tech_stage', '')),
                'red_stage': str(sig.get('red_stage', '')), 'market_weak': bool(sig.get('market_weak', False)),
                'tech_momentum_13w': float(sig.get('tech_mom_fast', 0)), 'market_liquidity_ratio': float(ratio.iloc[-1]),
                'cross_market': str(sig.get('us_risk_state_lagged', '')), 'description': str(sig.get('cross_market_reason', ''))}
            result['indicators']['CN'] = extract_cn_indicators(p, base, market_tape)
            result['liquidity'] = [{'d': str(t.date()), 'market_turn_100m': float(r.market_turn_100m),
                'market_close': float(r.market_close), 'tech_share_pct': float(r.tech_turn_100m/r.market_turn_100m*100),
                'red_share_pct': float(r.red_turn_100m/r.market_turn_100m*100),
                'volume_ratio_4_13': None if pd.isna(ratio.loc[t]) else float(ratio.loc[t])} for t, r in market_tape.iterrows()]
        except Exception as error:
            result['strategy_status']['CN'] = {'state': 'unavailable', 'message': str(error) if isinstance(error, (ValueError, KeyError)) else type(error).__name__ + '：策略重算失败'}
    elif 'CN' in prices:
        result['strategy_status']['CN'] = {'state': 'unavailable', 'message': '美股跨市场输入缺失，保留历史信号'}
    for market, models in result['models'].items():
        cutoff, p = cutoffs[market], prices[market]
        up_to_date = p.index[-1] == clock.latest_week(market, now)
        # Conservative fallback: incomplete calendars are never advertised as verified.
        state = 'current' if up_to_date and clock.status(market, now)['state'] != 'calendar_unverified' else 'unverified' if up_to_date else 'stale'
        result['strategy_status'][market] = {'state': state, 'cutoff': cutoff,
            'message': f'完整周信号截至 {cutoff}；' + ('新交易周生效' if state == 'current' else '行情过期，暂停形成新执行建议' if state == 'stale' else '交易日历未核验，需核对交易日')}
        for model in models.values():
            model['data_state'] = state
        result['quality'].append(verify_market_history(p, label=market))
        sha = hashlib.sha256(p.to_csv().encode()).hexdigest()
        result['frozen_signals'].append(frozen_signal_record(market, cutoff, str(p.index[-1].date()),
            models['baseline']['target'], {'source': source, 'dataset': 'separate_online_history',
                'fill': 'next_week_close' if market == 'CN' else 'next_week_open'}, source_sha=sha))
    return result


def monitor(symbol, bars, raw_daily, clock, now):
    """Observe intraday stop confirmations only; no inferred fills, holdings or cooldowns."""
    region = SYMBOLS[symbol][1]
    cfg = StopConfig(market=region, core_floor=.30 if region == 'US' else .20 if symbol == '515180.SH' else 0)
    f = normalize_bars(validate_frame(bars, intraday=True), cfg)
    # Never expose an unfinished close to the engine.
    f = f.loc[f.index + pd.Timedelta(minutes=30) + pd.Timedelta(seconds=5) <= now]
    if f.empty:
        raise ValueError('无已完成30分钟K线')
    zone = ZONES[region]
    day = f.index[-1].tz_convert(zone).strftime('%Y-%m-%d')
    context = clock.complete_daily(raw_daily, region, now)
    context = context.loc[context.index < pd.Timestamp(day)]
    if context.empty or (pd.Timestamp(day)-context.index[-1]).days > 14:
        raise ValueError('前一交易日日线缺失或过期')
    line = stop_line(context.close, cfg)
    current = f.loc[f.index.tz_convert(zone).strftime('%Y-%m-%d') == day]
    eng = IntradayStopEngine(symbol, cfg)
    eng.session_start(day, line['line'], line['prior_close'], line['daily_sigma'])
    observations = []
    for t, row in current.iterrows():
        obs = eng.on_closed_bar(t, float(row.open), float(row.close),
             prev_close=line['prior_close'], sigma=line['daily_sigma'], known_at=now)
        observations.append({'d': (t+pd.Timedelta(minutes=30)).isoformat(), 'close': float(row.close),
                             'line': line['line'], 'below_bars': obs['below_bars']})
    age = (now - (current.index[-1] + pd.Timedelta(minutes=30))).total_seconds()
    same_day = now.tz_convert(zone).strftime('%Y-%m-%d') == day
    return {'symbol': symbol, 'session': day, **line, 'price_basis': 'unadjusted',
            'observations': observations, 'below_bars': eng.s.below_count,
            'confirmation': '本交易日一级破位曾确认，待核验真实持仓' if eng.s.pending_stage else '未触发一级确认',
            'pending_stage': eng.s.pending_stage, 'data_state': 'current' if same_day and age <= 2400 else 'last_session',
            'last_bar_end': observations[-1]['d'],
            'execution': 'observation_only_no_orders_or_assumed_fills',
            'note': '仅观察本交易日完整K线。未读取真实持仓，不推断减仓成交或冷却状态；重启按同一交易日数据确定性重放。'}
