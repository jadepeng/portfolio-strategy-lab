"""Strict V22 intraday event replay. Research only, does not place orders.

Usage:
  python scripts/reproduce_v22.py
  python scripts/reproduce_v22.py --bars <vendor-30m-file.csv> --daily <context.csv>

Input columns: symbol,market,datetime_local,open,close; local time per market.
The included vendor sample is ONLY 2 sessions per instrument. All results are
window-specific; never compare these event returns to the full V18/V20 years.
"""
from __future__ import annotations
import argparse,json,sys
from pathlib import Path
import numpy as np,pandas as pd
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from history.v13.daily_ohlc_2026 import load as load_cn_daily
from history.v22.intraday import StopConfig,stop_line,normalize_bars,make_daily_lines,simulate_asset

def run(bars_path:Path,daily_path:Path):
    raw=pd.read_csv(bars_path)
    required={'symbol','market','datetime_local','open','close'}
    if not required.issubset(raw):raise ValueError('required data fields missing')
    df=load_cn_daily()
    hist={'159915.SZ':df.tech_close,'515180.SH':df.red_close,'518880.SH':df.gold_close}
    us=pd.read_csv(daily_path,parse_dates=['date']).set_index('date')['close']
    hist['QQQ.US']=us
    out=ROOT/'research_outputs';out.mkdir(exist_ok=True)
    results=[];trades=[];all_lines=[]
    for sym,part in raw.groupby('symbol',sort=True):
        if sym not in hist:raise ValueError('unknown instrument '+sym)
        m=str(part.market.iloc[0]);tz='Asia/Shanghai' if m=='CN' else 'America/New_York'
        if not (part.market==m).all():raise ValueError('mixed market')
        x=part.copy();x.index=pd.DatetimeIndex(pd.to_datetime(x.datetime_local).dt.tz_localize(tz))
        x=x[['open','close']].sort_index()
        cfg=StopConfig(market=m,core_floor={'159915.SZ':0.,'515180.SH':.2,'518880.SH':0.,'QQQ.US':.3}[sym]);cfg.validate()
        lines=make_daily_lines(x,cfg,hist[sym])
        if not lines:raise ValueError('insufficient history for '+sym)
        start={'159915.SZ':.35,'515180.SH':.44,'518880.SH':.10,'QQQ.US':1.0}[sym]
        led,orders=simulate_asset(x,sym,cfg,start_weight=start,daily_context=hist[sym])
        if len(led)==0:raise ValueError('empty ledger')
        first,last=led.iloc[0],led.iloc[-1]
        base_start=first.base_nav;stop_start=first.nav
        # Both paths share the same first after-open mark; a held position not
        # exposed to future bars is not an executed stop order.
        strategy_ret=100*(last.nav/stop_start-1)
        baseline_ret=100*(last.base_nav/base_start-1)
        latestline=lines[max(lines)]
        cfgrow={'symbol':sym,'market':m,'first_bar':str(led.index[0]),'last_bar':str(led.index[-1]),
                'bar_count':len(led),'observed_sessions':len(lines),
                'stop_level_last_session':latestline['line'],
                'stop_line_pct_below_prior_close':round(100*(latestline['line']/latestline['prior_close']-1),3),
                'risk_trades':len(orders),'baseline_event_return_pct':round(baseline_ret,4),
                'guard_event_return_pct':round(strategy_ret,4),
                'delta_pp':round(strategy_ret-baseline_ret,4),
                'end_weight':round(float(last.held_weight),4),
                'source':'Longbridge forward-adjusted 30m event samples; selected dates, not complete history'}
        results.append(cfgrow)
        led.to_csv(out/f'V22_{sym.replace(".","_")}_event_ledger.csv',index_label='datetime')
        for day,vals in lines.items():all_lines.append({'symbol':sym,'market':m,'date':day,**vals})
        if not orders.empty:
            orders=orders.copy();orders.insert(0,'source_symbol',sym)
            trades.extend(orders.to_dict('records'))
    pd.DataFrame(all_lines).to_csv(out/'V22_daily_precommitted_stop_lines.csv',index=False)
    pd.DataFrame(trades).to_csv(out/'V22_event_trade_intents.csv',index=False)
    pd.DataFrame(results).to_csv(out/'V22_event_comparison.csv',index=False,encoding='utf-8-sig')
    planned=[]
    stored=ROOT/'research_outputs'/'V21_dashboard_snapshot.json'
    source_model=json.loads(stored.read_text(encoding='utf-8')) if stored.exists() else {}
    for sym in ('159915.SZ','515180.SH','QQQ.US'):
        cfg=StopConfig(market='US' if sym=='QQQ.US' else 'CN',
                       core_floor={'159915.SZ':0.,'515180.SH':.2,'QQQ.US':.3}[sym])
        line=stop_line(hist[sym],cfg)
        region='US' if sym=='QQQ.US' else 'CN'
        key={'159915.SZ':'tech','515180.SH':'red','QQQ.US':'qqq'}[sym]
        w=float(source_model['models'][region]['baseline']['target'][key]) if source_model else None
        planned.append({'symbol':sym,'market':region,'asset':key,
                        'display_name':{'159915.SZ':'创业板ETF','515180.SH':'红利ETF','QQQ.US':'纳指100 ETF'}[sym],
                        'asof':'2026-11-20','for_session':'next_actual_trading_session',
                        'baseline_target_weight':w,'core_floor':cfg.core_floor,**line})
    payload={'version':'V22','source_asof':'2026-11-20','scope':'short vendor 30-minute selected event windows, NON-representative, no full-portfolio backtest',
             'fill':'two consecutive closed 30m bars below prior-day frozen stop; next regular bar OPEN with 10bp slippage and 15bp fee; gap one-bar confirmation',
             'notice':'first/second-stage drawdown guard cannot guarantee execution at the threshold; stage-2 and cooldown are research logic',
             'samples':results,'lines':all_lines,'next_session_candidates':planned,'intents':trades}
    (out/'V22_intraday_research_snapshot.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    print(pd.DataFrame(results).to_string(index=False))
    print('Order intents:',len(trades))
    return payload

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--bars',type=Path,default=ROOT/'market_data'/'v22_verified_30m_event_samples.csv')
    parser.add_argument('--daily',type=Path,default=ROOT/'market_data'/'v22_qqq_daily_close_context.csv')
    opts=parser.parse_args();run(opts.bars,opts.daily)
