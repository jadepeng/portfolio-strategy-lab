"""Time-weighted real NAV (TWR), three-track attribution, slippage audit, and review export."""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from live.store import INSTRUMENT_META, TradingStore, clean_float, market_symbols


def build_price_history_for_review(
    market: str,
    live_daily: dict[str, Any] | None,
    events: list[dict[str, Any]],
    quotes: dict[str, dict[str, Any]] | None,
    today_local_date: str,
) -> pd.DataFrame:
    """Assemble consistent unadjusted daily close series for TWR valuation."""
    symbols = market_symbols(market)
    series_map: dict[str, pd.Series] = {}

    for sym in symbols:
        entry = (live_daily or {}).get(sym)
        raw_df = entry.get('raw') if isinstance(entry, dict) else entry
        s = pd.Series(dtype=float)
        if isinstance(raw_df, pd.DataFrame) and not raw_df.empty and 'close' in raw_df:
            idx = pd.DatetimeIndex(raw_df.index)
            if idx.tz is not None:
                tz = 'Asia/Shanghai' if market == 'CN' else 'America/New_York'
                idx = idx.tz_convert(tz).tz_localize(None).normalize()
            else:
                idx = idx.normalize()
            s = pd.Series(raw_df['close'].to_numpy(float), index=idx)
            s = s[~s.index.duplicated(keep='last')].sort_index()

        # Overlay fills only where daily close is missing so offline/synthetic tests work cleanly
        for ev in events:
            if ev.get('symbol') == sym and (ev.get('price') or 0) > 0:
                dt = pd.Timestamp(str(ev['trade_date']))
                if dt not in s.index:
                    s.loc[dt] = float(ev['price'])

        q_px = clean_float((quotes or {}).get(sym, {}).get('price'))
        if q_px is not None and q_px > 0:
            s.loc[pd.Timestamp(today_local_date)] = q_px

        if not s.empty:
            s = s.sort_index()
        series_map[sym] = s

    all_dates = set()
    for s in series_map.values():
        all_dates.update(s.index)
    for ev in events:
        all_dates.add(pd.Timestamp(str(ev['trade_date'])))
    if events:
        all_dates.add(pd.Timestamp(today_local_date))

    if not all_dates:
        return pd.DataFrame(columns=list(symbols))

    idx = pd.DatetimeIndex(sorted(all_dates))
    df = pd.DataFrame(index=idx)
    for sym in symbols:
        s = series_map[sym]
        df[sym] = s.reindex(idx).ffill() if not s.empty else np.nan
    return df


