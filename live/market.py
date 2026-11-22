"""Market adapters. Raw quotes/stops and adjusted weekly prices never mix."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import importlib.util
import os
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd

SYMBOLS = {
    '159915.SZ': ('159915.SZ', 'CN', 'tech', '创业板ETF'),
    '515180.SH': ('515180.SS', 'CN', 'red', '红利ETF'),
    '511260.SH': ('511260.SS', 'CN', 'bond_511260_adj', '10年国债ETF'),
    '518880.SH': ('518880.SS', 'CN', 'gold_518880_adj', '黄金ETF'),
    '511880.SH': ('511880.SS', 'CN', 'cash_511880_adj', '货币ETF'),
    **{s+'.US': (s, 'US', s, s) for s in ('QQQ', 'BIL', 'SPY', 'SMH', 'RSP')},
}
RISK_SYMBOLS = ('159915.SZ', '515180.SH', 'QQQ.US')
ZONES = {'CN': 'Asia/Shanghai', 'US': 'America/New_York'}


def utc_now():
    return pd.Timestamp.now(tz='UTC')


def validate_frame(frame, *, intraday=False):
    if frame.empty or not {'open', 'close'}.issubset(frame):
        raise ValueError('empty or incomplete OHLC response')
    if not isinstance(frame.index, pd.DatetimeIndex) or frame.index.has_duplicates or not frame.index.is_monotonic_increasing:
        raise ValueError('duplicate or unordered timestamps')
    if intraday and frame.index.tz is None:
        raise ValueError('intraday timestamps require a timezone')
    values = frame[['open', 'close']].to_numpy(float)
    if not np.isfinite(values).all() or (values <= 0).any():
        raise ValueError('invalid OHLC values')
    return frame


class MarketClock:
    """Use exchange calendars when installed; otherwise expose an unverified clock.

    Fallback finalizes weeks only after Friday's normal close, including holiday
    weeks. It never mistakes an intraday daily candle for a completed session.
    """
    def __init__(self):
        try:
            import exchange_calendars as xc
            self.calendars = {m: xc.get_calendar(c) for m, c in [('CN', 'XSHG'), ('US', 'XNYS')]}
        except (ImportError, ValueError):
            self.calendars = {}

    def session_close(self, day, market):
        date = pd.Timestamp(day).tz_localize(None).normalize()
        if market in self.calendars:
            cal = self.calendars[market]
            # Out-of-range holiday tables must fail visibly, not guess.
            if not cal.is_session(date):
                raise ValueError('provider returned a non-trading date')
            return pd.Timestamp(cal.session_close(date)).tz_convert('UTC')
        hour = 15 if market == 'CN' else 16
        return date.tz_localize(ZONES[market]) + pd.Timedelta(hours=hour)

    def complete_daily(self, frame, market, now):
        now = pd.Timestamp(now).tz_convert('UTC')
        return frame.loc[[self.session_close(d, market) + pd.Timedelta(minutes=2) <= now for d in frame.index]].copy()

    def week_complete(self, monday, market, now):
        monday = pd.Timestamp(monday).tz_localize(None).normalize()
        if market in self.calendars:
            days = self.calendars[market].sessions_in_range(monday, monday + pd.Timedelta(days=4))
            return bool(len(days) and self.session_close(days[-1], market) + pd.Timedelta(minutes=2) <= now)
        friday = monday + pd.Timedelta(days=4)
        return self.session_close(friday, market) + pd.Timedelta(minutes=2) <= now

    def status(self, market, now):
        local = now.tz_convert(ZONES[market])
        cal = self.calendars.get(market)
        if cal is None:
            return {'state': 'calendar_unverified', 'label': '交易日历未核验', 'timezone': ZONES[market]}
        try:
            date = local.tz_localize(None).normalize()
            if not cal.is_session(date):
                state = 'closed'
            elif cal.is_open_on_minute(now.floor('min')):
                state = 'open'
            else:
                state = 'closed'
            return {'state': state, 'label': '交易中' if state == 'open' else '休市 / 非连续交易时段', 'timezone': ZONES[market]}
        except (ValueError, IndexError):
            return {'state': 'calendar_unverified', 'label': '交易日历超出覆盖范围', 'timezone': ZONES[market]}

    def latest_week(self, market, now):
        monday = now.tz_convert(ZONES[market]).tz_localize(None).normalize().to_period('W-SUN').start_time
        for i in range(8):
            week = monday - pd.Timedelta(weeks=i)
            if self.week_complete(week, market, now):
                return week
        raise ValueError('无法确定最近完整交易周')


def quote_record(symbol, price, prev_close, timestamp, source, now):
    stamp = pd.Timestamp(timestamp)
    if stamp.tzinfo is None:
        raise ValueError('quote timestamp has no timezone')
    if not np.isfinite([price, prev_close]).all() or min(price, prev_close) <= 0 or stamp > now + pd.Timedelta(seconds=30):
        raise ValueError('invalid price or future quote')
    return {'symbol': symbol, 'name': SYMBOLS[symbol][3], 'market': SYMBOLS[symbol][1],
            'price': float(price), 'prev_close': float(prev_close),
            'change_pct': float((price / prev_close - 1) * 100),
            'timestamp': stamp.isoformat(), 'source': source,
            'age_seconds': max(0, int((now - stamp).total_seconds())),
            'latency': 'provider_timestamp_only', 'price_basis': 'unadjusted'}


class PublicProvider:
    """Yahoo timestamped, possibly delayed quotes; Eastmoney index turnover.

    Never promises an exchange real-time entitlement. Network/coverage failures
    stay local to a symbol or market. No synthetic index turnover is substituted.
    """
    name = 'Yahoo Finance / Eastmoney'

    @staticmethod
    def history(symbol, *, interval='1d', adjusted=False):
        import yfinance as yf
        ticker = SYMBOLS[symbol][0]
        kwargs = {'period': '7y' if interval == '1d' else '5d', 'interval': interval,
                  'auto_adjust': adjusted, 'actions': False, 'prepost': False,
                  'timeout': 12, 'raise_errors': True}
        # start/end avoids provider-specific support for a "7y" period.
        if interval == '1d':
            kwargs.pop('period')
            kwargs['start'] = (utc_now() - pd.Timedelta(days=365*7)).strftime('%Y-%m-%d')
        f = yf.Ticker(ticker).history(**kwargs)
        f.columns = [str(c).lower() for c in f.columns]
        if interval == '1d' and isinstance(f.index, pd.DatetimeIndex):
            if f.index.tz is not None:
                f.index = f.index.tz_convert(ZONES[SYMBOLS[symbol][1]]).tz_localize(None).normalize()
        f.attrs['source'] = 'Yahoo Finance'
        return validate_frame(f, intraday=interval != '1d')

    def quotes(self, now):
        def one(symbol):
            f = self.history(symbol, interval='1m')
            # 1m close belongs to its END, not the start label. Drop live candle.
            f = f.loc[f.index + pd.Timedelta(minutes=1) <= now]
            if f.empty:
                raise ValueError('no completed quote observation')
            local_days = f.index.tz_convert(ZONES[SYMBOLS[symbol][1]]).date
            last_day = local_days[-1]
            older = f.loc[local_days < last_day]
            if older.empty:
                raw = getattr(self, '_daily_raw', {}).get(symbol)
                if raw is None:
                    raise ValueError('previous regular close unavailable')
                prior = raw.loc[raw.index < pd.Timestamp(last_day)]
                if prior.empty:
                    raise ValueError('previous regular close unavailable')
                previous_close = float(prior.close.iloc[-1])
            else:
                previous_close = float(older.close.iloc[-1])
            return quote_record(symbol, float(f.close.iloc[-1]), previous_close,
                                f.index[-1] + pd.Timedelta(minutes=1), f.attrs.get('source', self.name)+' · 1分钟收盘观察（可能延迟）', now)
        return collect(SYMBOLS, one)

    def daily(self):
        # Fetch adjusted and raw independently; raw context is mandatory for stops.
        def one(symbol):
            return {'adjusted': self.history(symbol, adjusted=True),
                    'raw': self.history(symbol, adjusted=False)}
        return collect(SYMBOLS, one)

    def bars(self):
        return collect(RISK_SYMBOLS, lambda s: self.history(s, interval='30m'))

    def tape(self):
        fields = {'market': '1.000985', 'tech': '0.399006', 'star': '1.000688', 'red': '1.000922'}
        def one(key):
            query = urlencode({'secid': fields[key], 'klt': 101, 'fqt': 0, 'beg': '20200101', 'end': '20500101',
                               'lmt': 2000, 'fields1': 'f1,f2,f3,f4,f5,f6', 'fields2': 'f51,f52,f53,f54,f55,f56,f57'})
            req = Request('https://push2his.eastmoney.com/api/qt/stock/kline/get?' + query,
                          headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://quote.eastmoney.com/'})
            with urlopen(req, timeout=12) as response:
                rows = (json.load(response).get('data') or {}).get('klines', [])
            if not rows:
                raise ValueError('index turnover unavailable: ' + key)
            parsed = [r.split(',') for r in rows]
            f = pd.DataFrame({'close': [float(r[2]) for r in parsed],
                              'turnover': [float(r[6]) for r in parsed]},
                             index=pd.DatetimeIndex([r[0] for r in parsed]))
            if f.index.has_duplicates or not f.index.is_monotonic_increasing or not np.isfinite(f.to_numpy()).all() or (f <= 0).any().any():
                raise ValueError('invalid index turnover: ' + key)
            return f
        values, errors = collect(fields, one)
        if errors:
            raise ValueError('指数成交额缺失：' + ', '.join(errors))
        return values


class LongbridgeProvider(PublicProvider):
    """Optional persistent read-only OpenAPI context; credentials remain server-side."""
    name = 'Longbridge OpenAPI'

    def __init__(self):
        from longbridge.openapi import Config, QuoteContext
        self.context = QuoteContext(Config.from_apikey_env())

    def history(self, symbol, *, interval='1d', adjusted=False):
        from longbridge.openapi import Period, AdjustType
        now = utc_now()
        rows = self.context.history_candlesticks_by_date(
            symbol, Period.Day if interval == '1d' else Period.Min_30,
            AdjustType.ForwardAdjust if adjusted else AdjustType.NoAdjust,
            (now - pd.Timedelta(days=365*7 if interval == '1d' else 10)).date(), now.date())
        idx = pd.DatetimeIndex([r.timestamp for r in rows])
        if idx.tz is None:
            raise ValueError('Longbridge timestamps require timezone')
        if interval == '1d':
            idx = idx.tz_convert(ZONES[SYMBOLS[symbol][1]]).tz_localize(None).normalize()
        return validate_frame(pd.DataFrame({'open': [float(r.open) for r in rows],
                                            'close': [float(r.close) for r in rows]}, index=idx), intraday=interval != '1d')

    def quotes(self, now):
        rows = self.context.quote(list(SYMBOLS))
        result, errors = {}, {}
        for r in rows:
            try:
                result[r.symbol] = quote_record(r.symbol, float(r.last_done), float(r.prev_close), r.timestamp,
                                                 'Longbridge OpenAPI · 依账户行情权限', now)
            except (ValueError, KeyError):
                errors[r.symbol] = '长桥报价校验失败'
        for s in SYMBOLS.keys() - result.keys():
            errors.setdefault(s, '长桥未返回该标的报价')
        return result, errors


def collect(keys, function):
    results, errors = {}, {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(function, k): k for k in keys}
        for future in as_completed(futures):
            key = futures[future]
            try:
                results[key] = future.result()
            except Exception as error:
                # Do not expose exception text: upstream URLs can include credentials.
                errors[key] = type(error).__name__ + ': 数据请求或校验失败'
    return results, errors


def make_provider(choice='auto'):
    configured = all(os.getenv('LONGBRIDGE_' + k) for k in ('APP_KEY', 'APP_SECRET', 'ACCESS_TOKEN'))
    if choice == 'longbridge' or (choice == 'auto' and configured):
        return LongbridgeProvider()
    if choice == 'efinance' or (choice == 'auto' and importlib.util.find_spec('efinance') is not None):
        from live.efinance_provider import EfinanceProvider
        return EfinanceProvider()
    return PublicProvider()
