from pathlib import Path
from dataclasses import replace,asdict
import sys,json,hashlib
import pandas as pd
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from history.strategies.fusion_v6 import V6Config,v6_base
from history.strategies.fusion_v7 import V7Config
from history.strategies.fusion_v10 import V10Config,signals_v10,simulate_v10,results,TARGETS,fixed_mix_signals
from history.strategies.fusion_v11 import V11Config,signals_v11,simulate_v11
pfile=ROOT/'market_data/longbridge_5asset_fwd_2020_2026_v10.csv'
p=pd.read_csv(pfile,parse_dates=['week_start']).set_index('week_start')
base=v6_base('week');model=V6Config();cash=V7Config(red_core=.30,cash_mode='money_price',money_etf_side_cost=.0001)
v10=V10Config(portfolio_vol_brake=True)
s10=signals_v10(p,base,model,cash,v10)
baseline=simulate_v10(p,s10,v10)
# These are pre-specified robustness comparisons, not a search for best parameters.
configs={'V11 ensemble only':V11Config(risk_guard=False), 'V11 guard only':V11Config(ensemble=False),'V11 combined':V11Config(),'V11 combined 8%':V11Config(risk_cap=.08),'V11 combined 12%':V11Config(risk_cap=.12),'V11 combined priority exit':V11Config(priority_exit=True)}
runs={'V10':baseline,'Static 5 assets':simulate_v10(p,fixed_mix_signals(p),v10)}
for name,cfg in configs.items():
 s=signals_v11(p,base,model,cash,v10,cfg);runs[name]=simulate_v11(p,s,v10,cfg)
fees=replace(v10,tech_fee=.003,red_fee=.003,bond_fee=.0006,gold_fee=.003,money_fee=.0002)
for name in ['V10','V11 combined']:
 f=runs[name];runs[name+' double fees']=simulate_v10(p,f,fees)
 # one additional week delay: queue the already delayed targets one more week
 s=f.copy();s[list(TARGETS)]=s[list(TARGETS)].shift(1).fillna(s[list(TARGETS)].iloc[0]);runs[name+' extra delay']=simulate_v10(p,s,v10)

def bootstrap(a,b,seed=20261121):
 d=np.diff(np.log(a.nav))-np.diff(np.log(b.nav));N=len(d);rng=np.random.default_rng(seed)
 arr=np.array([np.mean(d[((rng.integers(0,N,size=int(np.ceil(N/8)))[:,None]+np.arange(8))%N).ravel()[:N]])*5200 for _ in range(3000)])
 return {'spread_annual_log_pp':float(d.mean()*5200),'interval90_pp':np.percentile(arr,[5,95]).tolist()}

def by_year(f):
 out={}
 for y in range(2021,2027):
    q=f.nav[f.index.year==y];prev=f.nav[f.index.year<y]
    if len(q) and len(prev):out[str(y)]=100*(q.iloc[-1]/prev.iloc[-1]-1)
 return out

def stat(f):
 a=results(f.nav);a['turnover']=float(f.turnover.sum());a['calendar_return']=by_year(f)
 a['mean_cash']=float(f.held_money.mean());return a
out={'data':str(pfile.relative_to(ROOT)),'sha256':hashlib.sha256(pfile.read_bytes()).hexdigest(),'sample':len(p),'method':'retrospective, no untouched holdout; next-week close fills; same V10 trading fees', 'results':{k:stat(f) for k,f in runs.items()},'bootstrap':bootstrap(runs['V11 combined'],baseline),'latest':runs['V11 combined'][list(TARGETS)+['worst_window_vol','core_override','bond_signal_agreement','gold_signal_agreement']].iloc[-1].to_dict()}
for name,st in out['results'].items():print(name,round(st['total'],2),round(st['mdd'],2),round(st['cagr'],2),round(st['positive_52w'],1),round(st['turnover'],1),{k:round(v,2) for k,v in st['calendar_return'].items()})
print('latest',out['latest']);print('bootstrap',out['bootstrap'])
(ROOT/'history/verification/verified_fusion_v11_results.json').write_text(json.dumps(out,ensure_ascii=False,indent=2,default=lambda x:x.item()),encoding='utf-8')
runs['V11 combined'].to_csv(ROOT/'market_data/v11_weekly_ledger.csv',index_label='week_start')
