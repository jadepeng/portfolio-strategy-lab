"""2026-only execution research, no forward price access.
Weekly allocation signals available strictly after last session of the week;
independent risk exit may be generated on any daily close and execute next open.
This is NOT an implementation of Nick's private 趋势动物 signals.
"""
from dataclasses import dataclass
import numpy as np,pandas as pd
from v13.daily_ohlc_2026 import load
from history.strategies.fusion_v10 import PRICES,TARGETS,V10Config

@dataclass(frozen=True)
class DailyExitConfig:
    use_stop:bool=True
    dd_stop:float=.07
    shock:float=-.055
    sma_weeks:int=2 # daily 10-day sma
    rel_window:int=5
    relative_guard:bool=True
    min_exit:float=0.04
    reentry_streak:int=5
    red_fast:bool=False # experimental; not validated
    red_start:float=.55
    red_relative:float=.04
    red_mom5:float=.01
    profit_lock:bool=False
    lock_activation:float=.10
    lock_max_drop:float=.025
    turnover_deadband:float=.12

# Avoid construction-specific walrus/type annotations in dataclass namespace.

def simulate(p,signals,weekly_run,c:DailyExitConfig=DailyExitConfig(),fees:V10Config=V10Config()):
    data=load();dates=data.index
    mon=pd.Series(dates-pd.to_timedelta(dates.weekday,unit='D'),index=dates)
    opens=data[['tech_open','red_open','bond_open','gold_open','money_open']].to_numpy(float)
    closes=data[['tech_close','red_close','bond_close','gold_close','money_close']].to_numpy(float)
    t=data.tech_close;r=data.red_close
    ma_t=t.rolling(10,min_periods=10).mean();ma_r=r.rolling(10,min_periods=10).mean()
    draw=t/t.rolling(20,min_periods=20).max()-1
    rel5=t.pct_change(c.rel_window,fill_method=None)-r.pct_change(c.rel_window,fill_method=None)
    rr5=r.pct_change(5,fill_method=None)
    prev_close=p.loc[:'2025'].iloc[-1][list(PRICES)].to_numpy(float)
    last=weekly_run.loc[:'2025'].iloc[-1]
    nav0=float(last.nav)
    holdings=last[['held_tech','held_red','held_bond','held_gold','held_money']].to_numpy(float)*nav0
    fee_arr=np.array([fees.tech_fee,fees.red_fee,fees.bond_fee,fees.gold_fee,fees.money_fee])
    last_known_key=signals.index[signals.index<dates[0]][-1]
    weekly_desired=signals.loc[last_known_key,list(TARGETS)].to_numpy(float)
    # No reference to signals for the *current* week before its final close.
    ban=False;ban_days=0;red_override=False;red_age=0;profit_locked=False
    rows=[]
    for i,d in enumerate(dates):
        holdings*=opens[i]/prev_close
        gross=float(holdings.sum());cur=holdings/gross
        des=weekly_desired.copy()
        if ban:des[4]+=des[0];des[0]=0.
        if red_override:
            amount=max(0,min(c.red_start-des[1],des[4]-.05))
            des[1]+=amount;des[4]-=amount
        if profit_locked:
            des[4]+=des[0]+des[3];des[0]=0;des[3]=0
            if des[1]>.3:des[4]+=des[1]-.3;des[1]=.3
        to=np.abs(cur-des)
        priority=bool(ban and cur[0]-des[0]>c.min_exit)
        if to.sum()>=c.turnover_deadband or priority:
            cost=float(to@fee_arr)
            holdings=des*gross*(1-cost)
            traded=float(to.sum())
        else:cost=0.;traded=0.
        holdings*=closes[i]/opens[i]
        nav=float(holdings.sum())
        # Close-of-day risk monitoring (only prior and current daily observations).
        emergency=bool(c.use_stop and i>=20 and
             (t.pct_change(fill_method=None).iloc[i]<=c.shock or
              (draw.iloc[i]<=-c.dd_stop and t.iloc[i]<ma_t.iloc[i] and
               (not c.relative_guard or rel5.iloc[i]<0))))
        if emergency:ban=True;ban_days=0
        elif ban:ban_days+=1
        # A red acceleration signal is a separate, optional hypothesis.
        red_candidate=bool(i>=20 and c.red_fast and rr5.iloc[i]>=c.red_mom5 and -rel5.iloc[i]>=c.red_relative and r.iloc[i]>ma_r.iloc[i] and (ban or weekly_desired[0]<.15))
        if red_candidate:red_override=True;red_age=0
        elif red_override:
            red_age+=1
            if (r.iloc[i]<ma_r.iloc[i] and rr5.iloc[i]<0) or red_age>=15:red_override=False
        # Weekly signals reveal at the end of the final real market session.
        is_final=(i==len(dates)-1 or mon.iloc[i+1]!=mon.iloc[i])
        if is_final:
            last_known_key=mon.iloc[i]
            weekly_desired=signals.loc[last_known_key,list(TARGETS)].to_numpy(float)
            # Re-entry to an asset after a stop requires a *new* full-week signal
            # and daily price recovery; no rebuying from stale targets.
            if ban and ban_days>=c.reentry_streak and signals.loc[last_known_key,'tech_stage']>=5 and t.iloc[i]>ma_t.iloc[i] and rel5.iloc[i]>=0:
                ban=False
        if c.profit_lock and nav/nav0>=1+c.lock_activation: # activation at actual close
            # Trailing lock is activated as soon as high-water reaches threshold.
            pass
        if c.profit_lock:
            # All previous highs only, no current week's future returns.
            high=max([nav0]+[v['nav'] for v in rows]+[nav])
            if high/nav0>=1+c.lock_activation and nav/high-1<-c.lock_max_drop:profit_locked=True
        rows.append(dict(date=d,nav=nav,tech=holdings[0]/nav,red=holdings[1]/nav,bond=holdings[2]/nav,gold=holdings[3]/nav,money=holdings[4]/nav,fee=cost,turnover=traded,stop=emergency,ban=ban,red_override=red_override,profit_locked=profit_locked))
        prev_close=closes[i]
    return pd.DataFrame(rows).set_index('date'),100*(rows[-1]['nav']/nav0-1)
