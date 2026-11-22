"""End-to-end tests for Personal Trading Management System (PTMS)."""
from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path
import sys

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from live.decisions import (
    add_trading_sessions,
    build_rebalance_plan,
    compute_execution_window,
    create_weekly_decision_ticket,
    evaluate_account_data_gate,
    execute_decision_ticket,
    sync_account_intraday_stops,
)
from live.importer import commit_broker_csv, preview_broker_csv
from live.market import MarketClock, SYMBOLS, quote_record
from live.notifications import evaluate_and_emit_notifications
from live.review import build_review_report, compute_twr_series, render_review_markdown
from live.service import LiveService, make_server
from live.store import TradingStore


@pytest.fixture
def clock():
    c = MarketClock()
    c.calendars = {}
    return c


@pytest.fixture
def store(tmp_path):
    return TradingStore(tmp_path / 'market_data' / 'personal_trading.db')


class StubProvider:
    name = 'stub'

    def quotes(self, now):
        return {}, {}

    def daily(self):
        return {}, {}

    def tape(self):
        return {}

    def bars(self):
        return {}, {}


@pytest.fixture
def ptms_service(tmp_path, clock):
    (tmp_path / 'research_outputs').mkdir()
    (tmp_path / 'research_outputs' / 'V22_dashboard_snapshot.json').write_bytes(
        (ROOT / 'research_outputs' / 'V22_dashboard_snapshot.json').read_bytes()
    )
    return LiveService(StubProvider(), root=tmp_path, clock=clock)


def test_ledger_cost_t_plus_one_and_reconcile(store):
    store.init_account(
        'CN_MAIN',
        trade_date='2026-11-18',
        cash=50000.0,
        positions=[{'symbol': '515180.SH', 'quantity': 10000, 'price': 1.35}],
    )
    # Today (2026-11-20) buy T+1 159915.SZ and T+0 511880.SH
    store.record_event(
        'CN_MAIN',
        event_type='BUY',
        trade_date='2026-11-20',
        symbol='159915.SZ',
        quantity=5000,
        price=2.20,
        fee=16.5,
        today_local_date='2026-11-20',
    )
    store.record_event(
        'CN_MAIN',
        event_type='BUY',
        trade_date='2026-11-20',
        symbol='511880.SH',
        quantity=100,
        price=100.5,
        fee=1.0,
        today_local_date='2026-11-20',
    )

    pf_today = store.get_portfolio('CN_MAIN', today_local_date='2026-11-20')
    pos_map = {p['symbol']: p for p in pf_today['positions']}
    assert pos_map['159915.SZ']['shares'] == 5000
    assert pos_map['159915.SZ']['sellable_shares'] == 0
    assert pos_map['511880.SH']['sellable_shares'] == 100

    # Selling T+1 shares bought today must be rejected
    with pytest.raises(ValueError, match='T\\+1'):
        store.record_event(
            'CN_MAIN',
            event_type='SELL',
            trade_date='2026-11-20',
            symbol='159915.SZ',
            quantity=1000,
            price=2.22,
            today_local_date='2026-11-20',
        )

    # T+0 cash ETF bought today CAN be sold today
    store.record_event(
        'CN_MAIN',
        event_type='SELL',
        trade_date='2026-11-20',
        symbol='511880.SH',
        quantity=100,
        price=100.52,
        fee=1.0,
        today_local_date='2026-11-20',
    )

    # Cross-market symbol rejected
    with pytest.raises(ValueError, match='跨币种|跨市场'):
        store.record_event(
            'CN_MAIN',
            event_type='BUY',
            trade_date='2026-11-20',
            symbol='QQQ.US',
            quantity=10,
            price=600.0,
            today_local_date='2026-11-20',
        )

    # Next trading day unlocks T+1 shares; sell half and check realized PnL
    store.record_event(
        'CN_MAIN',
        event_type='SELL',
        trade_date='2026-11-23',
        symbol='159915.SZ',
        quantity=2500,
        price=2.40,
        fee=9.0,
        today_local_date='2026-11-23',
    )
    pf_next = store.get_portfolio(
        'CN_MAIN',
        prices={'159915.SZ': {'price': 2.50, 'source': 'test'}},
        today_local_date='2026-11-23',
    )
    pos_next = {p['symbol']: p for p in pf_next['positions']}
    assert pos_next['159915.SZ']['shares'] == 2500
    assert pos_next['159915.SZ']['sellable_shares'] == 2500
    assert pf_next['realized_pnl'] > 450.0
    assert pf_next['unrealized_pnl'] > 700.0

    # Reconcile to exact broker statement
    rec = store.reconcile_account(
        'CN_MAIN',
        trade_date='2026-11-23',
        target_cash=42000.0,
        target_shares={'159915.SZ': 2600, '515180.SH': 10000},
        today_local_date='2026-11-23',
    )
    rec_pos = {p['symbol']: p for p in rec['portfolio']['positions']}
    assert rec['portfolio']['cash'] == pytest.approx(42000.0)
    assert rec_pos['159915.SZ']['shares'] == 2600


