"""Deterministic V9 research release, including baseline and stress results."""
from pathlib import Path
from dataclasses import replace, asdict
import hashlib, json, sys
import pandas as pd
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from history.strategies.fusion_v5 import metrics
from history.strategies.fusion_v6 import V6Config, v6_base
from history.strategies.fusion_v7 import V7Config, simulate_v7
from history.strategies.fusion_v8 import V8Config, simulate_v8
from history.strategies.fusion_v9 import V9Config, simulate_v9, signals_v9
from history.strategies.fusion_v9_execution import V9ExecutionConfig, simulate_v9_execution

ETFPATH=ROOT/'market_data/longbridge_etf_fwd_2020_2026_v7_money.csv'
INDEXPATH=ROOT/'market_data/longbridge_2016_2026_tech_dividend_prices.csv'
inputs={'ETF':pd.read_csv(ETFPATH,parse_dates=['week_start']).set_index('week_start'),
        'INDEX':pd.read_csv(INDEXPATH,parse_dates=['week_start']).set_index('week_start')}
for name,p in inputs.items():
    assert p.index.is_unique and p.index.is_monotonic_increasing
    assert p[['tech','red']].notna().all().all()
base=v6_base('week');model=V6Config();v8=V8Config();v9=V9Config()
windows={'ETF':{'完整区间':(None,None),'2020-2022':('2020-10-12','2022-12-31'),
                 '2023-2026':('2023-01-01','2026-11-20'),'2026':('2026-01-01','2026-11-20')},
         'INDEX':{'完整区间':(None,None),'2016-2019':('2016-01-11','2019-12-31'),
                  '2020-2022':('2020-01-01','2022-12-31'),
                  '2023-2026':('2023-01-01','2026-11-20'),'2026':('2026-01-01','2026-11-20')}}


def calc_win_stats(f):
    price=f.nav
    ret=price.pct_change(fill_method=None).dropna()
    rollweek=price.pct_change(52,fill_method=None).dropna()
    longest=0;current=0
    for flag in (price<price.cummax()-1e-10):
        current=current+1 if flag else 0
        longest=max(longest,current)
    return {'每周正收益比例%':round(float((ret>0).mean())*100,1),
            '滚动52周正收益比例%':round(float((rollweek>0).mean())*100,1) if len(rollweek) else None,
            '最大回撤恢复等待周数下限':int(longest),
            '累计费用占初始净值%':round(float(f.fee.sum())*100,2)}


def delayed_simulation(prices,cash):
    sig=signals_v9(prices,base,model,cash,v9,v8).copy()
    for k in ('target_tech','target_red','target_cash'):
        sig[k]=sig[k].shift(1).fillna(float(sig[k].iloc[0]))
    return simulate_v7(prices,base,model,cash,sig)

output={'release':'V9','asof':'2026-11-20','generated_on':'2026-11-21',
 'logic':'V8 re-entry + weak relative momentum tech cap + volatility based tech de-risk; no tech re-entry permanently disabled',
 'parameters':asdict(v9),
 'assumptions':{'signal':'周末已知收盘生成信号，后一周收盘调仓；数据索引week_start是周一的标签，信号实际在当周最后一个交易日确认',
                'equity_fee_one_side_bps':15,'money_fee_one_side_bps':1,'dividend_core':.30,
                'ETF':'159915/515180/511880 Longbridge forward adjusted provider close price proxies',
                'INDEX':'399006/000922 price indices; cash assumed 1% annual yield; dividends not reinvested',
                'validation':'All candidates designed with visibility of 2026 crash; historical split is NOT untouched out-of-sample evidence.',
                'normalization':'Both strategies share exact market data, fees and fill conventions.'},
 'inputs':{name:{'filename':path.name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'bars':len(inputs[name]),
                 'first_week_label':str(inputs[name].index[0].date()),'last_week_label':str(inputs[name].index[-1].date())}
           for name,path in [('ETF',ETFPATH),('INDEX',INDEXPATH)]},
 'results':{},'current':{},'cost_scenarios':{},'parameter_stability':{},'execution_latency_stress':{},'execution_layer_experiment':{}}
