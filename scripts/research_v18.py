"""V18 robustness checks: ablation and execution sensitivity, same source data."""
from __future__ import annotations
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from dataclasses import replace
import pandas as pd
import numpy as np
from fusion_v6 import V6Config,v6_base
from fusion_v7 import V7Config
from fusion_v10 import V10Config,simulate_v10,TARGETS
from fusion_v11 import V11Config,signals_v11
from v13.regime_v13 import RegimeConfig,signals_v13
from v13.animal_state import AnimalConfig
from v14.market_snapshot import load_weekly
from v14.crowding import build_factors,classify,state_overlay,OverlayStateConfig
from v15.cross_market import us_to_cn_overlay,CrossConfig
from v15.us_snapshot import load_us_prices,load_us_open
from v15.us_strategy import USConfig,us_signals,simulate_us
from v17.us_v17 import USV17Config,simulate_us_v17

prices=pd.read_csv(ROOT/'market_data/verified_5asset_weekly_v13.csv',parse_dates=['week_start']).set_index('week_start')
market=load_weekly();us=load_us_prices();uo=load_us_open()

def metrics(sim):
    nav=sim.nav
    def segment(s,e):
        x=nav.loc[s:e]; ref=nav.loc[nav.index<x.index[0]].iloc[-1] if len(x) and (nav.index<x.index[0]).any() else 1
        return 100*(x.iloc[-1]/ref-1) if len(x) else np.nan
    eq=nav.to_numpy();pk=np.maximum.accumulate(np.r_[1.,eq]);mdd=min(0,(eq/pk[1:]-1).min())
    yy=(nav.index[-1]-nav.index[0]).days/365.25
    return {
        'total':round(100*(eq[-1]/eq[0]-1),2),
        'cagr':round(100*((eq[-1]/eq[0])**(1/yy)-1),2),
        'mdd':round(mdd*100,2),
        '2021_23':round(segment('2021','2023'),2),
        '2024':round(segment('2024','2024'),2),
        '2025':round(segment('2025','2025'),2),
        '2026':round(segment('2026','2026'),2),
        'trades':int((sim.turnover>0).sum()),
        'turnover':round(float(sim.turnover.sum()),2),
    }

cnrows=[]
for cfg in [
    ('V15 base',.30,.15,'capped',False,.12,.70,True),
    ('V17 Gemini',.40,.25,'inverse_vol',True,.15,.55,True),
    ('V17 no inverse',.40,.25,'capped',True,.15,.55,True),
    ('V17 no priority',.40,.25,'inverse_vol',False,.15,.55,True),
    ('V17 12% deadband',.40,.25,'inverse_vol',True,.12,.55,True),
    ('V17 20% deadband',.40,.25,'inverse_vol',True,.20,.55,True),
    ('CN 35/20 inverse',.35,.20,'inverse_vol',True,.15,.55,True),
    ('CN 35/15 inverse',.35,.15,'inverse_vol',True,.15,.55,True),
    ('CN 30/20 inverse',.30,.20,'inverse_vol',True,.15,.55,True),
    ('CN 40/20 inverse',.40,.20,'inverse_vol',True,.15,.55,True),
    ('CN 30/25 inverse',.30,.25,'inverse_vol',True,.15,.55,True),
    ('CN 35/25 inverse',.35,.25,'inverse_vol',True,.15,.55,True),
    ('CN 40/15 inverse',.40,.15,'inverse_vol',True,.15,.55,True),
    ('CN cap70 instead55',.40,.25,'inverse_vol',True,.15,.70,True),
    ('CN no vol brake',.40,.25,'inverse_vol',True,.15,.55,False),
]:
    name,bcap,gcap,allocation,priority,dead,techmax,brake=cfg
    vc=V10Config(bond_cap=bcap,gold_cap=gcap,allocation=allocation,priority_exit=priority,execution_deadband=dead,portfolio_vol_brake=brake)
    base=signals_v11(prices,v6_base('week'),V6Config(),V7Config(red_core=.30,cash_mode='money_price',money_etf_side_cost=.0001),vc,V11Config(risk_guard=False))
    trend=signals_v13(prices,base,RegimeConfig(hold_weeks=8,animal=AnimalConfig(bond_max=bcap,gold_max=gcap,tech_max=techmax)))
    signal=state_overlay(prices,trend,classify(build_factors(prices,market)),OverlayStateConfig(freeze_weeks=3))
    signal=us_to_cn_overlay(prices,signal,us,CrossConfig())
    sim=simulate_v10(prices,signal,vc)
    cnrows.append({'variant':name,**metrics(sim),'latest_red':round(float(signal.target_red.iloc[-1]),3),'latest_bond':round(float(signal.target_bond.iloc[-1]),3),'latest_money':round(float(signal.target_money.iloc[-1]),3)})

usrows=[]
for core in [0,.30]:
    for params in [
        ('V15 30w',None,None,None,None,None),
        ('V17 Gemini',.005,.24,.20,(10,20,30),13),
        ('V17 lower volcap',.005,.20,.20,(10,20,30),13),
        ('V17 higher volcap',.005,.28,.20,(10,20,30),13),
        ('V17 wide hysteresis',.01,.24,.20,(10,20,30),13),
        ('V17 no hysteresis',0.,.24,.20,(10,20,30),13),
        ('V17 more rebalances',.005,.24,.08,(10,20,30),13),
        ('V17 fewer rebalances',.005,.24,.30,(10,20,30),13),
        ('V17 13/26/39',.005,.24,.20,(13,26,39),13),
        ('V17 10/20/30 slowvol',.005,.24,.20,(10,20,30),26),
    ]:
        name,hys,cap,db,windows,volw=params
        if name.startswith('V15'):
            conf=USConfig(qqq_core=core,fill_mode='next_week_open');sim=simulate_us(us,us_signals(us,conf),conf,uo)
        else:
            conf=USV17Config(qqq_core=core,hysteresis=hys,vol_cap=cap,rebalance_threshold=db,ma_windows=windows,vol_window=volw)
            _,sim=simulate_us_v17(us,conf,uo)
        usrows.append({'core':core,'variant':name,**metrics(sim)})

out=ROOT/'research_outputs';out.mkdir(exist_ok=True)
cn=pd.DataFrame(cnrows);u=pd.DataFrame(usrows)
cn.to_csv(out/'V18_CN_robustness.csv',index=False,encoding='utf-8-sig')
u.to_csv(out/'V18_US_robustness.csv',index=False,encoding='utf-8-sig')
print('==== CHINA ====');print(cn.to_string(index=False))
print('==== US ====');print(u.to_string(index=False))
