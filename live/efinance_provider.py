"""Optional lightweight mainland ETF adapter, paired with Yahoo for U.S. ETFs."""
from __future__ import annotations

import pandas as pd

from live.market import PublicProvider, SYMBOLS, utc_now, validate_frame


def eastmoney_frame(frame, *, interval='1d', code=None):
    """Eastmoney minute candles carry END labels; internal engine uses START.

    A 10:00 30m candle is 09:30--10:00, never 10:00--10:30. Preserve this
    distinction so the V22 engine cannot read or confirm an unfinished candle.
    """
    if frame.empty or not {'日期', '开盘', '收盘'}.issubset(frame):
        raise ValueError('efinance returned empty/incomplete history')
    code_col = '股票代码' if '股票代码' in frame else '代码'
    if code is not None and (code_col not in frame or not frame[code_col].astype(str).str.zfill(6).eq(code).all()):
        raise ValueError('efinance security code mismatch')
    f = frame.rename(columns={'开盘': 'open', '收盘': 'close', '最高': 'high', '最低': 'low',
                              '成交量': 'volume', '成交额': 'turnover'}).copy()
    f.index = pd.DatetimeIndex(pd.to_datetime(f['日期']))
    if interval != '1d':
        minutes = 30 if interval == '30m' else 1
        f.index = f.index.tz_localize('Asia/Shanghai') - pd.Timedelta(minutes=minutes)
    else:
        f.index = f.index.normalize()
    for col in ('open', 'close', 'high', 'low', 'volume', 'turnover'):
        if col in f:
            f[col] = pd.to_numeric(f[col], errors='raise')
    f.attrs['source'] = 'efinance / Eastmoney'
    return validate_frame(f, intraday=interval != '1d')


class EfinanceProvider(PublicProvider):
    name = 'efinance / Eastmoney (CN) + yfinance (US)'

    def __init__(self):
        import efinance as ef
        from efinance.shared import session
        from requests.adapters import HTTPAdapter

        class BoundedHTTPSAdapter(HTTPAdapter):
            def send(self, request, **kwargs):
                kwargs['timeout'] = (5, 12)
                kwargs['verify'] = True
                return super().send(request, **kwargs)

        # This adapter is confined to efinance's shared client, never requests
        # globally. History requests stay bounded and retain TLS verification.
        session.mount('https://', BoundedHTTPSAdapter(max_retries=0))
        self.ef = ef
        self._daily_raw = {}

    def history(self, symbol, *, interval='1d', adjusted=False):
        if SYMBOLS[symbol][1] == 'US':
            return super().history(symbol, interval=interval, adjusted=adjusted)
        code, suffix = symbol.split('.')
        secid = ('1.' if suffix == 'SH' else '0.') + code
        start = utc_now() - pd.Timedelta(days=365*7 if interval == '1d' else 10)
        f = self.ef.stock.get_quote_history(secid, beg=start.strftime('%Y%m%d'),
            end='20500101', klt={'1d': 101, '30m': 30, '1m': 1}[interval],
            fqt=1 if adjusted else 0, quote_id_mode=True)
        result = eastmoney_frame(f, interval=interval, code=code)
        if interval == '1d' and not adjusted:
            self._daily_raw[symbol] = result
        return result
