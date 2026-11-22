"""Portfolio: audit-first integration of Trend asset risk budget and US trend ensembles.

CN and US strategies are *independent* accounts; U.S. information only supplies
risk control to ChiNext tactical entry through the pre-existing causal gate.

All weekly signals are computed after the weekly closing session. An execution
at the next week's OPEN references signals from the PREVIOUS week.
"""
from __future__ import annotations
from dataclasses import dataclass, replace
import numpy as np
import pandas as pd
from strategy_core.us_trend import USTrendConfig, build_us_trend_signals, simulate_us_trend
from strategy_core.cn_strategy import ChinaTrendConfig, build_china_trend_weekly, replay_china_trend_daily
from strategy_core.us_strategy import USConfig, simulate_us


@dataclass(frozen=True)
class ChinaPortfolioConfig:
    """Balanced candidate, not hindsight-optimized or proven out of sample."""
    bond_cap: float = 0.35
    gold_cap: float = 0.20
    tech_max: float = 0.55
    allocation: str = 'inverse_vol'
    priority_exit: bool = True
    execution_deadband: float = 0.15
    portfolio_vol_brake: bool = True

    def to_trend_config(self) -> ChinaTrendConfig:
        if not (0 <= self.bond_cap <= .5 and 0 <= self.gold_cap <= .3):
            raise ValueError('bond/gold cap out of range')
        if not (.05 <= self.tech_max <= 1.0):
            raise ValueError('invalid tech cap')
        if not (0 <= self.execution_deadband <= .4):
            raise ValueError('invalid turnover threshold')
        if self.allocation not in ('inverse_vol','capped'):
            raise ValueError('invalid allocation')
        return ChinaTrendConfig(bond_cap=self.bond_cap,gold_cap=self.gold_cap,
            tech_max=self.tech_max,allocation=self.allocation,
            priority_exit=self.priority_exit,
            execution_deadband=self.execution_deadband,
            portfolio_vol_brake=self.portfolio_vol_brake)


def build_china_portfolio_weekly(prices: pd.DataFrame, market: pd.DataFrame,
                       us_prices: pd.DataFrame,
                       cfg: ChinaPortfolioConfig=ChinaPortfolioConfig()):
    """Return MultiAssetConfig, CN signals and weekly simulation; Trend code is reused."""
    return build_china_trend_weekly(prices,market,us_prices,cfg.to_trend_config())


@dataclass(frozen=True)
class USPortfolioConfig:
    qqq_core: float = 0.30
    ma_windows: tuple[int, ...] = (10,20,30)
    hysteresis: float = .005
    vol_cap: float = .24
    vol_window: int = 13
    min_vol_scale: float = .30
    rebalance_threshold: float = .20
    urgent_cut_pp: float = .04
    priority_exit: bool = False  # Ex ante unvalidated; opt in only.
    fill_mode: str = 'next_week_open'
    qqq_side_fee: float = .001
    bil_side_fee: float = .0002

    def to_trend_config(self) -> USTrendConfig:
        if not isinstance(self.ma_windows, tuple) or len(self.ma_windows) < 2 or len(set(self.ma_windows)) != len(self.ma_windows):
            raise ValueError('Need at least two distinct MA windows')
        if not (8 <= self.vol_window <= 52):
            raise ValueError('vol_window must be 8..52')
        if not (0 <= self.urgent_cut_pp <= .30):
            raise ValueError('urgent cut must be 0..0.3')
        if not (0 <= self.qqq_side_fee <= .03 and 0 <= self.bil_side_fee <= .03):
            raise ValueError('invalid fees')
        if self.fill_mode not in ('next_week_open','next_week_close'):
            raise ValueError('invalid fill mode')
        c=USTrendConfig(qqq_core=self.qqq_core, ma_windows=self.ma_windows,
            hysteresis=self.hysteresis,vol_window=self.vol_window,vol_cap=self.vol_cap,
            min_vol_scale=self.min_vol_scale,rebalance_threshold=self.rebalance_threshold,
            qqq_side_fee=self.qqq_side_fee,bil_side_fee=self.bil_side_fee,
            fill_mode=self.fill_mode)
        c.validate()
        return c


def build_us_portfolio(p: pd.DataFrame, cfg: USPortfolioConfig=USPortfolioConfig(),
                 opens: pd.DataFrame|None=None):
    """Gemini Trend trend + optional exit-first execution, no leverage."""
    conf=cfg.to_trend_config()
    sig=build_us_trend_signals(p,conf)
    if not cfg.priority_exit:
        return sig,simulate_us_trend(p,conf,opens)[1]
    if cfg.fill_mode=='next_week_open':
        if opens is None:
            from strategy_core.us_snapshot import load_us_open
            opens=load_us_open()
        if not opens.index.equals(p.index):
            raise ValueError('US opens date mismatch')
    if not p.index.equals(sig.index):
        raise ValueError('US signal date mismatch')
    held=np.array([cfg.qqq_core,1-cfg.qqq_core],float)
    rows=[]
    for i,date in enumerate(p.index):
        if i==0:
            nav=1.0;cost=turn=0.0;urgent=False
        else:
            prev=p.iloc[i-1][['QQQ','BIL']].to_numpy(float)
            close=p.iloc[i][['QQQ','BIL']].to_numpy(float)
            op=(opens.loc[date,['QQQ','BIL']].to_numpy(float) if cfg.fill_mode=='next_week_open' else close)
            target=sig.iloc[i-1][['target_qqq','target_bil']].to_numpy(float)
            gross=held*op/prev
            gv=float(gross.sum())
            actual=gross/gv
            urn=float(actual[0]-target[0])
            urgent=urn > cfg.urgent_cut_pp+1e-12
            turn=float(np.abs(target-actual).sum())
            if urgent or turn >= cfg.rebalance_threshold:
                cost=gv*float(np.abs(target-actual)@np.array([cfg.qqq_side_fee,cfg.bil_side_fee]))
                held=target*(gv-cost)
            else:
                cost=0.;held=gross;turn=0.
            if cfg.fill_mode=='next_week_open':held=held*close/op
            nav=float(held.sum())
        if not np.isfinite(nav) or nav<=0 or not np.isclose(held.sum(),nav):
            raise AssertionError('budget violation')
        rows.append({'week_start':date,'nav':nav,'held_qqq':held[0]/nav,'held_bil':held[1]/nav,
            'cost':cost,'turnover':turn,'risk_priority_exit':urgent,
            'source_signal_week':p.index[i-1] if i>0 else pd.NaT})
    return sig,pd.DataFrame(rows).set_index('week_start')
