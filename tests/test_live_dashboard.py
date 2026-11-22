"""Chronology, source isolation, failure recovery and local API regressions."""
from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
import sys
import threading

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from live.market import MarketClock, SYMBOLS, quote_record
from live.engine import weekly_prices, recompute, monitor
from live.service import LiveService, make_server, load_research, render_dashboard, json_bytes
from live.efinance_provider import eastmoney_frame
from live.backtest import effective_weights, run_bt_reference


@pytest.fixture
def clock():
    c = MarketClock()
    c.calendars = {}  # deterministic conservative clock, no network or calendar dependency
    return c


@pytest.fixture(scope='module')
def historical_inputs():
    """Existing weekly observations recast as Friday fixtures, never live quotes."""
    from v15.us_snapshot import load_us_prices, load_us_open
    from v14.market_snapshot import load_weekly
    cn = pd.read_csv(ROOT/'market_data'/'verified_5asset_weekly_v13.csv', parse_dates=['week_start']).set_index('week_start')
    us, op, tape = load_us_prices(), load_us_open(), load_weekly()
    daily = {}
    for s, (_, market, key, _) in SYMBOLS.items():
        p = cn if market == 'CN' else us
        opening = op[key] if market == 'US' and key in op else p[key]
        f = pd.DataFrame({'close': p[key].to_numpy(), 'open': opening.to_numpy()},
                         index=p.index + pd.Timedelta(days=4))
        daily[s] = {'raw': f.copy(), 'adjusted': f.copy()}
    daily_tape = {}
    for key in ('market', 'tech', 'star', 'red'):
        daily_tape[key] = pd.DataFrame({'turnover': tape[key+'_turn_100m'].to_numpy()*1e8,
                                     'close': tape.market_close.to_numpy()}, index=tape.index+pd.Timedelta(days=4))
    return daily, daily_tape


def test_daily_and_weekly_not_finalized_before_us_close(clock, historical_inputs):
    daily, _ = historical_inputs
    now = pd.Timestamp('2026-11-20T18:00:00Z')  # Friday 13:00 New York
    p, _, cutoff = weekly_prices(daily, 'US', clock, now)
    assert p.index[-1] == pd.Timestamp('2026-11-09')
    assert cutoff == '2026-11-13'
    cn, _, cutoff_cn = weekly_prices(daily, 'CN', clock, now)
    assert cn.index[-1] == pd.Timestamp('2026-11-16')
    assert cutoff_cn == '2026-11-20'


def test_incomplete_asset_dates_fail_closed(clock, historical_inputs):
    daily, _ = deepcopy(historical_inputs)
    daily['BIL.US']['adjusted'] = daily['BIL.US']['adjusted'].iloc[:-1]
    with pytest.raises(ValueError, match='末日不一致'):
        weekly_prices(daily, 'US', clock, pd.Timestamp('2026-11-22T00:00Z'))


def test_intraday_ignores_unfinished_candle(clock):
    dates = pd.bdate_range(end='2026-11-19', periods=25)
    raw = pd.DataFrame({'open': 100., 'close': 100.}, index=dates)
    times = pd.date_range('2026-11-20 09:30', periods=3, freq='30min', tz='Asia/Shanghai')
    bars = pd.DataFrame({'open': [100., 96., 95.], 'close': [96., 95., 1.]}, index=times)
    now = pd.Timestamp('2026-11-20 10:10', tz='Asia/Shanghai')
    result = monitor('159915.SZ', bars, raw, clock, now)
    assert len(result['observations']) == 1
    assert result['below_bars'] == 1 and result['pending_stage'] == 0
    result = monitor('159915.SZ', bars, raw, clock, pd.Timestamp('2026-11-20 10:30:06', tz='Asia/Shanghai'))
    assert len(result['observations']) == 2 and result['pending_stage'] == 1
    assert result['execution'] == 'observation_only_no_orders_or_assumed_fills'
    assert result == monitor('159915.SZ', bars, raw, clock, pd.Timestamp('2026-11-20 10:30:06', tz='Asia/Shanghai'))


