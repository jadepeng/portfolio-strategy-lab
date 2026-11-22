"""Paired 8-week block resampling of candidate-vs-baseline weekly log RETURNS.
Not independent unseen evaluation. Intervals do NOT account for model selection.
"""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import numpy as np
import pandas as pd
import json
from v15.us_snapshot import load_us_prices,load_us_open
from v14.market_snapshot import load_weekly
from v20.integrated import run_advisory_us,run_cn_v20

u,o=load_us_prices(),load_us_open()
_,_,usbase,usguard=run_advisory_us(u,o)
p=pd.read_csv(ROOT/'market_data'/'verified_5asset_weekly_v13.csv',parse_dates=['week_start']).set_index('week_start')
m=load_weekly();cn=run_cn_v20(p,m,u)

rng=np.random.default_rng(20261121)
res={}
for name,baseline,candidate in [('US_advisory',usbase,usguard),('CN_25pct_blend',cn['baseline_ledger'],cn['ledger'])]:
    if not baseline.index.equals(candidate.index):raise AssertionError('mismatch')
    x=(np.log(candidate.nav).diff()-np.log(baseline.nav).diff()).dropna().to_numpy(float)
    N=len(x);L=8;B=4000
    estim=[]
    for _ in range(B):
        selected=[]
        while len(selected)<N:
            pos=int(rng.integers(0,N-L+1))
            selected.extend(x[pos:pos+L])
        estim.append(52*100*float(np.mean(selected[:N])))
    res[name]={'n_weekly_returns':N,'block_size_weeks':L,'bootstrap_draws':B,
               'annualized_log_return_diff_pct_points':round(52*100*float(x.mean()),3),
               'empirical_90pct_interval_pp':list(np.round(np.quantile(estim,[.05,.95]),3)),
               'claim':'descriptive within repeatedly tuned history, NOT prospective validation'}
out=ROOT/'research_outputs'/'V20_bootstrap.json'
out.write_text(json.dumps(res,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(res,ensure_ascii=False,indent=2))