def test_persistent_intraday_stop_and_cooldown_across_restarts(tmp_path, clock):
    db_file = tmp_path / 'market_data' / 'personal_trading.db'
    store1 = TradingStore(db_file)
    store1.init_account(
        'CN_MAIN',
        trade_date='2026-11-18',
        cash=20000.0,
        positions=[
            {'symbol': '515180.SH', 'quantity': 40000, 'price': 1.40},  # ~56% > 20% core floor
            {'symbol': '511880.SH', 'quantity': 200, 'price': 100.0},
        ],
    )
    acc = store1.get_account('CN_MAIN')
    quotes = {
        '515180.SH': {'price': 1.40, 'source': 'test'},
        '511880.SH': {'price': 100.0, 'source': 'test'},
    }
    pf = store1.get_portfolio('CN_MAIN', quotes, today_local_date='2026-11-19')

    # Day 1 daily context: strong uptrend -> high stop line
    d1 = pd.bdate_range(end='2026-11-18', periods=25)
    raw1 = pd.DataFrame({'open': [1.20 + i * 0.01 for i in range(25)], 'close': [1.20 + i * 0.01 for i in range(25)]}, index=d1)
    states1 = sync_account_intraday_stops(
        store1, acc, pf, {}, {'515180.SH': raw1}, quotes, clock, pd.Timestamp('2026-11-19 10:00', tz='Asia/Shanghai')
    )
    high_line = states1['515180.SH']['active_line']
    assert high_line > 1.38

    # Simulate service restart with a brand new TradingStore instance and LOWER daily context on Day 2
    store2 = TradingStore(db_file)
    d2 = pd.bdate_range(end='2026-11-19', periods=25)
    raw2 = pd.DataFrame({'open': [1.05] * 25, 'close': [1.05] * 25}, index=d2)
    # 2 closed 30m bars below persistent active_line on 2026-11-20
    bar_idx = pd.DatetimeIndex(
        [pd.Timestamp('2026-11-20 09:30', tz='Asia/Shanghai'), pd.Timestamp('2026-11-20 10:00', tz='Asia/Shanghai')]
    )
    bars = {'515180.SH': pd.DataFrame({'open': [1.36, 1.35], 'close': [1.35, 1.34]}, index=bar_idx)}
    pf2 = store2.get_portfolio('CN_MAIN', quotes, today_local_date='2026-11-20')
    states2 = sync_account_intraday_stops(
        store2, acc, pf2, bars, {'515180.SH': raw2}, quotes, clock, pd.Timestamp('2026-11-20 10:30:10', tz='Asia/Shanghai')
    )
    # Line stayed monotonic across restart despite lower raw2!
    assert states2['515180.SH']['active_line'] == pytest.approx(high_line)
    assert states2['515180.SH']['pending_stage'] == 1

    # Idempotent second sync does not duplicate INTRADAY_STOP ticket
    sync_account_intraday_stops(
        store2, acc, pf2, bars, {'515180.SH': raw2}, quotes, clock, pd.Timestamp('2026-11-20 10:31:00', tz='Asia/Shanghai')
    )
    pending = store2.list_tickets('CN_MAIN', status='PENDING')
    assert len(pending) == 1
    ticket = pending[0]
    assert ticket['source'] == 'INTRADAY_STOP'
    sell_order = ticket['snapshot']['orders'][0]
    assert sell_order['side'] == 'SELL' and sell_order['quantity'] > 0 and sell_order['quantity'] % 100 == 0

    # Execute stop ticket -> triggers 3-session cooldown
    res = execute_decision_ticket(
        store2,
        'CN_MAIN',
        ticket['decision_id'],
        trade_date='2026-11-20',
        fills=[{'symbol': '515180.SH', 'side': 'SELL', 'quantity': sell_order['quantity'], 'price': 1.34, 'fee': 20.0}],
        discipline_tag='RISK_CUT',
        clock=clock,
    )
    expected_cd = add_trading_sessions('2026-11-20', 3, 'CN', clock)
    assert res['stop_states']['515180.SH']['cooldown_until_date'] == expected_cd
    assert res['stop_states']['515180.SH']['stage'] == 1


