from pathlib import Path
from dataclasses import replace
import sys
import pandas as pd
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from fusion_v10 import *
from fusion_v6 import v6_base,V6Config
from fusion_v7 import V7Config,simulate_v7
from history.strategies.fusion_v8 import V8Config
from history.strategies.fusion_v9 import V9Config,signals_v9,simulate_v9
from fusion_v5 import metrics
p=pd.read_csv(ROOT/'market_data/longbridge_5asset_fwd_2020_2026_v10.csv',parse_dates=['week_start']).set_index('week_start')
base=v6_base('week');model=V6Config(); cash=V7Config(red_core=.30,cash_mode='money_price',money_etf_side_cost=.0001)
sig=signals_v9(p,base,model,cash,V9Config(),V8Config())
print('input shape',p.shape)
original=simulate_v9(p,base,model,cash)
recomputed=simulate_v10(p,baseline_5asset_signals(sig))
print('Old baseline',metrics(original));print('New baseline',results(recomputed.nav),'NAV delta',float(recomputed.nav.iloc[-1]-original.nav.iloc[-1]))
variants={
'V9 3assets':(None,None),
'5asset fixed':('fixed',None),
'bond+gold cash collateral':('v10',V10Config()),
'bond only':('v10',V10Config(gold_cap=0)),
'gold only':('v10',V10Config(bond_cap=0)),
'inverse vol allocation':('v10',V10Config(allocation='inverse_vol')),
'portvol 10%':('v10',V10Config(portfolio_vol_brake=True)),
'portvol 8%':('v10',V10Config(portfolio_vol_brake=True,portfolio_annual_vol_cap=.08)),
'portvol 12%':('v10',V10Config(portfolio_vol_brake=True,portfolio_annual_vol_cap=.12)),
'bondgold monthly':('v10',V10Config(execution_rebalance_weeks=4,execution_deadband=.08)),
'bondgold weekly deadband':('v10',V10Config(execution_deadband=.08)),
'longer trend':('v10',V10Config(trend_weeks=30,momentum_weeks=26,slow_momentum_weeks=26)),
'fast trend':('v10',V10Config(trend_weeks=12,momentum_weeks=8,slow_momentum_weeks=26)),
'cash hurdle off':('v10',V10Config(signal_cash_hurdle=False)),
'bond no gold trend30':('v10',V10Config(gold_cap=0,trend_weeks=30)),
}
res={}
for name,(kind,c) in variants.items():
    if kind is None: f=recomputed
    elif kind=='fixed':f=simulate_v10(p,fixed_mix_signals(p))
    else: f=simulate_v10(p,signals_v10(p,base,model,cash,c),c)
    res[name]=f
    full=results(f.nav)
    a=f.loc['2020-10-12':'2022-12-31'].nav
    b=f.loc['2023-01-01':].nav
    c26=f.loc['2026-01-01':].nav
    print(f"{name:30s}  total {full['total']:+7.2f}%  MDD {full['mdd']:7.2f}% CAGR {full['cagr']:5.2f}% 2020-22 {results(a)['total']:+7.2f}% 2023+ {results(b)['total']:+7.2f}%  2026 {results(c26)['total']:+7.2f}%   turnover {f.turnover.sum():6.1f} fees {100*f.fee.sum():5.2f}%")
for name in ['V9 3assets','bond+gold cash collateral','inverse vol allocation','portvol 10%','bondgold monthly']:
    print('CURRENT',name,res[name][['target_tech','target_red','target_bond','target_gold','target_money']].iloc[-1].round(4).to_dict())
