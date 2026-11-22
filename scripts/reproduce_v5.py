"""Re-run the bundled V5 models and export a manifest and auditable ledgers."""
from pathlib import Path
from dataclasses import asdict
import sys,json,hashlib
import pandas as pd
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from history.strategies.fusion_v5 import FusionConfig,weekly_config,compare,metrics,simulate
from history.strategies.trend_animal_v4 import TrendAnimalV4Config,build_temperature_signals

DATA={
    'daily':('market_data/longbridge_daily_2026.csv','date',FusionConfig()),
    'weekly':('market_data/longbridge_2016_2026_tech_dividend_prices.csv','week_start',weekly_config())
}
windows={
 'daily':{'2026_05_11':('2026-05-01','2026-11-20'),
          '2026_07_11':('2026-06-30','2026-11-20')},
 'weekly':{'all':(None,None),
           '2016_2022':('2016-01-11','2022-12-31'),
           '2023_2026':('2023-01-01','2026-11-20'),
           '2018':('2018-01-01','2018-12-31'),
           '2020_V':('2020-02-01','2020-08-31'),
           '2022':('2022-01-01','2022-12-31'),
           '2026':('2026-09-28','2026-11-20')}
}

def standard(x):
 if isinstance(x,dict):return {k:standard(v) for k,v in x.items()}
 if isinstance(x,list):return [standard(v) for v in x]
 if hasattr(x,'item'):return x.item()
 return x

out={'generated_for':'2026-11-21 close','source':'Longbridge index price data',
    'warning':'Not Nick proprietary formula; price-only indices and retrospective exploration, NOT independent forward performance',
    'execution':'t close signal -> fill at t+1 close -> subsequent returns, costs 15bps per transacted weight',
    'time_label_note':'weekly week_start is the candle opening Monday; price is last close in that trading week',
    'constraints':'no shorting/leverage, cash rate 0; red index excludes dividends; no ETF adjustment/tax/slippage',
    'models':{}}
for k,(file,datefield,c) in DATA.items():
 fn=ROOT/file;df=pd.read_csv(fn,parse_dates=[datefield]).set_index(datefield)
 results=compare(df,c)
 if k=='weekly':
  v4=build_temperature_signals(df,TrendAnimalV4Config())
  results['V4相同延迟重算']=simulate(df,v4,c)
 selected=results['V5完整版']
 selected.to_csv(ROOT/'market_data'/f'v5_{k}_ledger.csv',index_label=datefield,encoding='utf-8-sig')
 out['models'][k]={
   'config':asdict(c),'bars':len(df),'data_start':df.index[0].date().isoformat(),
   'data_end':df.index[-1].date().isoformat(),
   'sha256_csv':hashlib.sha256(fn.read_bytes()).hexdigest(),
   'latest_signal':{'tech_temperature':str(selected.iloc[-1].tech_temperature),
                   'red_temperature':str(selected.iloc[-1].red_temperature),
                   'tech_target':float(selected.iloc[-1].target_tech),
                   'red_target':float(selected.iloc[-1].target_red),
                   'cash_target':float(selected.iloc[-1].target_cash)},
   'windows':{key:{name:standard(metrics(res,start,end)) for name,res in results.items()}
                for key,(start,end) in windows[k].items()}}
manifest=ROOT/'verified_fusion_v5_results.json'
manifest.write_text(json.dumps(out,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
print('manifest',manifest)
for freq in ['weekly','daily']:
 for k in ('all' if freq=='weekly' else '2026_05_11',):
  r=out['models'][freq]['windows'][k]
  for name in ['科技买入持有','红利买入持有','50/50四周再平衡','V5完整版']+(['V4相同延迟重算'] if freq=='weekly' else []):
   x=r[name];print(f'{freq:<6} {k:<13} {name:<17} 累计{x["累计收益%"]:+.2f}%, MDD{x["最大回撤%"]:+.2f}%')
