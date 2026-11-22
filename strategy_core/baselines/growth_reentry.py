"""GrowthReentry: bounded re-entry of the ChiNext ETF on top of CashSleeve dividend/money allocation.

Two auditable paths:
  1. Reversal probe after a substantial decline has visibly stabilized.
  2. Confirmed relative-momentum/trend breakout, including a fast V-shaped rebound.

Uses *only closes available at time t*. The shared CashSleeve simulator fills target t
at close t+1. This research model is not a copy of a proprietary trading system.
"""
from __future__ import annotations
from dataclasses import dataclass, replace
import numpy as np
import pandas as pd
from strategy_core.baselines.trend_rotation import TrendRotationConfig
from strategy_core.baselines.dividend_overlay import DividendOverlayConfig
from strategy_core.baselines.cash_sleeve import CashSleeveConfig, build_cash_sleeve_signals, simulate_cash_sleeve

@dataclass(frozen=True)
class GrowthReentryConfig:
    probe_weight: float = 0.12
    confirmed_weight: float = 0.38
    winning_weight: float = 0.52
    min_reversal_drawdown: float = -0.12
    rebound_from_low: float = 0.075
    relative_confirm: float = 0.02
    stop_from_entry: float = -0.085
    stop_from_peak: float = -0.12
    cooldown_bars: int = 2
    require_two_probe_closes: bool = True
    enable_probe: bool = True
    enable_v_reentry: bool = False
    enable_relative: bool = True
    # preserve a hard dividend core and protect against portfolio over-allocation


def _ma(series, n):
    return series.rolling(n, min_periods=n).mean()


def _ret(series, n):
    return series.pct_change(n,fill_method=None)


def validate_growth_reentry(c: GrowthReentryConfig, cash_cfg: CashSleeveConfig) -> None:
    if not (0 <= c.probe_weight <= c.confirmed_weight <= c.winning_weight <= 1-cash_cfg.red_core + 1e-12):
        raise ValueError('必须满足：0 ≤ 试仓 ≤ 确认仓 ≤ 盈利加仓 ≤ 1-红利核心仓')
    if not (-.6 < c.min_reversal_drawdown <= -.03 and .01 <= c.rebound_from_low <= .4):
        raise ValueError('反转阈值超出合理范围')
    if not (0 <= c.relative_confirm <= .3 and -.5 < c.stop_from_entry < 0 and -.5 < c.stop_from_peak < 0):
        raise ValueError('确认/止损阈值不合法')
    if not (1 <= c.cooldown_bars <= 16):
        raise ValueError('冷却期必须是1—16个bar')