def test_intraday_line_excludes_same_day_daily_close(clock):
    dates = pd.bdate_range(end='2026-11-20', periods=26)
    raw = pd.DataFrame({'open': 100., 'close': 100.}, index=dates)
    raw.loc[dates[-1], 'close'] = 1  # day's eventual close must not leak
    bars = pd.DataFrame({'open': [100.], 'close': [99.]}, index=pd.DatetimeIndex(['2026-11-20 09:30'], tz='Asia/Shanghai'))
    result = monitor('159915.SZ', bars, raw, clock, pd.Timestamp('2026-11-20T08:00Z'))
    assert result['prior_close'] == 100.
    assert result['line'] > 90.


def test_efinance_end_labels_convert_to_start_and_validate_identity():
    raw = pd.DataFrame({'股票代码': ['159915'], '日期': ['2026-11-20 10:00:00'], '开盘': [100], '收盘': [99]})
    f = eastmoney_frame(raw, interval='30m', code='159915')
    assert f.index[0] == pd.Timestamp('2026-11-20 09:30', tz='Asia/Shanghai')
    with pytest.raises(ValueError, match='code mismatch'):
        eastmoney_frame(raw, interval='30m', code='515180')


def test_both_real_engines_recompute_and_keep_research_untouched(clock, historical_inputs):
    daily, tape = historical_inputs
    frozen = load_research()
    before = json_bytes(frozen)
    result = recompute(daily, tape, clock, pd.Timestamp('2026-11-22T00:00Z'), 'fixture_not_live')
    assert set(result['models']) == {'CN', 'US'}, result['strategy_status']
    assert result['models']['CN']['baseline']['target']['red'] == pytest.approx(.44)
    assert result['models']['US']['baseline']['target']['qqq'] == pytest.approx(.3)
    for market in ('CN', 'US'):
        assert result['strategy_status'][market]['state'] == 'unverified'
        for model in result['models'][market].values():
            assert sum(model['target'].values()) == pytest.approx(1, abs=1e-6)
            assert model['cutoff'] == '2026-11-20'
            assert model['history'][-1]['d'] == '2026-11-16'
    assert before == json_bytes(frozen)
    json_bytes(result)  # rejects NaN and NumPy scalar leakage


def test_missing_cn_tape_cannot_disable_us_engine(clock, historical_inputs):
    daily, _ = historical_inputs
    result = recompute(daily, {}, clock, pd.Timestamp('2026-11-22T00:00Z'), 'fixture_not_live')
    assert 'US' in result['models'] and 'CN' not in result['models']
    assert result['strategy_status']['CN']['state'] == 'unavailable'


def test_friday_cn_signal_waits_for_complete_us_input(clock, historical_inputs):
    daily, tape = historical_inputs
    result = recompute(daily, tape, clock, pd.Timestamp('2026-11-20T18:00Z'), 'fixture_not_live')
    assert 'US' in result['models'] and 'CN' not in result['models']
    assert '跨市场完整周' in result['strategy_status']['CN']['message']


def test_future_quote_and_missing_timezone_rejected():
    now = pd.Timestamp('2026-11-22T00:00Z')
    with pytest.raises(ValueError, match='future quote'):
        quote_record('QQQ.US', 100, 99, now+pd.Timedelta(hours=1), 'fixture', now)
    with pytest.raises(ValueError, match='timezone'):
        quote_record('QQQ.US', 100, 99, pd.Timestamp('2026-11-20'), 'fixture', now)


class BrokenProvider:
    name = 'failure fixture'

    def quotes(self, now):
        raise ConnectionError('private upstream address must not appear')

    def daily(self):
        return {}, {'QQQ.US': 'request failed'}

    def tape(self):
        raise ConnectionError('not connected')

    def bars(self):
        return {}, {}


@pytest.fixture
def service(tmp_path, clock):
    (tmp_path/'research_outputs').mkdir()
    (tmp_path/'research_outputs'/'V22_dashboard_snapshot.json').write_bytes((ROOT/'research_outputs'/'V22_dashboard_snapshot.json').read_bytes())
    return LiveService(BrokenProvider(), root=tmp_path, clock=clock)


