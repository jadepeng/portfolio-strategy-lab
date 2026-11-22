"""Research-only year-reset trailing profit protection; weekly close-to-next close.
Portfolio peak and current NAV must be observed at close t, so lock starts at t+1.
The rule is not Nick's described trend-following exit, but a user risk-budget overlay.
"""
from dataclasses import dataclass,replace
import numpy as np,pandas as pd
from history.strategies.fusion_v10 import PRICES,TARGETS,V10Config,validate

@dataclass(frozen=True)
class ProfitGuard:
    activation:float=.10
    giveback:float=.025
    red_core:float=.30
    exit_gold:bool=True
    stop_tech:bool=True
    annual_reset:bool=True

def simulate(prices,signals,fee:V10Config=V10Config(),c:ProfitGuard=ProfitGuard()):
    validate(prices,fee)
    if not prices.index.equals(signals.index):raise ValueError('misaligned')
    if not (.03<=c.activation<=.3 and .005<=c.giveback<=.15):raise ValueError('bad profit floor')
    xx=prices[list(PRICES)].to_numpy(float)
    targets=signals[list(TARGETS)].to_numpy(float)
    fees=np.array([fee.tech_fee,fee.red_fee,fee.bond_fee,fee.gold_fee,fee.money_fee])
    holdings=targets[0].copy()
    nav0=1.;year_high=1.;locked=False;active_year=prices.index[0].year
    rows=[]
    for i,dt in enumerate(prices.index):
        if i>0:holdings*=xx[i]/xx[i-1]
        gross=holdings.sum();cur=holdings/gross
        year=dt.year
        if year!=active_year:
            # Portfolio value just before new-year trade includes the return from last
            # year's close to first new-year close. We use pre-gap nav for year baseline.
            nav0=rows[-1]['nav']
            year_high=nav0;locked=False;active_year=year
        desired=targets[i-1].copy() if i>0 else targets[0].copy()
        if locked:
            if c.stop_tech:desired[4]+=desired[0];desired[0]=0
            if c.exit_gold:desired[4]+=desired[3];desired[3]=0
            if desired[1]>c.red_core:desired[4]+=desired[1]-c.red_core;desired[1]=c.red_core
        turn=np.abs(cur-desired)
        if i>0 and turn.sum()<fee.execution_deadband:
            desired=cur.copy();turn=np.zeros(5)
        elif i==0:turn=np.zeros(5)
        cost=float(turn@fees)
        nav=float(gross*(1-cost))
        holdings=desired*nav
        year_high=max(year_high,nav)
        # Once threshold is met and profit retreats by giveback, lock from next week.
        was_locked=locked
        if year_high/nav0>=1+c.activation and nav/year_high-1<=-c.giveback:locked=True
        rows.append(dict(date=dt,nav=nav,year_reference=nav0,year_high=year_high,locked=locked,just_locked=(not was_locked and locked),turnover=turn.sum(),fee=nav*cost,held_tech=desired[0],held_red=desired[1],held_bond=desired[2],held_gold=desired[3],held_money=desired[4]))
    return pd.DataFrame(rows).set_index('date')
