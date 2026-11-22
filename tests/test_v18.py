"""V18 regression: Gemini integration must preserve causal execution and budgets."""
from __future__ import annotations
from dataclasses import replace
from functools import lru_cache
import numpy as np
import pandas as pd
import pytest
from history.strategies.fusion_v10 import TARGETS
from history.v14.market_snapshot import load_weekly
from history.v15.us_snapshot import load_us_prices,load_us_open
from history.v18.integrated import V18CNConfig,V18USConfig,build_cn_v18_weekly,build_us_v18


@lru_cache(maxsize=1)
def cn_data():
    p=pd.read_csv('market_data/verified_5asset_weekly_v13.csv',parse_dates=['week_start']).set_index('week_start')
    return p,load_weekly(),load_us_prices()


@lru_cache(maxsize=1)
def cn_model():
    p,m,u=cn_data()
    return build_cn_v18_weekly(p,m,u)


def test_cn_default_current_weights_and_budget():
    _,sig,sim=cn_model()
    tar=sig[list(TARGETS)].to_numpy(float)
    assert np.all(tar>=-1e-12)
    assert np.allclose(tar.sum(axis=1),1)
    assert np.all(tar[:,2]<=.35+1e-9)
    assert np.all(tar[:,3]<=.20+1e-9)
    assert np.all(tar[:,0]<=.55+1e-9)
    assert np.all(tar[:,1]>=.20-1e-9)  # regime risk budget may temporarily reduce 30% core to 20%
    np.testing.assert_allclose(tar[-1],np.array([0.,.44,.35,0.,.21]),rtol=0,atol=1e-10)
    assert sim.nav.iloc[-1]>1


def test_cn_latest_future_close_cannot_change_prior_signals():
    p,m,u=cn_data()
    _,original,_=cn_model()
    changed=p.copy()
    changed.iloc[-1,changed.columns.get_loc('tech')]*=1.7
    changed.iloc[-1,changed.columns.get_loc('gold_518880_adj')]*=.65
    _,new,_=build_cn_v18_weekly(changed,m,u)
    np.testing.assert_allclose(original[list(TARGETS)].iloc[:-1].to_numpy(float),
        new[list(TARGETS)].iloc[:-1].to_numpy(float),rtol=0,atol=1e-12)


@pytest.mark.parametrize('config',[V18CNConfig(bond_cap=.9),V18CNConfig(gold_cap=.31),
    V18CNConfig(tech_max=-.1),V18CNConfig(execution_deadband=.5),V18CNConfig(allocation='bad')])
def test_cn_reject_invalid(config):
    with pytest.raises(ValueError): config.to_v17()


def test_us_latest_weights_and_floor():
    p=load_us_prices();o=load_us_open()
    cfg=V18USConfig(qqq_core=.3)
    s,h=build_us_v18(p,cfg,o)
    np.testing.assert_allclose(s[['target_qqq','target_bil']].sum(axis=1),1,atol=1e-12)
    assert s.target_qqq.min()>=.3-1e-10
    assert np.isclose(s.target_qqq.iloc[-1],.3)
    assert np.isclose(h.iloc[-1].held_qqq+h.iloc[-1].held_bil,1)
    assert (h.source_signal_week.iloc[1:].to_numpy() == p.index[:-1].to_numpy()).all()


def test_us_future_data_isolation():
    p=load_us_prices()
    s,_=build_us_v18(p)
    changed=p.copy()
    changed.iloc[-1,changed.columns.get_loc('QQQ')]*=1.5
    s2,_=build_us_v18(changed)
    np.testing.assert_allclose(s.target_qqq.iloc[:-1].to_numpy(),s2.target_qqq.iloc[:-1].to_numpy(),atol=1e-12,rtol=0)


@pytest.mark.parametrize('kwargs',[dict(ma_windows=(10,10,30)),dict(vol_window=0),dict(qqq_side_fee=-.01),
    dict(fill_mode='this_week_close'),dict(qqq_core=1.3)])
def test_us_reject_invalid(kwargs):
    with pytest.raises(ValueError): V18USConfig(**kwargs).to_v17()


def test_us_priority_exit_optin_is_budgeted_and_not_a_promised_backtest_gain():
    p=load_us_prices();o=load_us_open()
    s,baseline=build_us_v18(p,V18USConfig(),o)
    s2,priority=build_us_v18(p,V18USConfig(priority_exit=True),o)
    assert s.target_qqq.equals(s2.target_qqq)
    assert np.all(priority.nav.to_numpy()>0)
    assert np.allclose(priority.held_qqq+priority.held_bil,1)
    # With Gemini's current defaults the optional urgent path makes no difference.
    np.testing.assert_allclose(priority.nav.to_numpy(),baseline.nav.to_numpy(),rtol=0,atol=1e-10)


def test_us_rebalance_uses_yesterday_week_signal_not_current():
    p=load_us_prices();o=load_us_open()
    _,baseline=build_us_v18(p,V18USConfig(),o)
    changed=p.copy();changed.loc[changed.index[-1],'QQQ']=changed.loc[changed.index[-1],'QQQ']*1.7
    _,bumped=build_us_v18(changed,V18USConfig(),o)
    # Earlier completed wealth entries cannot change when only the final close changes.
    np.testing.assert_allclose(baseline.nav.iloc[:-1],bumped.nav.iloc[:-1],atol=1e-12,rtol=0)