def test_network_failure_is_visible_and_never_advances_price_timestamp(service):
    stamp = '2026-11-20T21:00:00+00:00'
    service.live['quotes'] = {'QQQ.US': {'market': 'US', 'timestamp': stamp, 'price': 100}}
    assert service.refresh_once(history=True)
    payload = service.payload()
    assert payload['status']['state'] == 'offline'
    assert payload['status']['last_success'] is None
    assert payload['live']['quotes']['QQQ.US']['timestamp'] == stamp
    assert payload['live']['quotes']['QQQ.US']['stale']
    assert 'private upstream' not in json_bytes(payload).decode()


def test_refresh_is_single_flight(service):
    entered, release = threading.Event(), threading.Event()
    def blocked(now):
        entered.set()
        assert release.wait(3)
        return {}, {}
    service.provider.quotes = blocked
    thread = threading.Thread(target=service.refresh_once)
    thread.start()
    try:
        assert entered.wait(3)
        assert service.refresh_once() is False
        assert service.request_refresh() is False
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive()


def test_cache_restores_without_claiming_realtime(service):
    service.cache.parent.mkdir(parents=True)
    service.cache.write_bytes(json_bytes({'schema': 1, 'saved_at': '2026-11-20T21:00Z',
        'live': {'quotes': {'QQQ.US': {'market': 'US', 'timestamp': '2026-11-20T21:00Z', 'price': 100}}}}))
    restored = LiveService(BrokenProvider(), root=service.root, clock=service.clock).payload()
    assert restored['status']['state'] == 'cached'
    assert restored['live']['quotes']['QQQ.US']['stale']


def test_local_api_refresh_and_no_filesystem_exposure(service):
    server = make_server(service, port=0)
    class Connection:
        """In-memory transport runs the actual HTTP parser in restricted runtimes."""
        def __init__(self, request):
            self.input, self.output = BytesIO(request), BytesIO()
        def makefile(self, *args):
            return self.input
        def sendall(self, data):
            self.output.write(data)
        def settimeout(self, value):
            pass
    def request(method, path, headers=''):
        raw = f'{method} {path} HTTP/1.0\r\nHost: 127.0.0.1:{server.server_port}\r\n{headers}\r\n'.encode()
        conn = Connection(raw)
        server.RequestHandlerClass(conn, ('127.0.0.1', 0), server)
        head, body = conn.output.getvalue().split(b'\r\n\r\n', 1)
        return int(head.split()[1]), head, body
    try:
        status, headers, body = request('GET', '/api/dashboard')
        assert status == 200 and json.loads(body)['schema'] == 1
        assert b'Cache-Control: no-store' in headers
        assert request('POST', '/api/refresh')[0] == 202
        assert request('POST', '/api/refresh', 'Origin: https://evil.example\r\n')[0] == 403
        assert request('POST', '/api/refresh', 'Content-Length: 12\r\n')[0] == 400
        assert request('GET', '/../requirements.txt')[0] == 404
    finally:
        server.server_close()


def test_bt_schedule_has_exactly_one_week_delay():
    p = pd.DataFrame({'QQQ': [100, 110, 80], 'BIL': [100, 100, 100]}, index=pd.date_range('2026-01-05', periods=3, freq='W-MON'))
    signal = pd.DataFrame({'target_qqq': [.3, .8, .1], 'target_bil': [.7, .2, .9]}, index=p.index)
    weights = effective_weights(p, signal, 'US')
    assert list(weights.qqq) == [.3, .3, .8]
    altered = signal.copy()
    altered.iloc[-1] = [.99, .01]
    pd.testing.assert_frame_equal(effective_weights(p, altered, 'US'), weights)


def test_bt_reference_against_independent_accounting_if_installed():
    pytest.importorskip('bt')
    idx = pd.date_range('2025-01-06', periods=60, freq='W-MON')
    prices = pd.DataFrame({'QQQ': np.linspace(100, 130, 60), 'BIL': np.linspace(100, 102, 60)}, index=idx)
    signal = pd.DataFrame({'target_qqq': .3, 'target_bil': .7}, index=idx)
    result = run_bt_reference(prices, signal, 'US')
    expected = (prices.pct_change().fillna(0) @ np.array([.3, .7]) + 1).cumprod()
    np.testing.assert_allclose([x['nav'] for x in result['history']], expected, atol=1e-8)


