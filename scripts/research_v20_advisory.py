"""V20 public-rule increase-only warning: fixed 3x3 neighborhood, 1x/2x/3x fee, fill sensitivity."""
from dataclasses import replace
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import numpy as np
import pandas as pd
from v15.us_snapshot import load_us_prices,load_us_open
from v15.us_strategy import USConfig,simulate_us
from v18.integrated import build_us_v18,V18USConfig
from v20.public_rules import advisory_gate_us,V20Config
from v20.integrated import run_advisory_cn
from v14.market_snapshot import load_weekly
from v20.metrics import metrics

U=load_us_prices();O=load_us_open()
P=pd.read_csv(ROOT/'market_data'/'verified_5asset_weekly_v13.csv',parse_dates=['week_start']).set_index('week_start')
M=load_weekly()
OUT=ROOT/'research_outputs';OUT.mkdir(exist_ok=True)

rows=[];events=[]
for fill in ('next_week_open','next_week_close'):
   cf=V18USConfig(fill_mode=fill)
   original,origledger=build_us_v18(U,cf,O)
   for lookback in (35,40,45):
      for momentum in (22,26,30):
        cfg=V20Config(trend_weeks=lookback,momentum_weeks=momentum,us_fill_mode=fill)
        modified=advisory_gate_us(U,original,cfg)
        changes=int(modified.public_warning_restricted.sum())
        if fill=='next_week_open' and (lookback,momentum)==(40,26):
            events=modified[modified.public_warning_restricted][['target_qqq','target_bil','public_warning_restricted']].reset_index().to_dict('records')
            modified.to_csv(OUT/'V20_US_advisory_signals.csv',index_label='week_start')
        for multiple in (1.,2.,3.) if (lookback,momentum)==(40,26) else (1.,):
            sc=USConfig(qqq_core=.30,fill_mode=fill,rebalance_threshold=cf.rebalance_threshold,
                        qqq_side_fee=cf.qqq_side_fee*multiple,bil_side_fee=cf.bil_side_fee*multiple)
            ledger=simulate_us(U,modified,sc,O)
            if fill=='next_week_open' and (lookback,momentum,multiple)==(40,26,1.):
                ledger.to_csv(OUT/'V20_US_advisory_ledger.csv',index_label='week_start')
            rows.append({'market':'US','version':'guarded','fill':fill,'trend_weeks':lookback,
                        'momentum_weeks':momentum,'fee_multiple':multiple,'warnings':changes,**metrics(ledger)})
   for multiple in (1.,2.,3.):
      sc=USConfig(qqq_core=.30,fill_mode=fill,rebalance_threshold=cf.rebalance_threshold,
                  qqq_side_fee=cf.qqq_side_fee*multiple,bil_side_fee=cf.bil_side_fee*multiple)
      ledger=simulate_us(U,original,sc,O)
      rows.append({'market':'US','version':'baseline','fill':fill,'trend_weeks':40,
                    'momentum_weeks':26,'fee_multiple':multiple,'warnings':0,**metrics(ledger)})

for cfg in [V20Config(),V20Config(trend_weeks=35),V20Config(trend_weeks=45)]:
    base,signal,bl,ledger=run_advisory_cn(P,M,U,cfg)
    rows.append({'market':'CN','version':'guarded','fill':'next_week_close',
                'trend_weeks':cfg.trend_weeks,'momentum_weeks':cfg.momentum_weeks,
                'fee_multiple':1.,'warnings':int(signal.public_warning_restricted.sum()),**metrics(ledger)})

f=pd.DataFrame(rows)
f.to_csv(OUT/'V20_advisory_robustness.csv',index=False,encoding='utf-8-sig')
(OUT/'V20_US_advisory_event_dates.csv')
pd.DataFrame(events).to_csv(OUT/'V20_US_advisory_event_dates.csv',index=False,encoding='utf-8-sig')
print('US 1x open 3x3')
print(f[(f.market=='US')&(f.fill=='next_week_open')&(f.fee_multiple==1)][['version','trend_weeks','momentum_weeks','warnings','cumulative_pct','max_dd_pct','r2022_pct','r2024_pct','r2025_pct','r2026_pct']].to_string(index=False))
print('US fill/cost')
print(f[(f.market=='US')&(f.trend_weeks==40)&(f.momentum_weeks==26)][['version','fill','fee_multiple','cumulative_pct','max_dd_pct','r2026_pct']].to_string(index=False))
print('CN guarded windows')
print(f[f.market=='CN'][['trend_weeks','warnings','cumulative_pct','max_dd_pct','r2026_pct']].to_string(index=False))
