"""Build standalone, offline V21 research dashboard from actual V18/V20 strategy runs.

Example: python scripts/build_v21_dashboard.py
No external network or live quotes; uses frozen 2026-11-20 snapshots.
"""
from __future__ import annotations
import csv
from datetime import datetime,timezone
import json
from pathlib import Path
import sys
import pandas as pd
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from v14.market_snapshot import load_weekly
from v15.us_snapshot import load_us_prices,load_us_open
from v18.integrated import build_cn_v18_weekly,build_us_v18,V18USConfig
from v20.integrated import run_advisory_cn,run_advisory_us,run_cn_v20,run_us_v20
from v20.public_rules import V20Config
from v21.analytics import (verify_market_history,describe,equity_curve,yearly,
                           latest_weights,execution_gate,frozen_signal_record,sha256_file)

ASOF='2026-11-20'
OUT=ROOT/'research_outputs';OUT.mkdir(exist_ok=True)
cn_file=ROOT/'market_data'/'verified_5asset_weekly_v13.csv'
cn=pd.read_csv(cn_file,parse_dates=['week_start']).set_index('week_start')
us=load_us_prices();us_open=load_us_open();market=load_weekly()
cn_quality=verify_market_history(cn[['tech','red','bond_511260_adj','gold_518880_adj','cash_511880_adj']],label='CN')
us_quality=verify_market_history(us,label='US')
# CN and US each have 313 weekly bars but start on DIFFERENT Mondays.
# Preserve native time indexes; cross-market signal alignment is performed
# by V15/V16 causal mapper, never by positional row number.
if cn.index[-1].strftime('%Y-%m-%d')!='2026-11-16':raise AssertionError('unreviewed live data')
if us.index[-1].strftime('%Y-%m-%d')!='2026-11-16':raise AssertionError('unreviewed US live data')

ccfg,cn_base_signals,cn_base=build_cn_v18_weekly(cn,market,us)
_,cn_guard_signals,_,cn_guard=run_advisory_cn(cn,market,us,V20Config())
cn_blend_out=run_cn_v20(cn,market,us,V20Config(cn_blend=.25))
us_base_signals,us_base=build_us_v18(us,V18USConfig(),us_open)
_,us_guard_signals,_,us_guard=run_advisory_us(us,us_open,V20Config())
us_blend_out=run_us_v20(us,us_open,V20Config(us_blend=.25))

cweights=('held_tech','held_red','held_bond','held_gold','held_money')
uweights=('held_qqq','held_bil')
versions={
 'CN':{
  'baseline':('V18冻结基准',cn_base_signals,cn_base),
  'advisory':('V20新增仓双重否决',cn_guard_signals,cn_guard),
  'blend25':('25%公开规则集成',cn_blend_out['signal'],cn_blend_out['ledger'])},
 'US':{
  'baseline':('V18多周期趋势',us_base_signals,us_base),
  'advisory':('V20新增仓双重否决',us_guard_signals,us_guard),
  'blend25':('25%公开规则集成',us_blend_out['signal'],us_blend_out['ledger'])}}

strategies={};comparisons=[]
for region,cases in versions.items():
    strategies[region]={}
    for key,(name,sig,led) in cases.items():
        cols=cweights if region=='CN' else uweights
        weights=latest_weights(sig,tuple('target_'+x.removeprefix('held_') for x in cols))
        realized={x.removeprefix('held_'):round(float(led.iloc[-1][x]),6) for x in cols}
        # Independently rounded holdings can total 0.999999/1.000001. Assign
        # only the display rounding residue to the cash sleeve, preserving NAV.
        cash_key='money' if region=='CN' else 'bil'
        realized[cash_key]=round(realized[cash_key]+1-sum(realized.values()),6)
        val={'key':key,'label':name,'metrics':describe(led),
             'target':weights,'held':realized,'history':equity_curve(led,cols=cols),
             'annual':yearly(led),'cutoff':'2026-11-20',
             'signalWeek':str(sig.index[-1].date()),
             'tradeAtNextWeek':True,
             'lastReason':str(sig.iloc[-1].get('cross_market_reason','')) if region=='CN' else str(sig.iloc[-1].get('us_trend_decision','')),
             'riskState':str(sig.iloc[-1].get('us_risk_state','')) if region=='US' else ('熊市/低量能' if cn_base_signals.iloc[-1].get('market_weak',False) else '低风险增量观察')}
        strategies[region][key]=val
        comparisons.append({'market':region,'version':key,'name':name,'execution': '下一周收盘' if region=='CN' else '下一周开盘',**val['metrics']})

