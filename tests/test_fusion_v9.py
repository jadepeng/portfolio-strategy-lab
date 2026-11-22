"""Regression / causality checks for V9 trend-reentry risk budget."""
from pathlib import Path
from dataclasses import replace
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pytest
import numpy as np
import pandas as pd
from fusion_v6 import V6Config, v6_base
from fusion_v7 import V7Config
from fusion_v8 import V8Config, signals_v8
from fusion_v9 import V9Config, validate_v9, signals_v9, simulate_v9

ROOT=Path(__file__).resolve().parents[1]

@pytest.fixture(scope='module')
def etf():
    return pd.read_csv(ROOT/'market_data/longbridge_etf_fwd_2020_2026_v7_money.csv',parse_dates=['week_start']).set_index('week_start')

@pytest.fixture(scope='module')
def index():
    return pd.read_csv(ROOT/'market_data/longbridge_2016_2026_tech_dividend_prices.csv',parse_dates=['week_start']).set_index('week_start')

@pytest.fixture(scope='module')
def config():
    return (v6_base('week'), V6Config(), V7Config(red_core=.3), V9Config())

@pytest.mark.parametrize('dataset',['ETF','INDEX'])
def test_budget_and_dividend_core(etf,index,config,dataset):
    x=etf if dataset=='ETF' else index
    base,money,cash,v9=config
    if dataset=='INDEX':cash=replace(cash,cash_mode='assumed_yield')
    a=signals_v9(x,base,money,cash,v9)
    w=a[['target_tech','target_red','target_cash']]
    np.testing.assert_allclose(w.sum(axis=1),1.,atol=1e-10)
    assert (w>=-1e-10).all().all()
    assert (a.target_red>=cash.red_core-1e-10).all()


def test_v9_can_only_reduce_technology(etf,config):
    b,m,c,v=config
    a=signals_v8(etf,b,m,c,V8Config())
    z=signals_v9(etf,b,m,c,v)
    assert (z.target_tech<=a.target_tech+1e-9).all()
    np.testing.assert_allclose(z.target_red,a.target_red,atol=1e-12)
    assert (z.target_cash>=a.target_cash-1e-9).all()
    assert (z.target_tech<a.target_tech-1e-5).any()


def test_both_filters_matter(etf,config):
    b,m,c,v=config
    normal=signals_v9(etf,b,m,c,v)
    relaxed=signals_v9(etf,b,m,c,replace(v,use_relative_brake=False,use_volatility_brake=False))
    assert (normal.target_tech<relaxed.target_tech-1e-5).any()


def test_reentry_not_permanently_disabled(etf,config):
    df=signals_v9(etf,*config)
    assert (df.target_tech>.10).sum()>12
    assert df.target_tech.iloc[-1]==0
    assert df.target_red.iloc[-1]>.3


def test_no_future_price_leakage(etf,config):
    before=signals_v9(etf,*config)
    changed=etf.copy()
    changed.loc[changed.index[-1],['tech','red','cash_511880_adj']]*=1.35
    after=signals_v9(changed,*config)
    cols=['target_tech','target_red','target_cash','v9_tech_vs_red_6w','v9_tech_annual_vol']
    np.testing.assert_allclose(before[cols].iloc[:-1].to_numpy(), after[cols].iloc[:-1].to_numpy(),equal_nan=True,atol=1e-10)


def test_execution_uses_previous_bar_signal(etf,config):
    b,m,c,v=config
    s=signals_v9(etf,*config)
    df=simulate_v9(etf,*config)
    # A week's post-fill weights equal the preceding week's targets only if
    # the change exceeds min_rebalance. A risk cap does not alter current P&L.
    i=next(j for j in range(35,len(df)) if df.turnover.iloc[j]>.2)
    np.testing.assert_allclose(df[['held_tech','held_red','held_cash']].iloc[i].to_numpy(float),
                               s[['target_tech','target_red','target_cash']].iloc[i-1].to_numpy(float),atol=1e-9)


def test_technology_risk_reduction_from_relative_weakness(etf,config):
    b,m,c,v=config
    with_vol=signals_v9(etf,b,m,c,v)
    no_rel=signals_v9(etf,b,m,c,replace(v,use_relative_brake=False))
    assert (with_vol.target_tech<no_rel.target_tech-1e-6).any()


def test_technology_vol_reduction(etf,config):
    b,m,c,v=config
    with_vol=signals_v9(etf,b,m,c,v)
    no_vol=signals_v9(etf,b,m,c,replace(v,use_volatility_brake=False))
    assert (with_vol.target_tech<no_vol.target_tech-1e-6).any()

@pytest.mark.parametrize('kwargs',[
    {'relative_weeks':1},{'volatility_weeks':1},
    {'weak_relative_threshold':.5},{'weak_relative_cap':.95},
    {'target_annual_vol':0},{'min_vol_multiplier':1.1}
])
def test_invalid_params_rejected(kwargs):
    with pytest.raises(ValueError): validate_v9(V9Config(**kwargs),V7Config(red_core=.3))


def test_cost_monotonic(etf,config):
    b,m,c,v=config
    cheaper=simulate_v9(etf,b,m,c,v)
    dearer=simulate_v9(etf,replace(b,cost_per_side=.003),m,replace(c,money_etf_side_cost=.0005),v)
    assert cheaper.nav.iloc[-1]>dearer.nav.iloc[-1]


def test_weekly_only(etf,config):
    b,m,c,v=config
    with pytest.raises(ValueError): signals_v9(etf,replace(b,frequency='day'),m,c,v)


def test_no_infinite_or_nan_targets(etf,config):
    x=signals_v9(etf,*config)
    assert np.isfinite(x[['target_tech','target_red','target_cash']].to_numpy()).all()