def compute_twr_series(
    account: dict[str, Any],
    events: list[dict[str, Any]],
    price_df: pd.DataFrame,
    quotes: dict[str, dict[str, Any]] | None,
    today_local_date: str,
) -> list[dict[str, Any]]:
    """Compute fund-unit Time-Weighted Return (TWR) immune to deposits/withdrawals."""
    if not events:
        return []

    market = account['market']
    symbols = market_symbols(market)
    ordered = sorted(
        events,
        key=lambda e: (str(e['trade_date']), str(e['event_time']), int(e['event_id'])),
    )
    start_date = str(ordered[0]['trade_date'])

    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for ev in ordered:
        by_day[str(ev['trade_date'])].append(ev)

    date_set = {str(d.date()) for d in price_df.index if start_date <= str(d.date()) <= today_local_date}
    date_set.update(by_day.keys())
    if today_local_date >= start_date:
        date_set.add(today_local_date)
    days = sorted(date_set)

    cash = 0.0
    shares = {s: 0.0 for s in symbols}
    last_px = {s: 0.0 for s in symbols}
    units = 0.0
    nav = 1.0
    peak_nav = 1.0
    out: list[dict[str, Any]] = []

    for day in days:
        dt = pd.Timestamp(day)
        if dt in price_df.index:
            for s in symbols:
                px = clean_float(price_df.loc[dt, s])
                if px is not None and px > 0:
                    last_px[s] = px
        if day == today_local_date and quotes:
            for s in symbols:
                q_px = clean_float((quotes.get(s) or {}).get('price'))
                if q_px is not None and q_px > 0:
                    last_px[s] = q_px

        day_events = by_day.get(day, [])
        # Populate fallback last_px from event prices if mark price has not appeared yet
        for ev in day_events:
            sym = ev.get('symbol')
            px = clean_float(ev.get('price'))
            if sym and px is not None and px > 0 and last_px.get(sym, 0.0) <= 0:
                last_px[sym] = px

        # Value existing portfolio BEFORE external cash/position flows on this day
        pre_flow_equity = cash + sum(shares[s] * last_px[s] for s in symbols)
        pre_flow_nav = (pre_flow_equity / units) if units > 1e-9 else nav

        net_flow_today = 0.0
        for ev in day_events:
            et = ev['event_type']
            sym = ev.get('symbol')
            qty = float(ev.get('quantity') or 0.0)
            px = float(ev.get('price') or 0.0)
            fee = float(ev.get('fee') or 0.0)
            cd = float(ev.get('cash_delta') or 0.0)

            cash += cd
            if et == 'INIT_POSITION':
                if sym and sym in shares:
                    shares[sym] += qty
                    net_flow_today += qty * px + fee + cd
                else:
                    net_flow_today += cd
            elif et in ('CASH_IN', 'CASH_OUT'):
                net_flow_today += cd
            elif et == 'BUY':
                shares[sym] += qty
            elif et == 'SELL':
                shares[sym] = max(0.0, shares[sym] - qty)
            elif et == 'DIVIDEND':
                pass  # cash already credited; internal return
            elif et == 'RECONCILE':
                flow = cd
                if sym and sym in shares:
                    ref_p = px if px > 0 else last_px.get(sym, 0.0)
                    shares[sym] = max(0.0, shares[sym] + qty)
                    flow += qty * ref_p
                net_flow_today += flow

        if units <= 1e-9:
            if net_flow_today > 0:
                units = net_flow_today
                pre_flow_nav = 1.0
        elif abs(net_flow_today) > 1e-9:
            delta_units = net_flow_today / max(pre_flow_nav, 1e-9)
            units = max(0.0, units + delta_units)

        end_equity = cash + sum(shares[s] * last_px[s] for s in symbols)
        if units > 1e-9:
            nav = max(1e-6, end_equity / units)
        peak_nav = max(peak_nav, nav)
        dd_pct = (nav / peak_nav - 1.0) * 100.0
        out.append(
            {
                'd': day,
                'nav': round(nav, 6),
                'equity': round(end_equity, 2),
                'cash': round(cash, 2),
                'dd_pct': round(dd_pct, 3),
                'cum_ret_pct': round((nav - 1.0) * 100.0, 3),
                'net_flow': round(net_flow_today, 2),
            }
        )
    return out


def _align_benchmark_curve(model_history: list[dict[str, Any]], start_date: str | None) -> tuple[list[dict[str, Any]], float | None, float | None]:
    if not model_history or not start_date:
        return [], None, None
    prior = [row for row in model_history if str(row['d']) <= start_date]
    anchor = prior[-1] if prior else model_history[0]
    anchor_nav = float(anchor['nav']) if float(anchor['nav']) > 0 else 1.0
    sub = [row for row in model_history if str(row['d']) >= str(anchor['d'])]
    if not sub:
        return [], 0.0, 0.0
    curve = []
    peak = 1.0
    max_dd = 0.0
    for row in sub:
        rel_nav = float(row['nav']) / anchor_nav
        peak = max(peak, rel_nav)
        dd = (rel_nav / peak - 1.0) * 100.0
        max_dd = min(max_dd, dd)
        curve.append(
            {
                'd': str(row['d']),
                'nav': round(rel_nav, 6),
                'cum_ret_pct': round((rel_nav - 1.0) * 100.0, 3),
                'dd_pct': round(dd, 3),
            }
        )
    return curve, curve[-1]['cum_ret_pct'], round(max_dd, 3)


