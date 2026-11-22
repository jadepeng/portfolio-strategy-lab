from pathlib import Path
import numpy as np
import pandas as pd
import pytest
from history.strategies.fusion_v6 import V6Config,v6_base
from history.strategies.fusion_v7 import V7Config
from history.strategies.fusion_v10 import V10Config,PRICES,TARGETS,simulate_v10
from history.strategies.fusion_v11 import V11Config,risk_project,signals_v11,simulate_v11,_vol


def test_risk_projection_can_reduce_core_if_needed():
    s=np.diag([.30**2,.50**2,.02**2,.20**2,.001**2])
    w=np.array([.10,.30,.30,.10,.20])
    new,fail=risk_project(w,[s],.10,.30)
    assert not fail and new[1]<.30 and _vol(new,s)<=.10+1e-10
    assert abs(new.sum()-1)<1e-10


def test_risk_projection_respects_worst_window():
    s1=np.diag([.40**2,.30**2,.02**2,.25**2,.001**2])
    s2=np.diag([.60**2,.20**2,.03**2,.40**2,.001**2])
    w=np.array([.45,.30,.10,.10,.05]);new,fail=risk_project(w,[s1,s2],.10)
    assert not fail and max(_vol(new,s1),_vol(new,s2))<=.10+1e-10
    assert (new>=0).all() and np.isclose(sum(new),1.)


def test_cash_anchor_failure_is_explicit():
    s=np.eye(5)*.50**2
    new,fail=risk_project([0,.3,.3,.2,.2],[s],.03)
    assert fail and np.allclose(new,[0,0,0,0,1])


def test_compliant_risk_target_unchanged():
    w=np.array([0,.3,.3,0,.4]);new,fail=risk_project(w,[np.eye(5)*.001],.10)
    assert not fail and np.allclose(w,new)


@pytest.fixture(scope='module')
def price():
    rng=np.random.default_rng(114)
    rets=rng.normal(.001,[.04,.025,.004,.03,.0002],size=(180,5))
    return pd.DataFrame(100*np.cumprod(1+rets,axis=0),columns=PRICES,index=pd.date_range('2020-01-06',periods=180,freq='W-MON'))


def calc(p,cfg=V11Config()):
    return signals_v11(p,v6_base('week'),V6Config(),V7Config(red_core=.3,cash_mode='money_price'),V10Config(portfolio_vol_brake=True),cfg)


def test_ensemble_weights_and_worst_risk(price):
    f=calc(price)
    assert np.allclose(f[list(TARGETS)].sum(axis=1),1.)
    assert (f[list(TARGETS)]>=-1e-10).all().all()
    assert (f.worst_window_vol.dropna()<=.10+1e-10).all()
    assert (f.target_bond<=.30+1e-10).all() and (f.target_gold<=.15+1e-10).all()


def test_ensemble_causal(price):
    f=calc(price);prefix=calc(price.iloc[:125])
    np.testing.assert_allclose(f[list(TARGETS)].iloc[:125],prefix[list(TARGETS)],atol=1e-12)


def test_execution_and_trading_cost(price):
    f=calc(price)
    c=V10Config(portfolio_vol_brake=True,execution_deadband=0.)
    run=simulate_v11(price,f,c)
    np.testing.assert_allclose(run[['held_tech','held_red','held_bond','held_gold','held_money']].iloc[1:],f[list(TARGETS)].iloc[:-1],atol=1e-12)
    assert (run.fee>=0).all() and (run.nav>0).all()


def test_ensemble_no_guard_keeps_core(price):
    f=calc(price,V11Config(risk_guard=False))
    assert (f.target_red>=.30-1e-9).all()
