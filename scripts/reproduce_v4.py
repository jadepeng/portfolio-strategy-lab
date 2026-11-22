"""Recompute V4 vs V3 static historical-price comparison from bundled CSV."""
import json
from pathlib import Path
from dataclasses import replace
import sys
import pandas as pd
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root))
from history.strategies.trend_animal_v4 import TrendAnimalV4Config,backtest_v4,get_stage_snapshot
from history.strategies.v3_engine import V3Config,run_v3,metrics

src=root/'market_data/longbridge_2016_2026_tech_dividend_prices.csv'
df=pd.read_csv(src,parse_dates=['week_start']).set_index('week_start')
base=TrendAnimalV4Config()
v3=run_v3(df,V3Config(),compare=True)
res={
 '科技持有':v3['科技持有'],'红利持有':v3['红利持有'],
 '50/50再平衡':v3['50/50再平衡'],'V3多因子防御':v3['V3多因子防御'],
 'V4趋势温度+纪律':backtest_v4(df,base),
 'V4不含肥尾兑现':backtest_v4(df,replace(base,champagne_overlay=False)),
 'V4不含危险/移动止损':backtest_v4(df,replace(base,risk_overlay=False)),
 'V4慢试仓敏感性':backtest_v4(df,replace(base,enter_fraction=.25,cooldown_weeks=6)),
 'V4快趋势敏感性':backtest_v4(df,replace(base,mom_short=8,mom_long=20,short_ma=8,long_ma=26)),
}
windows={'全样本':(None,None),'2016—2022':('2016-01-11','2022-12-26'),
         '2023—2026（回顾性分段）':('2023-01-02','2026-11-20'),
         '2018下跌':('2018-01-01','2018-12-31'),
         '2020 V形':('2020-02-01','2020-08-31'),
         '2022熊市':('2022-01-01','2022-12-31'),
         '2026本轮':('2026-09-28','2026-11-20')}
report={'source':'Longbridge history weekly close, price-only index; verified at 2026-11-21',
        'input':str(src.name),'rows':len(df),'last_week_start':df.index[-1].strftime('%Y-%m-%d'),
        'limitations':['中证红利为价格指数，不含股息','周末收盘成交为理想化执行','2023年后是回顾性时段，并非真正样本外','模型为独立公开概念量化代理，非趋势动物官方温度'],
        'settings':base.__dict__,'snap':get_stage_snapshot(res['V4趋势温度+纪律']),
        'windows':{name:{k:{key:(None if pd.isna(v) else float(v) if isinstance(v,(float,int)) and not isinstance(v,bool) else v) for key,v in metrics(df_bt,start,end).items()} for k,df_bt in res.items()} for name,(start,end) in windows.items()}}
out=root/'history/verification/verified_trend_animal_v4_results.json'
out.write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
print(f'Wrote {out} from {len(df)} verified weekly observations')
for name in ['科技持有','红利持有','50/50再平衡','V3多因子防御','V4趋势温度+纪律']:
 r=report['windows']['全样本'][name]
 print(f'{name:<18} total {r["累计收益%"]:+6.2f}% maxDD {r["最大回撤%"]:+6.2f}%')