def test_weekly_intraday_arbitration_clipping_and_lot_conservation(store, clock):
    store.init_account(
        'CN_MAIN',
        trade_date='2026-11-18',
        cash=50000.0,
        positions=[{'symbol': '515180.SH', 'quantity': 20000, 'price': 1.40}],
    )
    # Put 159915.SZ into active cooldown and 515180.SH normal
    store.save_stop_state(
        'CN_MAIN',
        '159915.SZ',
        {'cooldown_until_date': '2026-11-25', 'stage': 1, 'active_line': 2.20},
    )
    acc = store.get_account('CN_MAIN')
    quotes = {
        '159915.SZ': {'price': 2.25, 'source': 'test'},
        '515180.SH': {'price': 1.40, 'source': 'test'},
        '511260.SH': {'price': 135.0, 'source': 'test'},
        '518880.SH': {'price': 7.20, 'source': 'test'},
        '511880.SH': {'price': 100.5, 'source': 'test'},
    }
    pf = store.get_portfolio('CN_MAIN', quotes, today_local_date='2026-11-23')
    stop_states = {
        '159915.SZ': {'cooldown_active': True, 'cooldown_until_date': '2026-11-25', 'pending_stage': 0},
        '515180.SH': {'cooldown_active': False, 'pending_stage': 0},
    }
    # Weekly model wants to buy 40% tech + 35% red + 25% money, but tech is in cooldown!
    weekly_model = {
        'label': 'V18',
        'cutoff': '2026-11-20',
        'signalWeek': '2026-11-16',
        'target': {'tech': 0.40, 'red': 0.35, 'bond': 0.0, 'gold': 0.0, 'money': 0.25},
    }
    plan = build_rebalance_plan(acc, pf, weekly_model, stop_states, '2026-11-23', [])
    eff = {w['bucket']: w['effective_target'] for w in plan['weights_comparison']}
    assert eff['tech'] == pytest.approx(0.0)
    assert eff['money'] == pytest.approx(0.65)
    assert len(plan['cooldown_clips']) == 1
    assert all(o['symbol'] != '159915.SZ' for o in plan['orders'])
    assert all(o['quantity'] % 100 == 0 for o in plan['orders'])
    assert plan['projected_cash_after'] >= 0.0

    # Priority exit bypasses deadband when tech is held and weekly drops tech to 0
    store.record_event(
        'CN_MAIN',
        event_type='BUY',
        trade_date='2026-11-21',
        symbol='159915.SZ',
        quantity=1000,
        price=2.25,
        fee=3.0,
        today_local_date='2026-11-23',
    )
    pf_exit = store.get_portfolio('CN_MAIN', quotes, today_local_date='2026-11-23')
    weekly_exit = {
        'label': 'V18',
        'cutoff': '2026-11-20',
        'signalWeek': '2026-11-16',
        'target': {'tech': 0.0, 'red': 0.36, 'bond': 0.0, 'gold': 0.0, 'money': 0.64},
    }
    # turnover ~ 0.06 < deadband 0.15, yet tech dropped to 0 -> ACTION_PRIORITY_EXIT!
    plan_exit = build_rebalance_plan(acc, pf_exit, weekly_exit, {'159915.SZ': {'cooldown_active': False}}, '2026-11-23', [])
    assert plan_exit['verdict'] == 'ACTION_PRIORITY_EXIT'
    assert plan_exit['should_execute'] is True


def test_review_twr_immune_to_large_cash_inflows_and_slippage(store):
    store.init_account(
        'US_MAIN',
        trade_date='2026-11-16',
        cash=0.0,
        positions=[{'symbol': 'QQQ.US', 'quantity': 1000, 'price': 100.0}],
    )
    # On 2026-11-18 price rises 10% to 110 AND user deposits 1,000,000 USD cash!
    store.record_event('US_MAIN', event_type='CASH_IN', trade_date='2026-11-18', amount=1_000_000.0)
    acc = store.get_account('US_MAIN')
    events = store._fetch_events_for_test = list(reversed(store.list_events('US_MAIN', limit=100)))
    idx = pd.DatetimeIndex(['2026-11-16', '2026-11-17', '2026-11-18'])
    price_df = pd.DataFrame({'QQQ.US': [100.0, 105.0, 110.0], 'BIL.US': [91.5, 91.5, 91.5]}, index=idx)
    twr = compute_twr_series(acc, events, price_df, {'QQQ.US': {'price': 110.0}, 'BIL.US': {'price': 91.5}}, '2026-11-18')
    assert twr[-1]['cum_ret_pct'] == pytest.approx(10.0, abs=1e-3)

    # Create and execute a ticket with known reference price to verify slippage bp & review markdown
    quotes = {'QQQ.US': {'price': 110.0, 'source': 'test'}, 'BIL.US': {'price': 91.5, 'source': 'test'}}
    pf = store.get_portfolio('US_MAIN', quotes, today_local_date='2026-11-18')
    plan = build_rebalance_plan(
        acc,
        pf,
        {'label': 'V18', 'cutoff': '2026-11-16', 'signalWeek': '2026-11-16', 'target': {'qqq': 0.30, 'bil': 0.70}},
        {},
        '2026-11-18',
        [],
    )
    t = create_weekly_decision_ticket(store, plan)
    execute_decision_ticket(
        store,
        'US_MAIN',
        t['decision_id'],
        trade_date='2026-11-18',
        fills=[{'symbol': 'BIL.US', 'side': 'BUY', 'quantity': 1000, 'price': 91.683, 'fee': 5.0}],
        discipline_tag='FOLLOW',
        review_note='轻微不利滑点20bp',
    )
    store.upsert_review_note(
        'US_MAIN',
        period_type='WEEKLY',
        period_key='2026-W47',
        summary={'twr': 10.0},
        reflection='大额入金未失真 TWR，严格执行减仓至30%核心底仓。',
    )
    pf_after = store.get_portfolio('US_MAIN', quotes, today_local_date='2026-11-18')
    report = build_review_report(
        store,
        acc,
        pf_after,
        [{'d': '2026-11-16', 'nav': 2.0}, {'d': '2026-11-23', 'nav': 2.02}],
        {},
        quotes,
        '2026-11-18',
    )
    assert report['discipline']['avg_slippage_bp'] == pytest.approx(20.0, abs=0.2)
    md = render_review_markdown(report)
    assert '个人交易复盘报告 · US_MAIN' in md
    assert '2026-W47' in md


