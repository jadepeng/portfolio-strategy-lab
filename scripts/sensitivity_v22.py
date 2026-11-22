"""Selected-event V22 parameter stress. Not a 2026 yearly or full V18 backtest."""
from pathlib import Path
from itertools import product
from dataclasses import replace
import sys
import pandas as pd
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from history.v13.daily_ohlc_2026 import load as load_cn
from history.v22.intraday import StopConfig,simulate_asset

raw=pd.read_csv(ROOT/'market_data'/'v22_verified_30m_event_samples.csv')
ctx=load_cn();qqq=pd.read_csv(ROOT/'market_data'/'v22_qqq_daily_close_context.csv',parse_dates=['date']).set_index('date')['close']
contexts={'159915.SZ':ctx.tech_close,'515180.SH':ctx.red_close,'QQQ.US':qqq}
starts={'159915.SZ':.35,'515180.SH':.44,'QQQ.US':1.}
floors={'159915.SZ':0.,'515180.SH':.20,'QQQ.US':.30}
rows=[]
for sym,g in raw.groupby('symbol'):
    market=g.market.iloc[0];tz='Asia/Shanghai' if market=='CN' else 'America/New_York'
    bars=g[['open','close']].copy();bars.index=pd.DatetimeIndex(pd.to_datetime(g.datetime_local).dt.tz_localize(tz))
    for shock,bars_needed,slippage in product((.025,.03,.04),(2,3),(.001,.003)):
        cfg=StopConfig(market=market,core_floor=floors[sym],shock_pct=shock,
                       confirm_bars=bars_needed,second_stage_bars=bars_needed+2,slippage=slippage)
        hist,orders=simulate_asset(bars,sym,cfg,start_weight=starts[sym],daily_context=contexts[sym])
        if len(hist)<2:continue
        a=hist.iloc[0];z=hist.iloc[-1]
        base=100*(z.base_nav/a.base_nav-1)
        guard=100*(z.nav/a.nav-1)
        rows.append({'symbol':sym,'market':market,'shock_pct':100*shock,'confirm_bars':bars_needed,
                     'slippage_bp':10000*slippage,'risk_trades':len(orders),
                     'event_window_base_pct':round(base,4),
                     'event_window_guard_pct':round(guard,4),
                     'difference_pp':round(guard-base,4)})
out=ROOT/'research_outputs'/'V22_selected_event_parameter_stress.csv';pd.DataFrame(rows).to_csv(out,index=False,encoding='utf-8-sig')
print(pd.DataFrame(rows).groupby('symbol')['difference_pp'].agg(['min','max','median']).round(3))
print('rows',len(rows),'file',out)
