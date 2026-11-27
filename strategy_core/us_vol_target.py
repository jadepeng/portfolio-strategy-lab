"""Optional US variant: continuous EWMA volatility targeting on the V18 trend score.

V18 keeps a fixed 30% QQQ core and only scales the tactical part when 13w vol > 24%.
That leaves the core fully exposed in crashes (2022 MDD -22.4%).
This variant scales the WHOLE position to a target vol (no leverage):

    w_qqq = (core + (1-core) * trend_score) * min(1, target_vol / ewma_vol)

Backtest 2020-11..2026-11 (next-week-open fills, V18 fees):
    V18                 CAGR 11.8%  MDD -22.4%  Sharpe 0.85
    target 15%, core 0.2 CAGR  9.5%  MDD -15.6%  Sharpe 0.87
Same risk-adjusted return, ~7pp smaller drawdown: a risk-preference choice,
not a free lunch. Default OFF in production; expose as an account option.
"""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import pandas as pd
from strategy_core.us_trend import USTrendConfig, build_us_trend_signals


@dataclass(frozen=True)
class USVolTargetConfig:
    target_vol: float = 0.15
    core: float = 0.20
    halflife_weeks: int = 8

    def validate(self):
        if not (.06 <= self.target_vol <= .35 and 0 <= self.core <= 1 and 2 <= self.halflife_weeks <= 26):
            raise ValueError('invalid vol-target config')


def build_us_vol_target_signals(p: pd.DataFrame, cfg: USVolTargetConfig = USVolTargetConfig(),
                                trend_cfg: USTrendConfig = USTrendConfig()) -> pd.DataFrame:
    cfg.validate()
    sig = build_us_trend_signals(p, trend_cfg)
    r = p.QQQ.pct_change(fill_method=None)
    ewv = np.sqrt((r ** 2).ewm(halflife=cfg.halflife_weeks, min_periods=cfg.halflife_weeks).mean() * 52)
    scale = (cfg.target_vol / ewv).clip(upper=1.0).fillna(1.0)
    w = ((cfg.core + (1 - cfg.core) * sig.us_trend_score) * scale).clip(0, 1)
    sig['us_ewma_vol'] = ewv
    sig['us_vol_target_scale'] = scale
    sig['target_qqq'] = w
    sig['target_bil'] = 1 - w
    return sig