def test_ptms_http_endpoints_and_security(ptms_service, monkeypatch):
    import numpy as np
    from strategy_core.us_snapshot import load_us_open, load_us_prices

    server = make_server(ptms_service, port=0)

    class Connection:
        def __init__(self, request_bytes):
            self.input, self.output = BytesIO(request_bytes), BytesIO()

        def makefile(self, *args):
            return self.input

        def sendall(self, data):
            self.output.write(data)

        def settimeout(self, value):
            pass

    def make_caller(srv):
        def call(method, path, body=None, extra_headers=''):
            payload = json.dumps(body).encode('utf-8') if body is not None else b''
            hdr = f'Host: 127.0.0.1:{srv.server_port}\r\n'
            if body is not None:
                hdr += f'Content-Type: application/json\r\nContent-Length: {len(payload)}\r\n'
            hdr += extra_headers
            raw = f'{method} {path} HTTP/1.0\r\n{hdr}\r\n'.encode('utf-8') + payload
            conn = Connection(raw)
            srv.RequestHandlerClass(conn, ('127.0.0.1', 0), srv)
            head, resp_body = conn.output.getvalue().split(b'\r\n\r\n', 1)
            return int(head.split()[1]), head, resp_body
        return call

    call = make_caller(server)
    try:
        # Static PTMS assets work; traversal blocked
        st, _, css = call('GET', '/assets/ptms.css')
        assert st == 200 and b'.ptms-verdict' in css
        st, _, js = call('GET', '/assets/ptms.js')
        assert st == 200 and b'__ptmsRender' in js
        assert call('GET', '/assets/../live/store.py')[0] == 404

        # Cross-origin blocked on PTMS POST
        assert call('POST', '/api/ptms/ledger/init', {'account_id': 'CN_MAIN'}, 'Origin: https://evil.example\r\n')[0] == 403
        # /api/refresh remains strict about rejecting body
        assert call('POST', '/api/refresh', {'x': 1})[0] == 400

        # Offline StubProvider -> BLOCKED gate, notifications emitted, plan create blocked, manual ledger still allowed
        st, _, data = call('GET', '/api/ptms/state')
        state_obj = json.loads(data)
        assert st == 200 and 'CN_MAIN' in state_obj['accounts']
        assert state_obj['accounts']['CN_MAIN']['plan']['data_gate']['status'] == 'BLOCKED'
        assert state_obj['accounts']['CN_MAIN']['plan']['can_execute_plan'] is False
        assert state_obj['unread_notifications'] >= 2

        st, _, res_init = call(
            'POST',
            '/api/ptms/ledger/init',
            {
                'account_id': 'CN_MAIN',
                'account_mode': 'REAL',
                'trade_date': '2026-11-20',
                'cash': 100000.0,
                'positions': [],
            },
        )
        assert st == 200 and json.loads(res_init)['state']['accounts']['CN_MAIN']['portfolio']['cash'] == 100000.0

        # Plan creation is blocked when data_gate is BLOCKED
        assert call('POST', '/api/ptms/decisions/create', {'account_id': 'CN_MAIN'})[0] == 400
        # Manual event entry of already-occurred trade remains unblocked
        st, _, ev_res = call(
            'POST',
            '/api/ptms/ledger/event',
            {
                'account_id': 'CN_MAIN',
                'event_type': 'CASH_IN',
                'trade_date': '2026-11-20',
                'amount': 5000.0,
                'note': '补录银证转入',
                'client_request_id': 'manual-cash-1',
            },
        )
        assert st == 200 and json.loads(ev_res)['state']['accounts']['CN_MAIN']['portfolio']['cash'] == 105000.0
    finally:
        server.server_close()

    # Phase 2: Ready US provider while CN fails -> US READY, CN BLOCKED, full HTTP ticket/partial/reverse/backup/import lifecycle
    fixed_now = pd.Timestamp('2026-11-23T15:00:00Z')  # Monday 10:00 ET / 23:00 CST

    class VerifiedSessions:
        def __init__(self, tz_name):
            self.tz_name = tz_name

        def is_session(self, d):
            return pd.Timestamp(d).dayofweek < 5

        def is_open_on_minute(self, ts):
            local = pd.Timestamp(ts).tz_convert(self.tz_name)
            return local.dayofweek < 5 and (9, 30) <= (local.hour, local.minute) <= (15, 0)

        def sessions_in_range(self, start, end):
            return pd.bdate_range(pd.Timestamp(start), pd.Timestamp(end))

        def session_open(self, d):
            return pd.Timestamp(f'{pd.Timestamp(d).date()} 09:30:00', tz=self.tz_name).tz_convert('UTC')

        def session_close(self, d):
            return pd.Timestamp(f'{pd.Timestamp(d).date()} 15:00:00', tz=self.tz_name).tz_convert('UTC')

    class UsOnlyProvider:
        name = 'us_only_live'

        def quotes(self, now):
            return {
                'QQQ.US': quote_record('QQQ.US', 500.0, 498.0, now - pd.Timedelta(seconds=15), 'quote', now),
                'BIL.US': quote_record('BIL.US', 91.5, 91.5, now - pd.Timedelta(seconds=15), 'quote', now),
            }, {'515180.SH': 'cn offline'}

        def daily(self):
            us, op = load_us_prices(), load_us_open()
            out = {}
            for s, (_, market, key, _) in SYMBOLS.items():
                if market == 'US':
                    f = pd.DataFrame({'close': us[key].to_numpy(), 'open': (op[key] if key in op else us[key]).to_numpy()}, index=us.index + pd.Timedelta(days=4))
                    out[s] = {'raw': f.copy(), 'adjusted': f.copy()}
            return out, {'515180.SH': 'cn offline'}

        def tape(self):
            return {}

        def bars(self):
            return {}, {}

    monkeypatch.setattr('live.service.utc_now', lambda: fixed_now)
    clk = MarketClock()
    clk.calendars = {'US': VerifiedSessions('America/New_York'), 'CN': VerifiedSessions('Asia/Shanghai')}
    srv2_root = ptms_service.root / 'srv2'
    (srv2_root / 'research_outputs').mkdir(parents=True)
    (srv2_root / 'research_outputs' / 'V22_dashboard_snapshot.json').write_bytes(
        (ROOT / 'research_outputs' / 'V22_dashboard_snapshot.json').read_bytes()
    )
    svc2 = LiveService(UsOnlyProvider(), root=srv2_root, clock=clk)
    assert svc2.refresh_once(history=True)
    srv2 = make_server(svc2, port=0)
    call2 = make_caller(srv2)
    try:
        st, _, st_bytes = call2('GET', '/api/ptms/state')
        st2 = json.loads(st_bytes)
        assert st == 200
        assert st2['accounts']['CN_MAIN']['plan']['data_gate']['status'] == 'BLOCKED'
        assert st2['accounts']['US_MAIN']['plan']['data_gate']['status'] == 'READY'

        assert call2('POST', '/api/ptms/ledger/init', {
            'account_id': 'US_MAIN', 'account_mode': 'REAL', 'trade_date': '2026-11-23', 'cash': 100000.0, 'positions': [],
        })[0] == 200
        assert call2('POST', '/api/ptms/account/config', {
            'account_id': 'US_MAIN', 'strategy_version': 'baseline', 'rebalance_deadband': 0.05,
        })[0] == 200

        st, _, ticket_res = call2('POST', '/api/ptms/decisions/create', {'account_id': 'US_MAIN'})
        assert st == 200
        ticket = json.loads(ticket_res)['ticket']
        orders = ticket['snapshot']['orders']
        assert len(orders) > 0

        # Partial fill first leg (half qty), verify PARTIAL & idempotency
        o0 = orders[0]
        half_qty = round(o0['quantity'] / 2, 2)
        st, _, exec_res = call2('POST', '/api/ptms/decisions/execute', {
            'account_id': 'US_MAIN',
            'decision_id': ticket['decision_id'],
            'trade_date': '2026-11-23',
            'fills': [{'symbol': o0['symbol'], 'side': o0['side'], 'quantity': half_qty, 'price': o0['ref_price'], 'fee': 1.0}],
            'client_request_id': 'http-fill-1',
        })
        assert st == 200
        exec_obj = json.loads(exec_res)['result']
        assert exec_obj['ticket']['status'] == 'PARTIAL'
        assert exec_obj['ticket']['progress']['remaining_shares'] > 0

        # Idempotent retry
        st, _, dup_res = call2('POST', '/api/ptms/decisions/execute', {
            'account_id': 'US_MAIN',
            'decision_id': ticket['decision_id'],
            'trade_date': '2026-11-23',
            'fills': [{'symbol': o0['symbol'], 'side': o0['side'], 'quantity': half_qty, 'price': o0['ref_price'], 'fee': 1.0}],
            'client_request_id': 'http-fill-1',
        })
        assert st == 200 and json.loads(dup_res)['result']['duplicate'] is True

        # Cancel remainder
        st, _, cancel_res = call2('POST', '/api/ptms/decisions/cancel-remainder', {
            'account_id': 'US_MAIN',
            'decision_id': ticket['decision_id'],
            'review_note': '余量不再追价',
        })
        assert st == 200 and json.loads(cancel_res)['ticket']['status'] == 'EXPIRED'

        # CSV import preview & commit
        csv_text = 'date,symbol,side,quantity,price,fee,trade_id\n2026-11-23,BIL,BUY,10,91.50,0.5,US-CSV-1\n'
        st, _, pv_res = call2('POST', '/api/ptms/import/preview', {'account_id': 'US_MAIN', 'csv_text': csv_text})
        assert st == 200 and json.loads(pv_res)['preview']['counts']['new'] == 1
        st, _, cm_res = call2('POST', '/api/ptms/import/commit', {'account_id': 'US_MAIN', 'csv_text': csv_text})
        assert st == 200 and json.loads(cm_res)['result']['imported_count'] == 1

        # Reverse last event & note review & backup/restore & read notifications
        ev_id = json.loads(cm_res)['result']['created_event_ids'][0]
        assert call2('POST', '/api/ptms/ledger/reverse', {'account_id': 'US_MAIN', 'event_id': ev_id, 'reason': '测试冲正'})[0] == 200
        assert call2('POST', '/api/ptms/review/note', {
            'account_id': 'US_MAIN', 'period_type': 'WEEKLY', 'period_key': '2026-W47', 'reflection': 'HTTP测试复盘',
        })[0] == 200
        st, _, rev_body = call2('GET', '/api/ptms/review?account_id=US_MAIN')
        rev_obj = json.loads(rev_body)
        assert st == 200 and rev_obj['notes'][0]['reflection'] == 'HTTP测试复盘'
        assert 'plan_completion_rate_pct' in rev_obj['discipline']

        st, _, backup_body = call2('GET', '/api/ptms/backup')
        backup_obj = json.loads(backup_body)
        assert st == 200 and backup_obj['schema_version'] == 2
        assert call2('POST', '/api/ptms/restore/preview', {'backup': backup_obj})[0] == 200
        assert call2('POST', '/api/ptms/restore', {'backup': backup_obj})[0] == 200

        st, _, md_body = call2('GET', '/api/ptms/export?account_id=US_MAIN&format=markdown')
        assert st == 200 and 'HTTP测试复盘' in md_body.decode('utf-8')
        st, _, notifs_body = call2('GET', '/api/ptms/notifications')
        assert st == 200 and len(json.loads(notifs_body)['notifications']) >= 1
        assert call2('POST', '/api/ptms/notifications/read', {})[0] == 200
    finally:
        srv2.server_close()


