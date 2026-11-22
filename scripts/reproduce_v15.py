"""Reproduce V15 regional independent strategies and lagged US-to-China ablation."""
from pathlib import Path
import sys,json
from dataclasses import replace
import pandas as pd
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from history.strategies.fusion_v6 import v6_base,V6Config
from history.strategies.fusion_v7 import V7Config
from history.strategies.fusion_v10 import V10Config,simulate_v10,results,TARGETS
from history.strategies.fusion_v11 import V11Config,signals_v11
from history.v13.regime_v13 import RegimeConfig,signals_v13
from history.v14.market_snapshot import load_weekly
from history.v14.crowding import build_factors,classify,state_overlay,OverlayStateConfig
from history.v15.us_snapshot import load_us_prices
from history.v15.us_strategy import USConfig,us_signals,simulate_us
from history.v15.cross_market import CrossConfig,us_to_cn_overlay

p=pd.read_csv(ROOT/'market_data/verified_5asset_weekly_v13.csv',parse_dates=['week_start']).set_index('week_start')
market=load_weekly()
cnconfig=V10Config(portfolio_vol_brake=True)
base=signals_v11(p,v6_base('week'),V6Config(),V7Config(red_core=.30,cash_mode='money_price',money_etf_side_cost=.0001),cnconfig,V11Config(risk_guard=False))
regime=signals_v13(p,base,RegimeConfig(hold_weeks=8))
factors=classify(build_factors(p,market))
cn=state_overlay(p,regime,factors,OverlayStateConfig(freeze_weeks=3))
us=load_us_prices()
cn_us=us_to_cn_overlay(p,cn,us,CrossConfig())
cn_normal_sim=simulate_v10(p,cn,cnconfig)
cn_overlay_sim=simulate_v10(p,cn_us,cnconfig)


def period_return(sim,yr:int)->float:
    s=sim.loc[str(yr)]
    if s.empty:return float('nan')
    prev=sim.loc[sim.index<s.index[0]].nav
    return round(100*(s.nav.iloc[-1]/prev.iloc[-1]-1),3) if len(prev) else float('nan')

def model_line(label,sim):
    a=results(sim.nav)
    return {'strategy':label,'total_pct':round(float(a['total']),3),'mdd_pct':round(float(a['mdd']),3),'2024_pct':period_return(sim,2024),'2025_pct':period_return(sim,2025),'2026_pct':period_return(sim,2026),'weeks':len(sim)}

runs=[model_line('A股 V14（无美股影响）',cn_normal_sim),model_line('A股 V15（美股风险闸门）',cn_overlay_sim)]
ussigs={}
for name,config in [('美股QQQ全战术30周',USConfig(qqq_core=0)),('美股30%核心+70%战术',USConfig(qqq_core=.30)),('美股QQQ长期持有',USConfig(qqq_core=1))]:
    ussig=us_signals(us,config)
    sim=simulate_us(us,ussig,config)
    runs.append(model_line(name,sim))
    ussigs[name]=(ussig,sim)
out=ROOT/'research_outputs';out.mkdir(exist_ok=True)
pd.DataFrame(runs).to_csv(out/'V15_regional_comparison.csv',index=False,encoding='utf-8-sig')
cn_us.to_csv(out/'V15_CN_US_lagged_weekly_signals.csv',index_label='week_start')
cn_overlay_sim.to_csv(out/'V15_CN_with_US_ledger.csv',index_label='week_start')
ussigs['美股30%核心+70%战术'][1].to_csv(out/'V15_US_30core_ledger.csv',index_label='week_start')
ussigs['美股30%核心+70%战术'][0].to_csv(out/'V15_US_30core_signals.csv',index_label='week_start')
copy=us.copy();copy.to_csv(ROOT/'market_data'/'longbridge_us_5asset_weekly_2020_2026_v15.csv',index_label='week_start')
latest=cn_us.iloc[-1]
summary={
 'version':'V15 research','China_lag_logic':'Chinese week t cannot use US Friday close of same week; US t-1 is used in CN t signal',
 'price_through':'2026-11-20', 'us_prices':{'start':str(us.index[0].date()),'end':str(us.index[-1].date()),'count':len(us),'source':'Longbridge weekly forward-adjusted, rounded to cents'},
 'CN_latest_weights':{k:float(latest[k]) for k in TARGETS},
 'CN_latest_US_source_week':str(latest.us_source_week.date()) if pd.notna(latest.us_source_week) else None,
 'CN_latest_US_gate':str(latest.us_risk_state_lagged),
 'US_latest_target':{k:float(v) for k,v in ussigs['美股30%核心+70%战术'][0].iloc[-1][['target_qqq','target_bil']].items()},
 'results':runs, 'posthoc_selection_warning':True,
}
(out/'V15_verified_results.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False),encoding='utf-8')
print(pd.DataFrame(runs).to_string(index=False))
print('latest China',summary['CN_latest_weights'],'US source',summary['CN_latest_US_source_week'],'state',summary['CN_latest_US_gate'])
print('latest US',summary['US_latest_target'])
print('overlay effect weeks',int((cn_us.cross_market_tech_cut>1e-9).sum()),'2026',int((cn_us.loc['2026'].cross_market_tech_cut>1e-9).sum()))
