"""Reproduce V7 vs V8 ETF/index comparisons and re-entry event logs."""
from pathlib import Path
from dataclasses import replace
import hashlib, json, sys
import pandas as pd
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from history.strategies.fusion_v5 import metrics
from history.strategies.fusion_v6 import v6_base,V6Config
from history.strategies.fusion_v7 import V7Config,simulate_v7
from history.strategies.fusion_v8 import V8Config,signals_v8,simulate_v8

p_etf=ROOT/'market_data/longbridge_etf_fwd_2020_2026_v7_money.csv'
p_index=ROOT/'market_data/longbridge_2016_2026_tech_dividend_prices.csv'
prices_etf=pd.read_csv(p_etf,parse_dates=['week_start']).set_index('week_start')
prices_index=pd.read_csv(p_index,parse_dates=['week_start']).set_index('week_start')
base=v6_base('week');model=V6Config()
cash_etf=V7Config(red_core=.30,money_etf_side_cost=.0001)
cash_index=replace(cash_etf,cash_mode='assumed_yield',assumed_cash_annual=.01)
strategies={
  'V7_base': None,
  'V8_default_probe_confirm':V8Config(enable_v_reentry=False),
  'V8_no_probe':V8Config(enable_probe=False,enable_v_reentry=False),
  'V8_fast_vshape_exp':V8Config(enable_v_reentry=True),
  'V8_smaller_probe':V8Config(probe_weight=.08,confirmed_weight=.32,winning_weight=.45,enable_v_reentry=False),
}
windows_etf={'full':(None,None),'2022':('2022-01-01','2022-12-31'),
 '2023_2026':('2023-01-01','2026-11-20'),'2026':('2026-01-01','2026-11-20')}
windows_index={'full':(None,None),'2018':('2018-01-01','2018-12-31'),
 '2020_crash_rebound':('2020-02-01','2020-08-31'),'2022':('2022-01-01','2022-12-31'),
 '2023_2026':('2023-01-01','2026-11-20'),'2026':('2026-01-01','2026-11-20')}
report={
 'model':'V8 right-side tech ETF re-entry candidate',
 'asof':'2026-11-20',
 'data':{'ETF':{'file':p_etf.name,'sha256':hashlib.sha256(p_etf.read_bytes()).hexdigest(),
                 'start':'2020-10-12','end':'2026-11-20','weekly_bars':len(prices_etf)},
         'INDEX':{'file':p_index.name,'sha256':hashlib.sha256(p_index.read_bytes()).hexdigest(),
                  'start':'2016-01-11','end':'2026-11-20','weekly_bars':len(prices_index)}},
 'assumptions':{
   'signals':'Closing signal at week t -> simulation fills close of t+1; week_start label corresponds to trading week, not known price on Monday',
   'etf':'ChiNext 159915.SZ, dividend 515180.SH, money 511880.SH provider forward-adjusted closing quotes as proxy',
   'index':'ChiNext 399006.SZ, dividend price index 000922.SH, cash assumed annualized 1%',
   'cost':'equity 15bp per side, money ETF 1bp per side, 30% dividend core',
   'material_limitation':'ETF series begins after March 2020 pandemic crash; index price series is not ETF total return; retrospective in-sample selected parameters; weekly execution not live tradable record.'},
 'results':{},'current_target':{},'current_indicators':{}
}
for label,prices,cash,windows in [('ETF',prices_etf,cash_etf,windows_etf),('INDEX',prices_index,cash_index,windows_index)]:
 report['results'][label]={}
 for name,cfg in strategies.items():
  df=simulate_v7(prices,base,model,cash) if cfg is None else simulate_v8(prices,base,model,cash,cfg)
  report['results'][label][name]={'windows':{key:metrics(df,*rng) for key,rng in windows.items()},
        'latest':{'tech':float(df.target_tech.iloc[-1]),'dividend':float(df.target_red.iloc[-1]),
                  'money':float(df.target_cash.iloc[-1])},
        'turnover':round(float(df.turnover.sum()),3)}
  if name=='V8_default_probe_confirm':
   df.to_csv(ROOT/'market_data'/('v8_'+label.lower()+'_full_ledger.csv'),encoding='utf-8-sig',index_label='week_start')
   chg=df.v8_reentry_state.ne(df.v8_reentry_state.shift(1).fillna('off'))
   events=df.loc[chg|df.v8_reason.str.contains('低位右侧企稳|双重动量趋势确认',regex=True),
          ['tech','red','target_tech','target_red','target_cash','v8_reentry_state','v8_reason']]
   events.to_csv(ROOT/'market_data'/('v8_'+label.lower()+'_reentry_events.csv'),encoding='utf-8-sig',index_label='week_start')
   if label=='ETF':
    p=prices.tech;red=prices.red
    report['current_indicators']={
      'tech_close':float(p.iloc[-1]),
      'ma4':float(p.rolling(4).mean().iloc[-1]),
      'ma10':float(p.rolling(10).mean().iloc[-1]),
      'ma30':float(p.rolling(30).mean().iloc[-1]),
      'last_4w_return':float(p.iloc[-1]/p.iloc[-5]-1),
      'last_6w_tech_minus_red':float((p.iloc[-1]/p.iloc[-7]-1)-(red.iloc[-1]/red.iloc[-7]-1)),
      'last_12w_min':float(p.iloc[-12:].min()),
      'last_26w_high':float(p.iloc[-26:].max()),
      'probe_signal':bool(df.v8_probe_signal.iloc[-1]),
      'confirm_signal':bool(df.v8_confirm_signal.iloc[-1])}
   report['current_target'][label]=report['results'][label][name]['latest']

out=ROOT/'verified_fusion_v8_results.json'
out.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
print('Saved',out)
for k,results in report['results'].items():
 print(k)
 for s,row in results.items():
  print(' ',s,row['windows']['full']['累计收益%'],row['windows']['full']['最大回撤%'],row['latest'])
print('current',report['current_indicators'])