def test_partial_fill_idempotency_reversal_and_separated_review_metrics(store):
    store.init_account('CN_MAIN', trade_date='2026-11-20', cash=100000.0, positions=[])
    acc = store.get_account('CN_MAIN')
    quotes = {
        '159915.SZ': {'price': 2.23, 'source': 'quote'},
        '515180.SH': {'price': 1.35, 'source': 'quote'},
        '511260.SH': {'price': 137.0, 'source': 'quote'},
        '518880.SH': {'price': 7.28, 'source': 'quote'},
        '511880.SH': {'price': 100.9, 'source': 'quote'},
    }
    pf = store.get_portfolio('CN_MAIN', quotes, today_local_date='2026-11-23')
    plan = build_rebalance_plan(
        acc, pf,
        {'label': 'V18', 'cutoff': '2026-11-20', 'signalWeek': '2026-11-16', 'target': {'tech': 0.0, 'red': 0.44, 'bond': 0.0, 'gold': 0.0, 'money': 0.56}},
        {}, '2026-11-23', [],
    )
    # Build explicit 32,100-share single-leg snapshot reproducing the audit scenario
    plan['orders'] = [{
        'symbol': '515180.SH', 'name': '红利ETF', 'bucket': 'red', 'side': 'BUY',
        'quantity': 32100.0, 'lot_size': 100, 'sellable_shares': 0.0, 'ref_price': 1.35,
        'est_amount': 43335.0, 'est_fee': 4.33, 't_plus': 1, 'warning': '', 'reason': '建仓红利',
    }]
    ticket = create_weekly_decision_ticket(store, plan)
    did = ticket['decision_id']

    # Fill 100 / 32,100 shares
    res1 = execute_decision_ticket(
        store, 'CN_MAIN', did, trade_date='2026-11-23',
        fills=[{'symbol': '515180.SH', 'side': 'BUY', 'quantity': 100.0, 'price': 1.35, 'fee': 1.0}],
        discipline_tag='FOLLOW', client_request_id='req-100',
    )
    assert res1['duplicate'] is False
    assert res1['ticket']['status'] == 'PARTIAL'
    assert res1['ticket']['progress']['filled_shares'] == pytest.approx(100.0)
    assert res1['ticket']['progress']['remaining_shares'] == pytest.approx(32000.0)
    assert res1['ticket']['progress']['completion_pct'] == pytest.approx(100 / 32100 * 100, abs=0.05)

    # Duplicate client_request_id must not double-book
    res_dup = execute_decision_ticket(
        store, 'CN_MAIN', did, trade_date='2026-11-23',
        fills=[{'symbol': '515180.SH', 'side': 'BUY', 'quantity': 100.0, 'price': 1.35, 'fee': 1.0}],
        discipline_tag='FOLLOW', client_request_id='req-100',
    )
    assert res_dup['duplicate'] is True
    assert store.get_portfolio('CN_MAIN', quotes)['positions'][1]['shares'] == pytest.approx(100.0)

    # Review separates plan completion rate (~0.3%) from trade discipline rate (100%)
    rep = build_review_report(store, acc, store.get_portfolio('CN_MAIN', quotes), [], {}, quotes, '2026-11-23')
    assert rep['discipline']['plan_completion_rate_pct'] == pytest.approx(0.3, abs=0.1)
    assert rep['discipline']['trade_discipline_rate_pct'] == pytest.approx(100.0)
    assert rep['discipline']['partial_tickets'] == 1

    # Reverse the 100-share buy -> ticket returns to PENDING with 32,100 remaining & portfolio cash restored
    buy_ev_id = res1['created_event_ids'][0]
    store.reverse_event('CN_MAIN', buy_ev_id, reason='填错委托', trade_date='2026-11-23')
    assert store.get_portfolio('CN_MAIN', quotes)['cash'] == pytest.approx(100000.0)
    t_after_rev = store.get_ticket(did)
    assert t_after_rev['status'] == 'PENDING'
    assert t_after_rev['progress']['remaining_shares'] == pytest.approx(32100.0)

    # Re-fill 100 shares then cancel remainder -> EXPIRED
    execute_decision_ticket(
        store, 'CN_MAIN', did, trade_date='2026-11-23',
        fills=[{'symbol': '515180.SH', 'side': 'BUY', 'quantity': 100.0, 'price': 1.35, 'fee': 1.0}],
        discipline_tag='FOLLOW', client_request_id='req-100-v2',
    )
    cancelled = store.cancel_ticket_remainder('CN_MAIN', did, review_note='放弃余量')
    assert cancelled['status'] == 'EXPIRED'


