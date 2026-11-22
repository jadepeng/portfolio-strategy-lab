"""V6: deterministic, inspectable overlay on V5's trend state engine.

Purpose: test *incremental* value of defensive rotation, portfolio-sized risk control,
and hysteresis (avoid churning tiny changes). No private Nick formula is asserted.

Signals at the close of t; all simulations fill at close of t+1 in fusion_v5.simulate.
Never evaluate future labels in the generation of any trading signal.
"""
from __future__ import annotations
from dataclasses import dataclass, replace
import numpy as np
import pandas as pd
from fusion_v5 import FusionConfig, create_signals, simulate, validate_input, metrics

@dataclass(frozen=True)
class V6Config:
    # Cross-asset defensive rotation; each item can be independently disabled.
    enable_defensive_rotation: bool = False
    red_core: float = 0.20    # persistent dividend sleeve of strategy capital
    red_floor: float = 0.30
    red_rel_min: float = 0.035
    red_abs_min: float = 0.0
    red_max_dd: float = -0.10
    red_confirm: int = 2
    # Exit immediately on new deterioration; only add risk after it persists.
    enable_hysteresis: bool = False
    min_change: float = 0.12     # full L1 turnover; 0.12 ~= 6 percentage points switched
    add_confirm: int = 2
    round_weight: float = 0.0
    # Side-specific volatility guard; stricter technology risk budget.
    enable_vol_guard: bool = False
    extreme_tech_vol: float = 0.48
    tech_cap_at_high_vol: float = 0.35
    # Optional cash buffer after a 3-day / 1-week swing loss: no future bar access.
    enable_drawdown_speed: bool = False
    tech_speed_loss: float = -0.09
    tech_speed_cap: float = 0.25


def config_checks(c: V6Config):
    if not 0 <= c.red_floor <= 1 or not 0<=c.red_core<=.5: raise ValueError('red_floor/red_core 不在允许范围')
    if not 0 <= c.red_confirm <= 20 or not 1 <= c.add_confirm <= 20: raise ValueError('确认期无效')
    if not 0 <= c.min_change <= 1 or not 0 <= c.round_weight <= .2: raise ValueError('平滑参数无效')
    if not 0 <= c.tech_cap_at_high_vol <= 1: raise ValueError('科技上限无效')


