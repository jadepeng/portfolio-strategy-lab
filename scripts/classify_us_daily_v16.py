"""Import a COMPLETE U.S. daily closing-price CSV to regenerate V16 events.

Input columns: date, QQQ, SPY, SMH, RSP, VIX; one completed New York
market session per row, no duplicated or missing instrument data.

Usage:
  python scripts/classify_us_daily_v16.py --input path/to/US_daily.csv --output market_data/us_daily_risk_latest.csv
The snapshot used for historical V16 tests is NOT silently replaced.
"""
from pathlib import Path
import sys,argparse
import pandas as pd
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from history.v16.overnight import classify_us_daily,close_available_cn
parser=argparse.ArgumentParser(description='V16: complete US daily bars to point-in-time risk events')
parser.add_argument('--input',required=True,type=Path)
parser.add_argument('--output',type=Path,default=ROOT/'market_data'/'us_daily_risk_latest.csv')
args=parser.parse_args()
p=pd.read_csv(args.input,parse_dates=['date']).set_index('date')
if len(p)<3:raise SystemExit('at least 3 complete sessions required')
f=classify_us_daily(p)
cut=f.loc[f.level.isin(['S','M'])].copy()
cut['available_at_cn']=[close_available_cn(i) for i in cut.index]
args.output.parent.mkdir(parents=True,exist_ok=True)
cut.to_csv(args.output,index_label='us_session')
print('Source US sessions:',len(p),'US events:',len(cut),'last source:',str(p.index[-1].date()),'export:',args.output)
print('NOTE: Source file must exclude incomplete current NY trading session. No orders are submitted.')