def build_growth_reentry_signals(prices: pd.DataFrame, base: TrendRotationConfig, model: DividendOverlayConfig,
               cash: CashSleeveConfig, c: GrowthReentryConfig=GrowthReentryConfig()) -> pd.DataFrame:
    """Overlay reentry signals onto CashSleeve without losing the dividend core.

    The baseline CashSleeve has a tradeable cash sleeve. GrowthReentry shifts cash and optionally
    the *tactical* part of dividends (never the core) to technology after
    additional right-side confirmation. It can take exposure before CashSleeve's
    original trend state machine but only with a small conditional probe.
    """
    validate_growth_reentry(c,cash)
    f=build_cash_sleeve_signals(prices,base,model,cash).copy()
    p=prices.tech.astype(float)
    red=prices.red.astype(float)
    m4=_ma(p,4);m6=_ma(p,6);m10=_ma(p,10);m30=_ma(p,30)
    ret2=_ret(p,2);ret4=_ret(p,4);ret6=_ret(p,6);ret12=_ret(p,12)
    rel4=ret4-_ret(red,4)
    rel6=ret6-_ret(red,6)
    high26=p.rolling(26,min_periods=26).max()
    low12=p.rolling(12,min_periods=12).min()
    high8=p.shift(1).rolling(8,min_periods=8).max()
    # Causal and explicit: a 'previous 8-week high' never includes future closes.
    dd26=p/high26-1
    rebound=p/low12-1
    above_ma4=p>m4
    probe=(dd26<=c.min_reversal_drawdown)&(rebound>=c.rebound_from_low)&above_ma4& (m4>m4.shift(1))&(ret2>0)
    if c.require_two_probe_closes:
        probe &= (p.shift(1)>m4.shift(1))
    probe &= (rel4>-.035)
    confirm=(p>m10)&(p>m30)&(ret6>0)&(ret12>0)
    if c.enable_relative:
        confirm &= (rel6>c.relative_confirm)
    v_break=(p>high8)&(p>m10)&(ret4>.09)&(rel4>.045)
    # V-shape reentry is a separate route; confirmed MA30 not required.
    if not c.enable_v_reentry:
        v_break &= False
    cool=0;mode='off';entry=0.;peak=0.;weeks_live=0
    targets=[]
    for i in range(len(f)):
        t=float(f.target_tech.iloc[i]);r=float(f.target_red.iloc[i]);px=float(p.iloc[i])
        why=[];path='off';shift=0.
        if i < max(31,base.long_ma):
            targets.append((t,r,1-t-r,'原CashSleeve预热', 'off', 0.,False,False))
            continue
        crash=bool(_ret(p,1).iloc[i] <= base.tech_exit_shock)
        fast_broken=bool((p.iloc[i]<m4.iloc[i]) and (p.iloc[i-1]<m4.iloc[i-1]))
        stop_entry=mode!='off' and px/entry-1 <= c.stop_from_entry
        stop_peak=mode!='off' and px/peak-1 <= c.stop_from_peak
        baseline_hard_exit=('tech风控退出' in str(f.reason.iloc[i]))
        hard=bool(crash or baseline_hard_exit or ((stop_entry or stop_peak) and px<m6.iloc[i]) or (fast_broken and mode!='off'))
        if hard and mode!='off':
            why.append('试仓/趋势失效，退出增量科技');mode='off';cool=c.cooldown_bars;entry=0.;peak=0.;weeks_live=0
        if crash or baseline_hard_exit:
            cool=max(c.cooldown_bars,cool)
        eligible_confirm=bool(confirm.iloc[i]);eligible_v=bool(v_break.iloc[i])
        eligible_probe=c.enable_probe and bool(probe.iloc[i]);
        if mode=='off' and cool==0 and not crash:
            if eligible_confirm or eligible_v:
                mode='confirm';entry=px;peak=px;weeks_live=0
                why.append('V形突破' if eligible_v and not eligible_confirm else '双重动量趋势确认')
            elif eligible_probe:
                mode='probe';entry=px;peak=px;weeks_live=0
                why.append('低位右侧企稳，限额试仓')
        elif mode!='off':
            weeks_live+=1;peak=max(peak,px)
            if (eligible_confirm or eligible_v) and mode=='probe':
                mode='confirm';why.append('试仓升级：趋势确认')
            if (not (eligible_confirm or eligible_v) and mode=='probe') and weeks_live>=4 and p.iloc[i]<m4.iloc[i]:
                why.append('试仓超时且跌破4周线');mode='off';cool=c.cooldown_bars;entry=0.;peak=0.;weeks_live=0
            elif mode=='confirm' and px < m10.iloc[i] and ret4.iloc[i]<0 and (not eligible_confirm):
                # May retain original CashSleeve exposure if the CashSleeve state machine is still live.
                why.append('确认趋势弱化，取消增量');mode='off';cool=c.cooldown_bars;entry=0.;peak=0.;weeks_live=0
        if mode=='probe':want=c.probe_weight;path='反转试仓'
        elif mode=='confirm':
            want=c.confirmed_weight;path='趋势确认'
            if entry>0 and px/entry-1 >=.045 and px>m6.iloc[i] and rel6.iloc[i]>.015:
                want=c.winning_weight;path='盈利加仓'
        else:want=0.
        # Don't force an immediate tech re-entry against an acute crash.
        if crash or baseline_hard_exit:want=0.
        if want>t:
            # Preserve red_core, but allow spending *tactical* red to follow tech.
            new_t=min(want,1-cash.red_core)
            new_r=min(r,1-new_t)
            new_r=max(cash.red_core,new_r)
            new_t=min(new_t,1-new_r)
            shift=max(0,new_t-t)
            t=new_t;r=new_r
            why.append('由货币ETF或红利战术仓转入科技')
        if not why:why.append('保留CashSleeve目标')
        targets.append((t,r,max(0,1-t-r),'；'.join(why),path,shift,eligible_probe,eligible_confirm or eligible_v))
        if cool>0:cool-=1
    f[['target_tech','target_red','target_cash','reentry_reason','reentry_state','reentry_increment','reentry_probe_signal','reentry_confirm_signal']]=pd.DataFrame(targets,index=f.index)
    w=f[['target_tech','target_red','target_cash']]
    if not np.allclose(w.sum(axis=1),1) or (w < -1e-9).any().any() or (w>1+1e-9).any().any():
        raise AssertionError('仓位约束失效')
    if (f.target_red < cash.red_core-1e-9).any():
        raise AssertionError('常驻红利底仓未保留')
    return f


def simulate_growth_reentry(prices: pd.DataFrame, base: TrendRotationConfig, model: DividendOverlayConfig,
                cash: CashSleeveConfig,c:GrowthReentryConfig=GrowthReentryConfig()) -> pd.DataFrame:
    signals=build_growth_reentry_signals(prices,base,model,cash,c)
    return simulate_cash_sleeve(prices,base,model,cash,signals)