def signal_v6(prices: pd.DataFrame, base: FusionConfig, config: V6Config) -> pd.DataFrame:
    """One-way-causal overlay; V5 model unchanged for an apples-to-apples ablation."""
    validate_input(prices,base);config_checks(config)
    f=create_signals(prices,base).copy()
    warm=max(base.slow,base.long_ma,base.vol_window,60 if base.frequency=='day' else 30)
    last=np.array([.5,.5,0.])
    add_streak=np.zeros(2,dtype=int)
    red_streak=0
    hist=[]
    for i,(_,r) in enumerate(f.iterrows()):
        raw=np.array([float(r.target_tech),float(r.target_red)],dtype=float)
        marks=[]
        if i<warm:
            hist.append(((1-config.red_core)*raw[0],config.red_core+(1-config.red_core)*raw[1],(1-config.red_core)*(1-raw.sum()),'准备期：红利核心 + 预热等权战术',False))
            last=np.array([raw[0],raw[1],1-raw.sum()])
            continue
        desired=raw.copy()
        # Hysteresis states use only current and earlier factor values.
        rel=float(r.red_short_rel) if pd.notna(r.red_short_rel) else np.nan
        red_abs=float(r.red_short_return) if pd.notna(r.red_short_return) else np.nan
        red_above_short=bool(r.red>r.red_short_ma)
        red_dd=float(r.red_dd)
        red_ok=(np.isfinite(rel) and rel>=config.red_rel_min
                and red_abs>config.red_abs_min and red_above_short
                and red_dd>=config.red_max_dd and float(r.red_r1)>base.red_exit_shock)
        red_streak=red_streak+1 if red_ok else 0
        if config.enable_defensive_rotation and red_streak>=config.red_confirm and desired[0]<=.3:
            red_vol=float(r.red_vol)
            tgt_vol=base.target_red_vol
            red_vol_factor=min(1.,max(.25,tgt_vol/red_vol)) if np.isfinite(red_vol) and red_vol>0 else 1.
            floor=config.red_floor*red_vol_factor
            if desired[1]<floor:
                desired[1]=floor;marks.append('防御轮动：红利短期强于科技且自身上行')
        if config.enable_vol_guard and float(r.tech_vol)>=config.extreme_tech_vol:
            if desired[0]>config.tech_cap_at_high_vol:
                desired[0]=config.tech_cap_at_high_vol;marks.append('科技高波动上限')
        if config.enable_drawdown_speed:
            h=max(2,base.fast//4)
            ret=float(f.tech.iloc[i]/f.tech.iloc[i-h]-1)
            if ret<=config.tech_speed_loss and float(r.tech)<float(r.tech_ma_fast):
                if desired[0]>config.tech_speed_cap:
                    desired[0]=config.tech_speed_cap;marks.append('科技急速回撤上限')
        if desired.sum()>1: desired/=desired.sum()
        if config.round_weight>0:
            desired=np.round(desired/config.round_weight)*config.round_weight
            if desired.sum()>1:desired/=desired.sum()
        if config.enable_hysteresis:
            # De-risk immediately. Add risk only after two consecutive signals.
            for j in range(2):
                if desired[j]<=last[j]+1e-9:
                    add_streak[j]=0
                else:
                    add_streak[j]+=1
                    if add_streak[j]<config.add_confirm:desired[j]=last[j]
            if desired.sum()>1:desired/=desired.sum()
            # Very small changes are ignored unless latest intended position exits to zero.
            turnover=abs(desired[0]-last[0])+abs(desired[1]-last[1])
            if turnover < config.min_change:
                desired=last[:2].copy()
            # Never prevent an explicit V5 hard sell via smoothing.
            for j in range(2):
                if raw[j]<.001 and last[j]>.001 and desired[j]>raw[j]:
                    desired[j]=raw[j];marks.append('基础趋势硬退出')
        desired=np.maximum(0,desired)
        if desired.sum()>1: desired/=desired.sum()
        last=np.array([desired[0],desired[1],1-desired.sum()])
        core=config.red_core
        hist.append(((1-core)*last[0],core+(1-core)*last[1],(1-core)*last[2],
                     '红利常驻核心 + '+('；'.join(marks) or 'V5趋势纪律'),red_ok))
    f[['target_tech','target_red','target_cash','v6_reason','v6_red_eligible']]=pd.DataFrame(hist,index=f.index)
    assert np.allclose(f[['target_tech','target_red','target_cash']].sum(axis=1),1.)
    return f


def v6_base(frequency:str='week'):
    """Smaller pilot and turnover deadband; no changes to V5 hard exits."""
    from fusion_v5 import weekly_config
    return replace(weekly_config() if frequency=='week' else FusionConfig(),trial=.20,min_rebalance=.12)


def simulate_v6(prices: pd.DataFrame,base:FusionConfig,c:V6Config)->pd.DataFrame:
    return simulate(prices,signal_v6(prices,base,c),base)


def ablate(prices: pd.DataFrame,base:FusionConfig,c:V6Config):
    """Targeted ablation; V5 default deliberately preserved as baseline."""
    from fusion_v5 import weekly_config
    old = (weekly_config() if base.frequency=='week' else FusionConfig())
    old=replace(old,cost_per_side=base.cost_per_side)
    return {
      'V5原版':simulate(prices,create_signals(prices,old),old),
      'V6当前设置':simulate_v6(prices,base,c),
      'V6移除红利底仓':simulate_v6(prices,base,replace(c,red_core=0)),
      'V6提前红利试仓':simulate_v6(prices,base,replace(c,enable_defensive_rotation=True)),
      'V6增加加仓确认':simulate_v6(prices,base,replace(c,enable_hysteresis=True,round_weight=.05)),
      'V6增加科技波动上限':simulate_v6(prices,base,replace(c,enable_vol_guard=True)),
      'V6增加回撤速度闸门':simulate_v6(prices,base,replace(c,enable_drawdown_speed=True)),
      '科技买入持有':simulate(prices,create_signals(prices,old),old,'tech'),
      '红利买入持有':simulate(prices,create_signals(prices,old),old,'red'),
    }
