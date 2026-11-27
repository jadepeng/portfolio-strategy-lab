"""Calibrate backtest fee rates from the account's REAL fills (broker CSV / manual).

Why: the frozen research config charges 15bp per side for tech/red/gold ETFs.
Typical A-share ETF cost is commission (often 0.5-2.5bp, no stamp duty, no
transfer fee) plus spread/impact. On 2020-10..2026-11 data, 15bp -> ~7.5bp
lifts V18 Sharpe 0.99 -> 1.03 and rp40 1.12 -> 1.15 with ZERO rule changes.

Rules (conservative by design):
- effective rate = explicit fee / notional  +  slippage vs reference price
  (reference = ticket snapshot price when available, else none -> fee only).
- needs >= MIN_FILLS non-reversed BUY/SELL fills per bucket, else keep default.
- result is clamped to [FLOOR, default]: calibration can only LOWER the
  research assumption toward reality, never below a safety floor, so it cannot
  be used to make a backtest look better than the account has actually earned.
"""
from __future__ import annotations
from dataclasses import dataclass, replace
from typing import Any, Iterable, Mapping
import json
import statistics

BUCKET_FEE_FIELD = {'tech': 'tech_fee', 'red': 'red_fee', 'bond': 'bond_fee',
                    'gold': 'gold_fee', 'money': 'money_fee'}
MARKET_FEE_FIELDS = {'CN': BUCKET_FEE_FIELD, 'US': {'qqq': 'qqq_side_fee', 'bil': 'bil_side_fee'}}
FLOOR = {'tech': .0003, 'red': .0003, 'bond': .0002, 'gold': .0003, 'money': .00005,
         'qqq': .0001, 'bil': .00005}
MIN_FILLS = 8


@dataclass(frozen=True)
class FeeEstimate:
    bucket: str
    fills: int
    median_rate: float
    p75_rate: float
    applied_rate: float
    default_rate: float
    source: str      # 'calibrated' | 'default_insufficient_fills'


def _ref_price(event: Mapping[str, Any], tickets: Mapping[int, Mapping[str, Any]]) -> float | None:
    """Reference = the ref_price frozen into the decision ticket's order for this symbol."""
    tid = event.get('decision_id')
    if not tid or tid not in tickets:
        return None
    snap = tickets[tid].get('snapshot')
    if snap is None:
        try:
            snap = json.loads(tickets[tid].get('snapshot_json') or '{}')
        except (TypeError, ValueError):
            return None
    for order in (snap or {}).get('orders') or []:
        if order.get('symbol') == event.get('symbol') and order.get('side') == event.get('event_type'):
            px = order.get('ref_price')
            if isinstance(px, (int, float)) and px > 0:
                return float(px)
    return None


def estimate_fee_rates(events: Iterable[Mapping[str, Any]], bucket_of: Mapping[str, str],
                       defaults: Mapping[str, float], *,
                       tickets: Mapping[int, Mapping[str, Any]] | None = None) -> dict[str, FeeEstimate]:
    tickets = tickets or {}
    rates: dict[str, list[float]] = {b: [] for b in defaults}
    for e in events:
        if e.get('is_reversed') or e.get('reverses_event_id') or e.get('event_type') not in ('BUY', 'SELL'):
            continue
        b = bucket_of.get(str(e.get('symbol')))
        qty, px, fee = float(e.get('quantity') or 0), float(e.get('price') or 0), float(e.get('fee') or 0)
        if b not in rates or qty <= 0 or px <= 0 or fee < 0:
            continue
        rate = fee / (qty * px)
        ref = _ref_price(e, tickets)
        if ref:
            # adverse slippage only: buying above / selling below the reference
            slip = (px - ref) / ref if e['event_type'] == 'BUY' else (ref - px) / ref
            rate += max(0.0, slip)
        rates[b].append(rate)
    out = {}
    for b, d in defaults.items():
        xs = sorted(rates.get(b, []))
        if len(xs) >= MIN_FILLS:
            med = statistics.median(xs)
            p75 = xs[int(.75 * (len(xs) - 1))]
            applied = min(d, max(FLOOR.get(b, 0.0), p75))   # use p75, not median: conservative
            out[b] = FeeEstimate(b, len(xs), med, p75, applied, d, 'calibrated')
        else:
            out[b] = FeeEstimate(b, len(xs), float('nan'), float('nan'), d, d, 'default_insufficient_fills')
    return out


def apply_fee_estimates(cfg, estimates: Mapping[str, FeeEstimate], market: str = 'CN'):
    """Return a copy of the market config with calibrated per-bucket fees."""
    fields = MARKET_FEE_FIELDS[market]
    kw = {fields[b]: e.applied_rate for b, e in estimates.items() if b in fields and hasattr(cfg, fields[b])}
    return replace(cfg, **kw)


def calibrate_from_store(store, account_id: str, cfg, instrument_meta: Mapping[str, Mapping[str, Any]],
                         market: str = 'CN'):
    """Read the account ledger; return (calibrated cfg, JSON-safe per-bucket report)."""
    fields = MARKET_FEE_FIELDS[market]
    bucket_of = {sym: m['bucket'] for sym, m in instrument_meta.items() if m.get('market') == market}
    defaults = {b: float(getattr(cfg, f)) for b, f in fields.items()}
    events = store.list_events(account_id, limit=None)
    tickets = {t['decision_id']: t for t in store.list_tickets(account_id, limit=1000)}
    est = estimate_fee_rates(events, bucket_of, defaults, tickets=tickets)
    bp = lambda x: None if x != x else round(x * 1e4, 2)
    report = [{'bucket': b, 'fills': e.fills, 'median_bp': bp(e.median_rate), 'p75_bp': bp(e.p75_rate),
               'assumed_bp': bp(e.default_rate), 'applied_bp': bp(e.applied_rate),
               'floor_bp': bp(FLOOR.get(b, 0.0)), 'source': e.source} for b, e in est.items()]
    return apply_fee_estimates(cfg, est, market), report
