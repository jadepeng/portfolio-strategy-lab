"""Paired moving-block bootstrap of V19 vs V18, explanatory not predictive CI."""
from pathlib import Path
import sys,json
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import numpy as np
import pandas as pd
from history.v14.market_snapshot import load_weekly
from history.v15.us_snapshot import load_us_prices,load_us_open
from history.v19.ensemble import V19Config,build_cn_v19,build_us_v19

def block_bootstrap_diff(nav_a,nav_b,seed=20261121,replications=4000,block_size=8):
    """Annualized paired log-return gap (a-b), with overlapping 8-week resamples."""
    if not nav_a.index.equals(nav_b.index):raise ValueError('date mismatch')
    da=np.log(nav_a).diff().iloc[1:].to_numpy()
    db=np.log(nav_b).diff().iloc[1:].to_numpy()
    delta=da-db
    n=len(delta)
    starts=np.arange(n-block_size+1)
    rng=np.random.default_rng(seed)
    draws=np.empty(replications)
    for k in range(replications):
        chosen=rng.choice(starts,size=(n+block_size-1)//block_size,replace=True)
        idx=np.concatenate([np.arange(i,i+block_size) for i in chosen])[:n]
        draws[k]=np.mean(delta[idx])*52*100
    return {'difference_annual_log_pct':round(float(np.mean(delta)*52*100),3),
            'interval_90_lo':round(float(np.quantile(draws,.05)),3),
            'interval_90_hi':round(float(np.quantile(draws,.95)),3),
            'n_weeks':n,'block_weeks':block_size,'replications':replications,
            'is_forward_validation':False}

if __name__=='__main__':
    p=pd.read_csv(ROOT/'market_data/verified_5asset_weekly_v13.csv',parse_dates=['week_start']).set_index('week_start')
    m=load_weekly();us=load_us_prices();uo=load_us_open()
    c0=build_cn_v19(p,m,us,V19Config(cn_v18_share=.5))[3]
    c1=build_cn_v19(p,m,us,V19Config(cn_v18_share=1))[3]
    u0=build_us_v19(us,uo,V19Config(us_v18_share=.5))[3]
    u1=build_us_v19(us,uo,V19Config(us_v18_share=1))[3]
    report={'CN_50pct_versus_V18':block_bootstrap_diff(c0.nav,c1.nav),
            'US_50pct_versus_V18':block_bootstrap_diff(u0.nav,u1.nav)}
    out=ROOT/'research_outputs/V19_bootstrap_uncertainty.json'
    out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(report,ensure_ascii=False,indent=2))