def test_per_account_data_gate_and_execution_window(store):
    class FakeCal:
        def sessions_in_range(self, start, end):
            return pd.bdate_range(pd.Timestamp(start), pd.Timestamp(end))

    clk = MarketClock()
    clk.calendars = {'CN': FakeCal(), 'US': FakeCal()}
    acc_cn = store.get_account('CN_MAIN')
    cn_quotes = {s: {'price': 1.0, 'source': 'quote', 'stale': False, 'refresh_failed': False} for s in SYMBOLS if SYMBOLS[s][1] == 'CN'}
    model = {'origin': 'online_recomputed_simulation', 'cutoff': '2026-11-20', 'target': {'tech': 0.2, 'red': 0.8}}
    st_ok = {'state': 'current', 'cutoff': '2026-11-20'}

    # Pre-market Monday 2026-11-23 09:00 CST (01:00 UTC) -> WAIT_WINDOW
    gate_pre = evaluate_account_data_gate(
        acc_cn, model, st_ok, cn_quotes, clk, pd.Timestamp('2026-11-23T01:00:00Z'),
        market_status_entry={'state': 'pre_market', 'label': '盘前'},
    )
    assert gate_pre['status'] == 'WAIT_WINDOW' and gate_pre['can_execute_plan'] is False
    assert gate_pre['earliest_execution_date'] == '2026-11-23'

    # Open Monday 2026-11-23 09:35 CST (01:35 UTC) -> READY
    gate_open = evaluate_account_data_gate(
        acc_cn, model, st_ok, cn_quotes, clk, pd.Timestamp('2026-11-23T01:35:00Z'),
        market_status_entry={'state': 'open', 'label': '交易中'},
    )
    assert gate_open['status'] == 'READY' and gate_open['can_execute_plan'] is True

    # Stale quote while open -> BLOCKED
    stale_q = dict(cn_quotes)
    stale_q['159915.SZ'] = {'price': 2.2, 'source': 'quote', 'stale': True, 'refresh_failed': False}
    gate_stale = evaluate_account_data_gate(
        acc_cn, model, st_ok, stale_q, clk, pd.Timestamp('2026-11-23T02:00:00Z'),
        market_status_entry={'state': 'open', 'label': '交易中'},
    )
    assert gate_stale['status'] == 'BLOCKED' and gate_stale['can_execute_plan'] is False

    # Research fallback origin or unverified calendar -> BLOCKED
    gate_res = evaluate_account_data_gate(
        acc_cn, {'origin': 'frozen_research_snapshot', 'cutoff': '2026-11-20'}, st_ok, cn_quotes, clk,
        pd.Timestamp('2026-11-23T02:00:00Z'), market_status_entry={'state': 'open', 'label': '交易中'},
    )
    assert gate_res['status'] == 'BLOCKED'

    win = compute_execution_window('US', '2026-11-20', clk, pd.Timestamp('2026-11-23T14:00:00Z'))  # 09:00 ET
    assert win['window_open'] is False and win['earliest_execution_date'] == '2026-11-23'


