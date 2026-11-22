"""Persistent SQLite ledger, portfolio projection, and trading OS state store."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
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
VALID_STRATEGY_VERSIONS = ('baseline', 'advisory', 'blend25')
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

    ordered = sorted(
        events,
        key=lambda e: (str(e['trade_date']), str(e['event_time']), int(e['event_id'])),
    )

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
        'as_of_date': today_local_date,
        'initialized': len(ordered) > 0,
        'event_count': len(ordered),
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
                """
            )
        with self._tx() as conn:
            stamp = now_iso()
            for cfg in DEFAULT_ACCOUNTS:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO accounts (
                        account_id, market, currency, strategy_version,
                        rebalance_deadband, core_floors_json, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
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
        return {
            'account_id': row['account_id'],
            'market': row['market'],
            'currency': row['currency'],
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
    ) -> dict[str, Any]:
        with self._tx() as conn:
            acc = self._fetch_account(conn, account_id)
            next_ver = acc['strategy_version'] if strategy_version is None else str(strategy_version)
            if next_ver not in VALID_STRATEGY_VERSIONS:
                raise ValueError(f'无效策略版本: {next_ver}')
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
                SET strategy_version = ?, rebalance_deadband = ?, core_floors_json = ?, updated_at = ?
                WHERE account_id = ?
                """,
                (next_ver, next_db, json.dumps(next_floors, ensure_ascii=False), stamp, account_id),
            )
            return self._fetch_account(conn, account_id)

    def list_events(
        self,
        account_id: str,
        *,
        limit: int = 200,
        event_type: str | None = None,
    ) -> list[dict[str, Any]]:
        with self._connect() as conn:
            self._fetch_account(conn, account_id)
            sql = 'SELECT * FROM ledger_events WHERE account_id = ?'
            params: list[Any] = [account_id]
            if event_type and event_type != 'ALL':
                if event_type not in VALID_EVENT_TYPES:
                    raise ValueError(f'无效事件类型: {event_type}')
                sql += ' AND event_type = ?'
                params.append(event_type)
            sql += ' ORDER BY trade_date DESC, event_time DESC, event_id DESC LIMIT ?'
            params.append(max(1, min(1000, int(limit))))
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]

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

        cur_ts = now_iso()
        cursor = conn.execute(
            """
            INSERT INTO ledger_events (
                account_id, event_time, trade_date, event_type, symbol,
                quantity, price, fee, cash_delta, decision_id,
                discipline_tag, note, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
    ) -> dict[str, Any]:
        c_val = clean_float(cash)
        if c_val is None or c_val < 0:
            raise ValueError('初始现金不能为负数')

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
    ) -> dict[str, Any]:
        with self._tx() as conn:
            acc = self._fetch_account(conn, account_id)
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
            )
            row = conn.execute('SELECT * FROM ledger_events WHERE event_id = ?', (eid,)).fetchone()
            return dict(row)

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
            params.append(max(1, min(500, int(limit))))
            rows = conn.execute(sql, params).fetchall()
            return [self._row_to_ticket(r) for r in rows]

    def get_ticket(self, decision_id: int) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                'SELECT * FROM decision_tickets WHERE decision_id = ?',
                (int(decision_id),),
            ).fetchone()
            if row is None:
                raise KeyError(f'决策票据不存在: {decision_id}')
            return self._row_to_ticket(row)

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
                    return self._row_to_ticket(existing)
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
            return self._row_to_ticket(row)

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
        status: str = 'EXECUTED',
        ignore_t_plus: bool = False,
        today_local_date: str | None = None,
    ) -> dict[str, Any]:
        if status not in ('EXECUTED', 'PARTIAL'):
            raise ValueError(f'成交状态无效: {status}')
        if not fills:
            raise ValueError('至少需要一条有效成交明细')
        with self._tx() as conn:
            acc = self._fetch_account(conn, account_id)
            row = conn.execute(
                'SELECT * FROM decision_tickets WHERE decision_id = ?',
                (int(decision_id),),
            ).fetchone()
            if row is None or row['account_id'] != account_id:
                raise KeyError(f'决策票据不存在或不属于 {account_id}: {decision_id}')
            if row['status'] not in ('PENDING', 'PARTIAL'):
                raise ValueError(f'票据 #{decision_id} 状态为 {row["status"]}，不能重复确认成交')

            # Always apply SELL legs before BUY legs so proceeds fund buys cleanly
            ordered_fills = sorted(
                fills,
                key=lambda f: 0 if str(f.get('side', '')).upper() == 'SELL' else 1,
            )
            created_ids: list[int] = []
            for item in ordered_fills:
                side = str(item.get('side') or '').upper()
                if side not in ('BUY', 'SELL'):
                    raise ValueError(f'无效买卖方向: {side}')
                sym = str(item.get('symbol') or '').strip()
                qty = clean_float(item.get('quantity'))
                px = clean_float(item.get('price'))
                fee_val = clean_float(item.get('fee', 0.0))
                if qty is None or qty <= 0 or px is None or px <= 0 or fee_val is None or fee_val < 0:
                    raise ValueError(f'成交明细无效: {item}')
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
                )
                created_ids.append(eid)

            resolved_at = now_iso()
            conn.execute(
                """
                UPDATE decision_tickets
                SET status = ?, review_note = ?, resolved_at = ?
                WHERE decision_id = ?
                """,
                (status, str(review_note or ''), resolved_at, int(decision_id)),
            )
            updated = conn.execute(
                'SELECT * FROM decision_tickets WHERE decision_id = ?',
                (int(decision_id),),
            ).fetchone()
            return {
                'ticket': self._row_to_ticket(updated),
                'created_event_ids': created_ids,
            }

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
            return self._row_to_ticket(updated)

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
