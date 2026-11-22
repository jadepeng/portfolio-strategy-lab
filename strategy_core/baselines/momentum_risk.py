"""MomentumRisk: risk-budget overlay for GrowthReentry tech re-entry.

Formal research candidate, NOT Nick's private 趋势动物 algorithm.
All week-t targets use data ending at week-t close, with trades modeled at
week-(t+1) close through the unchanged CashSleeve simulator. The MomentumRisk overlay ONLY
reduces high-risk technology exposure; dividend core is never sold by MomentumRisk.
Do not use this weekly model as an intraday or automatic execution signal.
"""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import pandas as pd
from strategy_core.baselines.trend_rotation import TrendRotationConfig
from strategy_core.baselines.dividend_overlay import DividendOverlayConfig
from strategy_core.baselines.cash_sleeve import CashSleeveConfig, simulate_cash_sleeve
from strategy_core.baselines.growth_reentry import GrowthReentryConfig, build_growth_reentry_signals


@dataclass(frozen=True)
class MomentumRiskConfig:
    use_relative_brake: bool = True
    relative_weeks: int = 6
    weak_relative_threshold: float = 0.0
    weak_relative_cap: float = .32
    use_volatility_brake: bool = True
    volatility_weeks: int = 12
    target_annual_vol: float = .30
    min_vol_multiplier: float = .30


def validate_momentum_risk(c: MomentumRiskConfig, cash: CashSleeveConfig) -> None:
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


def build_momentum_risk_signals(prices: pd.DataFrame, base: TrendRotationConfig, model: DividendOverlayConfig,
               cash: CashSleeveConfig, risk_cfg: MomentumRiskConfig = MomentumRiskConfig(),
               reentry_cfg: GrowthReentryConfig = GrowthReentryConfig()) -> pd.DataFrame:
    """Derive risk-only overlay from pre-existing GrowthReentry targets.

    No raw position is increased; all released technology budget moves into
    target_cash. The whole historical prefix remains invariant when later
    observations are appended or modified, providing a direct leakage check.
    """
    if base.frequency != 'week':
        raise ValueError('MomentumRisk目前仅支持周线；GrowthReentry的4/10/30窗口不能直接用日频数据')
    validate_momentum_risk(risk_cfg, cash)
    f = build_growth_reentry_signals(prices, base, model, cash, reentry_cfg).copy()
    t = prices.tech.astype(float)
    r = prices.red.astype(float)
    relative = t.pct_change(risk_cfg.relative_weeks, fill_method=None) - r.pct_change(risk_cfg.relative_weeks, fill_method=None)
    volatility = t.pct_change(fill_method=None).rolling(risk_cfg.volatility_weeks, min_periods=risk_cfg.volatility_weeks).std(ddof=1) * np.sqrt(52.)
    updates = []
    for i in range(len(f)):
        old_tech = float(f.target_tech.iloc[i]); old_red = float(f.target_red.iloc[i])
        new_tech = old_tech
        reasons = []
        if i >= max(30, base.long_ma, base.slow) and risk_cfg.use_relative_brake and pd.notna(relative.iloc[i]) and relative.iloc[i] < risk_cfg.weak_relative_threshold:
            if new_tech > risk_cfg.weak_relative_cap:
                new_tech = risk_cfg.weak_relative_cap
                reasons.append('科技6周相对红利走弱，限制科技上限')
        v = volatility.iloc[i]
        if risk_cfg.use_volatility_brake and np.isfinite(v) and v > risk_cfg.target_annual_vol:
            scale = min(1., max(risk_cfg.min_vol_multiplier, risk_cfg.target_annual_vol / v))
            scaled = new_tech * scale
            if scaled < new_tech - 1e-10:
                new_tech = scaled
                reasons.append('科技波动率超预算，部分转入货币ETF')
        new_tech = min(old_tech, max(0., new_tech))
        # Leave original red allocation intact. Any technology reduction raises money/cash.
        new_cash = 1. - old_red - new_tech
        updates.append((new_tech, old_red, new_cash,
                        old_tech - new_tech, '；'.join(reasons) or '保留GrowthReentry买回与退出信号'))
    f[['target_tech', 'target_red', 'target_cash', 'tech_risk_reduction', 'risk_reason']] = pd.DataFrame(updates, index=f.index)
    f['tech_vs_dividend_6w'] = relative
    f['tech_annual_vol'] = volatility
    f['risk_override'] = f.tech_risk_reduction > 1e-9
    w = f[['target_tech', 'target_red', 'target_cash']]
    if not np.allclose(w.sum(axis=1), 1.) or (w < -1e-9).any().any() or (w > 1+1e-9).any().any():
        raise AssertionError('MomentumRisk仓位约束未通过')
    if (f.target_red < cash.red_core - 1e-9).any():
        raise AssertionError('MomentumRisk错误地挪用了红利核心')
    return f


def simulate_momentum_risk(prices: pd.DataFrame, base: TrendRotationConfig, model: DividendOverlayConfig,
                cash: CashSleeveConfig, risk_cfg: MomentumRiskConfig = MomentumRiskConfig(),
                reentry_cfg: GrowthReentryConfig = GrowthReentryConfig()) -> pd.DataFrame:
    return simulate_cash_sleeve(prices, base, model, cash, signals=build_momentum_risk_signals(prices, base, model, cash, risk_cfg, reentry_cfg))