def test_full_backup_restore_roundtrip_and_tamper_rejection(store, tmp_path):
    store.init_account('CN_MAIN', trade_date='2026-11-18', cash=200000.0, positions=[], account_mode='DEMO')
    for i in range(1005):
        store.record_event(
            'CN_MAIN', event_type='DIVIDEND', trade_date='2026-11-19', amount=1.0,
            note=f'bulk-{i}', client_request_id=f'bulk-{i}',
        )
    assert len(store.list_events('CN_MAIN', limit=None)) == 1006
    store.save_stop_state('CN_MAIN', '515180.SH', {'active_line': 1.33, 'stage': 1})
    store.upsert_review_note('CN_MAIN', period_type='WEEKLY', period_key='2026-W47', summary={'x': 1}, reflection='完整备份检查')
    store.notify_once('k1', account_id='CN_MAIN', category='WEEKLY_SIGNAL', severity='action', title='t', message='m')

    backup = store.export_full_backup()
    assert backup['counts']['ledger_events'] == 1006

    # Reject tampered checksum
    bad = json.loads(json.dumps(backup))
    bad['data']['accounts'][0]['strategy_version'] = 'advisory'
    with pytest.raises(ValueError, match='校验和'):
        store.preview_full_backup(bad)

    # Restore into fresh empty DB
    fresh = TradingStore(tmp_path / 'restored.db')
    fresh.restore_full_backup(backup)
    assert fresh.get_account('CN_MAIN')['account_mode'] == 'DEMO'
    assert fresh.get_portfolio('CN_MAIN')['cash'] == pytest.approx(201005.0)
    assert len(fresh.list_events('CN_MAIN', limit=None)) == 1006
    assert fresh.get_stop_states('CN_MAIN')['515180.SH']['stage'] == 1
    assert fresh.list_review_notes('CN_MAIN')[0]['reflection'] == '完整备份检查'
    assert len(fresh.list_notifications()) == 1


