"""V15: US risk gating is causal, regional budgets remain separate."""
import numpy as np
import pandas as pd
import pytest
from history.strategies.fusion_v10 import TARGETS
from history.v15.us_snapshot import load_us_prices,load_us_open
from history.v15.us_strategy import USConfig,us_factors,us_signals,simulate_us
from history.v15.cross_market import CrossConfig,observed_us_by_cn_week,us_to_cn_overlay

@pytest.fixture(scope='module')
def us():return load_us_prices()
@pytest.fixture(scope='module')
def cn():return pd.read_csv('market_data/verified_5asset_weekly_v13.csv',parse_dates=['week_start']).set_index('week_start')

def mock_cn_signals(cn):
    x=pd.DataFrame(0.,index=cn.index,columns=TARGETS)
    x['target_red']=.3;x['target_money']=.2;x['target_tech']=.5
    return x

def test_us_snapshot_valid(us):
    assert len(us)==313 and not us.index.has_duplicates
    assert us.index[0]==pd.Timestamp('2020-11-23')
    assert us.QQQ.iloc[-1]==608.45
    assert us.SPY.iloc[-1]==689.81
    assert load_us_open().loc[us.index[-1],'QQQ']==671.04

def test_us_last_trend_is_defensive(us):
    s=us_signals(us,USConfig(qqq_core=.30))
    assert s.target_qqq.iloc[-1]==.3
    assert s.qqq_30w.iloc[-1]>700
    assert s.us_bear.iloc[-1] and s.us_shock.iloc[-1]

def test_us_simulation_no_leverage_fee_lag(us):
    c=USConfig(qqq_core=.3)
    s=us_signals(us,c)
    x=simulate_us(us,s,c,load_us_open())
    assert (x[['held_qqq','held_bil']].between(0,1) if hasattr(x[['held_qqq','held_bil']],'between') else (x[['held_qqq','held_bil']]>=0)).all().all()
    assert np.allclose(x.held_qqq+x.held_bil,1)
    assert x.nav.notna().all() and x.nav.gt(0).all()
    assert (x.cost>=0).all()
    assert x.iloc[0].turnover==0

def test_us_no_future_data(us):
    n=201
    a=us_signals(us).iloc[:n]
    b=us_signals(us.iloc[:n])
    pd.testing.assert_frame_equal(a,b)

def test_us_same_week_close_not_seen_in_china(cn,us):
    seen=observed_us_by_cn_week(cn.index,us)
    week=pd.Timestamp('2026-11-16')
    assert seen.loc[week,'us_source_week']==pd.Timestamp('2026-11-09')
    assert seen.loc[week,'qqq_1w']==pytest.approx(us.QQQ.pct_change().loc['2026-11-09'])
    assert seen.loc[week,'qqq_1w']!=pytest.approx(us.QQQ.pct_change().loc[week])

def test_future_us_crash_does_not_change_current_cn_week(cn,us):
    c=mock_cn_signals(cn)
    x=us_to_cn_overlay(cn,c,us)
    mod=us.copy(); mod.loc['2026-11-16','QQQ']*=.55
    y=us_to_cn_overlay(cn,c,mod)
    week=pd.Timestamp('2026-11-16')
    pd.testing.assert_series_equal(x.loc[week,list(TARGETS)],y.loc[week,list(TARGETS)])
    assert x.loc[week,'us_source_week']==y.loc[week,'us_source_week']

def test_disabled_overlay_exactly_retains_cn_weights(cn,us):
    c=mock_cn_signals(cn)
    x=us_to_cn_overlay(cn,c,us,CrossConfig(enabled=False))
    pd.testing.assert_frame_equal(x[list(TARGETS)],c[list(TARGETS)])

def test_risk_gate_never_directly_sells_tech_or_other_assets(cn,us):
    c=mock_cn_signals(cn)
    x=us_to_cn_overlay(cn,c,us)
    assert (x.target_tech<=c.target_tech+1e-12).all()
    assert np.allclose(x[list(TARGETS)].sum(axis=1),1)
    for k in ['target_red','target_bond','target_gold']:
        assert np.allclose(x[k],c[k])
    assert np.allclose(x.target_money+c.target_tech,x.target_tech+c.target_money)
    assert (x.cross_market_tech_cut>=-1e-12).all()

def test_us_bear_can_cap_new_cn_tech(cn,us):
    s=mock_cn_signals(cn)
    s.loc[:'2026-11-09','target_tech']=0.
    s.loc[:'2026-11-09','target_money']=.7
    x=us_to_cn_overlay(cn,s,us)
    assert (x.cross_market_tech_cut>0).any()
    assert x.loc['2026-11-16','target_tech']<=.12

def test_cross_gate_prefix_stability(cn,us):
    mock=mock_cn_signals(cn)
    x=us_to_cn_overlay(cn,mock,us)
    t=pd.Timestamp('2026-08-10')
    y=us_to_cn_overlay(cn.loc[:t],mock.loc[:t],us.loc[:t])
    pd.testing.assert_frame_equal(x.loc[y.index,list(TARGETS)],y[list(TARGETS)])

def test_invalid_configs(cn,us):
    with pytest.raises(ValueError):us_signals(us,USConfig(qqq_core=2))
    with pytest.raises(ValueError):us_to_cn_overlay(cn,mock_cn_signals(cn),us,CrossConfig(bear_new_entry_cap=2))
