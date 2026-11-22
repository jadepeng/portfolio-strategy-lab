"""V14 point-in-time crowding+turnover study; no orders placed.

Signals are observable only at final weekly close; fills at next week's close.
No after-the-fact top dates are inputs. All results are hindsight research.
"""
from pathlib import Path
import sys,json
from dataclasses import replace
import numpy as np,pandas as pd
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from history.strategies.fusion_v6 import v6_base,V6Config
from history.strategies.fusion_v7 import V7Config
from history.strategies.fusion_v10 import V10Config,simulate_v10,results,TARGETS
from history.strategies.fusion_v11 import V11Config,signals_v11
from history.v13.regime_v13 import RegimeConfig,signals_v13
from history.v14.market_snapshot import load_weekly
from history.v14.crowding import CrowdConfig,OverlayStateConfig,build_factors,classify,overlay_signals,state_overlay
from history.v13.daily_state_v13 import simulate as daily_simulate,DailyExitConfig

p=pd.read_csv(ROOT/'market_data/verified_5asset_weekly_v13.csv',parse_dates=['week_start']).set_index('week_start')
v=load_weekly()
factors=classify(build_factors(p,v))
assert p.index.is_monotonic_increasing and len(p)==313
c=V10Config(portfolio_vol_brake=True)
b=signals_v11(p,v6_base('week'),V6Config(),V7Config(red_core=.30,cash_mode='money_price',money_etf_side_cost=.0001),c,V11Config(risk_guard=False))
base8=signals_v13(p,b,RegimeConfig(hold_weeks=8))
base12=signals_v13(p,b,RegimeConfig(hold_weeks=12))
configs={
 'V11-A':(b,None),
 'V13-8 baseline':(base8,None),
 'V13-12 baseline':(base12,None),
 'V14-A top-risk only':(base8,CrowdConfig(use_market_bear=False)),
 'V14-B market-bear only':(base8,CrowdConfig(use_top_warning=False)),
 'V14-C price-volume conjunction':(base8,CrowdConfig()),
 'V14-D plus conditional red rotation':(base8,CrowdConfig(use_red_transition=True)),
 'V14-E conservative stronger cap':(base8,CrowdConfig(tech_cap_warning=.15,tech_cap_bear=0)),
 'V14-F crowd-rank only':(base8,CrowdConfig(use_market_bear=False,high_volume_factor=99)),
 'V14-G freeze 2 weeks':(base8,OverlayStateConfig(freeze_weeks=2)),
 'V14-H freeze 3 weeks':(base8,OverlayStateConfig(freeze_weeks=3)),
 'V14-I freeze 4 weeks':(base8,OverlayStateConfig(freeze_weeks=4)),
 'V14-J freeze 3 + exit confirm':(base8,OverlayStateConfig(freeze_weeks=3,confirm_exit=True)),
 'V14-K freeze 3 + red':(base8,OverlayStateConfig(freeze_weeks=3,allow_red_takeover=True)),
 'V14-L freeze 3 no market':(base8,OverlayStateConfig(freeze_weeks=3,require_market_confirmation=False)),
}
res=[];ledger={}
def period_ret(df,period):
    s=df.loc[period]
    prev=df.loc[df.index<s.index[0]].nav
    return 100*(s.nav.iloc[-1]/prev.iloc[-1]-1) if len(prev) else np.nan
for name,(base,crowd) in configs.items():
    sig=(state_overlay(p,base,factors,crowd) if isinstance(crowd,OverlayStateConfig) else overlay_signals(p,base,factors,crowd)) if crowd is not None else base
    sim=simulate_v10(p,sig,c)
    all_stats=results(sim.nav)
    one={'version':name,'all_total_pct':round(all_stats['total'],3),'mdd_pct':round(all_stats['mdd'],3),'2024_pct':round(period_ret(sim,'2024'),3),'2025_pct':round(period_ret(sim,'2025'),3),'2026_pct':round(period_ret(sim,'2026'),3),'turnover':round(sim.turnover.sum(),2),'weeks_changed':round(int(((sig[list(TARGETS)]-base[list(TARGETS)]).abs().sum(axis=1)>.001).sum()))}
    res.append(one);ledger[name]=sim
    if name in ('V13-8 baseline','V14-C price-volume conjunction','V14-D plus conditional red rotation','V14-H freeze 3 weeks','V14-L freeze 3 no market'):
        sim.loc['2026'].to_csv(ROOT/'research_outputs'/('V14_'+name.replace(' ','_')+'_2026_weekly.csv'),index_label='week_start')
    if name in ('V13-8 baseline','V14-H freeze 3 weeks','V14-L freeze 3 no market'):
        for mode,dc in [('next_open',DailyExitConfig(use_stop=False)),('daily_stop',DailyExitConfig(use_stop=True)),('year_profit_guard',DailyExitConfig(use_stop=True,profit_lock=True))]:
            dl,ret=daily_simulate(p,sig,sim,dc)
            one['2026_'+mode+'_pct']=round(ret,3)
            if name=='V14-H freeze 3 weeks' and mode=='daily_stop':
                dl.to_csv(ROOT/'research_outputs'/'V14_2026_daily_next_open_ledger.csv',index_label='date')
    if name=='V14-H freeze 3 weeks':
        sig.to_csv(ROOT/'research_outputs'/'V14_H_complete_signals.csv',index_label='week_start')
result=pd.DataFrame(res)
result.to_csv(ROOT/'research_outputs'/'V14_market_crowding_ablation.csv',index=False)
factors.to_csv(ROOT/'market_data'/'market_turnover_derived_signals_2024_2026.csv',index_label='week_start')
rec=factors.loc['2026', ['market_turn_100m','tech_turn_share','double_creation_share','red_turn_share','market_turn_ratio','market_ma13','market_ma26','market_regime','volume_exhaustion','crowd_rollover','top_warning','bear_weak_volume']]
rec.to_csv(ROOT/'research_outputs'/'V14_2026_market_risk_timeline.csv',index_label='week_start')
print(result.to_string(index=False))
result.to_csv(ROOT/'research_outputs'/'V14_market_crowding_ablation.csv',index=False)

recommend=ledger['V14-H freeze 3 weeks'].loc[p.index[-1]]
summary={'study_date':'2026-11-21','price_through':'2026-11-20','volume_weeks':len(v),
         'warning_source':'index-basket proxy, not CICC top-5-stock concentration',
         'method':'weekly close signal -> next weekly close; daily next-open separate 2026 simulation',
         'baseline':res[1], 'recommended_experiment':res[[x['version'] for x in res].index('V14-H freeze 3 weeks')],
         'latest_market_regime':str(factors.iloc[-1].market_regime),
         'latest_target':{k:round(float(recommend[k]),6) for k in TARGETS},
         'no_forward_validation':True}
(ROOT/'research_outputs'/'V14_verified_research.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
print('market latest',factors.iloc[-1][['market_close','market_turn_100m','tech_turn_share','red_turn_share','market_turn_ratio','market_regime','top_warning','bear_weak_volume']].to_dict())
print('top warning event weeks 2026:',factors.loc['2026'].loc[lambda x:x.top_warning].index.strftime('%Y-%m-%d').tolist())
