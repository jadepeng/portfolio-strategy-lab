"""Reproduce V18 evidence from bundled V16 and uploaded Gemini V17 sources.

No market-data network calls. This reproduces the selected pre-frozen snapshots,
uses known weekly signal dates and executions, and exports inspectable ledgers.
"""
from __future__ import annotations
import sys, json
from dataclasses import replace
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import numpy as np
import pandas as pd
from history.strategies.fusion_v10 import V10Config,simulate_v10,PRICES,TARGETS
from v14.market_snapshot import load_weekly
from v15.us_snapshot import load_us_prices,load_us_open
from v16.overnight import DailyGateConfig,load_event_snapshot
from v13.daily_ohlc_2026 import load as load_cn_daily
from v17.cn_v17 import CNV17Config,build_cn_v17_weekly,run_cn_daily_replay_v17
from v18.integrated import V18CNConfig,V18USConfig,build_cn_v18_weekly,build_us_v18
from scripts.reproduce_v17 import weekly_metrics,daily_metrics

OUT=ROOT/'research_outputs'
OUT.mkdir(exist_ok=True)

def yr_nav(nav,s,e):
    part=nav.loc[s:e]
    if not len(part):return float('nan')
    old=nav.loc[nav.index<part.index[0]]
    return 100*(part.iloc[-1]/(old.iloc[-1] if len(old) else 1)-1)


def main():
    p=pd.read_csv(ROOT/'market_data/verified_5asset_weekly_v13.csv',parse_dates=['week_start']).set_index('week_start')
    m=load_weekly();us=load_us_prices();uo=load_us_open()
    daily=load_cn_daily();events=load_event_snapshot()
    models={
        'Gemini_V17_CN_40_25':CNV17Config(),
        'V18_robust_CN_35_20':V18CNConfig().to_v17(),
        'V18_CN_30_20':replace(CNV17Config(),bond_cap=.30,gold_cap=.20),
        'V18_CN_40_20':replace(CNV17Config(),bond_cap=.40,gold_cap=.20),
    }
    cn=[];signal_map={};hist_map={};vconf_map={}
    for name,conf in models.items():
        vc,signals,hist=build_cn_v17_weekly(p,m,us,conf)
        metrics=weekly_metrics(hist.nav,int((hist.turnover>0).sum()),float(hist.turnover.sum()))
        cn.append({'variant':name,**metrics})
        hist_map[name]=hist;signal_map[name]=signals;vconf_map[name]=vc
        hist.to_csv(OUT/f'{name}_weekly_ledger.csv',index_label='week_start')
    cn_table=pd.DataFrame(cn)
    cn_table.to_csv(OUT/'V18_CN_comparison.csv',index=False,encoding='utf-8-sig')

    us_rows=[]
    for core in (0.,.30):
        for name,conf in [
            ('Gemini_US_V17',V18USConfig(qqq_core=core)),
            ('US_fast_risk_exit',V18USConfig(qqq_core=core,priority_exit=True)),
            ('US_slow_trend_sensitivity',V18USConfig(qqq_core=core,ma_windows=(13,26,39))),
        ]:
            sig,sim=build_us_v18(us,conf,uo)
            us_rows.append({'variant':name,'core_pct':int(core*100),**weekly_metrics(sim.nav,int((sim.turnover>0).sum()),float(sim.turnover.sum()))})
            if name=='Gemini_US_V17':sim.to_csv(OUT/f'V18_US_{int(core*100)}core_weekly_ledger.csv',index_label='week_start')
    us_table=pd.DataFrame(us_rows)
    us_table.to_csv(OUT/'V18_US_comparison.csv',index=False,encoding='utf-8-sig')

    # Same initial holdings/last-close as historical year end, no future initialization.
    name='V18_robust_CN_35_20'
    hist=hist_map[name];sig=signal_map[name];conf=vconf_map[name]
    prior=hist.loc[hist.index<daily.index[0]].iloc[-1]
    pweight=prior[[f'held_{k}' for k in ('tech','red','bond','gold','money')]].to_numpy(float)
    previous_close=p.loc[prior.name,list(PRICES)].to_numpy(float)
    modes={
        'V18_weekly_no_daily_next_friday_close':(DailyGateConfig(mode='off'),'next_friday_close'),
        'V18_conditional_freeze_next_friday_close':(DailyGateConfig(mode='freeze',require_local_weakness=True),'next_friday_close'),
        'V18_conditional_freeze_next_monday_open':(DailyGateConfig(mode='freeze',require_local_weakness=True),'next_monday_open'),
    }
    rows=[]
    for label,(c,timing) in modes.items():
        ledger=run_cn_daily_replay_v17(daily,sig,pweight,conf,events,previous_close,c,timing)
        ledger.to_csv(OUT/f'{label}_2026_daily.csv',index_label='date')
        rows.append({'variant':label,**daily_metrics(ledger)})
    daily_table=pd.DataFrame(rows)
    daily_table.to_csv(OUT/'V18_CN_daily_execution_sensitivity.csv',index=False,encoding='utf-8-sig')

    # Fee/slippage sensitivity on exactly the same signals.
    costs=[]
    for name in ('Gemini_V17_CN_40_25','V18_robust_CN_35_20'):
        signal=signal_map[name];base=vconf_map[name]
        for fee_factor in (1,2,3):
            cfg=replace(base,**{k:getattr(base,k)*fee_factor for k in ('tech_fee','red_fee','bond_fee','gold_fee','money_fee')})
            result=simulate_v10(p,signal,cfg)
            costs.append({'variant':name,'fee_factor':fee_factor,**weekly_metrics(result.nav,int((result.turnover>0).sum()),float(result.turnover.sum()))})
    costs=pd.DataFrame(costs);costs.to_csv(OUT/'V18_CN_cost_stress.csv',index=False,encoding='utf-8-sig')
    result={'generated_from':'V16 bundled + user-uploaded Gemini V17','as_of':'2026-11-20',
      'latest_cn_target':{k:float(v) for k,v in signal_map[name].iloc[-1][list(TARGETS)].items()},
      'latest_us_30core_target':{k:float(v) for k,v in build_us_v18(us,V18USConfig(),uo)[0].iloc[-1][['target_qqq','target_bil']].items()},
      'cn_weekly':cn_table.to_dict(orient='records'),'us_weekly':us_table.to_dict(orient='records'),
      'cn_daily':daily_table.to_dict(orient='records'),'cn_fee_stress':costs.to_dict(orient='records')}
    (OUT/'V18_verified_results.json').write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding='utf8')
    print('\n=== A-shares weekly ===\n',cn_table.to_string(index=False))
    print('\n=== US weekly ===\n',us_table.to_string(index=False))
    print('\n=== A-shares daily ===\n',daily_table.to_string(index=False))
    print('\n=== A-shares fee sensitivity ===\n',costs[['variant','fee_factor','total_pct','mdd_pct','r26_pct']].to_string(index=False))
    print('\nTargets',result['latest_cn_target'],result['latest_us_30core_target'])

if __name__=='__main__':main()
