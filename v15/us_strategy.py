"""Standalone U.S. tactical strategy: 30-week Nasdaq trend and optional QQQ core.

Only weekly Friday-closing data is used for a signal. Fills at the NEXT weekly
close, matching the original V14 backtest execution convention. No shorting.
The U.S. 'volume/crowding' overlay is deliberately *not* forced into the base
strategy because it failed the earlier comparative backtest.
"""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import pandas as pd

@dataclass(frozen=True)
class USConfig:
    qqq_core:float=.30
    trend_weeks:int=30
    qqq_side_fee:float=.001
    bil_side_fee:float=.0002
    use_leadership_freeze:bool=False
    rebalance_threshold:float=.04
    fill_mode:str="next_week_open"


def us_factors(p:pd.DataFrame)->pd.DataFrame:
    for k in ['QQQ','SPY','SMH','RSP','BIL']:
        if k not in p:raise ValueError('missing US price: '+k)
    if p.index.has_duplicates or not p.index.is_monotonic_increasing or p.le(0).any().any() or p.isna().any().any():
        raise ValueError('invalid US input')
    out=pd.DataFrame(index=p.index)
    out['qqq_10w']=p.QQQ.rolling(10,min_periods=10).mean()
    out['qqq_30w']=p.QQQ.rolling(30,min_periods=30).mean()
    out['spy_13w']=p.SPY.rolling(13,min_periods=13).mean()
    out['spy_30w']=p.SPY.rolling(30,min_periods=30).mean()
    out['qqq_1w']=p.QQQ.pct_change(1,fill_method=None)
    out['qqq_4w']=p.QQQ.pct_change(4,fill_method=None)
    out['spy_4w']=p.SPY.pct_change(4,fill_method=None)
    out['smh_relative_4w']=(p.SMH/p.QQQ).pct_change(4,fill_method=None)
    out['rsp_relative_4w']=(p.RSP/p.SPY).pct_change(4,fill_method=None)
    out['above_30w']=p.QQQ>out.qqq_30w
    out['us_bear']=out.qqq_30w.notna() & (p.QQQ<out.qqq_30w) & (p.SPY<out.spy_13w)
    out['us_shock']=(out.qqq_1w<=-.045) | ((out.smh_relative_4w<=-.08) & (p.QQQ<out.qqq_10w))
    out['us_warning']=(out.smh_relative_4w<=-.05) & (out.rsp_relative_4w>=.03)
    # A warning does not prove a market top: it can only limit *new* QQQ additions.
    out['us_risk_state']=np.select([out.us_shock.fillna(False)&out.us_bear.fillna(False),out.us_bear.fillna(False),out.us_shock.fillna(False)|out.us_warning.fillna(False)],['严重风险','熊市趋势','科技退潮预警'],default='趋势正常')
    return out


def us_signals(p:pd.DataFrame,c:USConfig=USConfig())->pd.DataFrame:
    if not (0<=c.qqq_core<=1 and 10<=c.trend_weeks<=52 and 0<=c.qqq_side_fee<.03 and 0<=c.bil_side_fee<.03 and 0<=c.rebalance_threshold<=.3):
        raise ValueError('US configuration invalid')
    f=us_factors(p)
    slow=p.QQQ.rolling(c.trend_weeks,min_periods=c.trend_weeks).mean()
    w=pd.Series(np.where(slow.notna() & (p.QQQ>slow),1.,c.qqq_core),index=p.index)
    if c.use_leadership_freeze:
        # Suppress increases only, never force a sell based on volume/breadth.
        for i in range(1,len(w)):
            if bool(f.us_warning.iloc[i]) and w.iloc[i]>w.iloc[i-1]:
                w.iloc[i]=max(c.qqq_core,w.iloc[i-1])
    f['target_qqq']=w
    f['target_bil']=1-w
    f['us_trend_decision']=np.where(slow.notna() & (p.QQQ>slow),'30周趋势持有','30周趋势防守')
    return f


def simulate_us(p:pd.DataFrame,signals:pd.DataFrame,c:USConfig=USConfig(),opens:pd.DataFrame|None=None)->pd.DataFrame:
    if not p.index.equals(signals.index):raise ValueError('US signal dates do not match')
    if c.fill_mode not in ('next_week_open','next_week_close'):raise ValueError('unknown fill mode')
    if c.fill_mode=='next_week_open':
        if opens is None:
            from v15.us_snapshot import load_us_open
            opens=load_us_open()
        if not opens.index.equals(p.index):raise ValueError('US opens date mismatch')
    # Wealth is tracked as asset market values, with no hidden cash/lending.
    held=np.array([c.qqq_core,1-c.qqq_core],dtype=float)
    rows=[]
    for i,(date,row) in enumerate(p.iterrows()):
        if i==0:
            nav=float(held.sum());cost=0.;turn=0.
        else:
            pc=np.array([p.QQQ.iloc[i-1],p.BIL.iloc[i-1]])
            cp=np.array([p.QQQ.iloc[i],p.BIL.iloc[i]])
            target=signals.loc[p.index[i-1],['target_qqq','target_bil']].to_numpy(float)
            if c.fill_mode=='next_week_open':
                op=np.array([opens.QQQ.iloc[i],opens.BIL.iloc[i]])
                gross=held*op/pc
            else:
                op=cp
                gross=held*cp/pc
            nav_gross=float(gross.sum());actual=gross/nav_gross
            turn=float(np.abs(target-actual).sum())
            if turn>=c.rebalance_threshold:
                cost=nav_gross*(abs(target[0]-actual[0])*c.qqq_side_fee+abs(target[1]-actual[1])*c.bil_side_fee)
                held=target*(nav_gross-cost)
            else:
                cost=0.;held=gross;turn=0.
            if c.fill_mode=='next_week_open':held=held*cp/op
            nav=float(held.sum())
        if abs(held.sum()-nav)>1e-8:raise AssertionError('US wealth non-conservation')
        rows.append({'week_start':date,'nav':nav,'held_qqq':held[0]/nav,'held_bil':held[1]/nav,'cost':cost,'turnover':turn,'source_signal_week':p.index[i-1] if i>0 else pd.NaT})
    return pd.DataFrame(rows).set_index('week_start')
