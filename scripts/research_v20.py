"""One reproducible V20 search-independent, never-fit-across-2026 ablation.

Outputs inspectable forecasts/ledger for a single 25% research candidate; tests
4 fixed exposure strengths, 1x/2x/3x costs, two US execution conventions.
2020-2026 is tuning-exposed by earlier V1-V19; no claims of live or OOS proof.
"""
from dataclasses import replace
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import json
import numpy as np
import pandas as pd
from v14.market_snapshot import load_weekly
from v15.us_snapshot import load_us_prices,load_us_open
from v20.integrated import run_cn_v20,run_us_v20
from v20.public_rules import V20Config

OUT=ROOT/'research_outputs';OUT.mkdir(exist_ok=True)
prices=pd.read_csv(ROOT/'market_data'/'verified_5asset_weekly_v13.csv',parse_dates=['week_start']).set_index('week_start')
market=load_weekly();us=load_us_prices();opens=load_us_open()

from v20.metrics import metrics

rows=[]
for frac in [0.,.25,.5,1.]:
    for fee in [1.,2.,3.]:
        c=V20Config(cn_blend=frac,us_blend=frac,cn_fee_multiplier=fee,us_fee_multiplier=fee)
        cres=run_cn_v20(prices,market,us,c)
        ures=run_us_v20(us,opens,c)
        rows.append({'market':'CN','public_model_fraction':frac,'fee_multiple':fee,
                     'fill':'next_week_close',**metrics(cres['ledger'])})
        rows.append({'market':'US','public_model_fraction':frac,'fee_multiple':fee,
                     'fill':'next_week_open',**metrics(ures['ledger'])})
        if frac==.25 and fee==1.:
            cres['ledger'].to_csv(OUT/'V20_CN_25pct_ledger.csv',encoding='utf-8-sig')
            ures['ledger'].to_csv(OUT/'V20_US_25pct_ledger.csv',encoding='utf-8-sig')
            cres['signal'][['target_tech','target_red','target_bond','target_gold','target_money']].to_csv(OUT/'V20_CN_25pct_signals.csv')
            ures['signal'][['target_qqq','target_bil']].to_csv(OUT/'V20_US_25pct_signals.csv')
    uc=V20Config(us_blend=frac,us_fill_mode='next_week_close')
    out=run_us_v20(us,opens,uc)
    rows.append({'market':'US','public_model_fraction':frac,'fee_multiple':1.,
                 'fill':'next_week_close',**metrics(out['ledger'])})
frame=pd.DataFrame(rows)
frame.to_csv(OUT/'V20_public_rules_stress.csv',index=False,encoding='utf-8-sig')
meta={'asof':'2026-11-20','basis':'tuning-exposed historical in-sample, no promise',
      'data':{'cn_bars':len(prices),'us_bars':len(us)},'results':frame.to_dict('records'),
      'sources':[
       'https://mebfaber.com/timing-model/',
       'https://www.aqr.com/insights/research/journal-article/time-series-momentum',
       'https://concretumgroup.com/global-tactical-asset-allocation/',
       'https://alphaarchitect.com/quantitative-momentum-investing-philosophy/',
       'https://quantpedia.com/active-dual-momentum-gtaa-strategy/']}
(OUT/'V20_public_rules_results.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf-8')
print(frame[(frame.fee_multiple==1)&(frame.fill!='next_week_close')].to_string(index=False))
print('CN baseline/alternative (normal)')
print(frame[(frame.market=='CN')&(frame.fee_multiple==1)].to_string(index=False))
