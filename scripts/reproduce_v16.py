"""Reproduce V16 daily causal-US spillover experiment (2026, offline data)."""
from pathlib import Path
import sys,json
from dataclasses import replace
import numpy as np
import pandas as pd
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from fusion_v6 import v6_base,V6Config
from fusion_v7 import V7Config
from fusion_v10 import V10Config,simulate_v10,PRICES,TARGETS
from fusion_v11 import V11Config,signals_v11
from v13.regime_v13 import RegimeConfig,signals_v13
from v13.daily_ohlc_2026 import load as load_cn_daily
from v14.market_snapshot import load_weekly
from v14.crowding import build_factors,classify,state_overlay,OverlayStateConfig
from v15.us_snapshot import load_us_prices
from v15.cross_market import us_to_cn_overlay,CrossConfig
from v16.overnight import DailyGateConfig,load_event_snapshot,align_events_to_cn_open
from v16.daily_replay import run_daily_replay,annual_stats

prices=pd.read_csv(ROOT/'market_data'/'verified_5asset_weekly_v13.csv',parse_dates=['week_start']).set_index('week_start')
cn_daily=load_cn_daily()
market=load_weekly()
config=V10Config(portfolio_vol_brake=True)
base=signals_v11(prices,v6_base('week'),V6Config(),V7Config(red_core=.30,cash_mode='money_price',money_etf_side_cost=.0001),config,V11Config(risk_guard=False))
trend=signals_v13(prices,base,RegimeConfig(hold_weeks=8))
cn=state_overlay(prices,trend,classify(build_factors(prices,market)),OverlayStateConfig(freeze_weeks=3))
cn_us=us_to_cn_overlay(prices,cn,load_us_prices(),CrossConfig())
# We test *both* V14 and V15's weekly base: the daily risk layer adds only
# incremental actions to otherwise identical weekly signals.
weekly_baselines={'V14 China (no weekly US gate)':cn,'V15 China (weekly US gate)':cn_us}
fees=[config.tech_fee,config.red_fee,config.bond_fee,config.gold_fee,config.money_fee]
events=load_event_snapshot()
common_weeks=cn_daily.groupby(cn_daily.index.to_period('W-SUN')).last()
common_weeks.index=common_weeks.index.start_time
ref=prices.loc[common_weeks.index]
for et,pr in [('tech_close','tech'),('red_close','red'),('bond_close','bond_511260_adj'),('gold_close','gold_518880_adj'),('money_close','cash_511880_adj')]:
    err=(common_weeks[et]-ref[pr]).abs().max()
    assert err<.00051,(et,err)

out=ROOT/'research_outputs';out.mkdir(exist_ok=True)
lines=[];sample_logs={}
for base_name,weekly_targets in weekly_baselines.items():
    historic=simulate_v10(prices,weekly_targets,config)
    prev_week=historic.loc[historic.index<cn_daily.index[0]].iloc[-1]
    prior_weights=prev_week[[f'held_{k}' for k in ('tech','red','bond','gold','money')]].to_numpy(float)
    pclose=prices.loc[prev_week.name,list(PRICES)].to_numpy(float)
    yearly_hist=100*(historic.nav.iloc[-1]/prev_week.nav-1)
    for mode in ('off','freeze','halve','full_exit','freeze_local','halve_local','exit_local'):
        local=mode.endswith('_local')
        base_mode={'freeze_local':'freeze','halve_local':'halve','exit_local':'full_exit'}.get(mode,mode)
        state=DailyGateConfig(mode=base_mode,moderate_freeze_days=3,severe_freeze_days=5,require_local_weakness=local,emergency_fee_slippage=.0005)
        sim=run_daily_replay(cn_daily,weekly_targets,prior_weights,state,config,events,pclose)
        val=annual_stats(sim.nav)
        l={**{'weekly_base':base_name,'daily_mode':mode},**{k:round(float(v),3) for k,v in val.items()},
           'emergency_days':int((sim.emergency_turnover>0).sum()),'weekly_rebalance_days':int((sim.weekly_turnover>0).sum()),
           'emergency_cost_pct_initial':round(float(sim.emergency_cost.sum())*100,4),
           '2026_weekly_reference_pct':round(float(yearly_hist),3)}
        lines.append(l)
        sample_logs[(base_name,mode)]=sim
        if base_name.startswith('V15'):
            sim.to_csv(out/f'V16_CN_2026_{mode}_daily_ledger.csv',index_label='date')
    if abs(next(x['annual_pct'] for x in lines if x['weekly_base']==base_name and x['daily_mode']=='off')-yearly_hist)>0.06:
        print('WARNING: daily vs V15 reference mismatch',base_name,next(x['annual_pct'] for x in lines if x['weekly_base']==base_name and x['daily_mode']=='off'),yearly_hist)

pd.DataFrame(lines).to_csv(out/'V16_daily_cross_market_comparison.csv',index=False,encoding='utf-8-sig')
mapped=align_events_to_cn_open(cn_daily.index,events)
mapped.to_csv(out/'V16_US_events_mapped_to_CN_open.csv',index_label='cn_trade_day')
summary={'version':'V16 daily US-to-CN research','effective_through_CN':'2026-11-20','us_event_snapshot_last_close':'2026-11-20 NY16:00 (2026-11-21 CN05:00); not tradable before the following Chinese session',
 'CN_bars':len(cn_daily),'US_observed_moderate_severe_events':len(events),'events_mapped_to_2026_CN_open':len(mapped),
 'accounting':'CN weekly prior-week signal fills at this week final A close; emergency US shock fills at A next-open, same for all variants','benchmark_caveat':'US event subset; live feed must supply COMPLETE daily closes; historical parameters were selected after observing 2026',
 'results':lines}
(out/'V16_verified_results.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False),encoding='utf-8')
print(pd.DataFrame(lines).to_string(index=False))
print('Mapped 2026 US events',len(mapped),'end-of-week last event',events.iloc[-1].available_at_cn)
print('Latest CN models:',{k:float(v) for k,v in cn_us.iloc[-1][list(TARGETS)].items()})
