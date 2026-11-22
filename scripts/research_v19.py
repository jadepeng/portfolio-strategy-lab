"""Reproducible V19 ensemble ablation; all reported years are historical tuning-exposed data."""
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from dataclasses import replace
import json
import numpy as np
import pandas as pd
from v14.market_snapshot import load_weekly
from v15.us_snapshot import load_us_prices,load_us_open
from v19.ensemble import V19Config, build_cn_v19,build_us_v19

OUT=ROOT/'research_outputs';OUT.mkdir(exist_ok=True)
p=pd.read_csv(ROOT/'market_data/verified_5asset_weekly_v13.csv',parse_dates=['week_start']).set_index('week_start')
m=load_weekly();us=load_us_prices();uo=load_us_open()

def stats(ledger,turnover=True):
    n=ledger.nav
    r=n.pct_change(fill_method=None).dropna()
    top=n.cummax();dd=n/top-1
    ret={'cumulative_pct':100*(n.iloc[-1]/n.iloc[0]-1),
         'mdd_pct':100*min(0.,dd.min()),'annual_vol_pct':100*r.std(ddof=1)*np.sqrt(52),
         'fee_initial_pct':100*float(ledger.fee.sum()) if 'fee' in ledger else 100*float(ledger.cost.sum()),
         'trade_weeks':int((ledger.turnover>0).sum())}
    for begin,end,label in [('2021-01-01','2023-12-31','2021_23'),('2024-01-01','2026-12-31','2024_26'),('2024-01-01','2024-12-31','2024'),('2025-01-01','2025-12-31','2025'),('2026-01-01','2026-12-31','2026')]:
        s=n.loc[begin:end]
        if len(s):
            prev=n.loc[:s.index[0]].iloc[-2] if n.index.get_loc(s.index[0])>0 else n.iloc[0]
            ret['r'+label+'_pct']=100*(s.iloc[-1]/prev-1)
    return {k:round(float(v),3) if isinstance(v,(float,np.floating)) else v for k,v in ret.items()}

cn=[];us_runs=[]
for share in [0.,.25,.5,.75,1.]:
 for fees in [1.,2.]:
    conf=V19Config(cn_v18_share=share,cn_extra_fee_multiple=fees)
    old,modern,sig,ledger=build_cn_v19(p,m,us,conf)
    cn.append({'model':'CN V15/V18 ensemble','v18_share':share,'fee_factor':fees,**stats(ledger)})
    if share==.5 and fees==1:
       ledger.to_csv(OUT/'V19_CN_50_50_ledger.csv',index_label='week_start')
       sig[['target_tech','target_red','target_bond','target_gold','target_money']].to_csv(OUT/'V19_CN_50_50_signals.csv',index_label='week_start')
 for fees in [1.,2.]:
    for fill in ['next_week_open','next_week_close']:
       conf=V19Config(us_v18_share=share,us_extra_fee_multiple=fees,us_fill_mode=fill)
       old,modern,sig,ledger=build_us_v19(us,uo,conf)
       us_runs.append({'model':'US 30week/V17 ensemble','v18_share':share,'fee_factor':fees,'fill_mode':fill,**stats(ledger)})
       if share==.5 and fees==1. and fill=='next_week_open':
          ledger.to_csv(OUT/'V19_US_50_50_ledger.csv',index_label='week_start')
          sig[['target_qqq','target_bil']].to_csv(OUT/'V19_US_50_50_signals.csv',index_label='week_start')

c=pd.DataFrame(cn);u=pd.DataFrame(us_runs)
c.to_csv(OUT/'V19_CN_ensemble_robustness.csv',index=False,encoding='utf-8-sig')
u.to_csv(OUT/'V19_US_ensemble_robustness.csv',index=False,encoding='utf-8-sig')
res={'asof':'2026-11-20','study':'post-hoc, not prospective','china':c.to_dict(orient='records'),'us':u.to_dict(orient='records')}
(OUT/'V19_verified_results.json').write_text(json.dumps(res,ensure_ascii=False,indent=2),encoding='utf-8')
print('=== CHINA standard fees===')
print(c[c.fee_factor==1].to_string(index=False))
print('=== US standard fees, week-open===')
print(u[(u.fee_factor==1)&(u.fill_mode=='next_week_open')].to_string(index=False))
print('=== US week-close, default share===')
print(u[(u.v18_share==.5)&(u.fee_factor==1)].to_string(index=False))
