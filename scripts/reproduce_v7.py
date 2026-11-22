"""Build reproducible V7 strategy comparison and risk-window summaries."""
from pathlib import Path
from dataclasses import replace
import sys,hashlib,json
import pandas as pd
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from history.strategies.fusion_v5 import metrics
from history.strategies.fusion_v6 import V6Config,v6_base
from history.strategies.fusion_v7 import eval_cases,simulate_v7,V7Config
path=ROOT/'market_data/longbridge_etf_fwd_2020_2026_v7_money.csv'
prices=pd.read_csv(path,parse_dates=['week_start']).set_index('week_start')
assert len(prices)==313
runs=eval_cases(prices)
windows={'all':(None,None),'2022':('2022-01-01','2022-12-31'),'2023_2026':('2023-01-01','2026-11-20'),'2026':('2026-01-01','2026-11-20'),'crash2026':('2026-06-01','2026-11-20')}
result={'description':'V7: dividend equity core + rule-based trend trading + money ETF vs cash',
  'asof':'2026-11-20 close','source':path.name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
  'money_symbol':'511880.SH','money_data':'Longbridge 511880 forward_adjust true weekly close, proxy pending fund-level distribution reconciliation',
  'fee_model':'15bp per side on equity trades; 1bp per side on money-market ETF; idle cash fee=0; t close signals fill t+1 close',
  'limit':'Retrospectively selected; this is an in-sample stress-test, not forecast; money ETF adjusted quotes may differ from NAV total returns; missing ETF dividends/account fees.',
  'data_rows':len(prices), 'first':str(prices.index.min().date()),'last':str(prices.index.max().date()),
  'results':{},'current':{},'sensitivity':{}}
for name,r in runs.items():
    result['results'][name]={k:metrics(r,*v) for k,v in windows.items()}
    result['current'][name]={k:round(float(r.iloc[-1][k]),4) for k in ['target_tech','target_red','target_cash']}
    result['results'][name]['total_fees_%']=round(float(r.fee.sum()/r.nav.iloc[0]*100),2)
    if name in ('V6红利20%+511880','红利40%+511880','红利20%+严格动量最多加20%+511880'):
       r.to_csv(ROOT/'market_data'/('v7_'+{'V6红利20%+511880':'20money','红利40%+511880':'40money','红利20%+严格动量最多加20%+511880':'dynamic'}[name]+'_ledger.csv'),encoding='utf-8-sig',index_label='week_start')
base=v6_base('week')
for bp in [1,5,15]:
   p=V7Config(red_core=.2,cash_mode='money_price',money_etf_side_cost=bp/10000)
   r=simulate_v7(prices,base,V6Config(),p)
   result['sensitivity'][str(bp)+'bp_money_cost']=metrics(r)
for y in [.005,.01,.015,.02]:
   p=V7Config(red_core=.2,cash_mode='assumed_yield',assumed_cash_annual=y)
   r=simulate_v7(prices,base,V6Config(),p)
   result['sensitivity'][str(y)+'_fixed_cash_yield']=metrics(r)
file=ROOT/'history/verification/verified_fusion_v7_results.json'
file.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
for k in result['results']:
 print(k, 'all', result['results'][k]['all']['累计收益%'],'mdd',result['results'][k]['all']['最大回撤%'],
       '2022',result['results'][k]['2022']['累计收益%'],'2026',result['results'][k]['2026']['累计收益%'], 'target',result['current'][k])
print('saved',file)
