"""Persistent SQLite ledger, portfolio projection, and trading OS state store."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import threading
from typing import Any

import pandas as pd

INSTRUMENT_META: dict[str, dict[str, Any]] = {
    '159915.SZ': {
        'market': 'CN',
        'bucket': 'tech',
        'name': '创业板ETF',
        'lot_size': 100,
        't_plus': 1,
        'default_fee_rate': 0.0015,
        'risk_monitored': True,
        'is_cash_etf': False,
    },
    '515180.SH': {
        'market': 'CN',
        'bucket': 'red',
        'name': '红利ETF',
        'lot_size': 100,
        't_plus': 1,
        'default_fee_rate': 0.0015,
        'risk_monitored': True,
        'is_cash_etf': False,
    },
    '511260.SH': {
        'market': 'CN',
        'bucket': 'bond',
        'name': '10年国债ETF',
        'lot_size': 100,
        't_plus': 0,
        'default_fee_rate': 0.0015,
        'risk_monitored': False,
        'is_cash_etf': False,
    },
    '518880.SH': {
        'market': 'CN',
        'bucket': 'gold',
        'name': '黄金ETF',
        'lot_size': 100,
        't_plus': 0,
        'default_fee_rate': 0.0015,
        'risk_monitored': False,
        'is_cash_etf': False,
    },
    '511880.SH': {
        'market': 'CN',
        'bucket': 'money',
        'name': '货币ETF',
        'lot_size': 100,
        't_plus': 0,
        'default_fee_rate': 0.0001,
        'risk_monitored': False,
        'is_cash_etf': True,
    },
    'QQQ.US': {
        'market': 'US',
        'bucket': 'qqq',
        'name': '纳指100 ETF',
        'lot_size': 1,
        't_plus': 0,
        'default_fee_rate': 0.0010,
        'risk_monitored': True,
        'is_cash_etf': False,
    },
    'BIL.US': {
        'market': 'US',
        'bucket': 'bil',
        'name': '美元短债 ETF',
        'lot_size': 1,
        't_plus': 0,
        'default_fee_rate': 0.0002,
        'risk_monitored': False,
        'is_cash_etf': True,
    },
}

MARKET_TIMEZONES = {'CN': 'Asia/Shanghai', 'US': 'America/New_York'}
VALID_STRATEGY_VERSIONS = ('baseline', 'advisory', 'blend25', 'rp40')
# Versions that exist only for one market; never silently fall back to baseline.
MARKET_ONLY_VERSIONS = {'rp40': 'CN'}
VALID_ACCOUNT_MODES = ('REAL', 'DEMO')
VALID_EVENT_TYPES = (
    'INIT_POSITION',
    'BUY',
    'SELL',
    'CASH_IN',
    'CASH_OUT',
    'DIVIDEND',
    'RECONCILE',
)
VALID_DISCIPLINE_TAGS = (
    'FOLLOW',
    'DELAYED',
    'MANUAL_OVERRIDE',
    'RISK_CUT',
    'RECONCILE',
    'INIT',
    'CORRECTION',
    'IMPORT',
)
VALID_TICKET_SOURCES = ('WEEKLY_REBALANCE', 'INTRADAY_STOP', 'MANUAL')
VALID_TICKET_STATUSES = ('PENDING', 'EXECUTED', 'PARTIAL', 'SKIPPED', 'EXPIRED')
VALID_PERIOD_TYPES = ('DAILY', 'WEEKLY', 'MONTHLY')

DEFAULT_ACCOUNTS = (
    {
        'account_id': 'CN_MAIN',
        'market': 'CN',
        'currency': 'CNY',
        'strategy_version': 'baseline',
        'rebalance_deadband': 0.15,
        'core_floors': {'515180.SH': 0.20},
    },
    {
        'account_id': 'US_MAIN',
        'market': 'US',
        'currency': 'USD',
        'strategy_version': 'baseline',
        'rebalance_deadband': 0.20,
        'core_floors': {'QQQ.US': 0.30},
    },
)


def clean_float(val: Any, *, ndigits: int | None = None) -> float | None:
    if val is None or isinstance(val, bool):
        return None
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    return round(f, ndigits) if ndigits is not None else f


def clean_bool(val: Any) -> bool | None:
    if val is None:
        return None
    try:
        if pd.isna(val):
            return None
    except (TypeError, ValueError):
        pass
    return bool(val)


def market_symbols(market: str) -> tuple[str, ...]:
    if market not in ('CN', 'US'):
        raise ValueError(f'未知市场: {market}')
    return tuple(sym for sym, meta in INSTRUMENT_META.items() if meta['market'] == market)


def risk_monitored_symbols(market: str) -> tuple[str, ...]:
    return tuple(
        sym
        for sym, meta in INSTRUMENT_META.items()
        if meta['market'] == market and meta['risk_monitored']
    )


def bucket_to_symbol(market: str, bucket: str) -> str:
    for sym, meta in INSTRUMENT_META.items():
        if meta['market'] == market and meta['bucket'] == bucket:
            return sym
    raise ValueError(f'未知策略仓位映射: market={market}, bucket={bucket}')


def symbol_to_bucket(symbol: str) -> str:
    meta = INSTRUMENT_META.get(symbol)
    if meta is None:
        raise ValueError(f'不在策略允许标的池内: {symbol}')
    return str(meta['bucket'])


def cash_etf_symbol(market: str) -> str:
    return '511880.SH' if market == 'CN' else 'BIL.US'


def market_today(market: str, now: pd.Timestamp | None = None) -> str:
    ts = pd.Timestamp.now(tz='UTC') if now is None else pd.Timestamp(now)
    if ts.tzinfo is None:
        ts = ts.tz_localize('UTC')
    return ts.tz_convert(MARKET_TIMEZONES[market]).strftime('%Y-%m-%d')


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_trade_date(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError('交易日不能为空')
    try:
        dt = pd.Timestamp(value.strip())
    except Exception as exc:
        raise ValueError(f'无效交易日期: {value}') from exc
    if pd.isna(dt):
        raise ValueError(f'无效交易日期: {value}')
    return dt.strftime('%Y-%m-%d')


def normalize_event_time(value: str | None, market: str, trade_date: str) -> str:
    tz = MARKET_TIMEZONES[market]
    if not value:
        return pd.Timestamp(f'{trade_date} 15:00:00', tz=tz).isoformat()
    try:
        dt = pd.Timestamp(str(value).strip())
    except Exception as exc:
        raise ValueError(f'无效时间格式: {value}') from exc
    if pd.isna(dt):
        raise ValueError(f'无效时间格式: {value}')
    if dt.tzinfo is None:
        dt = dt.tz_localize(tz)
    else:
        dt = dt.tz_convert(tz)
    return dt.isoformat()


def _validate_symbol_for_market(symbol: str | None, market: str) -> str:
    if not symbol or symbol not in INSTRUMENT_META:
        raise ValueError(f'不支持的交易标的: {symbol}')
    if INSTRUMENT_META[symbol]['market'] != market:
        raise ValueError(f'跨币种/跨市场禁止混仓: {symbol} 不属于 {market} 账户')
    return symbol


def _parse_core_floors(raw: dict[str, Any] | None, market: str) -> dict[str, float]:
    allowed = set(market_symbols(market))
    bucket_map = {INSTRUMENT_META[s]['bucket']: s for s in allowed}
    out: dict[str, float] = {}
    for k, v in (raw or {}).items():
        sym = bucket_map.get(k, k)
        if sym not in allowed:
            raise ValueError(f'核心底仓标的 {k} 不属于 {market} 账户')
        val = clean_float(v)
        if val is None or not (0.0 <= val <= 1.0):
            raise ValueError(f'核心底仓比例无效: {k}={v}')
        if val > 1e-9:
            out[sym] = round(val, 6)
    if sum(out.values()) > 1.0 + 1e-6:
        raise ValueError('核心底仓比例之和不能超过 100%')
    return out


def project_portfolio(
    account: dict[str, Any],
    events: list[dict[str, Any]],
    prices: dict[str, dict[str, Any]] | None,
    today_local_date: str,
) -> dict[str, Any]:
    """Pure deterministic fold of immutable ledger events into live holdings & PnL."""
    market = account['market']
    symbols = market_symbols(market)
    pos_acc: dict[str, dict[str, float]] = {
        sym: {
            'shares': 0.0,
            'avg_cost': 0.0,
            'realized_pnl': 0.0,
            'today_bought_shares': 0.0,
            'total_fees': 0.0,
            'total_dividends': 0.0,
        }
        for sym in symbols
    }

    cash = 0.0
    total_fees = 0.0
    total_dividends = 0.0
    net_capital_in = 0.0

    ordered_all = sorted(
        events,
        key=lambda e: (str(e['trade_date']), str(e['event_time']), int(e['event_id'])),
    )
    reversed_ids = {
        int(ev['reverses_event_id'])
        for ev in ordered_all
        if ev.get('reverses_event_id') is not None
    }
    ordered = [
        ev
        for ev in ordered_all
        if int(ev['event_id']) not in reversed_ids and ev.get('reverses_event_id') is None
    ]

    for ev in ordered:
        et = ev['event_type']
        sym = ev.get('symbol')
        qty = float(ev.get('quantity') or 0.0)
        px = float(ev.get('price') or 0.0)
        fee = float(ev.get('fee') or 0.0)
        cd = float(ev.get('cash_delta') or 0.0)

        cash += cd
        total_fees += fee

        if et == 'INIT_POSITION':
            if sym and sym in pos_acc:
                st = pos_acc[sym]
                new_sh = st['shares'] + qty
                if new_sh > 1e-9:
                    st['avg_cost'] = (st['shares'] * st['avg_cost'] + qty * px + fee) / new_sh
                st['shares'] = new_sh
                st['total_fees'] += fee
                net_capital_in += qty * px + fee + cd
            else:
                net_capital_in += cd
        elif et in ('CASH_IN', 'CASH_OUT'):
            net_capital_in += cd
        elif et == 'BUY':
            st = pos_acc[sym]
            new_sh = st['shares'] + qty
            if new_sh > 1e-9:
                st['avg_cost'] = (st['shares'] * st['avg_cost'] + qty * px + fee) / new_sh
            st['shares'] = new_sh
            st['total_fees'] += fee
            if str(ev['trade_date']) == today_local_date:
                st['today_bought_shares'] += qty
        elif et == 'SELL':
            st = pos_acc[sym]
            st['realized_pnl'] += (px - st['avg_cost']) * qty - fee
            st['shares'] -= qty
            st['total_fees'] += fee
            if st['shares'] <= 1e-9:
                st['shares'] = 0.0
                st['avg_cost'] = 0.0
        elif et == 'DIVIDEND':
            total_dividends += cd
            if sym and sym in pos_acc:
                pos_acc[sym]['total_dividends'] += cd
                pos_acc[sym]['realized_pnl'] += cd
        elif et == 'RECONCILE':
            if sym and sym in pos_acc:
                st = pos_acc[sym]
                ref_p = px if px > 0 else st['avg_cost']
                if qty >= 0:
                    new_sh = st['shares'] + qty
                    if new_sh > 1e-9:
                        st['avg_cost'] = (st['shares'] * st['avg_cost'] + qty * ref_p) / new_sh
                    st['shares'] = new_sh
                    net_capital_in += qty * ref_p
                else:
                    dec = min(st['shares'], abs(qty))
                    st['shares'] = max(0.0, st['shares'] - dec)
                    if st['shares'] <= 1e-9:
                        st['shares'] = 0.0
                        st['avg_cost'] = 0.0
                    net_capital_in -= dec * ref_p
            net_capital_in += cd

    if abs(cash) < 1e-9:
        cash = 0.0

    positions: list[dict[str, Any]] = []
    holdings_value = 0.0
    unrealized_pnl = 0.0
    realized_pnl = 0.0

    price_map = prices or {}
    core_floors = account.get('core_floors', {})

    for sym in symbols:
        meta = INSTRUMENT_META[sym]
        st = pos_acc[sym]
        shares = round(st['shares'], 6)
        today_bought = round(min(shares, st['today_bought_shares']), 6)
        sellable = round(max(0.0, shares - today_bought), 6) if meta['t_plus'] == 1 else shares

        q = price_map.get(sym)
        q_price = clean_float(q.get('price') if isinstance(q, dict) else None)
        if q_price is not None and q_price > 0:
            mark_price = q_price
            price_source = str(q.get('source') or 'quote')
            price_ts = q.get('timestamp')
            price_stale = bool(q.get('stale', False))
        elif st['avg_cost'] > 0:
            mark_price = st['avg_cost']
            price_source = 'cost_fallback'
            price_ts = None
            price_stale = True
        else:
            mark_price = None
            price_source = 'unavailable'
            price_ts = None
            price_stale = True

        mv = (shares * mark_price) if (mark_price is not None and shares > 1e-9) else 0.0
        upnl = ((mark_price - st['avg_cost']) * shares) if (mark_price is not None and shares > 1e-9) else 0.0
        rpnl = st['realized_pnl']

        holdings_value += mv
        unrealized_pnl += upnl
        realized_pnl += rpnl

        positions.append(
            {
                'symbol': sym,
                'bucket': meta['bucket'],
                'name': meta['name'],
                'lot_size': meta['lot_size'],
                't_plus': meta['t_plus'],
                'is_cash_etf': meta['is_cash_etf'],
                'risk_monitored': meta['risk_monitored'],
                'shares': shares,
                'sellable_shares': sellable,
                'today_bought_shares': today_bought,
                'avg_cost': round(st['avg_cost'], 6) if st['avg_cost'] > 0 else 0.0,
                'mark_price': round(mark_price, 6) if mark_price is not None else None,
                'price_source': price_source,
                'price_timestamp': price_ts,
                'price_stale': price_stale,
                'market_value': round(mv, 4),
                'unrealized_pnl': round(upnl, 4),
                'realized_pnl': round(rpnl, 4),
                'total_fees': round(st['total_fees'], 4),
                'total_dividends': round(st['total_dividends'], 4),
                'actual_weight': 0.0,
                'core_floor': float(core_floors.get(sym, 0.0)),
            }
        )

    total_equity = cash + holdings_value
    actual_weights: dict[str, float] = {}
    bucket_weights: dict[str, float] = {}
    for item in positions:
        w = (item['market_value'] / total_equity) if total_equity > 1e-9 else 0.0
        item['actual_weight'] = round(w, 6)
        actual_weights[item['symbol']] = round(w, 6)
        bucket_weights[item['bucket']] = round(w, 6)

    cash_weight = (cash / total_equity) if total_equity > 1e-9 else 0.0
    actual_weights['cash'] = round(cash_weight, 6)
    cash_etf_bucket = INSTRUMENT_META[cash_etf_symbol(market)]['bucket']
    effective_cash_bucket_weight = round(bucket_weights[cash_etf_bucket] + cash_weight, 6)

    return {
        'account_id': account['account_id'],
        'market': market,
        'currency': account['currency'],
        'account_mode': account.get('account_mode', 'REAL'),
        'as_of_date': today_local_date,
        'initialized': len(ordered) > 0,
        'event_count': len(ordered_all),
        'active_event_count': len(ordered),
        'cash': round(cash, 4),
        'cash_weight': round(cash_weight, 6),
        'holdings_value': round(holdings_value, 4),
        'total_equity': round(total_equity, 4),
        'actual_weights': actual_weights,
        'bucket_weights': bucket_weights,
        'effective_cash_bucket_weight': effective_cash_bucket_weight,
        'positions': positions,
        'unrealized_pnl': round(unrealized_pnl, 4),
        'realized_pnl': round(realized_pnl, 4),
        'total_pnl': round(unrealized_pnl + realized_pnl, 4),
        'total_fees': round(total_fees, 4),
        'total_dividends': round(total_dividends, 4),
        'net_capital_in': round(net_capital_in, 4),
    }


def compute_ticket_progress(
    ticket: dict[str, Any],
    linked_events: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute per-leg and total fill completion for a decision ticket."""
    reversed_ids = {
        int(ev['reverses_event_id'])
        for ev in linked_events
        if ev.get('reverses_event_id') is not None
    }
    active = [
        ev
        for ev in linked_events
        if int(ev['event_id']) not in reversed_ids
        and ev.get('reverses_event_id') is None
        and ev.get('event_type') in ('BUY', 'SELL')
    ]

    orders = (ticket.get('snapshot') or {}).get('orders') or []
    legs: list[dict[str, Any]] = []
    planned_shares = 0.0
    filled_shares = 0.0
    remaining_shares = 0.0
    planned_notional = 0.0
    filled_notional = 0.0
    executed_notional = 0.0

    for o in orders:
        sym = str(o.get('symbol') or '').strip()
        side = str(o.get('side') or '').upper()
        p_qty = float(o.get('quantity') or 0.0)
        ref_px = float(o.get('ref_price') or 0.0)
        matched = [
            ev for ev in active if ev.get('symbol') == sym and ev.get('event_type') == side
        ]
        f_qty = sum(float(ev.get('quantity') or 0.0) for ev in matched)
        rem_qty = max(0.0, p_qty - f_qty)
        if rem_qty <= 1e-6:
            rem_qty = 0.0
        capped_qty = min(p_qty, f_qty) if p_qty > 0 else 0.0
        leg_exec = sum(float(ev.get('quantity') or 0.0) * float(ev.get('price') or 0.0) for ev in matched)

        planned_shares += p_qty
        filled_shares += f_qty
        remaining_shares += rem_qty
        planned_notional += p_qty * ref_px
        filled_notional += capped_qty * ref_px
        executed_notional += leg_exec

        legs.append(
            {
                'symbol': sym,
                'name': o.get('name', INSTRUMENT_META.get(sym, {}).get('name', sym)),
                'side': side,
                'lot_size': int(o.get('lot_size') or INSTRUMENT_META.get(sym, {}).get('lot_size', 1)),
                'planned_qty': round(p_qty, 6),
                'filled_qty': round(f_qty, 6),
                'remaining_qty': round(rem_qty, 6),
                'ref_price': round(ref_px, 4),
            }
        )

    any_fill = len(active) > 0
    all_filled = (len(legs) > 0 and all(leg['remaining_qty'] <= 1e-6 for leg in legs)) or (
        len(legs) == 0 and any_fill
    )
    if planned_notional > 1e-9:
        completion_pct = round(min(100.0, filled_notional / planned_notional * 100.0), 2)
    else:
        completion_pct = 100.0 if any_fill else 0.0

    return {
        'legs': legs,
        'planned_shares': round(planned_shares, 6),
        'filled_shares': round(filled_shares, 6),
        'remaining_shares': round(remaining_shares, 6),
        'planned_notional': round(planned_notional, 2),
        'filled_notional': round(filled_notional, 2),
        'executed_notional': round(executed_notional, 2),
        'completion_pct': completion_pct,
        'all_filled': bool(all_filled),
    }


