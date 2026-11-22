"""Point-in-time A-share OHLC daily replay with US overnight emergency risk.

A-share Friday-close signals are filled on the NEXT A-share week's FINAL
trading close, to match the USBase weekly baseline. US overnight emergency
reductions trade at the NEXT Chinese market open after 16:00 New York close.
No FX pooling; risky action sells only 159915.SZ (ChiNext) and buys 511880.SH.

Initial 2026 holdings must be obtained from the *prior year* USBase weekly ledger;
the 2026 backtest never initializes from a year-end target or uses later prices.
"""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import pandas as pd
from strategy_core.baselines.multi_asset import MultiAssetConfig,TARGETS
from strategy_core.overnight import DailyGateConfig,load_event_snapshot,align_events_to_cn_open

_DAILY_KEYS=('tech','red','bond','gold','money')


def annual_stats(nav:pd.Series)->dict:
    p=nav.copy(); d=p/p.cummax().clip(lower=1)-1
    return dict(annual_pct=100*(p.iloc[-1]-1),mdd_pct=100*d.min(),last_nav=float(p.iloc[-1]))


def run_daily_replay(daily:pd.DataFrame,weekly_targets:pd.DataFrame,prior_weights:np.ndarray,
                     daily_config:DailyGateConfig=DailyGateConfig(),
                     weekly_config:MultiAssetConfig=MultiAssetConfig(),events:pd.DataFrame|None=None,
                     prior_close:np.ndarray|None=None)->pd.DataFrame:
    daily_config.validate()
    if events is None:events=load_event_snapshot()
    if not daily.index.is_monotonic_increasing or daily.index.has_duplicates:raise ValueError('daily calendar')
    if not set(TARGETS).issubset(weekly_targets.columns):raise ValueError('weekly target fields')
    if weekly_targets.index.has_duplicates or not weekly_targets.index.is_monotonic_increasing:raise ValueError('weekly signals')
    cols_open=[k+'_open' for k in _DAILY_KEYS];cols_close=[k+'_close' for k in _DAILY_KEYS]
    if not set(cols_open+cols_close).issubset(daily):raise ValueError('OHLC fields')
    if daily[cols_open+cols_close].isna().any().any() or (daily[cols_open+cols_close]<=0).any().any():raise ValueError('invalid daily prices')
    weights=np.asarray(prior_weights,dtype=float).copy()
    if len(weights)!=5 or np.any(weights<0) or not np.isclose(weights.sum(),1):raise ValueError('prior weights')
    fees=np.array([weekly_config.tech_fee,weekly_config.red_fee,weekly_config.bond_fee,weekly_config.gold_fee,weekly_config.money_fee]);
    ev=align_events_to_cn_open(daily.index,events)
    # Chinese week label (Monday) and its actual final trading day (holiday safe).
    weeks=daily.index.to_period('W-SUN').start_time
    lastday=pd.Series(daily.index,index=weeks).groupby(level=0,sort=True).max()
    last_day_to_week={pd.Timestamp(v):pd.Timestamp(k) for k,v in lastday.items()}
    held=weights.copy()  # normalized at the first available CN open
    prevclose=np.asarray(prior_close,dtype=float) if prior_close is not None else None
    if prevclose is not None and (prevclose.shape!=(5,) or (prevclose<=0).any()):raise ValueError('prior close')
    lock_until=-1
    max_tech_allowed=None
    live_severity='N'
    rows=[]
    idx=daily.index
    for i,today in enumerate(idx):
        x=daily.iloc[i]
        op=x[cols_open].to_numpy(float);cl=x[cols_close].to_numpy(float)
        if prevclose is not None:held*=op/prevclose
        value_open=float(held.sum())
        if not np.isfinite(value_open) or value_open<=0:raise ValueError('invalid gross wealth')
        event=ev.loc[today] if today in ev.index else None
        level=str(event['level']) if event is not None else 'N'
        source_us=(pd.Timestamp(event['us_session']) if event is not None else pd.NaT)
        available=(event['available_at_cn'] if event is not None else pd.NaT)
        emergency_cost=0.;weekly_cost=0.;emergency_turnover=0.;weekly_turnover=0.
        reason=[]
        if i>lock_until:
            max_tech_allowed=None
            live_severity='N'
        # A single severe new episode halves existing tech once; subsequent severe
        # US closes extend the freeze, not repeatedly halve it every morning.
        local_weakness=(i>=daily_config.cn_lookback and
                        daily.iloc[i-1].tech_close < daily.iloc[i-daily_config.cn_lookback:i].tech_close.mean())
        if daily_config.require_local_weakness and not local_weakness:
            # Daily CN confirmation must come from LAST trading day's close.
            # Do not block weekly trend-based buying solely on a US selloff.
            level='N'
        if daily_config.mode!='off' and level in ('M','S'):
            lock_until=max(lock_until,i+(daily_config.severe_freeze_days if level=='S' else daily_config.moderate_freeze_days)-1)
            actual_before=held/value_open
            if max_tech_allowed is None: max_tech_allowed=float(actual_before[0])
            if level=='S' and live_severity!='S' and daily_config.mode in ('halve','full_exit'):
                old_weight=float(actual_before[0])
                desired=0. if daily_config.mode=='full_exit' else old_weight*.5
                cut=old_weight-desired
                max_tech_allowed=min(max_tech_allowed,desired)
                if cut>=daily_config.min_cut_pp:
                    neww=actual_before.copy();neww[0]=desired;neww[4]+=cut
                    turnover=float(np.abs(neww-actual_before).sum())
                    fee=float(np.abs(neww-actual_before)@ (fees+daily_config.emergency_fee_slippage))
                    emergency_cost=value_open*fee
                    held=neww*(value_open-emergency_cost)
                    emergency_turnover=turnover
                    reason.append(f'US严重风险开盘减科技 {old_weight:.2%}→{desired:.2%}')
            if level=='S':live_severity='S'
            elif live_severity=='N':live_severity='M'
            reason.append('美股隔夜预警：冻结创业板新增仓')
        # All daily open positions marked to daily close; overlay never trades
        # shares at a price observed later than its signal time.
        held=held*cl/op
        closevalue=float(held.sum())
        if today in last_day_to_week:
            currweek=last_day_to_week[today]
            previous=weekly_targets.index[weekly_targets.index<currweek]
            if len(previous):
                target=weekly_targets.loc[previous[-1],list(TARGETS)].to_numpy(float).copy()
                if daily_config.mode!='off' and i<=lock_until and max_tech_allowed is not None:
                    original=float(target[0]);target[0]=min(float(target[0]),max_tech_allowed);target[4]+=original-target[0]
                    if original>target[0]+1e-9:reason.append('周频新增科技仓冻结')
                originalw=held/closevalue
                turn=float(np.abs(originalw-target).sum())
                # USBase weekly default: execute only once a week with same 12%
                # two-sided no-trade band; `priority_exit` remains disabled.
                if turn>=weekly_config.execution_deadband:
                    fee=float(np.abs(originalw-target)@fees)
                    weekly_cost=closevalue*fee
                    held=target*(closevalue-weekly_cost)
                    weekly_turnover=turn
                    reason.append('前一周信号：本周收盘调仓')
        nav=float(held.sum());prevclose=cl
        w=held/nav
        if np.any(w<-1e-9) or abs(w.sum()-1)>1e-8:raise AssertionError('budget drift')
        rows.append(dict(date=today,nav=nav,held_tech=w[0],held_red=w[1],held_bond=w[2],held_gold=w[3],held_money=w[4],
                         us_source_session=source_us,us_event_level=level,us_available_cn=available,local_cn_weakness=local_weakness,
                         emergency_cost=emergency_cost,weekly_cost=weekly_cost,emergency_turnover=emergency_turnover,
                         weekly_turnover=weekly_turnover,lock_until_index=lock_until,overlay_active=bool(i<=lock_until and daily_config.mode!='off'),
                         reason='; '.join(reason) if reason else '不调仓'))
    return pd.DataFrame(rows).set_index('date')
