from dataclasses import replace
from pathlib import Path
import sys
import numpy as np
import pandas as pd
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fusion_v5 import weekly_config,simulate,create_signals,metrics
from fusion_v6 import V6Config,v6_base,signal_v6,simulate_v6,config_checks

ROOT=Path(__file__).resolve().parents[1]
@pytest.fixture(scope='module')
def sample():
    return pd.read_csv(ROOT/'market_data/longbridge_2016_2026_tech_dividend_prices.csv',
                       parse_dates=['week_start']).set_index('week_start')

@pytest.fixture(scope='module')
def sample_day():
    return pd.read_csv(ROOT/'market_data/longbridge_daily_2026.csv',
                       parse_dates=['date']).set_index('date')

def test_invested_weights_and_sum(sample):
    sig=signal_v6(sample,v6_base('week'),V6Config())
    w=sig[['target_tech','target_red','target_cash']]
    assert np.isfinite(w.to_numpy()).all()
    assert (w>=0).all().all()
    assert np.allclose(w.sum(axis=1),1.)
    assert (w.target_red>=.20-1e-12).all()

def test_core_is_blend_not_timing_alpha(sample):
    c=v6_base('week')
    no=signal_v6(sample,c,V6Config(red_core=0))
    core=signal_v6(sample,c,V6Config(red_core=.20))
    assert np.allclose(core.target_tech,.8*no.target_tech)
    assert np.allclose(core.target_red,.20+.8*no.target_red)
    assert np.allclose(core.target_cash,.8*no.target_cash)

def test_future_prices_do_not_change_signal_prefix(sample):
    c=v6_base('week');x=sample.iloc[:375];cfg=V6Config()
    a=signal_v6(x,c,cfg)
    b=signal_v6(sample,c,cfg).iloc[:375]
    cols=['target_tech','target_red','target_cash','tech_temperature','red_temperature']
    pd.testing.assert_frame_equal(a[cols],b[cols])

def test_future_prices_do_not_change_daily_prefix(sample_day):
    c=v6_base('day');cfg=V6Config()
    a=signal_v6(sample_day.iloc[:150],c,cfg)
    b=signal_v6(sample_day,c,cfg).iloc[:150]
    cols=['target_tech','target_red','target_cash']
    pd.testing.assert_frame_equal(a[cols],b[cols])

def test_next_bar_delay(sample_day):
    c=v6_base('day');x=simulate_v6(sample_day,c,V6Config())
    for i in range(70,len(x)):
        # the executed holdings at bar i can never be identical to a known-future t+1 target by construction:
        # instead check exact previous signal or unaltered weights when the trading deadband is active.
        prev=x.iloc[i-1];now=x.iloc[i]
        gross=np.array([prev.held_tech*(now.tech/prev.tech),
                        prev.held_red*(now.red/prev.red),prev.held_cash]).sum()
        pre=np.array([prev.held_tech*now.tech/prev.tech,
                      prev.held_red*now.red/prev.red,prev.held_cash])/gross
        executed=np.array([now.held_tech,now.held_red,now.held_cash])
        if now.turnover>0:
            assert np.allclose(executed,np.array([prev.target_tech,prev.target_red,prev.target_cash]))
        else: assert np.allclose(executed,pre)

def test_augmented_experimental_rules_respect_weights(sample):
    c=v6_base('week');cfg=V6Config(enable_defensive_rotation=True,enable_hysteresis=True,
        round_weight=.05,enable_vol_guard=True,enable_drawdown_speed=True)
    out=signal_v6(sample,c,cfg)
    assert (out[['target_tech','target_red','target_cash']]>=0).all().all()
    assert np.allclose(out[['target_tech','target_red','target_cash']].sum(axis=1),1)

def test_cost_sensitivity(sample):
    c=v6_base('week');cfg=V6Config()
    lo=simulate_v6(sample,replace(c,cost_per_side=0),cfg)
    hi=simulate_v6(sample,replace(c,cost_per_side=.003),cfg)
    assert lo.iloc[-1].nav>hi.iloc[-1].nav

def test_cannot_set_invalid_core():
    with pytest.raises(ValueError):config_checks(V6Config(red_core=.6))
    with pytest.raises(ValueError):config_checks(V6Config(red_core=-.1))

def test_etf_data_is_forward_adjusted_source():
    data=pd.read_csv(ROOT/'market_data/longbridge_etf_fwd_2020_2026_v6.csv')
    assert len(data)==313 and data.week_start.is_unique
    assert data.week_start.iloc[0]=='2020-10-12'
    assert data.week_start.iloc[-1]=='2026-11-16'
    assert abs(data.tech.iloc[0]-2.650)<1e-10
    assert abs(data.red.iloc[0]-.765)<1e-10
    assert abs(data.tech.iloc[-1]-2.230)<1e-10
    assert abs(data.red.iloc[-1]-1.368)<1e-10

def test_july_two_signal_filled_july_three(sample_day):
    x=simulate_v6(sample_day,v6_base('day'),V6Config())
    assert x.loc['2026-07-02','target_tech']==0
    assert x.loc['2026-07-02','held_tech']>0
    assert x.loc['2026-07-03','held_tech']==0
    assert x.loc['2026-07-03','held_red']>=.19

def test_current_targets_are_tactical_not_full_account(sample_day):
    x=simulate_v6(sample_day,v6_base('day'),V6Config())
    assert x.index[-1]==pd.Timestamp('2026-11-20')
    assert np.isclose(x.iloc[-1].target_tech,0)
    assert np.isclose(x.iloc[-1].target_red,.2)
    assert np.isclose(x.iloc[-1].target_cash,.8)