def test_broker_csv_import_gb18030_dedup_and_out_of_pool(store):
    store.init_account('CN_MAIN', trade_date='2026-11-20', cash=50000.0, positions=[])
    csv_raw = (
        '成交日期,证券代码,买卖标志,成交数量,成交均价,手续费,成交编号\n'
        '2026-11-21,515180,证券买入,2000,1.35,2.7,CN-1001\n'
        '2026-11-21,600519,证券买入,100,1500,5.0,CN-OUT-1\n'
    ).encode('gb18030')

    pv = preview_broker_csv(store, 'CN_MAIN', csv_raw)
    assert pv['counts'] == {'new': 1, 'duplicate': 0, 'ignored': 1, 'error': 0}
    assert pv['cash_delta'] == pytest.approx(-(2000 * 1.35 + 2.7))

    res = commit_broker_csv(store, 'CN_MAIN', csv_raw)
    assert res['imported_count'] == 1 and res['ignored_count'] == 1

    pv2 = preview_broker_csv(store, 'CN_MAIN', csv_raw)
    assert pv2['counts']['new'] == 0 and pv2['counts']['duplicate'] == 1
    res2 = commit_broker_csv(store, 'CN_MAIN', csv_raw)
    assert res2['imported_count'] == 0 and res2['skipped_duplicates'] == 1


def test_notifications_dedup_once(store):
    state = {
        'accounts': {
            'US_MAIN': {
                'account': {'account_id': 'US_MAIN', 'market': 'US'},
                'plan': {
                    'data_gate': {'status': 'BLOCKED', 'reasons': ['缺少实时报价']},
                    'verdict': 'DATA_BLOCKED',
                    'verdict_label': '拦截',
                    'verdict_reason': '缺少报价',
                    'signal_cutoff': '2026-11-20',
                    'signal_sha256': 'abc123',
                    'orders': [],
                },
                'tickets': [],
            }
        }
    }
    first = evaluate_and_emit_notifications(store, state)
    second = evaluate_and_emit_notifications(store, state)
    assert len(first) == 1 and len(second) == 0

    state['accounts']['US_MAIN']['plan'].update({
        'data_gate': {'status': 'READY', 'can_execute_plan': True, 'reasons': []},
        'verdict': 'ACTION_REBALANCE',
        'verdict_label': '建议按周频调仓',
        'orders': [{'symbol': 'QQQ.US'}],
    })
    third = evaluate_and_emit_notifications(store, state)
    assert {n['category'] for n in third} == {'FEED_STATUS', 'WEEKLY_SIGNAL'}
    assert len(store.list_notifications(unread_only=True)) == 3
    store.mark_notifications_read()
    assert len(store.list_notifications(unread_only=True)) == 0
