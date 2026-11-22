"""Regime multi-regime selector, fully point-in-time.

A heat-up of the technology trend relative to its 26-week history activates
an assertive allocation; for a fixed 8/12/16-week trailing window it can
continue to manage a sector-rotation phase. Else EnsembleRisk-A is preserved exactly.
Only RESEARCH; retained-window sizes were investigated after 2026.
"""
from dataclasses import dataclass
import numpy as np
import pandas as pd
from strategy_core.baselines.multi_asset import TARGETS
from strategy_core.animal_state import AnimalConfig, signals_animal

@dataclass(frozen=True)
class RegimeConfig:
    hold_weeks:int=12
    animal:AnimalConfig=AnimalConfig()


def build_regime_signals(p:pd.DataFrame,ensemble_signals:pd.DataFrame,cfg:RegimeConfig=RegimeConfig()):
    if cfg.hold_weeks not in (0,4,8,12,16):
        raise ValueError('Research grid only: hold_weeks 0/4/8/12/16')
    if not ensemble_signals.index.equals(p.index):raise ValueError('misaligned inputs')
    stronger=(ensemble_signals.tech_stage>=5) & (p.tech.pct_change(26,fill_method=None)>0)
    activated=stronger.astype(float).rolling(cfg.hold_weeks+1,min_periods=1).max().astype(bool)
    a=signals_animal(p,ensemble_signals,cfg.animal)
    f=ensemble_signals.copy()
    f[list(TARGETS)]=a[list(TARGETS)].where(activated,ensemble_signals[list(TARGETS)])
    f['regime_active']=activated
    f['regime_explanation']=np.where(activated,a.animal_reason,'EnsembleRisk-A baseline: no active trend regime')
    arr=f[list(TARGETS)].to_numpy(float)
    assert (arr>=-1e-9).all() and np.allclose(arr.sum(axis=1),1)
    return f