def build_review_report(
    store: TradingStore,
    account: dict[str, Any],
    portfolio: dict[str, Any],
    model_history: list[dict[str, Any]],
    live_daily: dict[str, Any] | None,
    quotes: dict[str, dict[str, Any]] | None,
    today_local_date: str,
) -> dict[str, Any]:
    """Assemble full performance, execution drift, slippage, and discipline review."""
    account_id = account['account_id']
    market = account['market']
    events_desc = store.list_events(account_id, limit=1000)
    events = list(reversed(events_desc))
    tickets = store.list_tickets(account_id, limit=500)
    notes = store.list_review_notes(account_id, limit=100)

    price_df = build_price_history_for_review(market, live_daily, events, quotes, today_local_date)
    twr_curve = compute_twr_series(account, events, price_df, quotes, today_local_date)
    start_date = twr_curve[0]['d'] if twr_curve else None

    benchmark_curve, model_ret_pct, model_mdd_pct = _align_benchmark_curve(model_history, start_date)

    twr_ret_pct = twr_curve[-1]['cum_ret_pct'] if twr_curve else None
    real_mdd_pct = round(min((r['dd_pct'] for r in twr_curve), default=0.0), 3) if twr_curve else None
    tracking_gap_pp = (
        round(twr_ret_pct - model_ret_pct, 3)
        if (twr_ret_pct is not None and model_ret_pct is not None)
        else None
    )

    # Ticket & slippage audit
    ticket_map = {t['decision_id']: t for t in tickets}
    trade_audits: list[dict[str, Any]] = []
    slippage_bps: list[float] = []
    followed_trades = 0
    override_trades = 0

    for ev in events_desc:
        if ev['event_type'] not in ('BUY', 'SELL'):
            continue
        did = ev.get('decision_id')
        ticket = ticket_map.get(did) if did is not None else None
        ref_price: float | None = None
        if ticket:
            orders = (ticket.get('snapshot') or {}).get('orders') or []
            for o in orders:
                if o.get('symbol') == ev.get('symbol') and str(o.get('side', '')).upper() == ev['event_type']:
                    ref_price = clean_float(o.get('ref_price'))
                    break
        fill_px = float(ev['price'])
        slip_bp: float | None = None
        if ref_price is not None and ref_price > 0 and fill_px > 0:
            if ev['event_type'] == 'BUY':
                slip_bp = round((fill_px / ref_price - 1.0) * 10000.0, 2)
            else:
                slip_bp = round((1.0 - fill_px / ref_price) * 10000.0, 2)
            slippage_bps.append(slip_bp)

        tag = str(ev.get('discipline_tag') or 'MANUAL_OVERRIDE')
        if tag in ('FOLLOW', 'RISK_CUT'):
            followed_trades += 1
        else:
            override_trades += 1

        sym = str(ev.get('symbol') or '')
        trade_audits.append(
            {
                'event_id': ev['event_id'],
                'trade_date': ev['trade_date'],
                'event_time': ev['event_time'],
                'side': ev['event_type'],
                'symbol': sym,
                'name': INSTRUMENT_META.get(sym, {}).get('name', sym),
                'quantity': float(ev['quantity']),
                'fill_price': round(fill_px, 4),
                'ref_price': round(ref_price, 4) if ref_price is not None else None,
                'slippage_bp': slip_bp,
                'fee': round(float(ev['fee']), 2),
                'decision_id': did,
                'ticket_source': ticket['source'] if ticket else 'MANUAL_DIRECT',
                'discipline_tag': tag,
                'note': ev.get('note', ''),
            }
        )

    executed_tickets = sum(1 for t in tickets if t['status'] in ('EXECUTED', 'PARTIAL'))
    skipped_tickets = sum(1 for t in tickets if t['status'] == 'SKIPPED')
    pending_tickets = sum(1 for t in tickets if t['status'] == 'PENDING')
    resolved_tickets = executed_tickets + skipped_tickets
    total_trades = followed_trades + override_trades

    discipline_stats = {
        'total_tickets': len(tickets),
        'executed_tickets': executed_tickets,
        'skipped_tickets': skipped_tickets,
        'pending_tickets': pending_tickets,
        'ticket_follow_rate_pct': round(executed_tickets / resolved_tickets * 100.0, 1) if resolved_tickets > 0 else 100.0,
        'total_trades': total_trades,
        'followed_trades': followed_trades,
        'override_trades': override_trades,
        'trade_discipline_rate_pct': round(followed_trades / total_trades * 100.0, 1) if total_trades > 0 else 100.0,
        'avg_slippage_bp': round(float(np.mean(slippage_bps)), 2) if slippage_bps else None,
        'slippage_samples': len(slippage_bps),
    }

    kpis = {
        'start_date': start_date,
        'as_of_date': today_local_date,
        'twr_return_pct': twr_ret_pct,
        'model_return_pct': model_ret_pct,
        'tracking_gap_pp': tracking_gap_pp,
        'real_max_dd_pct': real_mdd_pct,
        'model_max_dd_pct': model_mdd_pct,
        'current_equity': portfolio['total_equity'],
        'net_capital_in': portfolio['net_capital_in'],
        'total_pnl': portfolio['total_pnl'],
        'realized_pnl': portfolio['realized_pnl'],
        'unrealized_pnl': portfolio['unrealized_pnl'],
        'total_fees': portfolio['total_fees'],
        'total_dividends': portfolio['total_dividends'],
    }

    return {
        'account_id': account_id,
        'market': market,
        'currency': account['currency'],
        'strategy_version': account['strategy_version'],
        'kpis': kpis,
        'discipline': discipline_stats,
        'twr_curve': twr_curve,
        'benchmark_curve': benchmark_curve,
        'positions': portfolio['positions'],
        'trade_audits': trade_audits[:100],
        'tickets': tickets[:50],
        'notes': notes,
    }