for label,prices in inputs.items():
    cash=V7Config(red_core=.30,cash_mode='money_price' if label=='ETF' else 'assumed_yield',
                  assumed_cash_annual=.01,money_etf_side_cost=.0001)
    runs={
        '红利买入持有':simulate_v7(prices,base,model,cash,mode='red'),
        '50/50月度平衡':simulate_v7(prices,base,model,cash,mode='half'),
        'V7防守基准':simulate_v7(prices,base,model,cash),
        'V8买回规则':simulate_v8(prices,base,model,cash,v8),
        'V9动量约束':simulate_v9(prices,base,model,cash,replace(v9,use_volatility_brake=False),v8),
        'V9波动约束':simulate_v9(prices,base,model,cash,replace(v9,use_relative_brake=False),v8),
        'V9动量+波动预算':simulate_v9(prices,base,model,cash,v9,v8),
    }
    output['results'][label]={name:{'windows':{k:metrics(df,*span) for k,span in windows[label].items()},
                                      'risk_diagnostics':calc_win_stats(df)} for name,df in runs.items()}
    final=runs['V9动量+波动预算'];base8=runs['V8买回规则']
    final.to_csv(ROOT/'market_data'/('v9_etf_ledger.csv' if label=='ETF' else 'v9_index_ledger.csv'),index_label='week_start',encoding='utf-8-sig')
    events=final.loc[(final.v9_risk_override)|(final.v8_reentry_state!=final.v8_reentry_state.shift().fillna('off')),
        ['tech','red','v8_reentry_state','v9_tech_vs_red_6w','v9_tech_annual_vol','v9_tech_reduction',
         'target_tech','target_red','target_cash','v8_reason','v9_reason']]
    events.to_csv(ROOT/'market_data'/('v9_etf_events.csv' if label=='ETF' else 'v9_index_events.csv'),index_label='week_start',encoding='utf-8-sig')
    x=final.iloc[-1]
    output['current'][label]={'week_label':str(final.index[-1].date()),
         'tech_target':float(x.target_tech),'red_target':float(x.target_red),'money_target':float(x.target_cash),
         'relative_momentum_6wk':float(x.v9_tech_vs_red_6w),'tech_ann_vol_12wk':float(x.v9_tech_annual_vol),
         'tech_temperature':str(x.tech_temperature),'red_temperature':str(x.red_temperature),
         'reentry_state':str(x.v8_reentry_state),'reason':str(x.v9_reason)}
    output['execution_latency_stress'][label]={'one_week':metrics(final),'two_weeks':metrics(delayed_simulation(prices,cash))}
    output['execution_layer_experiment'][label] = {}
    for bnd in (.12,.16,.20,.24):
        x=simulate_v9_execution(prices,base,model,cash,v9,V9ExecutionConfig(ordinary_turnover_deadband=bnd,forced_equity_reduction=.04),v8)
        output['execution_layer_experiment'][label][f'deadband_{bnd:.0%}']={k:metrics(x)[k] for k in ('累计收益%','最大回撤%','累计双边换手','显著调仓次数')}
        if bnd == .20:
            x.to_csv(ROOT/'market_data'/('v9_etf_experimental_execution.csv' if label=='ETF' else 'v9_index_experimental_execution.csv'),index_label='week_start',encoding='utf-8-sig')
    output['cost_scenarios'][label]={}
    for eqbp in (5,15,30):
        for mbp in (1,5):
            b=replace(base,cost_per_side=eqbp/10000.)
            c=replace(cash,money_etf_side_cost=mbp/10000.)
            v=simulate_v9(prices,b,model,c,v9,v8)
            output['cost_scenarios'][label][f'equity_{eqbp}bp_money_{mbp}bp']={k:metrics(v)[k] for k in ('累计收益%','最大回撤%','累计双边换手')}
    output['parameter_stability'][label]={}
    for voltarget in (.25,.30,.35,.40):
        cfg=replace(v9,target_annual_vol=voltarget)
        df=simulate_v9(prices,base,model,cash,cfg,v8)
        output['parameter_stability'][label][f'vol_target_{voltarget:.0%}']={k:metrics(df)[k] for k in ('累计收益%','最大回撤%')}
    for core in (.25,.30,.35,.40):
        c=replace(cash,red_core=core)
        df=simulate_v9(prices,base,model,c,v9,v8)
        output['parameter_stability'][label][f'red_core_{core:.0%}']={k:metrics(df)[k] for k in ('累计收益%','最大回撤%')}

p=ROOT/'verified_fusion_v9_results.json'
p.write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf-8')
print('Saved',p)
for label in inputs:
 print('\n',label)
 for n,v in output['results'][label].items():
  s=v['windows']['完整区间']; r=v['windows'].get('2023-2026',{})
  print(f'{n}: total={s["累计收益%"]:+.2f}%, mdd={s["最大回撤%"]:.2f}%, 2023+= {r.get("累计收益%"):+.2f}%')
 print('Latest',output['current'][label]);print('cost 30bp',output['cost_scenarios'][label]['equity_30bp_money_5bp'])
 print('two week',output['execution_latency_stress'][label]['two_weeks']['累计收益%'])
