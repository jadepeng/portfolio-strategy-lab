"""Reproduce V10 five-asset/volatility-budget simulation and transparency checks.

Requires local deterministic Longbridge snapshots; makes no network calls.
The indexed weekly timestamp is a WEEK OPEN label, not the Friday execution date.
All data points beyond a signal are excluded by design.
"""
from pathlib import Path
from dataclasses import replace,asdict
import hashlib, json,sys
import numpy as np
import pandas as pd
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from fusion_v10 import V10Config,signals_v10,simulate_v10,baseline_5asset_signals,fixed_mix_signals,results,TARGETS
from fusion_v6 import v6_base,V6Config
from fusion_v7 import V7Config,simulate_v7
from fusion_v8 import V8Config
from fusion_v9 import V9Config,signals_v9

FILE=ROOT/'market_data/longbridge_5asset_fwd_2020_2026_v10.csv'
prices=pd.read_csv(FILE,parse_dates=['week_start']).set_index('week_start')
base=v6_base('week');model=V6Config()
cash=V7Config(red_core=.30,cash_mode='money_price',money_etf_side_cost=.0001)
v8=V8Config();v9=V9Config();v10=V10Config(portfolio_vol_brake=True,portfolio_annual_vol_cap=.10)
s9=signals_v9(prices,base,model,cash,v9,v8)


def run(c:V10Config,with_aux=True):
    s=signals_v10(prices,base,model,cash,c,v9,v8)
    return simulate_v10(prices,s,c),s

def stat_window(f,start,end):
    series=f.nav.loc[start:end]
    return results(series) if len(series)>=3 else None

def paired_bootstrap(a,b,block=8,n=5000,seed=20261121):
    # sampling blocks of weekly log relative performance: the interval describes
    # within-sample sampling variability, not economic regime robustness.
    d=np.log(a.nav.to_numpy()[1:]/a.nav.to_numpy()[:-1])-np.log(b.nav.to_numpy()[1:]/b.nav.to_numpy()[:-1])
    rng=np.random.default_rng(seed);N=len(d)
    annual=[]
    for _ in range(n):
        block_count=int(np.ceil(N/block))
        starts=rng.integers(0,N,size=block_count)
        ix=np.concatenate([(s+np.arange(block))%N for s in starts])[:N]
        annual.append(np.mean(d[ix])*52*100)
    return {'annualized_log_return_spread_pp':float(d.mean()*52*100),
            'block8_90pct_interval_pp':list(np.percentile(annual,[5,95]).round(3)),
            'bootstrap_prob_spread_le_zero':float(np.mean(np.array(annual)<=0))}

def period_rows(f):
    v={}
    for name,start,end in [('2020_2022','2020-10-12','2022-12-31'),('2023_2026','2023-01-01','2026-11-20'),
                           ('2022','2022-01-01','2022-12-31'),('2024','2024-01-01','2024-12-31'),
                           ('2025','2025-01-01','2025-12-31'),('2026','2026-01-01','2026-11-20'),
                           ('JulNov2026','2026-06-29','2026-11-20')]:
        v[name]=stat_window(f,start,end)
    return v

raw_baseline=simulate_v7(prices,base,model,cash,signals=s9)
bench=simulate_v10(prices,baseline_5asset_signals(s9))
assert np.allclose(raw_baseline.nav.to_numpy(),bench.nav.to_numpy(),atol=1e-12), 'V9 benchmark reproduction mismatch'

candidates={
 'V9 three sleeves':bench,
 'Five asset static allocation':simulate_v10(prices,fixed_mix_signals(prices)),
 'V10 asset trend no portfolio vol cap':run(replace(v10,portfolio_vol_brake=False))[0],
 'V10 default portfolio vol cap 10%':run(v10)[0],
 'V10 bond only':run(replace(v10,gold_cap=0))[0],
 'V10 gold only':run(replace(v10,bond_cap=0))[0],
 'V10 inverse vol bond gold':run(replace(v10,allocation='inverse_vol'))[0],
 'V10 vol cap 8%':run(replace(v10,portfolio_annual_vol_cap=.08))[0],
 'V10 vol cap 12%':run(replace(v10,portfolio_annual_vol_cap=.12))[0],
 'V10 trend 12w':run(replace(v10,trend_weeks=12))[0],
 'V10 trend 30w':run(replace(v10,trend_weeks=30))[0],
 'V10 elevated transaction costs':run(replace(v10,tech_fee=.003,red_fee=.003,bond_fee=.0006,gold_fee=.003,money_fee=.0002))[0],
 'V10 doubled execution deadband':run(replace(v10,execution_deadband=.24))[0],
 'V10 execution priority exit':run(replace(v10,priority_exit=True))[0],
 'V10 monthly execution':run(replace(v10,execution_rebalance_weeks=4,priority_exit=True))[0],
}

