"""V9: risk-budget overlay for V8 tech re-entry.

Formal research candidate, NOT Nick's private 趋势动物 algorithm.
All week-t targets use data ending at week-t close, with trades modeled at
week-(t+1) close through the unchanged V7 simulator. The V9 overlay ONLY
reduces high-risk technology exposure; dividend core is never sold by V9.
Do not use this weekly model as an intraday or automatic execution signal.
"""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import pandas as pd
from history.strategies.fusion_v5 import FusionConfig
from history.strategies.fusion_v6 import V6Config
from history.strategies.fusion_v7 import V7Config, simulate_v7
from history.strategies.fusion_v8 import V8Config, signals_v8


@dataclass(frozen=True)
class V9Config:
    use_relative_brake: bool = True
    relative_weeks: int = 6
    weak_relative_threshold: float = 0.0
    weak_relative_cap: float = .32
    use_volatility_brake: bool = True
    volatility_weeks: int = 12
    target_annual_vol: float = .30
    min_vol_multiplier: float = .30


def validate_v9(c: V9Config, cash: V7Config) -> None:
    if not 3 <= c.relative_weeks <= 52 or not 5 <= c.volatility_weeks <= 52:
        raise ValueError('相对动量及波动率计算周期超出范围')
    if not -.30 <= c.weak_relative_threshold <= .30:
        raise ValueError('相对动量阈值超出范围')
    if not 0 <= c.weak_relative_cap <= 1 - cash.red_core + 1e-9:
        raise ValueError('弱相对动量下的科技上限与红利底仓冲突')
    if not .05 <= c.target_annual_vol <= 1.5:
        raise ValueError('科技年化波动率目标超出范围')
    if not .05 <= c.min_vol_multiplier <= 1:
        raise ValueError('最低风险倍率必须在5%—100%之间')


def signals_v9(prices: pd.DataFrame, base: FusionConfig, model: V6Config,
               cash: V7Config, v9: V9Config = V9Config(),
               v8: V8Config = V8Config()) -> pd.DataFrame:
    """Derive risk-only overlay from pre-existing V8 targets.

    No raw position is increased; all released technology budget moves into
    target_cash. The whole historical prefix remains invariant when later
    observations are appended or modified, providing a direct leakage check.
    """
    if base.frequency != 'week':
        raise ValueError('V9目前仅支持周线；V8的4/10/30窗口不能直接用日频数据')
    validate_v9(v9, cash)
    f = signals_v8(prices, base, model, cash, v8).copy()
    t = prices.tech.astype(float)
    r = prices.red.astype(float)
    relative = t.pct_change(v9.relative_weeks, fill_method=None) - r.pct_change(v9.relative_weeks, fill_method=None)
    volatility = t.pct_change(fill_method=None).rolling(v9.volatility_weeks, min_periods=v9.volatility_weeks).std(ddof=1) * np.sqrt(52.)
    updates = []
    for i in range(len(f)):
        old_tech = float(f.target_tech.iloc[i]); old_red = float(f.target_red.iloc[i])
        new_tech = old_tech
        reasons = []
        if i >= max(30, base.long_ma, base.slow) and v9.use_relative_brake and pd.notna(relative.iloc[i]) and relative.iloc[i] < v9.weak_relative_threshold:
            if new_tech > v9.weak_relative_cap:
                new_tech = v9.weak_relative_cap
                reasons.append('科技6周相对红利走弱，限制科技上限')
        v = volatility.iloc[i]
        if v9.use_volatility_brake and np.isfinite(v) and v > v9.target_annual_vol:
            scale = min(1., max(v9.min_vol_multiplier, v9.target_annual_vol / v))
            scaled = new_tech * scale
            if scaled < new_tech - 1e-10:
                new_tech = scaled
                reasons.append('科技波动率超预算，部分转入货币ETF')
        new_tech = min(old_tech, max(0., new_tech))
        # Leave original red allocation intact. Any technology reduction raises money/cash.
        new_cash = 1. - old_red - new_tech
        updates.append((new_tech, old_red, new_cash,
                        old_tech - new_tech, '；'.join(reasons) or '保留V8买回与退出信号'))
    f[['target_tech', 'target_red', 'target_cash', 'v9_tech_reduction', 'v9_reason']] = pd.DataFrame(updates, index=f.index)
    f['v9_tech_vs_red_6w'] = relative
    f['v9_tech_annual_vol'] = volatility
    f['v9_risk_override'] = f.v9_tech_reduction > 1e-9
    w = f[['target_tech', 'target_red', 'target_cash']]
    if not np.allclose(w.sum(axis=1), 1.) or (w < -1e-9).any().any() or (w > 1+1e-9).any().any():
        raise AssertionError('V9仓位约束未通过')
    if (f.target_red < cash.red_core - 1e-9).any():
        raise AssertionError('V9错误地挪用了红利核心')
    return f


def simulate_v9(prices: pd.DataFrame, base: FusionConfig, model: V6Config,
                cash: V7Config, v9: V9Config = V9Config(),
                v8: V8Config = V8Config()) -> pd.DataFrame:
    return simulate_v7(prices, base, model, cash, signals=signals_v9(prices, base, model, cash, v9, v8))
