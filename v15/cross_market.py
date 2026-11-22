"""Causal U.S.-to-China equity risk overlay.

U.S. *Friday* close occurs after the Chinese *Friday* market close.
Accordingly we strictly shift US weekly indicators by one completed week
before using them for a Chinese Friday-close signal. A-share original V14
five-asset weights remain primary; the U.S. gate only blocks ADDITIONAL
ChiNext purchases and routes suppressed budget into the RMB money ETF.
Different currencies are NOT pooled or traded as one self-financing account.
"""
from dataclasses import dataclass
import numpy as np
import pandas as pd
from fusion_v10 import TARGETS
from v15.us_strategy import us_factors

@dataclass(frozen=True)
class CrossConfig:
    enabled:bool=True
    bear_new_entry_cap:float=.12
    shock_new_entry_cap:float=.08
    warning_new_entry_cap:float=.20
    require_cn_local_weakness:bool=False


def observed_us_by_cn_week(cn_index:pd.DatetimeIndex,us_prices:pd.DataFrame)->pd.DataFrame:
    """US risk from the previous full US week ONLY, never same-week Friday."""
    if not cn_index.is_monotonic_increasing or cn_index.has_duplicates:raise ValueError('CN bad dates')
    r=us_factors(us_prices)
    # Reindex to union of calendars BEFORE ffill; a missing US holiday week
    # may not silently transform a Friday-close signal into an earlier reading.
    prev=r.shift(1).reindex(cn_index,method='ffill')
    # Note: source available date is the Monday of the previous US market week.
    source=pd.Series(us_prices.index,index=us_prices.index).shift(1)
    prev['us_source_week']=source.reindex(cn_index,method='ffill')
    return prev


def us_to_cn_overlay(cn_prices:pd.DataFrame,cn_v14:pd.DataFrame,us_prices:pd.DataFrame,c:CrossConfig=CrossConfig())->pd.DataFrame:
    if not cn_prices.index.equals(cn_v14.index):raise ValueError('CN strategy price/signal mismatch')
    if not set(TARGETS).issubset(cn_v14.columns):raise ValueError('missing 5 CN targets')
    if not (0<=c.shock_new_entry_cap<=c.bear_new_entry_cap<=1 and 0<=c.warning_new_entry_cap<=1):
        raise ValueError('invalid spillover caps')
    seen=observed_us_by_cn_week(cn_prices.index,us_prices)
    out=cn_v14.copy()
    w=out[list(TARGETS)].to_numpy(float).copy()
    origin=w.copy();why=[]
    last_tech=0.
    for i in range(len(w)):
        obs=seen.iloc[i]
        risk=bool(obs.us_bear) if pd.notna(obs.us_bear) else False
        shock=bool(obs.us_shock) if pd.notna(obs.us_shock) else False
        warn=bool(obs.us_warning) if pd.notna(obs.us_warning) else False
        if c.require_cn_local_weakness and 'v14_market_regime' in out:
            # A bullish China market can justify retaining its independent strength.
            if str(out.iloc[i]['v14_market_regime'])=='放量牛市':risk=shock=warn=False
        # Do not reduce existing target from the prior CN week merely because the US warning changed.
        target=float(w[i,0]);cap=None
        if c.enabled and pd.notna(obs.us_source_week):
            if shock and risk:cap=c.shock_new_entry_cap
            elif risk:cap=c.bear_new_entry_cap
            elif shock or warn:cap=c.warning_new_entry_cap
        if cap is not None:
            cap=max(cap,last_tech)
            w[i,0]=min(target,cap)
            w[i,4]+=target-w[i,0]
        reason=('US严重风险：冻结新增创业板仓' if shock and risk else 'US熊市：限制新增创业板仓' if risk else 'US科技退潮：暂停追仓' if shock or warn else '无跨市场限制') if cap is not None else '中国原策略执行'
        why.append(reason)
        last_tech=float(w[i,0])
    if (w< -1e-8).any() or not np.allclose(w.sum(axis=1),1,atol=1e-8):
        raise AssertionError('CN budget violation')
    out[list(TARGETS)]=w
    out['us_risk_state_lagged']=seen.us_risk_state.values
    out['us_source_week']=seen.us_source_week.values
    out['us_qqq_1w_lagged']=seen.qqq_1w.values
    out['us_qqq_below30_lagged']=((seen['above_30w']==False) & seen['qqq_30w'].notna()).values
    out['cross_market_reason']=why
    out['cross_market_tech_cut']=origin[:,0]-w[:,0]
    return out