class TradingStore:
    """Thread-safe SQLite store for accounts, immutable ledger, tickets, stop states, and reviews."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_schema()

    @contextmanager
    def _connect(self):
        with self._lock:
            conn = sqlite3.connect(str(self.db_path), timeout=5.0, isolation_level=None)
            conn.row_factory = sqlite3.Row
            try:
                conn.execute('PRAGMA journal_mode=WAL;')
                conn.execute('PRAGMA foreign_keys=ON;')
                conn.execute('PRAGMA busy_timeout=5000;')
                yield conn
            finally:
                conn.close()

    @contextmanager
    def _tx(self):
        with self._connect() as conn:
            conn.execute('BEGIN IMMEDIATE;')
            try:
                yield conn
                if conn.in_transaction:
                    conn.execute('COMMIT;')
            except Exception:
                if conn.in_transaction:
                    conn.execute('ROLLBACK;')
                raise

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS accounts (
                    account_id TEXT PRIMARY KEY,
                    market TEXT NOT NULL CHECK(market IN ('CN', 'US')),
                    currency TEXT NOT NULL CHECK(currency IN ('CNY', 'USD')),
                    account_mode TEXT NOT NULL DEFAULT 'REAL' CHECK(account_mode IN ('REAL', 'DEMO')),
                    strategy_version TEXT NOT NULL DEFAULT 'baseline',
                    rebalance_deadband REAL NOT NULL,
                    core_floors_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS ledger_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id TEXT NOT NULL REFERENCES accounts(account_id),
                    event_time TEXT NOT NULL,
                    trade_date TEXT NOT NULL,
                    event_type TEXT NOT NULL CHECK(event_type IN (
                        'INIT_POSITION','BUY','SELL','CASH_IN','CASH_OUT','DIVIDEND','RECONCILE'
                    )),
                    symbol TEXT,
                    quantity REAL NOT NULL DEFAULT 0,
                    price REAL NOT NULL DEFAULT 0,
                    fee REAL NOT NULL DEFAULT 0,
                    cash_delta REAL NOT NULL,
                    decision_id INTEGER REFERENCES decision_tickets(decision_id),
                    discipline_tag TEXT NOT NULL DEFAULT 'FOLLOW',
                    note TEXT NOT NULL DEFAULT '',
                    client_request_id TEXT,
                    external_trade_id TEXT,
                    reverses_event_id INTEGER REFERENCES ledger_events(event_id),
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_ledger_account_date
                    ON ledger_events(account_id, trade_date, event_time, event_id);

                CREATE TABLE IF NOT EXISTS decision_tickets (
                    decision_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id TEXT NOT NULL REFERENCES accounts(account_id),
                    source TEXT NOT NULL CHECK(source IN ('WEEKLY_REBALANCE','INTRADAY_STOP','MANUAL')),
                    signal_cutoff TEXT NOT NULL,
                    signal_sha256 TEXT,
                    status TEXT NOT NULL CHECK(status IN ('PENDING','EXECUTED','PARTIAL','SKIPPED','EXPIRED')),
                    reason TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    review_note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    resolved_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_tickets_account_status
                    ON decision_tickets(account_id, status, decision_id);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_tickets_intraday_unique
                    ON decision_tickets(account_id, source, signal_cutoff)
                    WHERE source = 'INTRADAY_STOP';

                CREATE TABLE IF NOT EXISTS stop_states (
                    account_id TEXT NOT NULL REFERENCES accounts(account_id),
                    symbol TEXT NOT NULL,
                    session_date TEXT,
                    active_line REAL,
                    below_count INTEGER NOT NULL DEFAULT 0,
                    stage INTEGER NOT NULL DEFAULT 0,
                    pending_stage INTEGER NOT NULL DEFAULT 0,
                    pending_issued_at TEXT,
                    pending_line REAL,
                    pending_reason TEXT NOT NULL DEFAULT '',
                    cooldown_until_date TEXT,
                    last_bar_start TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (account_id, symbol)
                );

                CREATE TABLE IF NOT EXISTS review_notes (
                    review_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id TEXT NOT NULL REFERENCES accounts(account_id),
                    period_type TEXT NOT NULL CHECK(period_type IN ('DAILY','WEEKLY','MONTHLY')),
                    period_key TEXT NOT NULL,
                    summary_json TEXT NOT NULL,
                    reflection TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    UNIQUE(account_id, period_type, period_key)
                );

                CREATE TABLE IF NOT EXISTS notifications (
                    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id TEXT,
                    event_key TEXT NOT NULL UNIQUE,
                    category TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    title TEXT NOT NULL,
                    message TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    read_at TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_notifications_created
                    ON notifications(created_at DESC, notification_id DESC);
                """
            )
            # Additive schema migrations for pre-existing personal_trading.db files
            acc_cols = {r['name'] for r in conn.execute('PRAGMA table_info(accounts)').fetchall()}
            if 'account_mode' not in acc_cols:
                conn.execute("ALTER TABLE accounts ADD COLUMN account_mode TEXT NOT NULL DEFAULT 'REAL';")
            ev_cols = {r['name'] for r in conn.execute('PRAGMA table_info(ledger_events)').fetchall()}
            if 'client_request_id' not in ev_cols:
                conn.execute('ALTER TABLE ledger_events ADD COLUMN client_request_id TEXT;')
            if 'external_trade_id' not in ev_cols:
                conn.execute('ALTER TABLE ledger_events ADD COLUMN external_trade_id TEXT;')
            if 'reverses_event_id' not in ev_cols:
                conn.execute('ALTER TABLE ledger_events ADD COLUMN reverses_event_id INTEGER REFERENCES ledger_events(event_id);')
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_ledger_req_id
                    ON ledger_events(account_id, client_request_id)
                    WHERE client_request_id IS NOT NULL
                """
            )
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_ledger_ext_id
                    ON ledger_events(account_id, external_trade_id)
                    WHERE external_trade_id IS NOT NULL
                """
            )
        with self._tx() as conn:
            stamp = now_iso()
            for cfg in DEFAULT_ACCOUNTS:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO accounts (
                        account_id, market, currency, account_mode, strategy_version,
                        rebalance_deadband, core_floors_json, updated_at
                    ) VALUES (?, ?, ?, 'REAL', ?, ?, ?, ?)
                    """,
                    (
                        cfg['account_id'],
                        cfg['market'],
                        cfg['currency'],
                        cfg['strategy_version'],
                        cfg['rebalance_deadband'],
                        json.dumps(cfg['core_floors'], ensure_ascii=False),
                        stamp,
                    ),
                )

    @staticmethod
    def _row_to_account(row: sqlite3.Row) -> dict[str, Any]:
        keys = row.keys()
        return {
            'account_id': row['account_id'],
            'market': row['market'],
            'currency': row['currency'],
            'account_mode': row['account_mode'] if 'account_mode' in keys else 'REAL',
            'strategy_version': row['strategy_version'],
            'rebalance_deadband': float(row['rebalance_deadband']),
            'core_floors': json.loads(row['core_floors_json']),
            'updated_at': row['updated_at'],
        }

    def _fetch_account(self, conn: sqlite3.Connection, account_id: str) -> dict[str, Any]:
        row = conn.execute('SELECT * FROM accounts WHERE account_id = ?', (account_id,)).fetchone()
        if row is None:
            raise KeyError(f'账户不存在: {account_id}')
        return self._row_to_account(row)

    def _fetch_events(self, conn: sqlite3.Connection, account_id: str) -> list[dict[str, Any]]:
        rows = conn.execute(
            """
            SELECT * FROM ledger_events
            WHERE account_id = ?
            ORDER BY trade_date ASC, event_time ASC, event_id ASC
            """,
            (account_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def list_accounts(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute('SELECT * FROM accounts ORDER BY account_id ASC').fetchall()
            return [self._row_to_account(r) for r in rows]

    def get_account(self, account_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            return self._fetch_account(conn, account_id)

    def update_account_config(
        self,
        account_id: str,
        *,
        strategy_version: str | None = None,
        rebalance_deadband: float | None = None,
        core_floors: dict[str, Any] | None = None,
        account_mode: str | None = None,
    ) -> dict[str, Any]:
        with self._tx() as conn:
            acc = self._fetch_account(conn, account_id)
            next_ver = acc['strategy_version'] if strategy_version is None else str(strategy_version)
            if next_ver not in VALID_STRATEGY_VERSIONS:
                raise ValueError(f'无效策略版本: {next_ver}')
            if MARKET_ONLY_VERSIONS.get(next_ver, acc['market']) != acc['market']:
                raise ValueError(f'策略版本 {next_ver} 仅适用于 {MARKET_ONLY_VERSIONS[next_ver]} 账户')
            next_mode = acc.get('account_mode', 'REAL') if account_mode is None else str(account_mode).upper()
            if next_mode not in VALID_ACCOUNT_MODES:
                raise ValueError(f'无效账户模式: {next_mode}')
            if rebalance_deadband is None:
                next_db = acc['rebalance_deadband']
            else:
                db = clean_float(rebalance_deadband)
                if db is None or not (0.0 <= db <= 0.40):
                    raise ValueError('调仓死区必须在 0% ~ 40% 之间')
                next_db = db
            next_floors = (
                acc['core_floors']
                if core_floors is None
                else _parse_core_floors(core_floors, acc['market'])
            )
            stamp = now_iso()
            conn.execute(
                """
                UPDATE accounts
                SET account_mode = ?, strategy_version = ?, rebalance_deadband = ?, core_floors_json = ?, updated_at = ?
                WHERE account_id = ?
                """,
                (next_mode, next_ver, next_db, json.dumps(next_floors, ensure_ascii=False), stamp, account_id),
            )
            return self._fetch_account(conn, account_id)

    def list_events(
        self,
        account_id: str,
        *,
        limit: int | None = 200,
        event_type: str | None = None,
    ) -> list[dict[str, Any]]:
        with self._connect() as conn:
            self._fetch_account(conn, account_id)
            rev_rows = conn.execute(
                'SELECT reverses_event_id FROM ledger_events WHERE account_id = ? AND reverses_event_id IS NOT NULL',
                (account_id,),
            ).fetchall()
            reversed_ids = {int(r['reverses_event_id']) for r in rev_rows}
            sql = 'SELECT * FROM ledger_events WHERE account_id = ?'
            params: list[Any] = [account_id]
            if event_type and event_type != 'ALL':
                if event_type not in VALID_EVENT_TYPES:
                    raise ValueError(f'无效事件类型: {event_type}')
                sql += ' AND event_type = ?'
                params.append(event_type)
            sql += ' ORDER BY trade_date DESC, event_time DESC, event_id DESC'
            if limit is not None:
                sql += ' LIMIT ?'
                params.append(max(1, int(limit)))
            rows = conn.execute(sql, params).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                d['is_reversed'] = int(d['event_id']) in reversed_ids
                out.append(d)
            return out

    def get_portfolio(
        self,
        account_id: str,
        prices: dict[str, dict[str, Any]] | None = None,
        *,
        today_local_date: str | None = None,
    ) -> dict[str, Any]:
        with self._connect() as conn:
            acc = self._fetch_account(conn, account_id)
            events = self._fetch_events(conn, account_id)
        today = today_local_date or market_today(acc['market'])
        return project_portfolio(acc, events, prices, today)

    def _insert_event_validated(
        self,
        conn: sqlite3.Connection,
        acc: dict[str, Any],
        *,
        event_type: str,
        trade_date: str,
        event_time: str | None,
        symbol: str | None = None,
        quantity: float = 0.0,
        price: float = 0.0,
        fee: float = 0.0,
        cash_delta: float | None = None,
        amount: float | None = None,
        decision_id: int | None = None,
        discipline_tag: str = 'MANUAL_OVERRIDE',
        note: str = '',
        ignore_t_plus: bool = False,
        today_local_date: str | None = None,
        client_request_id: str | None = None,
        external_trade_id: str | None = None,
        reverses_event_id: int | None = None,
    ) -> int:
        if event_type not in VALID_EVENT_TYPES:
            raise ValueError(f'无效事件类型: {event_type}')
        if discipline_tag not in VALID_DISCIPLINE_TAGS:
            raise ValueError(f'无效纪律标签: {discipline_tag}')

        market = acc['market']
        td = normalize_trade_date(trade_date)
        et_iso = normalize_event_time(event_time, market, td)
        today_str = today_local_date or market_today(market)

        qty = clean_float(quantity)
        px = clean_float(price)
        f_fee = clean_float(fee)
        if qty is None or px is None or f_fee is None:
            raise ValueError('数量、价格和费用必须为有效数字')
        if f_fee < 0 or px < 0:
            raise ValueError('价格与费用不能为负数')
        if event_type != 'RECONCILE' and qty < 0:
            raise ValueError('数量不能为负数')

        clean_sym: str | None = None
        if symbol is not None and str(symbol).strip():
            clean_sym = _validate_symbol_for_market(str(symbol).strip(), market)

        if event_type in ('BUY', 'SELL'):
            if not clean_sym:
                raise ValueError('买卖交易必须指定有效标的')
            if qty <= 0 or px <= 0:
                raise ValueError('买卖数量与成交价必须大于 0')
            computed_cd = -(qty * px + f_fee) if event_type == 'BUY' else (qty * px - f_fee)
        elif event_type == 'CASH_IN':
            if clean_sym is not None:
                raise ValueError('纯入金事件不应指定标的代码')
            val = clean_float(cash_delta if cash_delta is not None else amount)
            if val is None or val <= 0:
                raise ValueError('入金金额必须大于 0')
            computed_cd = val
            qty = 0.0
            px = 0.0
        elif event_type == 'CASH_OUT':
            if clean_sym is not None:
                raise ValueError('纯出金事件不应指定标的代码')
            raw_val = clean_float(cash_delta if cash_delta is not None else amount)
            if raw_val is None or raw_val == 0:
                raise ValueError('出金金额必须大于 0')
            if cash_delta is None and raw_val < 0:
                raise ValueError('出金金额必须大于 0')
            computed_cd = -abs(raw_val)
            qty = 0.0
            px = 0.0
        elif event_type == 'DIVIDEND':
            val = clean_float(cash_delta if cash_delta is not None else amount)
            if val is None or val <= 0:
                raise ValueError('分红金额必须大于 0')
            computed_cd = val
        elif event_type == 'INIT_POSITION':
            if clean_sym:
                if qty <= 0 or px <= 0:
                    raise ValueError('初始持仓股数和成本价必须大于 0')
                computed_cd = 0.0 if cash_delta is None else float(cash_delta)
            else:
                val = clean_float(cash_delta if cash_delta is not None else amount)
                if val is None or val < 0:
                    raise ValueError('初始现金不能为负')
                computed_cd = val
        else:  # RECONCILE
            computed_cd = 0.0 if cash_delta is None else clean_float(cash_delta)
            if computed_cd is None:
                raise ValueError('对账现金差额无效')

        # Validate state invariants against current projected portfolio
        if reverses_event_id is None:
            cur_events = self._fetch_events(conn, acc['account_id'])
            cur = project_portfolio(acc, cur_events, None, today_str)

            if event_type == 'BUY':
                need = -computed_cd
                if cur['cash'] + 0.01 < need:
                    raise ValueError(f'可用现金不足：需要 {need:.2f}，当前可用 {cur["cash"]:.2f}')
            elif event_type == 'CASH_OUT':
                need = -computed_cd
                if cur['cash'] + 0.01 < need:
                    raise ValueError(f'可用现金不足以出金：需要 {need:.2f}，当前可用 {cur["cash"]:.2f}')
            elif event_type == 'SELL':
                pos_item = next(p for p in cur['positions'] if p['symbol'] == clean_sym)
                if qty > pos_item['shares'] + 1e-6:
                    raise ValueError(f'{clean_sym} 持仓不足：当前持有 {pos_item["shares"]:g} 股，尝试卖出 {qty:g} 股')
                if not ignore_t_plus and qty > pos_item['sellable_shares'] + 1e-6:
                    raise ValueError(
                        f'{clean_sym} 为 T+1 品种，今日仅可卖 {pos_item["sellable_shares"]:g} 股'
                        f'（今日新买锁定 {pos_item["today_bought_shares"]:g} 股）'
                    )

        if decision_id is not None:
            t_row = conn.execute(
                'SELECT account_id FROM decision_tickets WHERE decision_id = ?',
                (int(decision_id),),
            ).fetchone()
            if t_row is None or t_row['account_id'] != acc['account_id']:
                raise ValueError('关联决策票据不存在或不属于当前账户')

        clean_req = str(client_request_id).strip() if client_request_id else None
        clean_ext = str(external_trade_id).strip() if external_trade_id else None

        cur_ts = now_iso()
        cursor = conn.execute(
            """
            INSERT INTO ledger_events (
                account_id, event_time, trade_date, event_type, symbol,
                quantity, price, fee, cash_delta, decision_id,
                discipline_tag, note, client_request_id, external_trade_id,
                reverses_event_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                acc['account_id'],
                et_iso,
                td,
                event_type,
                clean_sym,
                float(qty),
                float(px),
                float(f_fee),
                float(computed_cd),
                int(decision_id) if decision_id is not None else None,
                discipline_tag,
                str(note or ''),
                clean_req,
                clean_ext,
                int(reverses_event_id) if reverses_event_id is not None else None,
                cur_ts,
            ),
        )
        return int(cursor.lastrowid)

    def init_account(
        self,
        account_id: str,
        *,
        trade_date: str,
        event_time: str | None = None,
        cash: float = 0.0,
        positions: list[dict[str, Any]] | dict[str, dict[str, Any]] | None = None,
        note: str = '账户初始化建账',
        account_mode: str | None = None,
    ) -> dict[str, Any]:
        c_val = clean_float(cash)
        if c_val is None or c_val < 0:
            raise ValueError('初始现金不能为负数')
        if account_mode is not None and str(account_mode).upper() not in VALID_ACCOUNT_MODES:
            raise ValueError(f'无效账户模式: {account_mode}')

        pos_list: list[dict[str, Any]] = []
        if isinstance(positions, dict):
            for sym, payload in positions.items():
                pos_list.append({'symbol': sym, **(payload or {})})
        elif isinstance(positions, list):
            pos_list = list(positions)

        non_zero = [
            p for p in pos_list if (clean_float(p.get('quantity')) or 0.0) > 0
        ]
        if c_val <= 0 and not non_zero:
            raise ValueError('初始现金与初始持仓不能同时为空')

        with self._tx() as conn:
            if account_mode is not None:
                conn.execute(
                    'UPDATE accounts SET account_mode = ?, updated_at = ? WHERE account_id = ?',
                    (str(account_mode).upper(), now_iso(), account_id),
                )
            acc = self._fetch_account(conn, account_id)
            td = normalize_trade_date(trade_date)
            created_ids: list[int] = []
            if c_val > 0:
                created_ids.append(
                    self._insert_event_validated(
                        conn,
                        acc,
                        event_type='INIT_POSITION',
                        trade_date=td,
                        event_time=event_time,
                        symbol=None,
                        quantity=0.0,
                        price=0.0,
                        fee=0.0,
                        cash_delta=c_val,
                        discipline_tag='INIT',
                        note=note,
                    )
                )
            for item in non_zero:
                sym = str(item.get('symbol') or '').strip()
                qty = clean_float(item.get('quantity'))
                px = clean_float(item.get('price'))
                if qty is None or qty <= 0 or px is None or px <= 0:
                    raise ValueError(f'初始持仓 {sym} 的数量和成本价必须大于 0')
                created_ids.append(
                    self._insert_event_validated(
                        conn,
                        acc,
                        event_type='INIT_POSITION',
                        trade_date=td,
                        event_time=event_time,
                        symbol=sym,
                        quantity=qty,
                        price=px,
                        fee=0.0,
                        cash_delta=0.0,
                        discipline_tag='INIT',
                        note=note,
                    )
                )
            events = self._fetch_events(conn, account_id)
        return {
            'created_event_ids': created_ids,
            'portfolio': project_portfolio(acc, events, None, market_today(acc['market'])),
        }

    def record_event(
        self,
        account_id: str,
        *,
        event_type: str,
        trade_date: str,
        event_time: str | None = None,
        symbol: str | None = None,
        quantity: float = 0.0,
        price: float = 0.0,
        fee: float = 0.0,
        cash_delta: float | None = None,
        amount: float | None = None,
        decision_id: int | None = None,
        discipline_tag: str = 'MANUAL_OVERRIDE',
        note: str = '',
        ignore_t_plus: bool = False,
        today_local_date: str | None = None,
        client_request_id: str | None = None,
        external_trade_id: str | None = None,
    ) -> dict[str, Any]:
        clean_req = str(client_request_id).strip() if client_request_id else None
        clean_ext = str(external_trade_id).strip() if external_trade_id else None
        with self._tx() as conn:
            acc = self._fetch_account(conn, account_id)
            if clean_req:
                existing = conn.execute(
                    'SELECT * FROM ledger_events WHERE account_id = ? AND client_request_id = ?',
                    (account_id, clean_req),
                ).fetchone()
                if existing is not None:
                    d = dict(existing)
                    d['duplicate'] = True
                    return d
            if clean_ext:
                existing_ext = conn.execute(
                    'SELECT * FROM ledger_events WHERE account_id = ? AND external_trade_id = ?',
                    (account_id, clean_ext),
                ).fetchone()
                if existing_ext is not None:
                    d = dict(existing_ext)
                    d['duplicate'] = True
                    return d
            eid = self._insert_event_validated(
                conn,
                acc,
                event_type=event_type,
                trade_date=trade_date,
                event_time=event_time,
                symbol=symbol,
                quantity=quantity,
                price=price,
                fee=fee,
                cash_delta=cash_delta,
                amount=amount,
                decision_id=decision_id,
                discipline_tag=discipline_tag,
                note=note,
                ignore_t_plus=ignore_t_plus,
                today_local_date=today_local_date,
                client_request_id=clean_req,
                external_trade_id=clean_ext,
            )
            row = conn.execute('SELECT * FROM ledger_events WHERE event_id = ?', (eid,)).fetchone()
            return dict(row)

    def reverse_event(
        self,
        account_id: str,
        event_id: int,
        *,
        reason: str,
        trade_date: str | None = None,
        event_time: str | None = None,
        today_local_date: str | None = None,
    ) -> dict[str, Any]:
        clean_reason = str(reason or '').strip()
        if not clean_reason:
            raise ValueError('冲正流水必须填写原因')
        with self._tx() as conn:
            acc = self._fetch_account(conn, account_id)
            orig = conn.execute(
                'SELECT * FROM ledger_events WHERE event_id = ?',
                (int(event_id),),
            ).fetchone()
            if orig is None or orig['account_id'] != account_id:
                raise KeyError(f'流水记录不存在或不属于 {account_id}: {event_id}')
            if orig['reverses_event_id'] is not None:
                raise ValueError('不能对冲正流水再次冲正')
            already = conn.execute(
                'SELECT event_id FROM ledger_events WHERE reverses_event_id = ?',
                (int(event_id),),
            ).fetchone()
            if already is not None:
                raise ValueError(f'流水 #{event_id} 已被冲正 (#{already["event_id"]})')

            market = acc['market']
            today_str = today_local_date or market_today(market)
            td = normalize_trade_date(trade_date) if trade_date else today_str

            # Ensure removing this event does not violate non-negative cash or share balances
            all_events = self._fetch_events(conn, account_id)
            sim_events = [e for e in all_events if int(e['event_id']) != int(event_id)]
            sim_pf = project_portfolio(acc, sim_events, None, today_str)
            if sim_pf['cash'] < -0.01:
                raise ValueError(
                    f'冲正该笔流水会导致现金变为负值 ({sim_pf["cash"]:.2f})，请先冲正后续支出流水'
                )
            for p in sim_pf['positions']:
                if p['shares'] < -1e-6:
                    raise ValueError(
                        f'冲正该笔流水会导致 {p["symbol"]} 持仓为负 ({p["shares"]:g})，请先冲正后续卖出流水'
                    )

            rev_id = self._insert_event_validated(
                conn,
                acc,
                event_type='RECONCILE',
                trade_date=td,
                event_time=event_time,
                symbol=orig['symbol'],
                quantity=-float(orig['quantity'] or 0.0),
                price=float(orig['price'] or 0.0),
                fee=0.0,
                cash_delta=-float(orig['cash_delta'] or 0.0),
                decision_id=orig['decision_id'],
                discipline_tag='CORRECTION',
                note=f'冲正 #{event_id}：{clean_reason}',
                ignore_t_plus=True,
                today_local_date=today_str,
                reverses_event_id=int(event_id),
            )

            # Recompute ticket progress if linked to a decision ticket
            if orig['decision_id'] is not None:
                did = int(orig['decision_id'])
                t_row = conn.execute(
                    'SELECT * FROM decision_tickets WHERE decision_id = ?',
                    (did,),
                ).fetchone()
                if t_row is not None and t_row['status'] in ('EXECUTED', 'PARTIAL'):
                    ticket_obj = self._row_to_ticket(t_row)
                    linked = [
                        dict(r)
                        for r in conn.execute(
                            'SELECT * FROM ledger_events WHERE decision_id = ? ORDER BY event_id ASC',
                            (did,),
                        ).fetchall()
                    ]
                    prog = compute_ticket_progress(ticket_obj, linked)
                    next_st = 'EXECUTED' if prog['all_filled'] else ('PARTIAL' if prog['filled_shares'] > 0 else 'PENDING')
                    res_at = now_iso() if next_st == 'EXECUTED' else None
                    conn.execute(
                        'UPDATE decision_tickets SET status = ?, resolved_at = ? WHERE decision_id = ?',
                        (next_st, res_at, did),
                    )

            rev_row = conn.execute('SELECT * FROM ledger_events WHERE event_id = ?', (rev_id,)).fetchone()
            return dict(rev_row)

    def reconcile_account(
        self,
        account_id: str,
        *,
        trade_date: str,
        event_time: str | None = None,
        target_cash: float,
        target_shares: dict[str, Any],
        prices: dict[str, dict[str, Any]] | None = None,
        note: str = '券商持仓对账校准',
        today_local_date: str | None = None,
    ) -> dict[str, Any]:
        tc = clean_float(target_cash)
        if tc is None or tc < 0:
            raise ValueError('对账目标现金不能为负')

        with self._tx() as conn:
            acc = self._fetch_account(conn, account_id)
            market = acc['market']
            allowed = set(market_symbols(market))
            for sym, sh in (target_shares or {}).items():
                if sym not in allowed:
                    raise ValueError(f'对账标的 {sym} 不属于 {market} 账户')
                val = clean_float(sh)
                if val is None or val < 0:
                    raise ValueError(f'对账股数 {sym} 不能为负')

            today_str = today_local_date or market_today(market)
            cur = project_portfolio(acc, self._fetch_events(conn, account_id), prices, today_str)
            td = normalize_trade_date(trade_date)
            created_ids: list[int] = []

            cash_diff = tc - cur['cash']
            if abs(cash_diff) > 1e-6:
                created_ids.append(
                    self._insert_event_validated(
                        conn,
                        acc,
                        event_type='RECONCILE',
                        trade_date=td,
                        event_time=event_time,
                        symbol=None,
                        quantity=0.0,
                        price=0.0,
                        fee=0.0,
                        cash_delta=round(cash_diff, 6),
                        discipline_tag='RECONCILE',
                        note=note,
                        today_local_date=today_str,
                    )
                )

            for pos in cur['positions']:
                sym = pos['symbol']
                if sym not in target_shares:
                    continue
                target_qty = float(target_shares[sym])
                qty_diff = target_qty - pos['shares']
                if abs(qty_diff) > 1e-6:
                    ref_px = pos['mark_price'] or pos['avg_cost'] or 0.0
                    created_ids.append(
                        self._insert_event_validated(
                            conn,
                            acc,
                            event_type='RECONCILE',
                            trade_date=td,
                            event_time=event_time,
                            symbol=sym,
                            quantity=round(qty_diff, 6),
                            price=float(ref_px),
                            fee=0.0,
                            cash_delta=0.0,
                            discipline_tag='RECONCILE',
                            note=note,
                            today_local_date=today_str,
                        )
                    )

            events = self._fetch_events(conn, account_id)
        return {
            'created_event_ids': created_ids,
            'portfolio': project_portfolio(acc, events, prices, today_str),
        }

    # --- Decision Tickets ---

    @staticmethod
    def _row_to_ticket(row: sqlite3.Row) -> dict[str, Any]:
        return {
            'decision_id': int(row['decision_id']),
            'account_id': row['account_id'],
            'source': row['source'],
            'signal_cutoff': row['signal_cutoff'],
            'signal_sha256': row['signal_sha256'],
            'status': row['status'],
            'reason': row['reason'],
            'snapshot': json.loads(row['snapshot_json']),
            'review_note': row['review_note'],
            'created_at': row['created_at'],
            'resolved_at': row['resolved_at'],
        }

    def _enrich_ticket(self, conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        ticket = self._row_to_ticket(row)
        linked = [
            dict(r)
            for r in conn.execute(
                'SELECT * FROM ledger_events WHERE decision_id = ? ORDER BY event_id ASC',
                (int(row['decision_id']),),
            ).fetchall()
        ]
        ticket['progress'] = compute_ticket_progress(ticket, linked)
        return ticket

    def list_tickets(
        self,
        account_id: str,
        *,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        with self._connect() as conn:
            self._fetch_account(conn, account_id)
            sql = 'SELECT * FROM decision_tickets WHERE account_id = ?'
            params: list[Any] = [account_id]
            if status and status != 'ALL':
                if status not in VALID_TICKET_STATUSES:
                    raise ValueError(f'无效票据状态: {status}')
                sql += ' AND status = ?'
                params.append(status)
            sql += ' ORDER BY decision_id DESC LIMIT ?'
            params.append(max(1, min(1000, int(limit))))
            rows = conn.execute(sql, params).fetchall()
            return [self._enrich_ticket(conn, r) for r in rows]

    def get_ticket(self, decision_id: int) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                'SELECT * FROM decision_tickets WHERE decision_id = ?',
                (int(decision_id),),
            ).fetchone()
            if row is None:
                raise KeyError(f'决策票据不存在: {decision_id}')
            return self._enrich_ticket(conn, row)

    def create_ticket(
        self,
        account_id: str,
        *,
        source: str,
        signal_cutoff: str,
        reason: str,
        snapshot: dict[str, Any],
        signal_sha256: str | None = None,
    ) -> dict[str, Any]:
        if source not in VALID_TICKET_SOURCES:
            raise ValueError(f'无效票据来源: {source}')
        with self._tx() as conn:
            self._fetch_account(conn, account_id)
            if source == 'INTRADAY_STOP':
                existing = conn.execute(
                    """
                    SELECT * FROM decision_tickets
                    WHERE account_id = ? AND source = 'INTRADAY_STOP' AND signal_cutoff = ?
                    """,
                    (account_id, signal_cutoff),
                ).fetchone()
                if existing is not None:
                    return self._enrich_ticket(conn, existing)
            cur_ts = now_iso()
            cursor = conn.execute(
                """
                INSERT INTO decision_tickets (
                    account_id, source, signal_cutoff, signal_sha256,
                    status, reason, snapshot_json, review_note, created_at, resolved_at
                ) VALUES (?, ?, ?, ?, 'PENDING', ?, ?, '', ?, NULL)
                """,
                (
                    account_id,
                    source,
                    signal_cutoff,
                    signal_sha256,
                    reason,
                    json.dumps(snapshot, ensure_ascii=False),
                    cur_ts,
                ),
            )
            row = conn.execute(
                'SELECT * FROM decision_tickets WHERE decision_id = ?',
                (int(cursor.lastrowid),),
            ).fetchone()
            return self._enrich_ticket(conn, row)

    def upsert_intraday_ticket(
        self,
        account_id: str,
        *,
        signal_cutoff: str,
        reason: str,
        snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        return self.create_ticket(
            account_id,
            source='INTRADAY_STOP',
            signal_cutoff=signal_cutoff,
            reason=reason,
            snapshot=snapshot,
            signal_sha256=None,
        )

    def resolve_ticket_with_fills(
        self,
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
        today_local_date: str | None = None,
        client_request_id: str | None = None,
    ) -> dict[str, Any]:
        if status is not None and status not in ('EXECUTED', 'PARTIAL'):
            raise ValueError(f'成交状态无效: {status}')
        if not fills:
            raise ValueError('至少需要一条有效成交明细')
        clean_req = str(client_request_id).strip() if client_request_id else None
        with self._tx() as conn:
            acc = self._fetch_account(conn, account_id)
            row = conn.execute(
                'SELECT * FROM decision_tickets WHERE decision_id = ?',
                (int(decision_id),),
            ).fetchone()
            if row is None or row['account_id'] != account_id:
                raise KeyError(f'决策票据不存在或不属于 {account_id}: {decision_id}')

            if clean_req:
                prefix = clean_req + ':'
                dup_rows = conn.execute(
                    """
                    SELECT event_id FROM ledger_events
                    WHERE account_id = ?
                      AND (client_request_id = ? OR substr(client_request_id, 1, ?) = ?)
                    ORDER BY event_id ASC
                    """,
                    (account_id, clean_req, len(prefix), prefix),
                ).fetchall()
                if dup_rows:
                    return {
                        'ticket': self._enrich_ticket(conn, row),
                        'created_event_ids': [int(r['event_id']) for r in dup_rows],
                        'duplicate': True,
                    }

            if row['status'] not in ('PENDING', 'PARTIAL'):
                raise ValueError(f'票据 #{decision_id} 状态为 {row["status"]}，不能重复确认成交')

            # Always apply SELL legs before BUY legs so proceeds fund buys cleanly
            ordered_fills = sorted(
                fills,
                key=lambda f: 0 if str(f.get('side', '')).upper() == 'SELL' else 1,
            )
            created_ids: list[int] = []
            for idx, item in enumerate(ordered_fills):
                side = str(item.get('side') or '').upper()
                if side not in ('BUY', 'SELL'):
                    raise ValueError(f'无效买卖方向: {side}')
                sym = str(item.get('symbol') or '').strip()
                qty = clean_float(item.get('quantity'))
                px = clean_float(item.get('price'))
                fee_val = clean_float(item.get('fee', 0.0))
                if qty is None or qty <= 0 or px is None or px <= 0 or fee_val is None or fee_val < 0:
                    raise ValueError(f'成交明细无效: {item}')
                leg_req = f'{clean_req}:{idx}' if clean_req else None
                eid = self._insert_event_validated(
                    conn,
                    acc,
                    event_type=side,
                    trade_date=trade_date,
                    event_time=event_time,
                    symbol=sym,
                    quantity=qty,
                    price=px,
                    fee=fee_val,
                    decision_id=int(decision_id),
                    discipline_tag=discipline_tag,
                    note=str(item.get('note') or review_note or f'执行决策 #{decision_id}'),
                    ignore_t_plus=ignore_t_plus,
                    today_local_date=today_local_date,
                    client_request_id=leg_req,
                )
                created_ids.append(eid)

            updated_ticket = self._enrich_ticket(conn, row)
            all_filled = updated_ticket['progress']['all_filled']
            next_status = 'EXECUTED' if all_filled else 'PARTIAL'
            resolved_at = now_iso() if next_status == 'EXECUTED' else None
            conn.execute(
                """
                UPDATE decision_tickets
                SET status = ?, review_note = ?, resolved_at = ?
                WHERE decision_id = ?
                """,
                (next_status, str(review_note or ''), resolved_at, int(decision_id)),
            )
            updated = conn.execute(
                'SELECT * FROM decision_tickets WHERE decision_id = ?',
                (int(decision_id),),
            ).fetchone()
            return {
                'ticket': self._enrich_ticket(conn, updated),
                'created_event_ids': created_ids,
                'duplicate': False,
            }

    def cancel_ticket_remainder(
        self,
        account_id: str,
        decision_id: int,
        *,
        review_note: str = '',
    ) -> dict[str, Any]:
        return self.skip_ticket(
            account_id,
            decision_id,
            status='EXPIRED',
            review_note=str(review_note or '取消剩余未成交余量'),
        )

    def skip_ticket(
        self,
        account_id: str,
        decision_id: int,
        *,
        status: str = 'SKIPPED',
        review_note: str = '',
    ) -> dict[str, Any]:
        if status not in ('SKIPPED', 'EXPIRED'):
            raise ValueError(f'无效跳过状态: {status}')
        with self._tx() as conn:
            self._fetch_account(conn, account_id)
            row = conn.execute(
                'SELECT * FROM decision_tickets WHERE decision_id = ?',
                (int(decision_id),),
            ).fetchone()
            if row is None or row['account_id'] != account_id:
                raise KeyError(f'决策票据不存在或不属于 {account_id}: {decision_id}')
            if row['status'] not in ('PENDING', 'PARTIAL'):
                raise ValueError(f'票据 #{decision_id} 状态为 {row["status"]}，不能再跳过')
            resolved_at = now_iso()
            conn.execute(
                """
                UPDATE decision_tickets
                SET status = ?, review_note = ?, resolved_at = ?
                WHERE decision_id = ?
                """,
                (status, str(review_note or '手动跳过'), resolved_at, int(decision_id)),
            )
            if row['source'] == 'INTRADAY_STOP':
                snap = json.loads(row['snapshot_json'])
                sym = snap.get('symbol')
                if sym:
                    conn.execute(
                        """
                        UPDATE stop_states
                        SET pending_stage = 0, pending_issued_at = NULL,
                            pending_line = NULL, pending_reason = ?, updated_at = ?
                        WHERE account_id = ? AND symbol = ?
                        """,
                        (str(review_note or '已跳过盘中减仓票据'), resolved_at, account_id, sym),
                    )
            updated = conn.execute(
                'SELECT * FROM decision_tickets WHERE decision_id = ?',
                (int(decision_id),),
            ).fetchone()
            return self._enrich_ticket(conn, updated)

    # --- Stop States ---

    def get_stop_states(self, account_id: str) -> dict[str, dict[str, Any]]:
        with self._connect() as conn:
            self._fetch_account(conn, account_id)
            rows = conn.execute(
                'SELECT * FROM stop_states WHERE account_id = ?',
                (account_id,),
            ).fetchall()
            return {r['symbol']: dict(r) for r in rows}

    def save_stop_state(
        self,
        account_id: str,
        symbol: str,
        state: dict[str, Any],
    ) -> dict[str, Any]:
        with self._tx() as conn:
            acc = self._fetch_account(conn, account_id)
            _validate_symbol_for_market(symbol, acc['market'])
            stamp = now_iso()
            conn.execute(
                """
                INSERT INTO stop_states (
                    account_id, symbol, session_date, active_line, below_count,
                    stage, pending_stage, pending_issued_at, pending_line,
                    pending_reason, cooldown_until_date, last_bar_start, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(account_id, symbol) DO UPDATE SET
                    session_date = excluded.session_date,
                    active_line = excluded.active_line,
                    below_count = excluded.below_count,
                    stage = excluded.stage,
                    pending_stage = excluded.pending_stage,
                    pending_issued_at = excluded.pending_issued_at,
                    pending_line = excluded.pending_line,
                    pending_reason = excluded.pending_reason,
                    cooldown_until_date = excluded.cooldown_until_date,
                    last_bar_start = excluded.last_bar_start,
                    updated_at = excluded.updated_at
                """,
                (
                    account_id,
                    symbol,
                    state.get('session_date'),
                    clean_float(state.get('active_line')),
                    int(state.get('below_count') or 0),
                    int(state.get('stage') or 0),
                    int(state.get('pending_stage') or 0),
                    state.get('pending_issued_at'),
                    clean_float(state.get('pending_line')),
                    str(state.get('pending_reason') or ''),
                    state.get('cooldown_until_date'),
                    state.get('last_bar_start'),
                    stamp,
                ),
            )
            row = conn.execute(
                'SELECT * FROM stop_states WHERE account_id = ? AND symbol = ?',
                (account_id, symbol),
            ).fetchone()
            return dict(row)

    def reset_stop_state(
        self,
        account_id: str,
        symbol: str,
        *,
        reason: str = '',
        clear_cooldown: bool = True,
    ) -> dict[str, Any]:
        with self._tx() as conn:
            acc = self._fetch_account(conn, account_id)
            _validate_symbol_for_market(symbol, acc['market'])
            existing = conn.execute(
                'SELECT * FROM stop_states WHERE account_id = ? AND symbol = ?',
                (account_id, symbol),
            ).fetchone()
            cooldown = None if clear_cooldown else (existing['cooldown_until_date'] if existing else None)
            stamp = now_iso()
            conn.execute(
                """
                INSERT INTO stop_states (
                    account_id, symbol, session_date, active_line, below_count,
                    stage, pending_stage, pending_issued_at, pending_line,
                    pending_reason, cooldown_until_date, last_bar_start, updated_at
                ) VALUES (?, ?, NULL, NULL, 0, 0, 0, NULL, NULL, '', ?, NULL, ?)
                ON CONFLICT(account_id, symbol) DO UPDATE SET
                    active_line = NULL,
                    below_count = 0,
                    stage = 0,
                    pending_stage = 0,
                    pending_issued_at = NULL,
                    pending_line = NULL,
                    pending_reason = ?,
                    cooldown_until_date = ?,
                    updated_at = ?
                """,
                (
                    account_id,
                    symbol,
                    cooldown,
                    stamp,
                    str(reason or ''),
                    cooldown,
                    stamp,
                ),
            )
            # Mark any pending INTRADAY_STOP tickets for this symbol as EXPIRED
            rows = conn.execute(
                """
                SELECT decision_id, snapshot_json FROM decision_tickets
                WHERE account_id = ? AND source = 'INTRADAY_STOP' AND status = 'PENDING'
                """,
                (account_id,),
            ).fetchall()
            for r in rows:
                snap = json.loads(r['snapshot_json'])
                if snap.get('symbol') == symbol:
                    conn.execute(
                        """
                        UPDATE decision_tickets
                        SET status = 'EXPIRED', review_note = ?, resolved_at = ?
                        WHERE decision_id = ?
                        """,
                        (str(reason or '止损状态已手动重置'), stamp, int(r['decision_id'])),
                    )
            row = conn.execute(
                'SELECT * FROM stop_states WHERE account_id = ? AND symbol = ?',
                (account_id, symbol),
            ).fetchone()
            return dict(row)

    # --- Review Notes ---

    def upsert_review_note(
        self,
        account_id: str,
        *,
        period_type: str,
        period_key: str,
        summary: dict[str, Any],
        reflection: str,
    ) -> dict[str, Any]:
        if period_type not in VALID_PERIOD_TYPES:
            raise ValueError(f'无效复盘周期类型: {period_type}')
        key = str(period_key or '').strip()
        if not key:
            raise ValueError('复盘周期标识不能为空')
        with self._tx() as conn:
            self._fetch_account(conn, account_id)
            stamp = now_iso()
            conn.execute(
                """
                INSERT INTO review_notes (
                    account_id, period_type, period_key, summary_json, reflection, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(account_id, period_type, period_key) DO UPDATE SET
                    summary_json = excluded.summary_json,
                    reflection = excluded.reflection,
                    created_at = excluded.created_at
                """,
                (
                    account_id,
                    period_type,
                    key,
                    json.dumps(summary, ensure_ascii=False),
                    str(reflection or ''),
                    stamp,
                ),
            )
            row = conn.execute(
                """
                SELECT * FROM review_notes
                WHERE account_id = ? AND period_type = ? AND period_key = ?
                """,
                (account_id, period_type, key),
            ).fetchone()
            return {
                'review_id': int(row['review_id']),
                'account_id': row['account_id'],
                'period_type': row['period_type'],
                'period_key': row['period_key'],
                'summary': json.loads(row['summary_json']),
                'reflection': row['reflection'],
                'created_at': row['created_at'],
            }

    def list_review_notes(self, account_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as conn:
            self._fetch_account(conn, account_id)
            rows = conn.execute(
                """
                SELECT * FROM review_notes
                WHERE account_id = ?
                ORDER BY period_key DESC, review_id DESC
                LIMIT ?
                """,
                (account_id, max(1, min(500, int(limit)))),
            ).fetchall()
            return [
                {
                    'review_id': int(r['review_id']),
                    'account_id': r['account_id'],
                    'period_type': r['period_type'],
                    'period_key': r['period_key'],
                    'summary': json.loads(r['summary_json']),
                    'reflection': r['reflection'],
                    'created_at': r['created_at'],
                }
                for r in rows
            ]

    # --- Full Backup & Restore ---

    @staticmethod
    def _backup_hash(data: dict[str, Any]) -> str:
        raw = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
        return hashlib.sha256(raw).hexdigest()

    def export_full_backup(self) -> dict[str, Any]:
        with self._connect() as conn:
            data = {
                'accounts': [dict(r) for r in conn.execute('SELECT * FROM accounts ORDER BY account_id ASC').fetchall()],
                'decision_tickets': [
                    dict(r) for r in conn.execute('SELECT * FROM decision_tickets ORDER BY decision_id ASC').fetchall()
                ],
                'ledger_events': [
                    dict(r) for r in conn.execute('SELECT * FROM ledger_events ORDER BY event_id ASC').fetchall()
                ],
                'stop_states': [
                    dict(r) for r in conn.execute('SELECT * FROM stop_states ORDER BY account_id ASC, symbol ASC').fetchall()
                ],
                'review_notes': [
                    dict(r) for r in conn.execute('SELECT * FROM review_notes ORDER BY review_id ASC').fetchall()
                ],
                'notifications': [
                    dict(r) for r in conn.execute('SELECT * FROM notifications ORDER BY notification_id ASC').fetchall()
                ],
            }
        counts = {k: len(v) for k, v in data.items()}
        return {
            'schema_version': 2,
            'exported_at': now_iso(),
            'counts': counts,
            'data': data,
            'payload_sha256': self._backup_hash(data),
        }

    def preview_full_backup(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError('备份文件必须为 JSON 对象')
        if payload.get('schema_version') != 2:
            raise ValueError('不支持的备份格式版本（需要 schema_version = 2）')
        data = payload.get('data')
        if not isinstance(data, dict):
            raise ValueError('备份缺少 data 数据段')
        expected_sha = str(payload.get('payload_sha256') or '')
        if not expected_sha or self._backup_hash(data) != expected_sha:
            raise ValueError('备份校验和 mismatch：文件可能已被修改或损坏')

        required_tables = ('accounts', 'decision_tickets', 'ledger_events', 'stop_states', 'review_notes', 'notifications')
        for tbl in required_tables:
            if not isinstance(data.get(tbl), list):
                raise ValueError(f'备份数据表 {tbl} 缺失或格式无效')

        account_ids = {str(a.get('account_id') or '') for a in data['accounts']}
        if not {'CN_MAIN', 'US_MAIN'}.issubset(account_ids):
            raise ValueError('备份账户表缺少必要的 CN_MAIN / US_MAIN 账户定义')
        for tbl in ('decision_tickets', 'ledger_events', 'stop_states', 'review_notes'):
            for item in data[tbl]:
                if str(item.get('account_id') or '') not in account_ids:
                    raise ValueError(f'备份表 {tbl} 包含未知账户外键: {item.get("account_id")}')

        return {
            'valid': True,
            'schema_version': 2,
            'exported_at': payload.get('exported_at'),
            'payload_sha256': expected_sha,
            'counts': {k: len(data[k]) for k in required_tables},
            'accounts': [
                {
                    'account_id': a['account_id'],
                    'market': a['market'],
                    'currency': a['currency'],
                    'account_mode': a.get('account_mode', 'REAL'),
                    'strategy_version': a.get('strategy_version', 'baseline'),
                }
                for a in data['accounts']
            ],
        }

    def restore_full_backup(self, payload: dict[str, Any]) -> dict[str, Any]:
        preview = self.preview_full_backup(payload)
        data = payload['data']
        with self._tx() as conn:
            for tbl in ('notifications', 'review_notes', 'stop_states', 'ledger_events', 'decision_tickets', 'accounts'):
                conn.execute(f'DELETE FROM {tbl}')

            for a in data['accounts']:
                conn.execute(
                    """
                    INSERT INTO accounts (
                        account_id, market, currency, account_mode, strategy_version,
                        rebalance_deadband, core_floors_json, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        a['account_id'],
                        a['market'],
                        a['currency'],
                        a.get('account_mode', 'REAL'),
                        a.get('strategy_version', 'baseline'),
                        float(a['rebalance_deadband']),
                        a['core_floors_json'],
                        a['updated_at'],
                    ),
                )

            for t in sorted(data['decision_tickets'], key=lambda x: int(x['decision_id'])):
                conn.execute(
                    """
                    INSERT INTO decision_tickets (
                        decision_id, account_id, source, signal_cutoff, signal_sha256,
                        status, reason, snapshot_json, review_note, created_at, resolved_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        int(t['decision_id']),
                        t['account_id'],
                        t['source'],
                        t['signal_cutoff'],
                        t.get('signal_sha256'),
                        t['status'],
                        t['reason'],
                        t['snapshot_json'],
                        t.get('review_note', ''),
                        t['created_at'],
                        t.get('resolved_at'),
                    ),
                )

            for e in sorted(data['ledger_events'], key=lambda x: int(x['event_id'])):
                conn.execute(
                    """
                    INSERT INTO ledger_events (
                        event_id, account_id, event_time, trade_date, event_type, symbol,
                        quantity, price, fee, cash_delta, decision_id, discipline_tag,
                        note, client_request_id, external_trade_id, reverses_event_id, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        int(e['event_id']),
                        e['account_id'],
                        e['event_time'],
                        e['trade_date'],
                        e['event_type'],
                        e.get('symbol'),
                        float(e.get('quantity') or 0.0),
                        float(e.get('price') or 0.0),
                        float(e.get('fee') or 0.0),
                        float(e['cash_delta']),
                        int(e['decision_id']) if e.get('decision_id') is not None else None,
                        e.get('discipline_tag', 'FOLLOW'),
                        e.get('note', ''),
                        e.get('client_request_id'),
                        e.get('external_trade_id'),
                        int(e['reverses_event_id']) if e.get('reverses_event_id') is not None else None,
                        e['created_at'],
                    ),
                )

            for s in data['stop_states']:
                conn.execute(
                    """
                    INSERT INTO stop_states (
                        account_id, symbol, session_date, active_line, below_count,
                        stage, pending_stage, pending_issued_at, pending_line,
                        pending_reason, cooldown_until_date, last_bar_start, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        s['account_id'],
                        s['symbol'],
                        s.get('session_date'),
                        clean_float(s.get('active_line')),
                        int(s.get('below_count') or 0),
                        int(s.get('stage') or 0),
                        int(s.get('pending_stage') or 0),
                        s.get('pending_issued_at'),
                        clean_float(s.get('pending_line')),
                        s.get('pending_reason', ''),
                        s.get('cooldown_until_date'),
                        s.get('last_bar_start'),
                        s['updated_at'],
                    ),
                )

            for n in sorted(data['review_notes'], key=lambda x: int(x['review_id'])):
                conn.execute(
                    """
                    INSERT INTO review_notes (
                        review_id, account_id, period_type, period_key,
                        summary_json, reflection, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        int(n['review_id']),
                        n['account_id'],
                        n['period_type'],
                        n['period_key'],
                        n['summary_json'],
                        n.get('reflection', ''),
                        n['created_at'],
                    ),
                )

            for nt in sorted(data['notifications'], key=lambda x: int(x['notification_id'])):
                conn.execute(
                    """
                    INSERT INTO notifications (
                        notification_id, account_id, event_key, category, severity,
                        title, message, payload_json, read_at, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        int(nt['notification_id']),
                        nt.get('account_id'),
                        nt['event_key'],
                        nt['category'],
                        nt['severity'],
                        nt['title'],
                        nt['message'],
                        nt.get('payload_json', '{}'),
                        nt.get('read_at'),
                        nt['created_at'],
                    ),
                )

            seq_exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sqlite_sequence'"
            ).fetchone()
            if seq_exists is not None:
                for tbl, pk in (
                    ('decision_tickets', 'decision_id'),
                    ('ledger_events', 'event_id'),
                    ('review_notes', 'review_id'),
                    ('notifications', 'notification_id'),
                ):
                    max_id = conn.execute(f'SELECT COALESCE(MAX({pk}), 0) AS m FROM {tbl}').fetchone()['m']
                    conn.execute('DELETE FROM sqlite_sequence WHERE name = ?', (tbl,))
                    if max_id > 0:
                        conn.execute('INSERT INTO sqlite_sequence (name, seq) VALUES (?, ?)', (tbl, int(max_id)))
        return preview

    # --- Notifications ---

    def notify_once(
        self,
        event_key: str,
        *,
        account_id: str | None = None,
        category: str,
        severity: str,
        title: str,
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        key = str(event_key or '').strip()
        if not key:
            raise ValueError('event_key 不能为空')
        with self._tx() as conn:
            existing = conn.execute(
                'SELECT notification_id FROM notifications WHERE event_key = ?',
                (key,),
            ).fetchone()
            if existing is not None:
                return None
            stamp = now_iso()
            cursor = conn.execute(
                """
                INSERT INTO notifications (
                    account_id, event_key, category, severity, title, message, payload_json, read_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?)
                """,
                (
                    account_id,
                    key,
                    str(category),
                    str(severity),
                    str(title),
                    str(message),
                    json.dumps(payload or {}, ensure_ascii=False),
                    stamp,
                ),
            )
            row = conn.execute(
                'SELECT * FROM notifications WHERE notification_id = ?',
                (int(cursor.lastrowid),),
            ).fetchone()
            out = dict(row)
            out['payload'] = json.loads(out.pop('payload_json'))
            return out

    def list_notifications(self, *, limit: int = 50, unread_only: bool = False) -> list[dict[str, Any]]:
        with self._connect() as conn:
            sql = 'SELECT * FROM notifications'
            if unread_only:
                sql += ' WHERE read_at IS NULL'
            sql += ' ORDER BY notification_id DESC LIMIT ?'
            rows = conn.execute(sql, (max(1, min(500, int(limit))),)).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                d['payload'] = json.loads(d.pop('payload_json') or '{}')
                out.append(d)
            return out

    def mark_notifications_read(self, notification_ids: list[int] | None = None) -> int:
        stamp = now_iso()
        with self._tx() as conn:
            if notification_ids is None:
                cur = conn.execute('UPDATE notifications SET read_at = ? WHERE read_at IS NULL', (stamp,))
                return int(cur.rowcount)
            ids = [int(x) for x in notification_ids]
            if not ids:
                return 0
            placeholders = ','.join('?' for _ in ids)
            cur = conn.execute(
                f'UPDATE notifications SET read_at = ? WHERE read_at IS NULL AND notification_id IN ({placeholders})',
                [stamp, *ids],
            )
            return int(cur.rowcount)
