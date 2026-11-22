"""Regression/causality tests for public-method V20 candidate filters."""
from dataclasses import replace
from functools import lru_cache
import numpy as np
import pandas as pd
import pytest
from history.strategies.fusion_v10 import TARGETS
from v14.market_snapshot import load_weekly
from v15.us_snapshot import load_us_prices,load_us_open
from v18.integrated import build_us_v18,build_cn_v18_weekly,V18USConfig,V18CNConfig
from v20.public_rules import V20Config,candidate_cn,candidate_us,blend_signals,advisory_gate_cn,advisory_gate_us
from v20.integrated import run_cn_v20,run_us_v20,run_advisory_us

@lru_cache(1)
def sources():
    cn=pd.read_csv('market_data/verified_5asset_weekly_v13.csv',parse_dates=['week_start']).set_index('week_start')
    return cn,load_weekly(),load_us_prices(),load_us_open()

@lru_cache(1)
def baseline():
    p,m,u,o=sources()
    c,cs,cl=build_cn_v18_weekly(p,m,u,V18CNConfig())
    us,ul=build_us_v18(u,V18USConfig(),o)
    return (cs,cl,us,ul)

def test_cn_candidate_nonnegative_and_sum_one():
    p,*_=sources()
    x=candidate_cn(p)
    assert not x.isna().any().any()
    np.testing.assert_allclose(x[list(TARGETS)].sum(axis=1),1,atol=1e-12)
    assert (x[list(TARGETS)]>=0).all().all()
    assert (x.target_red>=.2).all()
    assert (x.target_tech<=.45+1e-12).all()
    assert (x.selected_assets<=2).all()

def test_us_candidate_guaranteed_core():
    _,_,u,_=sources();x=candidate_us(u)
    assert x.target_qqq.between(.3,1.).all()
    np.testing.assert_allclose(x.target_qqq+x.target_bil,1,atol=1e-12)
    assert set(x.target_qqq.unique()).issubset({.3,1.})

def test_baseline_exact_when_blend_zero():
    p,m,u,o=sources(); cs,cl,us,ul=baseline()
    c=run_cn_v20(p,m,u,V20Config(cn_blend=0.))
    t=run_us_v20(u,o,V20Config(us_blend=0.))
    np.testing.assert_allclose(c['signal'][list(TARGETS)],cs[list(TARGETS)],atol=1e-12)
    np.testing.assert_allclose(c['ledger'].nav,cl.nav,atol=1e-12)
    np.testing.assert_allclose(t['ledger'].nav,ul.nav,atol=1e-12)

def test_25pct_blending_exact_and_funding():
    p,m,u,o=sources()
    c=run_cn_v20(p,m,u);t=run_us_v20(u,o)
    np.testing.assert_allclose(c['signal'][list(TARGETS)],.75*c['baseline_signal'][list(TARGETS)]+.25*c['public_signal'][list(TARGETS)],atol=1e-12)
    np.testing.assert_allclose(t['signal'][['target_qqq','target_bil']],.75*t['baseline_signal'][['target_qqq','target_bil']]+.25*t['public_signal'][['target_qqq','target_bil']],atol=1e-12)
    assert (c['ledger'].nav>0).all() and (t['ledger'].nav>0).all()

def test_candidate_causal_cn_future_unrelated_to_past():
    p,*_=sources();a=candidate_cn(p)
    z=p.copy();z.iloc[-1,z.columns.get_loc('tech')]*=2
    b=candidate_cn(z)
    np.testing.assert_allclose(a[list(TARGETS)].iloc[:-1],b[list(TARGETS)].iloc[:-1],atol=1e-12,rtol=0)

def test_candidate_causal_us_future_unrelated_to_past():
    _,_,u,_=sources();a=candidate_us(u)
    z=u.copy();z.iloc[-1,z.columns.get_loc('QQQ')]*=1.7
    b=candidate_us(z)
    np.testing.assert_allclose(a[['target_qqq','target_bil']].iloc[:-1],b[['target_qqq','target_bil']].iloc[:-1],atol=1e-12,rtol=0)

def test_advisory_only_prevents_increment():
    p,_,u,_=sources(); cs,_,us,_=baseline()
    cn=advisory_gate_cn(p,cs); usa=advisory_gate_us(u,us)
    for key in ('tech','red','gold'):
        assert (cn['target_'+key]<=cs['target_'+key]+1e-12).all()
    assert (usa.target_qqq<=us.target_qqq+1e-12).all()
    np.testing.assert_allclose(cn[list(TARGETS)].sum(axis=1),1)
    np.testing.assert_allclose(usa.target_qqq+usa.target_bil,1)

@pytest.mark.parametrize('changes',[{'cn_blend':-0.1},{'us_blend':1.1},{'trend_weeks':2},{'momentum_weeks':80},{'skip_recent_weeks':0},{'skip_recent_weeks':30},{'top_n':5},{'red_core':.31},{'us_core':2},{'cn_fee_multiplier':0},{'us_fee_multiplier':12},{'us_fill_mode':'current_week'}])
def test_rejects_invalid_config(changes):
    with pytest.raises(ValueError):V20Config(**changes).validate()

def test_rejects_calendar_mismatch():
    a=pd.DataFrame({'target_qqq':[1.],'target_bil':[0.]},index=pd.DatetimeIndex(['2026-01-01']))
    b=a.copy();b.index=pd.DatetimeIndex(['2026-01-02'])
    with pytest.raises(ValueError):blend_signals(a,b,('target_qqq','target_bil'),.25)

def test_us_advisory_causal_and_market_no_lookahead():
    _,_,u,o=sources();_,_,us,_=baseline()
    a=advisory_gate_us(u,us)
    assert a.public_warning_restricted.sum()>0
    z=u.copy();z.iloc[-1,z.columns.get_loc('QQQ')]*=1.5
    # Baseline signal must be recomputed; only earlier signals are compared.
    b,_=build_us_v18(z,V18USConfig(),o)
    c=advisory_gate_us(z,b)
    np.testing.assert_allclose(a.target_qqq.iloc[:-1],c.target_qqq.iloc[:-1],rtol=0,atol=1e-12)

def test_restricted_signal_executes_next_week():
    _,_,u,o=sources();_,signal,_,ledger=run_advisory_us(u,o)
    assert (ledger.source_signal_week.iloc[1:].to_numpy()==u.index[:-1].to_numpy()).all()
    np.testing.assert_allclose(ledger.held_qqq+ledger.held_bil,1,atol=1e-10)

@pytest.mark.parametrize('bad',[float('nan'),0,-1])
def test_refuse_invalid_market_prices(bad):
    _,_,u,_=sources();z=u.copy();z.iloc[4,z.columns.get_loc('QQQ')]=bad
    with pytest.raises(ValueError):candidate_us(z)
