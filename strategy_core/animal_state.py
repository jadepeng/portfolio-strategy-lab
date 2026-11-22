from dataclasses import dataclass,replace
import pandas as pd,numpy as np
from strategy_core.baselines.fusion_v10 import TARGETS, PRICES
from strategy_core.baselines.fusion_v11 import risk_project,_cov

@dataclass(frozen=True)
class AnimalConfig:
    cap:float=.18
    tech_max:float=.70
    red_max:float=.80
    core_red:float=.20
    enter_tech:float=.30
    enter_red:float=.50
    tech_exit_dd:float=.08
    tech_shock:float=-.07
    red_shock:float=-.06
    rel_gain:float=.03
    require_heat:bool=False
    avoid_late_entry:bool=False
    red_early:bool=True
    red_hold:bool=True
    portfolio_lock:bool=False
    bond_max:float=.3
    gold_max:float=.15
    min_money:float=.05
    use_risk:bool=True

def signals_animal(p,base,c=AnimalConfig()):
    if not p.index.equals(base.index):raise ValueError('misaligned')
    tech=p.tech;red=p.red
    rt=tech.pct_change(fill_method=None);rr=red.pct_change(fill_method=None)
    m4t=tech.rolling(4).mean();m4r=red.rolling(4).mean()
    m3r=red.rolling(3).mean()
    t6=tech.pct_change(6,fill_method=None);r6=red.pct_change(6,fill_method=None)
    t2=tech.pct_change(2,fill_method=None);r2=red.pct_change(2,fill_method=None)
    rel=r2-t2
    h8=tech.rolling(8).max();dd=tech/h8-1
    ret=p.loc[:,PRICES].pct_change(fill_method=None).to_numpy(float)
    t_live=False;r_live=False;t_count=0;r_count=0;t_entry=0;r_entry=0
    result=[];events=[];flags=[]
    for i in range(len(base)):
        if i<60:
            result.append(base.iloc[i][list(TARGETS)].to_numpy(float));events.append('warmup');flags.append('warmup');continue
        st=int(base.tech_stage.iloc[i]);sr=int(base.red_stage.iloc[i]);t=float(tech.iloc[i]);r=float(red.iloc[i]);
        sell_t=(rt.iloc[i]<c.tech_shock or st<=3 or (dd.iloc[i]<=-c.tech_exit_dd and rel.iloc[i]>0) or (t_live and st<=4 and t<m4t.iloc[i] and tech.iloc[i-1]<m4t.iloc[i-1]))
        if sell_t:t_live=False;t_count=0
        entry_t=(st>=5 and t>m4t.iloc[i] and t6.iloc[i]>0 and (not c.require_heat or st==6))
        if not t_live and entry_t and not sell_t:
            t_live=True;t_count=1;t_entry=t
        elif t_live:t_count+=1
        want_t=0
        if t_live:
            if t_count==1:want_t=c.enter_tech
            elif st>=5:
                want_t=c.tech_max if t>t_entry and t2.iloc[i]>0 else min(c.tech_max,.48)
            else:want_t=min(c.tech_max,.22)
            if c.avoid_late_entry and (t/h8.iloc[i]>-.015 and t6.iloc[i]>.25) and t_count==1:
                want_t=min(want_t,.20)
        red_fast=bool(c.red_early and r2.iloc[i]>.009 and rel.iloc[i]>c.rel_gain and r>m3r.iloc[i] and rr.iloc[i]>0)
        sell_r=bool(rr.iloc[i]<=c.red_shock or sr<=2 and r<m4r.iloc[i] and r6.iloc[i]<0)
        if sell_r:
            r_live=False;r_count=0
        entry_r=bool((sr>=5 and r>m4r.iloc[i] and rr.iloc[i]>0) or red_fast)
        if not r_live and entry_r and not sell_r:
            r_live=True;r_count=1;r_entry=r
        elif r_live:r_count+=1
        want_r=c.core_red
        if r_live:
            if r_count==1:want_r=max(c.enter_red,c.core_red)
            else:want_r=c.red_max if r>=r_entry and (rr.iloc[i]>0 or c.red_hold) else max(c.enter_red,c.core_red)
            if c.red_hold and sr<=3 and r2.iloc[i]<0 and r<m4r.iloc[i]:r_live=False;want_r=c.core_red
        if want_t+want_r>1-c.min_money:
            # If tech trend fails OR red takeover confirmed, cede budget to defensive rotation.
            if st<=4 or (red_fast and rel.iloc[i]>c.rel_gain):want_t=max(0,1-c.min_money-want_r)
            else:want_r=max(c.core_red,1-c.min_money-want_t)
        remaining=1-want_t-want_r
        bond=min(c.bond_max,max(0,remaining-c.min_money)) if bool(base.bond_eligible.iloc[i]) else 0.
        gold=min(c.gold_max,max(0,remaining-bond-c.min_money)) if bool(base.gold_eligible.iloc[i]) else 0.
        w=np.array([want_t,want_r,bond,gold,max(0,1-want_t-want_r-bond-gold)])
        if c.use_risk and i>=26:
            x=ret[i-25:i+1];w,_=risk_project(w,[_cov(x,.5)],c.cap,core=c.core_red)
        result.append(w)
        flags.append('tech' if want_t>.35 else 'red' if want_r>.45 else 'cash')
        events.append(f'tech {st}, red {sr}, tech_live {t_live}, red_live {r_live}, red_early {red_fast}, sell_tech {sell_t}, d8 {dd.iloc[i]:+.2%}')
    out=base.copy();out[list(TARGETS)]=pd.DataFrame(result,index=p.index,columns=TARGETS);out['animal_state']=flags;out['animal_reason']=events
    assert np.allclose(out[list(TARGETS)].sum(axis=1),1) and (out[list(TARGETS)]>=-1e-8).all().all()
    return out
