"""Build offline, no-CDN V22 dashboard with extra 30-minute risk layer.

V21 historical baseline remains untouched. The V22 evidence is separate and
is not silently spliced into old full-year historical strategy returns.
"""
from __future__ import annotations
from pathlib import Path
import json
import pandas as pd
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'research_outputs'
base=json.loads((OUT/'V21_dashboard_snapshot.json').read_text(encoding='utf-8'))
guard=json.loads((OUT/'V22_intraday_research_snapshot.json').read_text(encoding='utf-8'))
base['metadata']['version']='V22'
base['intraday']=guard
traces={}
for x in guard['samples']:
    symbol=x['symbol']
    p=OUT/f"V22_{symbol.replace('.', '_')}_event_ledger.csv"
    frame=pd.read_csv(p)
    traces[symbol]=[{'d':str(row.datetime),'close':round(float(row.close),6),
                     'stop_line':round(float(row.stop_line),6),
                     'stage':int(row.stage),'price_kind':str(row.price_kind)}
                    for row in frame.itertuples()]
base['intraday']['traces']=traces
(OUT/'V22_dashboard_snapshot.json').write_text(json.dumps(base,ensure_ascii=False,separators=(',',':'),allow_nan=False),encoding='utf-8')
text=(ROOT/'web'/'template.html').read_text(encoding='utf-8')
css=(ROOT/'web'/'v21.css').read_text(encoding='utf-8')
js=(ROOT/'web'/'v21.js').read_text(encoding='utf-8')
contents=json.dumps(base,ensure_ascii=False,separators=(',',':'),allow_nan=False).replace('</','<\\/')
text=text.replace('/* V21_STYLES */',css).replace('/* V21_CODE */',js).replace('V21_DATA_JSON',contents)
if any(k in text for k in ('V21_DATA_JSON','/* V21_CODE */','/* V21_STYLES */')):raise RuntimeError('unresolved dashboard assets')
file=ROOT/'web'/'V22_盘中减仓线与双市场策略驾驶舱.html'
file.write_text(text,encoding='utf-8')
(ROOT/'web'/'index.html').write_text(text,encoding='utf-8')
print('V22 built',file,len(text),'bytes', 'guard instruments',len(guard['samples']))