main=candidates['V10 default portfolio vol cap 10%']
compare=candidates['V9 three sleeves']
# one extra week of execution delay, both variants need identical extra delay
f,s=run(v10)
shifted=s.copy()
for k in TARGETS:shifted[k]=shifted[k].shift(1).fillna(float(shifted[k].iloc[0]))
delay=simulate_v10(prices,shifted,v10)
candidates['V10 one extra week execution delay']=delay

# Explicit price & signals snapshots.
main.to_csv(ROOT/'market_data/v10_weekly_trades.csv',index_label='week_start',encoding='utf-8-sig')
bench.to_csv(ROOT/'market_data/v10_v9_comparable_baseline.csv',index_label='week_start',encoding='utf-8-sig')

out={'release':'V10 research candidate','asof':'2026-11-20',
     'research_date':'2026-11-21',
     'parameters':asdict(v10),
     'source':'Longbridge forward-adjusted weekly ETF prices (provider adjusted close), 159915.SZ 515180.SH 511260.SH 518880.SH 511880.SH',
     'market_data_file':str(FILE.relative_to(ROOT)),
     'market_data_sha256':hashlib.sha256(FILE.read_bytes()).hexdigest(),
     'n_weeks':len(prices), 'from_label':str(prices.index[0].date()),'to_label':str(prices.index[-1].date()),
     'notes':['Week-start index labels refer to weekly bars that include the close of the last trading day of the week.',
              'End-of-bar signal is filled at the close of the NEXT weekly bar. Trading at next-week close is a simplifying assumption, not live fill.',
              'Data vendor forward-adjusted prices have not been independently verified versus each ETF total-return and distributions.',
              'Both candidate design and analysis were performed after observing 2026 sell-off; this is retrospective, NOT untouched holdout testing.',
              'No leverage. 30% minimum dividend core; bond cap 30%, gold cap 15%, 8% reserve money. Historical returns after model transaction fees.',
              'Volatility cap uses last 26 weekly returns, 50% covariance shrinkage; cannot ensure realized volatility bound or protect price gaps.'],
     'results':{},'current':{},'bootstrap':{},'stress':{}}
for name,f in candidates.items():
    out['results'][name]={'full':results(f.nav),'periods':period_rows(f),
                           'turnover':round(float(f.turnover.sum()),3),
                           'fees_initial_nav_units_pct':round(float(f.fee.sum()*100),3),
                           'mean_weights_pct':{k:round(float(f['held_'+k].mean()*100),2) for k in ('tech','red','bond','gold','money')}}
out['bootstrap']=paired_bootstrap(main,compare)
q=main.iloc[-1]
out['current']={'week_label':str(main.index[-1].date()),
                'weights':{k:float(q['target_'+k]) for k in ('tech','red','bond','gold','money')},
                'estimated_portfolio_vol':float(q['estimated_portfolio_vol']),
                'portfolio_risk_multiplier':float(q['portfolio_risk_multiplier']),
                'asset_gates':{'bond':bool(q.bond_eligible),'gold':bool(q.gold_eligible)},
                'momenta':{k:float(q[k]) for k in ('bond_momentum_fast','bond_momentum_slow','gold_momentum_fast','gold_momentum_slow')},
                'labels':'Current signal is based on price through 2026-11-20 close; next weekly close execution proxy.'}
out['stress']={
 'future_gold_minus_15pct_equity_minus_10pct_bond_minus_3pct':{
  'shock':{'tech':-.10,'red':-.10,'bond':-.03,'gold':-.15,'money':.0002},
  'estimated_one_week_portfolio_loss_percent':round(100*sum(q['target_'+a]*v for a,v in {'tech':-.10,'red':-.10,'bond':-.03,'gold':-.15,'money':.0002}.items()),2),
  'note':'hypothetical one-week shock applying end-of-period targets; excludes trade gaps/spreads'},
}
path=ROOT/'verified_fusion_v10_results.json'
path.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
print('Data',len(prices),'ETF weeks; baseline exact:',float(np.max(abs(raw_baseline.nav-bench.nav))))
print('Bootstrap',out['bootstrap']); print('Current',out['current'])
for name,v in out['results'].items():
    a=v['full'];old=v['periods']['2020_2022'];new=v['periods']['2023_2026']
    print(f'{name:43s} total {a["total"]:+6.2f}% MDD {a["mdd"]:6.2f}% CAGR {a["cagr"]:5.2f}% vol {a["vol"]:5.2f}% dev {old["total"]:+6.2f}% later {new["total"]:+6.2f}% turnover {v["turnover"]:5.1f}')
