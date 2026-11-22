"""Offline V13 research reproduction; no order submission.
Run from repository root: python scripts/reproduce_v13.py
"""
from dataclasses import replace
from pathlib import Path
import json,sys
import numpy as np
import pandas as pd
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from history.strategies.fusion_v6 import v6_base,V6Config
from history.strategies.fusion_v7 import V7Config
from history.strategies.fusion_v10 import V10Config,simulate_v10,results,PRICES,TARGETS
from history.strategies.fusion_v11 import V11Config,signals_v11
from v13.regime_v13 import RegimeConfig,signals_v13
from v13.daily_ohlc_2026 import load
from v13.daily_state_v13 import DailyExitConfig,simulate

p=pd.read_csv(ROOT/'market_data/verified_5asset_weekly_v13.csv',parse_dates=['week_start']).set_index('week_start')
c=V10Config(portfolio_vol_brake=True)
s11=signals_v11(p,v6_base('week'),V6Config(),V7Config(red_core=.30,cash_mode='money_price',money_etf_side_cost=.0001),c,V11Config(risk_guard=False))
assert len(p)==313 and p.index.is_monotonic_increasing and not p.index.has_duplicates
assert pd.Timestamp('2026-02-16') not in p.index
# Weekly OHLC is aligned to *the containing calendar week*, but the signal only
# becomes visible after the final actual session of that week.
daily=load();week=(daily.index-pd.to_timedelta(daily.index.weekday,unit='D'))
agg=daily.groupby(week).last()
for name,col in [('tech_close','tech'),('red_close','red'),('bond_close','bond_511260_adj'),('gold_close','gold_518880_adj'),('money_close','cash_511880_adj')]:
    if not np.allclose(agg[name].to_numpy(float),p.loc['2026',col].to_numpy(float),atol=.001):
        raise AssertionError(f'daily/weekly series mismatch: {name}')

rows=[];daily_runs={};last_sig=None;last_run=None
for name,hold in [('V11-A',None),('V13-8week',8),('V13-12week',12)]:
    signal=s11 if hold is None else signals_v13(p,s11,RegimeConfig(hold_weeks=hold))
    wf=simulate_v10(p,signal,c)
    def yy(y):return 100*(wf.loc[str(y)].nav.iloc[-1]/wf.loc[:str(y-1)].nav.iloc[-1]-1)
    d=results(wf.nav)
    modes={
        'weekly_close':round(yy(2026),3),
        'next_open':round(simulate(p,signal,wf,DailyExitConfig(use_stop=False))[1],3),
        'daily_urgent_exit':round(simulate(p,signal,wf,DailyExitConfig(shock=-.07,dd_stop=.08))[1],3),
        'profit_guard':round(simulate(p,signal,wf,DailyExitConfig(shock=-.07,dd_stop=.08,profit_lock=True))[1],3),
    }
    rows.append({'model':name,'all_sample_return_percent':float(round(d['total'],3)),'max_drawdown_percent':float(round(d['mdd'],3)),'2022_percent':float(round(yy(2022),3)),'2024_percent':float(round(yy(2024),3)),'2025_percent':float(round(yy(2025),3)),**modes,'full_sample_turnover':float(round(wf.turnover.sum(),2))})
    if hold==12:
        last_sig=signal;last_run=wf
        da,dres=simulate(p,signal,wf,DailyExitConfig(shock=-.07,dd_stop=.08,profit_lock=True))
        daily_runs['V13_2026_daily_profitguard']=da
outdir=ROOT/'research_outputs';outdir.mkdir(exist_ok=True)
result=pd.DataFrame(rows);result.to_csv(outdir/'V13_results_reproduced.csv',index=False)
last_sig.to_csv(outdir/'V13_final_target_signals.csv',index_label='week_start')
last_run.to_csv(outdir/'V13_final_weekly_ledger.csv',index_label='week_start')
for name,data in daily_runs.items():data.to_csv(outdir/(name+'.csv'),index_label='date')
report={'method':'Research, retrospectively calibrated to 2026. No untouched out-of-sample holdout.', 'data':'Longbridge 313 matched weekly ETFs, 213 sessions in 2026. Forward-adjusted closes and opens.', 'fee_model':'V10 per-side L1 transaction costs; historical simulated fills', 'results':rows, 'latest_model_target':{k:round(float(last_sig[k].iloc[-1]),4) for k in TARGETS}, 'guard_first_day':str(daily_runs['V13_2026_daily_profitguard'].loc[lambda df:df.profit_locked].index[0].date())}
(outdir/'V13_verified_results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(result.to_string(index=False));print('latest',report['latest_model_target'],'profit guard first day',report['guard_first_day'])
