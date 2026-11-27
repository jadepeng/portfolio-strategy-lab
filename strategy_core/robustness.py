"""Overfitting / robustness diagnostics (research only, never changes signals).

- deflated_sharpe: Bailey & Lopez de Prado (2014) DSR, given number of trials N.
- subperiod_table: metrics per fixed, pre-declared sub-period.
- param_plateau: one-at-a-time sensitivity; flags "spiky" parameters.
"""
from __future__ import annotations
from dataclasses import replace
from statistics import NormalDist
import math
import numpy as np
import pandas as pd

_N = NormalDist()
EULER = 0.5772156649


def sharpe_calmar(nav: pd.Series, periods: int = 52) -> dict:
    nav = nav.astype(float)
    r = nav.pct_change(fill_method=None).dropna()
    years = (nav.index[-1] - nav.index[0]).days / 365.25
    cagr = (nav.iloc[-1] / nav.iloc[0]) ** (1 / years) - 1
    mdd = float((nav / nav.cummax() - 1).min())
    sd = r.std(ddof=1)
    downside = r[r < 0].std(ddof=1)
    return {'cagr_pct': 100 * cagr, 'mdd_pct': 100 * mdd,
            'sharpe': float(r.mean() / sd * math.sqrt(periods)) if sd > 0 else float('nan'),
            'sortino': float(r.mean() / downside * math.sqrt(periods)) if downside > 0 else float('nan'),
            'calmar': float(cagr / abs(mdd)) if mdd < 0 else float('nan')}


def deflated_sharpe(nav: pd.Series, n_trials: int) -> dict:
    r = nav.astype(float).pct_change(fill_method=None).dropna()
    T = len(r)
    sr = float(r.mean() / r.std(ddof=1))
    skew, kurt = float(r.skew()), float(r.kurt()) + 3
    n = max(2, int(n_trials))
    sr0 = math.sqrt(1 / T) * ((1 - EULER) * _N.inv_cdf(1 - 1 / n) + EULER * _N.inv_cdf(1 - 1 / (n * math.e)))
    denom = math.sqrt(max(1e-12, 1 - skew * sr + (kurt - 1) / 4 * sr ** 2))
    dsr = _N.cdf((sr - sr0) * math.sqrt(T - 1) / denom)
    return {'period_sharpe': sr, 'sr_benchmark': sr0, 'dsr': dsr, 'n_trials': n,
            'pass_95': dsr >= .95}


def subperiod_table(nav: pd.Series, periods=(('2020', '2023'), ('2024', '2026'))) -> pd.DataFrame:
    rows = []
    for a, b in periods:
        x = nav.loc[a:b]
        rows.append({'period': f'{a}-{b}', **sharpe_calmar(x / x.iloc[0])})
    return pd.DataFrame(rows)


def param_plateau(run, base_cfg, grid: dict[str, list], metric: str = 'sharpe') -> pd.DataFrame:
    """run(cfg) -> nav Series. Flags params where best/median metric ratio > 1.15."""
    rows = []
    for field, values in grid.items():
        for v in values:
            m = sharpe_calmar(run(replace(base_cfg, **{field: v})))
            rows.append({'param': field, 'value': v, **m})
    df = pd.DataFrame(rows)
    flag = df.groupby('param')[metric].agg(lambda s: s.max() / max(1e-9, s.median()) > 1.15)
    df['spiky'] = df.param.map(flag)
    return df