us_stress=pd.read_csv(OUT/'V20_advisory_robustness.csv',encoding='utf-8-sig')
cn_stress=pd.read_csv(OUT/'V18_CN_cost_stress.csv',encoding='utf-8-sig')
boot=json.loads((OUT/'V20_bootstrap.json').read_text(encoding='utf-8'))
gates={
 'CN':execution_gate(us_stress,strategies['CN']['baseline']['metrics'],strategies['CN']['advisory']['metrics'],market='CN'),
 'US':execution_gate(us_stress,strategies['US']['baseline']['metrics'],strategies['US']['advisory']['metrics'],market='US',bootstrap_interval=tuple(boot['US_advisory']['empirical_90pct_interval_pp']))}

csig=cn_base_signals.iloc[-1];usig=us_base_signals.iloc[-1]

# Use proper source hash for the underlying dataset. US prices are supplied by snapshot Python source.
records=[frozen_signal_record('CN',ASOF,str(cn.index[-1].date()),strategies['CN']['baseline']['target'],
           {'fill':'next_week_close','default':'V18_CN_35_20','proactive_2026_selected':False},source_sha=sha256_file(cn_file)),
         frozen_signal_record('US',ASOF,str(us.index[-1].date()),strategies['US']['baseline']['target'],
           {'fill':'next_week_open','default':'V18_US_30core','optional_filter':'V20_G_research_only'},
           source_sha=sha256_file(ROOT/'v15'/'us_snapshot.py'))]
