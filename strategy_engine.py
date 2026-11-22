"""Weekly, next-bar, no-lookahead, unlevered regime allocation backtester."""
from dataclasses import dataclass
from typing import Optional
import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Params:
    warn_dd: float = 0.08
    defense_dd: float = 0.12
    extreme_dd: float = 0.20
    macro_tlt_drop: float = 0.045
    macro_yield_rise_bp: float = 35.0
    trade_cost: float = 0.0015
    use_macro: str = 'TLT代理'  # TLT代理, 真实10Y收益率, 不使用
    cash_rate: float = 0.0  # annualized continuously approximated weekly by (1+r)^1/52


def to_weekly(raw: pd.DataFrame) -> pd.Series:
    """Raw DataFrame with Date index and Close column or single Series -> Friday-week close."""
    if isinstance(raw, pd.Series):
        s = raw
    else:
        s = raw['Close']
    s = pd.to_numeric(s, errors='coerce').dropna().sort_index()
    idx = pd.to_datetime(s.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    s.index = idx.normalize()
    s = s.loc[~s.index.duplicated(keep='last')]
    return s.resample('W-FRI').last().dropna()


def prepare_rate(raw: pd.DataFrame) -> pd.Series:
    """FRED CSV with DATE,DGS10; percentage points (4.92 means 4.92%)."""
    x = raw.copy()
    dc = next((c for c in x if str(c).upper() in ('DATE', 'OBSERVATION_DATE')), None)
    vc = next((c for c in x if str(c).upper() in ('DGS10', 'YIELD', 'VALUE')), None)
    if dc is None or vc is None:
        raise ValueError('收益率CSV必须包含 DATE 和 DGS10 两列；示例：2026-11-20,4.92')
    s = pd.Series(pd.to_numeric(x[vc].replace('.', np.nan), errors='coerce').values,
                  index=pd.to_datetime(x[dc], errors='coerce'))
    return s.dropna().sort_index().resample('W-FRI').last()


def make_signals(index_close: pd.Series, bond_close: Optional[pd.Series],
                 rate_10y: Optional[pd.Series], p: Params, strategy: str) -> pd.DataFrame:
    s = index_close.dropna().sort_index().astype(float)
    a = pd.DataFrame({'close': s})
    a['ma10'] = s.rolling(10, min_periods=10).mean()
    a['ma30'] = s.rolling(30, min_periods=30).mean()
    a['high26'] = s.rolling(26, min_periods=1).max()
    a['drawdown26'] = s / a['high26'] - 1
    a['return4'] = s.pct_change(4, fill_method=None)
    a['return8'] = s.pct_change(8, fill_method=None)
    if bond_close is not None:
        # Do not silently extend bond prices across long missing weeks.
        b = bond_close.sort_index().reindex(s.index).ffill(limit=1)
        a['tlt_r8'] = b.pct_change(8, fill_method=None)
    else:
        a['tlt_r8'] = np.nan
    if rate_10y is not None:
        q = rate_10y.sort_index().reindex(s.index).ffill(limit=1)
        a['yield_bp8'] = (q - q.shift(8)) * 100
    else:
        a['yield_bp8'] = np.nan
    signals, reasons = [], []
    for i, (date, row) in enumerate(a.iterrows()):
        c, m10, m30, d = row['close'], row['ma10'], row['ma30'], row['drawdown26']
        target, why = 1.0, []
        if strategy == '30周均线':
            if i >= 30 and c < m30:
                target, why = 0.0, ['30周线以下']
        elif strategy == '三级风控':
            if i >= 30:
                if c < m10 or d <= -p.warn_dd:
                    target, why = .75, ['预警']
                if c < m30 or d <= -p.defense_dd:
                    target, why = .50, ['防御']
                if d <= -p.extreme_dd or (c < m30 and d <= -.16):
                    target, why = .25, ['极端']
                if c > m10 and row['return4'] > 0 and d > -.16 and target < .50:
                    target, why = .50, ['短线修复']
                if c > m30 and c > m10 and row['return4'] > 0 and d > -.10:
                    target, why = 1.0, ['趋势修复']
                if (c / row['high26'] >= .94 and row['return8'] >= -.01):
                    if p.use_macro == 'TLT代理' and pd.notna(row['tlt_r8']) and row['tlt_r8'] <= -p.macro_tlt_drop:
                        target = min(.75, target)
                        why.append('TLT价格背离')
                    elif p.use_macro == '真实10Y收益率' and pd.notna(row['yield_bp8']) and row['yield_bp8'] >= p.macro_yield_rise_bp:
                        target = min(.75, target)
                        why.append('10Y收益率背离')
        signals.append(target)
        reasons.append('、'.join(why) if why else '正常')
    a['next_target'] = signals
    a['reason'] = reasons
    return a


def backtest(sig: pd.DataFrame, p: Params, strategy: str) -> pd.DataFrame:
    """Signals visible at weekly close t are held in week t+1. Price returns only."""
    x = sig.copy()
    x['weight'] = 1.0 if strategy == '买入持有' else x['next_target'].shift(1).fillna(1.0)
    x['index_ret'] = x['close'].pct_change(fill_method=None).fillna(0.0)
    cash_w = (1 + p.cash_rate) ** (1 / 52) - 1
    x['turnover'] = x['weight'].diff().abs().fillna(0.0)
    x['strategy_ret'] = ((1 + x['weight'] * x['index_ret']
                        + (1 - x['weight']) * cash_w)
                        * (1 - x['turnover'] * p.trade_cost) - 1)
    x['nav'] = (1 + x['strategy_ret']).cumprod()
    return x


def metrics(x: pd.DataFrame, start: str, end: str) -> dict:
    a = x.loc[start:end]
    if len(a) < 8:
        return {'区间收益率': np.nan, '年化收益率': np.nan, '最大回撤': np.nan,
                '年化波动率': np.nan, '平均仓位': np.nan, '调仓次数': 0, '累计换手': np.nan}
    nav = a['nav'].astype(float)
    r = nav.pct_change().dropna()
    total = nav.iloc[-1] / nav.iloc[0] - 1
    ann = (1 + total) ** (52 / (len(a) - 1)) - 1
    dd = (nav / nav.cummax() - 1).min()
    return {'区间收益率': total * 100, '年化收益率': ann * 100,
            '最大回撤': dd * 100, '年化波动率': r.std(ddof=1) * np.sqrt(52) * 100,
            '平均仓位': a['weight'].mean() * 100,
            '调仓次数': int((a['turnover'] > .05).sum()), '累计换手': a['turnover'].sum()}


def test_windows():
    return {
        '完整样本': ('2015-01-01', '2099-12-31'),
        '2015-16 A股股灾': ('2015-05-25', '2016-02-29'),
        '2018 下跌': ('2018-01-01', '2018-12-31'),
        '2020 疫情': ('2020-02-03', '2020-08-31'),
        '2022 熊市': ('2022-01-01', '2022-12-31'),
        '2026 本轮': ('2026-09-28', '2026-11-20'),
    }


def run_comparison(close, tlt, rate, p):
    out = {}
    for name in ['买入持有', '30周均线', '三级风控']:
        sig = make_signals(close, tlt, rate, p, name)
        out[name] = backtest(sig, p, name)
    return out
