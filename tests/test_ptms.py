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
    create_weekly_decision_ticket,
    execute_decision_ticket,
    sync_account_intraday_stops,
)
from live.market import MarketClock
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


def test_ptms_http_endpoints_and_security(ptms_service):
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

    def call(method, path, body=None, extra_headers=''):
        payload = json.dumps(body).encode('utf-8') if body is not None else b''
        hdr = f'Host: 127.0.0.1:{server.server_port}\r\n'
        if body is not None:
            hdr += f'Content-Type: application/json\r\nContent-Length: {len(payload)}\r\n'
        hdr += extra_headers
        raw = f'{method} {path} HTTP/1.0\r\n{hdr}\r\n'.encode('utf-8') + payload
        conn = Connection(raw)
        server.RequestHandlerClass(conn, ('127.0.0.1', 0), server)
        head, resp_body = conn.output.getvalue().split(b'\r\n\r\n', 1)
        return int(head.split()[1]), head, resp_body

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

        # Init -> Config -> Reconcile -> Create Ticket -> Execute -> Review Note -> Export
        st, _, data = call('GET', '/api/ptms/state')
        assert st == 200 and 'CN_MAIN' in json.loads(data)['accounts']

        st, _, res_init = call(
            'POST',
            '/api/ptms/ledger/init',
            {
                'account_id': 'CN_MAIN',
                'trade_date': '2026-11-20',
                'cash': 100000.0,
                'positions': [],
            },
        )
        assert st == 200 and json.loads(res_init)['state']['accounts']['CN_MAIN']['portfolio']['cash'] == 100000.0

        st, _, _ = call(
            'POST',
            '/api/ptms/account/config',
            {'account_id': 'CN_MAIN', 'strategy_version': 'baseline', 'rebalance_deadband': 0.10},
        )
        assert st == 200

        st, _, ticket_res = call('POST', '/api/ptms/decisions/create', {'account_id': 'CN_MAIN'})
        assert st == 200
        ticket = json.loads(ticket_res)['ticket']
        orders = ticket['snapshot']['orders']
        assert len(orders) > 0

        fills = [
            {'symbol': o['symbol'], 'side': o['side'], 'quantity': o['quantity'], 'price': o['ref_price'], 'fee': o['est_fee']}
            for o in orders
        ]
        st, _, exec_res = call(
            'POST',
            '/api/ptms/decisions/execute',
            {'account_id': 'CN_MAIN', 'decision_id': ticket['decision_id'], 'trade_date': '2026-11-20', 'fills': fills},
        )
        assert st == 200 and json.loads(exec_res)['result']['ticket']['status'] == 'EXECUTED'

        st, _, _ = call(
            'POST',
            '/api/ptms/review/note',
            {'account_id': 'CN_MAIN', 'period_type': 'WEEKLY', 'period_key': '2026-W47', 'reflection': 'HTTP测试复盘'},
        )
        assert st == 200

        st, _, rev_body = call('GET', '/api/ptms/review?account_id=CN_MAIN')
        assert st == 200 and json.loads(rev_body)['notes'][0]['reflection'] == 'HTTP测试复盘'

        st, _, md_body = call('GET', '/api/ptms/export?account_id=CN_MAIN&format=markdown')
        assert st == 200 and 'HTTP测试复盘' in md_body.decode('utf-8')
    finally:
        server.server_close()
