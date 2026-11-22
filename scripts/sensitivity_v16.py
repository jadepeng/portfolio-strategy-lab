from pathlib import Path
import runpy,sys
import pandas as pd
root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root))
a=runpy.run_path(str(root/'scripts/reproduce_v16.py'))
from history.v16.overnight import DailyGateConfig
from history.v16.daily_replay import annual_stats,run_daily_replay
c=a['config'];daily=a['cn_daily'];ev=a['events'];p=a['prices'];weekly=a['cn_us'];h=a['simulate_v10'](p,weekly,c);prev=h.loc[h.index<daily.index[0]].iloc[-1];priorw=prev[['held_tech','held_red','held_bond','held_gold','held_money']].to_numpy(float);prevprice=p.loc[prev.name,list(a['PRICES'])].to_numpy(float)
rows=[]
for window in [3,4,5,7,10,13]:
 for moderate,severe in [(2,3),(3,5),(4,7)]:
  z=run_daily_replay(daily,weekly,priorw,DailyGateConfig(mode='freeze',require_local_weakness=True,cn_lookback=window,moderate_freeze_days=moderate,severe_freeze_days=severe),c,ev,prevprice)
  t=annual_stats(z.nav);rows.append({'local_ma':window,'moderate_days':moderate,'severe_days':severe,'ret2026_pct':round(t['annual_pct'],3),'mdd2026_pct':round(t['mdd_pct'],3),'week_rebalance_days':int((z.weekly_turnover>0).sum())})
result=pd.DataFrame(rows)
result.to_csv(root/'research_outputs/V16_local_confirmation_sensitivity.csv',index=False)
print(result.to_string(index=False))