(OUT/'V21_frozen_signals.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
(OUT/'V21_strategy_comparison.csv').write_text(pd.DataFrame(comparisons).to_csv(index=False),encoding='utf-8-sig')

# Source-native actual shock alert dates, NOT an invented market breadth data series.
events_df=pd.read_csv(OUT/'V16_US_events_mapped_to_CN_open.csv',encoding='utf-8-sig')
events_df=events_df.fillna('')
events=events_df.tail(33).to_dict('records')

turn=market.copy().reset_index()
market_signal=[]
for i,row in turn.iterrows():
    y={'d':str(row.week_start.date()),'market_turn_100m':int(row.market_turn_100m),
       'tech_share_pct':round(float(100*row.tech_turn_100m/row.market_turn_100m),2),
       'red_share_pct':round(float(100*row.red_turn_100m/row.market_turn_100m),2),
       'market_close':int(row.market_close)}
    if i>=12:
        recent=turn.iloc[i-3:i+1]['market_turn_100m'].mean()
        medium=turn.iloc[i-12:i+1]['market_turn_100m'].mean()
        y['volume_ratio_4_13']=round(float(recent/medium),3)
    else:y['volume_ratio_4_13']=None
    market_signal.append(y)

stress=[]
for _,r in cn_stress[cn_stress.variant=='V18_robust_CN_35_20'].iterrows():
    stress.append({'market':'CN','name':'V18基准','fill':'next_week_close','fee':float(r.fee_factor),
                   'cum':float(r.total_pct),'dd':float(r.mdd_pct),'ret2026':float(r.r26_pct)})
for _,r in us_stress[(us_stress.market=='US')&(us_stress.trend_weeks==40)&
                     (us_stress.momentum_weeks==26)&(us_stress.version.isin(['baseline','guarded']))].iterrows():
    stress.append({'market':'US','name':'V20过滤器' if r.version=='guarded' else 'V18基准',
                   'fill':str(r.fill),'fee':float(r.fee_multiple),
                   'cum':float(r.cumulative_pct),'dd':float(r.max_dd_pct),'ret2026':float(r.r2026_pct)})

# 2026 market volatility and price filters remain as-of snapshots, not real-time feeds.
market_proxy_bearish=(float(market.market_close.iloc[-1])<float(market.market_close.tail(13).mean())
                      and float(market.market_close.iloc[-1])<float(market.market_close.tail(26).mean())
                      and market_signal[-1]['volume_ratio_4_13']<1)
signals={
 'CN':{'growth_stage':str(csig.get('tech_stage','')),'red_stage':str(csig.get('red_stage','')),
       'market_weak':market_proxy_bearish,
       'model_market_weak':bool(csig.get('market_weak',False)),
       'tech_momentum_13w':round(float(csig.get('tech_mom_fast',0)),4),
       'market_liquidity_ratio':market_signal[-1]['volume_ratio_4_13'],
       'cross_market':str(csig.get('us_risk_state_lagged','')),
       'description':'美股作为风险提醒；中国市场趋势决定是否真正减仓。'},
 'US':{'risk':str(usig.get('us_risk_state','')),
       'trend_score':float(usig.get('us_v17_trend_score',0)),
       'qqq_close':float(us.QQQ.iloc[-1]),
       'qqq_ma30':round(float(us.QQQ.tail(30).mean()),2),
       'spy_close':float(us.SPY.iloc[-1]),
       'vol_multiplier':round(float(usig.get('us_v17_vol_mult',1)),3),
       'description':'30% QQ​Q长期核心保留，70%战术资金在BIL中防守。'}}

payload={'metadata':{'product':'Portfolio Strategy Lab','version':'V21','asof':ASOF,
           'generated_utc':datetime.now(timezone.utc).isoformat(timespec='seconds'),
           'historical_tuning':'2020-2026反复参与参数选择，不构成独立样本外',
           'price_basis':'前复权ETF价格近似，分红/冲击成本仍需进一步核对',
           'weekly_labels':'每条日期为周起始日，信号在该周最后交易日收盘后才已知'},
         'models':strategies,'quality':[cn_quality,us_quality],
         'signals':signals,'promotion':gates,'liquidity':market_signal,
         'overnight_events':events,'stress':stress,
         'bootstrap':boot,'frozen_signals':records,
         'links':{'readme':'README.md','report':'V21_中美双市场策略可视化与前瞻研究报告.md'},
         'config':{'cn_assets':[['tech','创业板ETF','159915'],['red','红利ETF','515180'],['bond','10年国债ETF','511260'],['gold','黄金ETF','518880'],['money','货币ETF','511880']],
                   'us_assets':[['qqq','纳指100 ETF','QQQ'],['bil','短期国债 ETF','BIL']]}}
(OUT/'V21_dashboard_snapshot.json').write_text(json.dumps(payload,ensure_ascii=False,separators=(',',':'),allow_nan=False),encoding='utf-8')
(OUT/'V21_promotion_check.json').write_text(json.dumps(gates,ensure_ascii=False,indent=2),encoding='utf-8')

# One-file offline HTML - source CSS/JS kept separately for editing.
html=(ROOT/'web'/'template.html').read_text(encoding='utf-8')
css=(ROOT/'web'/'v21.css').read_text(encoding='utf-8')
js=(ROOT/'web'/'v21.js').read_text(encoding='utf-8')
pjson=json.dumps(payload,ensure_ascii=False,separators=(',',':'),allow_nan=False).replace('</','<\\/')
html=html.replace('/* V21_STYLES */',css).replace('/* V21_CODE */',js).replace('V21_DATA_JSON',pjson)
if 'V21_STYLES' in html or 'V21_CODE' in html or 'V21_DATA_JSON' in html:
    raise RuntimeError('unfilled template tokens')
path=ROOT/'web'/'V21_策略研究驾驶舱.html'
path.write_text(html,encoding='utf-8')
(ROOT/'web'/'index.html').write_text(html,encoding='utf-8')
print('built',path,'bytes',path.stat().st_size)
print('CN baseline',strategies['CN']['baseline']['metrics'],'US baseline',strategies['US']['baseline']['metrics'])
print('US gate:',gates['US'])
