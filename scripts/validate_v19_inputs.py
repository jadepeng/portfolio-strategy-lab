"""Point-in-time data integrity checks for V19, no network connection required."""
from pathlib import Path
import sys,json
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import pandas as pd
import numpy as np
from fusion_v10 import PRICES
from v14.market_snapshot import load_weekly
from v15.us_snapshot import load_us_prices,load_us_open
from v15.cross_market import observed_us_by_cn_week

cn=pd.read_csv(ROOT/'market_data/verified_5asset_weekly_v13.csv',parse_dates=['week_start']).set_index('week_start')
us=load_us_prices();uo=load_us_open();volume=load_weekly()
checks={}
for label,table,cols in [('CN_price',cn,PRICES),('US_price',us,('QQQ','SPY','SMH','RSP','BIL')),('US_week_open',uo,('QQQ','BIL'))]:
    assert len(table)>=300 and table.index.is_monotonic_increasing and table.index.is_unique
    assert (table.index.dayofweek==0).all()
    assert table.loc[:,cols].notna().all().all() and np.isfinite(table.loc[:,list(cols)].to_numpy()).all()
    assert (table.loc[:,cols]>0).all().all()
    checks[label]={'weeks':len(table),'first':str(table.index[0].date()),'last':str(table.index[-1].date())}
assert us.index.equals(uo.index)
assert volume.index.is_unique and volume.index.is_monotonic_increasing
miss=pd.date_range(cn.index[0],cn.index[-1],freq='W-MON').difference(cn.index)
checks['CN_skipped_full_week_holidays']=[str(x.date()) for x in miss]
# Source US week available to A should always precede the A week, not future US Friday.
mapped=observed_us_by_cn_week(cn.index,us)
valid=mapped.us_source_week.dropna()
assert (valid.index>valid.values).all(), 'possible US same-week lookahead'
checks['US_lagged_mapping']={'checked':len(valid),'last_cn_week':str(valid.index[-1].date()),'last_observed_US_week':str(valid.iloc[-1].date())}
print(json.dumps(checks,ensure_ascii=False,indent=2))
(ROOT/'research_outputs/V19_input_quality.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2),encoding='utf8')