def render_review_markdown(report: dict[str, Any]) -> str:
    """Format a personal trading review report as clean Markdown."""
    k = report['kpis']
    d = report['discipline']
    cur = report['currency']
    lines = [
        f"# 个人交易复盘报告 · {report['account_id']} ({report['market']} / {cur})",
        "",
        f"- **复盘窗口**: `{k.get('start_date') or '未建账'}` 至 `{k.get('as_of_date')}`",
        f"- **主策略版本**: `{report['strategy_version']}`",
        "",
        "## 一、三轨收益与资金概览",
        "",
        f"- **实盘份额净值收益 (TWR)**: `{k['twr_return_pct'] if k['twr_return_pct'] is not None else '—'}%`",
        f"- **同期策略模型收益**: `{k['model_return_pct'] if k['model_return_pct'] is not None else '—'}%`",
        f"- **执行跟踪偏差**: `{k['tracking_gap_pp'] if k['tracking_gap_pp'] is not None else '—'} pp`",
        f"- **实盘最大回撤 / 模型最大回撤**: `{k['real_max_dd_pct']}%` / `{k['model_max_dd_pct']}%`",
        f"- **当前总资产 ({cur})**: `{k['current_equity']:,.2f}`（净投入 `{k['net_capital_in']:,.2f}`，累计总盈亏 `{k['total_pnl']:+,.2f}`）",
        f"- **已实现 / 浮动盈亏 / 费用 / 分红**: `{k['realized_pnl']:+,.2f}` / `{k['unrealized_pnl']:+,.2f}` / `{k['total_fees']:,.2f}` / `{k['total_dividends']:+,.2f}`",
        "",
        "## 二、纪律与执行滑点审计",
        "",
        f"- **决策票据统计**: 总计 {d['total_tickets']}（已执行 {d['executed_tickets']} / 跳过 {d['skipped_tickets']} / 待处理 {d['pending_tickets']}），票据执行率 `{d['ticket_follow_rate_pct']}%`",
        f"- **交易纪律遵守率**: `{d['trade_discipline_rate_pct']}%`（守纪 {d['followed_trades']} 笔 / 偏离或手动 {d['override_trades']} 笔）",
        f"- **平均实现滑点**: `{d['avg_slippage_bp'] if d['avg_slippage_bp'] is not None else '—'} bp`（样本 {d['slippage_samples']} 笔）",
        "",
        "## 三、当前持仓快照",
        "",
        "| 标的 | 名称 | 股数 | 今日可卖 | 均价 | 现价 | 实盘权重 | 浮盈亏 |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for p in report.get('positions', []):
        if p['shares'] <= 0 and p['actual_weight'] <= 0:
            continue
        px_str = f"{p['mark_price']:.4f}" if p['mark_price'] is not None else '—'
        lines.append(
            f"| {p['symbol']} | {p['name']} | {p['shares']:g} | {p['sellable_shares']:g} | "
            f"{p['avg_cost']:.4f} | {px_str} | {p['actual_weight']*100:.1f}% | {p['unrealized_pnl']:+,.2f} |"
        )
    lines.extend(["", "## 四、复盘笔记与自我反思", ""])
    notes = report.get('notes') or []
    if not notes:
        lines.append("_暂无已保存的复盘笔记。_")
    else:
        for n in notes[:20]:
            lines.append(f"### [{n['period_type']}] {n['period_key']}")
            lines.append(n.get('reflection') or '（无正文）')
            lines.append("")
    return '\n'.join(lines)