def test_served_html_has_all_live_controls_and_embedded_offline_fallback():
    html = render_dashboard().decode('utf-8')
    assert all(x in html for x in ('id="data-mode"', 'id="live-quotes"', '/api/dashboard', 'id="bt-reference"'))
    assert '/* V21_CODE */' not in html and 'V21_DATA_JSON' not in html
    assert 'https://cdn.' not in html


def test_public_provider_avoids_yfinance_raise_errors_deprecation(monkeypatch):
    import warnings
    import yfinance as yf
    from live.market import PublicProvider

    captured = {}

    class DummyTicker:
        def __init__(self, symbol):
            captured['symbol'] = symbol

        def history(self, **kwargs):
            captured['kwargs'] = kwargs
            return pd.DataFrame(
                {'Open': [100.0, 101.0], 'Close': [101.0, 102.0]},
                index=pd.DatetimeIndex(['2026-11-19', '2026-11-20']),
            )

    monkeypatch.setattr(yf, 'Ticker', DummyTicker)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always')
        frame = PublicProvider.history('QQQ.US', interval='1d', adjusted=False)
    assert len(frame) == 2
    assert 'raise_errors' not in captured['kwargs']
    assert yf.config.debug.hide_exceptions is False
    assert not any(issubclass(w.category, DeprecationWarning) for w in caught)


def test_weekly_prices_tolerates_single_day_halt_and_fails_closed_on_missing_week(clock):
    from live.store import market_symbols

    dates = pd.bdate_range('2025-10-06', '2026-11-20')
    daily = {}
    for idx, sym in enumerate(market_symbols('CN')):
        close = pd.Series(np.linspace(1.0 + idx * 0.2, 1.5 + idx * 0.2, len(dates)), index=dates)
        open_ = close * 0.999
        frame = pd.DataFrame({'open': open_, 'close': close})
        daily[sym] = {'raw': frame.copy(), 'adjusted': frame.copy()}

    # Single-day halt (2026-10-12 Monday missing on 159915.SZ only, rest of week present) succeeds
    daily_one_day = deepcopy(daily)
    daily_one_day['159915.SZ']['adjusted'] = daily_one_day['159915.SZ']['adjusted'].drop(pd.Timestamp('2026-10-12'))
    p, op, cutoff = weekly_prices(daily_one_day, 'CN', clock, pd.Timestamp('2026-11-22T00:00Z'))
    assert cutoff == '2026-11-20'
    assert not p.isna().any().any() and not op.isna().any().any()

    # Full-week gap on one asset must still fail closed
    daily_full_week = deepcopy(daily)
    drop_week = pd.date_range('2026-10-12', '2026-10-16', freq='B')
    daily_full_week['159915.SZ']['adjusted'] = daily_full_week['159915.SZ']['adjusted'].drop(drop_week)
    with pytest.raises(ValueError, match='资产周线缺失'):
        weekly_prices(daily_full_week, 'CN', clock, pd.Timestamp('2026-11-22T00:00Z'))


def test_efinance_provider_tape_uses_quote_history():
    from live.efinance_provider import EfinanceProvider

    calls = []

    class DummyStock:
        def get_quote_history(self, secid, **kwargs):
            calls.append((secid, kwargs))
            code = secid.split('.', 1)[1]
            return pd.DataFrame({
                '股票代码': [code, code],
                '日期': ['2026-11-19', '2026-11-20'],
                '开盘': [1000.0, 1010.0],
                '收盘': [1010.0, 1020.0],
                '成交额': [1e11, 1.1e11],
            })

    class DummyEf:
        stock = DummyStock()

    prov = EfinanceProvider.__new__(EfinanceProvider)
    prov.ef = DummyEf()
    prov._daily_raw = {}
    tape = prov.tape()
    assert set(tape) == {'market', 'tech', 'star', 'red'}
    assert {secid for secid, _ in calls} == {'1.000985', '0.399006', '1.000688', '1.000922'}
    assert all(kw.get('quote_id_mode') is True for _, kw in calls)
