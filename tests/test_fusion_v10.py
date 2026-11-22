"""V10 correctness/causality tests (not a claim of future profitability)."""
from pathlib import Path
from dataclasses import replace
import sys
import numpy as np
import pandas as pd
import pytest
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from fusion_v6 import V6Config,v6_base
from fusion_v7 import V7Config,simulate_v7
from history.strategies.fusion_v8 import V8Config
from history.strategies.fusion_v9 import signals_v9
from fusion_v10 import V10Config,signals_v10,simulate_v10,baseline_5asset_signals,results,TARGETS,PRICES

P=pd.read_csv(ROOT/'market_data/longbridge_5asset_fwd_2020_2026_v10.csv',parse_dates=['week_start']).set_index('week_start')
BASE=v6_base('week');MODEL=V6Config();CASH=V7Config(red_core=.3,cash_mode='money_price',money_etf_side_cost=.0001)

def signal(prices=P,c=V10Config(portfolio_vol_brake=True)):
    return signals_v10(prices,BASE,MODEL,CASH,c)


def test_market_data_aligned_count_and_endpoints():
    assert P.shape==(313,5)
    assert P.index.is_unique and P.index.is_monotonic_increasing
    assert abs(P.bond_511260_adj.iloc[0]-104.196)<1e-8
    assert abs(P.gold_518880_adj.iloc[-1]-7.282)<1e-8


def test_reproduces_v9_nav_exactly():
    prior=signals_v9(P,BASE,MODEL,CASH)
    old=simulate_v7(P,BASE,MODEL,CASH,prior)
    new=simulate_v10(P,baseline_5asset_signals(prior))
    np.testing.assert_allclose(new.nav.to_numpy(),old.nav.to_numpy(),atol=1e-12,rtol=0)


def test_cash_budget_and_red_core_on_every_bar():
    f=signal()
    np.testing.assert_allclose(f[list(TARGETS)].sum(axis=1),1,rtol=0,atol=1e-10)
    assert (f[list(TARGETS)]>=0).all().all()
    assert (f.target_red>=.30-1e-10).all()


def test_bond_gold_caps_and_not_force_buy_when_unqualified():
    f=signal()
    assert (f.target_bond<=.30+1e-10).all()
    assert (f.target_gold<=.15+1e-10).all()
    assert ((f.target_bond[f.bond_eligible==False])==0).all()
    assert ((f.target_gold[f.gold_eligible==False])==0).all()


def test_cash_reserve_except_if_preexisting_v9_needs_cash():
    f=signal()
    base=signals_v9(P,BASE,MODEL,CASH)
    reserve=base.target_cash>=.08
    assert (f.target_money[reserve]>=.08-1e-9).all()


def test_volatility_cap_fits_realized_sample_lookback_estimate():
    f=signal()
    x=f.estimated_portfolio_vol.dropna()
    assert len(x)>150
    assert (x<=.10+1e-9).all()


def test_does_not_use_future_prices_in_signal():
    full=signal()
    prefix=signal(P.iloc[:190])
    np.testing.assert_allclose(full[list(TARGETS)].iloc[:190].to_numpy(),prefix[list(TARGETS)].to_numpy(),atol=1e-12)
    np.testing.assert_allclose(full.estimated_portfolio_vol.iloc[:190].fillna(-1).to_numpy(),
                               prefix.estimated_portfolio_vol.fillna(-1).to_numpy(),atol=1e-12)


def test_future_only_price_change_keeps_past_signals():
    prefix=signal(P)
    bad=P.copy();bad.iloc[-2:,bad.columns.get_loc('gold_518880_adj')]*=1.8
    changed=signal(bad)
    np.testing.assert_allclose(prefix[list(TARGETS)].iloc[:-2].to_numpy(),changed[list(TARGETS)].iloc[:-2].to_numpy(),atol=1e-12)


def test_signal_executes_next_bar_close_not_same_bar():
    idx=pd.date_range('2020-01-06',periods=60,freq='W-MON')
    p=pd.DataFrame({k:np.full(60,10.0) for k in PRICES},index=idx)
    f=pd.DataFrame({k:np.zeros(60) for k in TARGETS},index=idx)
    f['target_red']=1.
    f.loc[idx[30]:,'target_red']=0.
    f.loc[idx[30]:,'target_tech']=1.
    # no effect until bar31, since 30 becomes signal only after that close
    c=V10Config(execution_deadband=0.,tech_fee=0.,red_fee=0.,bond_fee=0.,gold_fee=0.,money_fee=0.)
    out=simulate_v10(p,f,c)
    assert abs(out.held_red.iloc[30]-1)<1e-10
    assert abs(out.held_tech.iloc[30])<1e-10
    assert abs(out.held_tech.iloc[31]-1)<1e-10


def test_zero_cost_backtest_has_no_fees():
    c=V10Config(portfolio_vol_brake=True,tech_fee=0,red_fee=0,bond_fee=0,gold_fee=0,money_fee=0)
    f=simulate_v10(P,signal(P,c),c)
    assert f.fee.max()==0.


def test_candidate_positive_return_and_not_full_drawdown_guarantee():
    f=simulate_v10(P,signal())
    s=results(f.nav)
    assert np.isfinite(s['total']) and np.isfinite(s['mdd'])
    assert s['mdd']<0


@pytest.mark.parametrize('field,value',[('gold_cap',-.1),('bond_cap',.8),('allocation','magic'),('trend_weeks',3),('portfolio_annual_vol_cap',.5),('execution_deadband',.8)])
def test_invalid_config_rejected(field,value):
    c=replace(V10Config(),**{field:value})
    with pytest.raises(ValueError):signal(P,c)


def test_queued_cash_is_not_riskless_when_511880_moves():
    f=signal()
    q=simulate_v10(P,f)
    p2=P.copy()
    p2['cash_511880_adj']*=np.linspace(1,.98,len(P))
    f2=signal(p2)
    q2=simulate_v10(p2,f2)
    assert abs(q.nav.iloc[-1]-q2.nav.iloc[-1])>.001


def test_no_shifted_prices_to_predict_2026_turn():
    f=signal()
    assert float(f.target_tech.iloc[-1])==0
    assert bool(f.bond_eligible.iloc[-1])
    assert not bool(f.gold_eligible.iloc[-1])
    assert .25< float(f.target_money.iloc[-1])<.27


def test_fee_sensitivity_not_an_alpha_assumption():
    a=V10Config(portfolio_vol_brake=True)
    b=replace(a,tech_fee=.003,red_fee=.003,bond_fee=.0006,gold_fee=.003,money_fee=.0002)
    v=simulate_v10(P,signal(P,a),a)
    u=simulate_v10(P,signal(P,b),b)
    assert u.nav.iloc[-1]<v.nav.iloc[-1]


def test_inverse_vol_allocator_obeys_cash_budget():
    c=V10Config(allocation='inverse_vol',bond_cap=.35,gold_cap=.18,portfolio_vol_brake=True)
    f=signal(P,c)
    assert (f.target_bond<=.35+1e-10).all()
    assert (f.target_gold<=.18+1e-10).all()
    np.testing.assert_allclose(f[list(TARGETS)].sum(axis=1).values,1,atol=1e-10)
