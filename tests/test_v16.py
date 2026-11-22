"""V16 temporal availability, regional risk budget and reproduction tests."""
from dataclasses import replace
import numpy as np
import pandas as pd
import pytest
from v16.overnight import (DailyGateConfig,classify_us_daily,close_available_cn,china_open_at,
                           load_event_snapshot,align_events_to_cn_open)
from v16.daily_replay import run_daily_replay,annual_stats
from v13.daily_ohlc_2026 import load
from fusion_v10 import TARGETS,V10Config

@pytest.fixture(scope='module')
def days():return load()
@pytest.fixture(scope='module')
def events():return load_event_snapshot()

def test_data_verified(days,events):
    assert len(days)==213 and len(events)==34
    assert days.index[0]==pd.Timestamp('2026-01-05')
    assert days.index[-1]==pd.Timestamp('2026-11-20')
    assert float(days.tech_close.iloc[-1])==2.230
    assert events.index[-1]==pd.Timestamp('2026-11-20')

def test_new_york_dst_conversion():
    assert close_available_cn(pd.Timestamp('2026-01-20')).hour==5
    assert close_available_cn(pd.Timestamp('2026-06-05')).hour==4
    assert close_available_cn(pd.Timestamp('2026-11-20')).hour==5

def test_us_friday_shock_waits_until_cn_monday(days,events):
    m=align_events_to_cn_open(days.index,events)
    assert m.loc['2026-06-08','us_session']==pd.Timestamp('2026-06-05')
    assert m.loc['2026-06-08','available_at_cn']<china_open_at(pd.Timestamp('2026-06-08'))
    assert pd.Timestamp('2026-06-05') not in m.index or m.loc['2026-06-05','us_session']!=pd.Timestamp('2026-06-05')

def test_nov20_us_close_not_usable_nov20_china(days,events):
    m=align_events_to_cn_open(days.index,events)
    assert not ((m.us_session==pd.Timestamp('2026-11-20')).any())
    assert close_available_cn(pd.Timestamp('2026-11-20'))>china_open_at(pd.Timestamp('2026-11-20'))

def test_china_holiday_mapping_preserves_severe():
    us=pd.DataFrame({'level':['S','M','S'],'available_at_cn':[close_available_cn(pd.Timestamp(d)) for d in ['2026-02-16','2026-02-17','2026-02-19']]},index=pd.to_datetime(['2026-02-16','2026-02-17','2026-02-19']))
    cn=pd.to_datetime(['2026-02-13','2026-02-24','2026-02-25'])
    m=align_events_to_cn_open(cn,us)
    assert len(m)==1 and m.iloc[0].level=='S' and m.index[0]==pd.Timestamp('2026-02-24')

def test_classification_no_future_data():
    idx=pd.bdate_range('2026-01-01',periods=9)
    p=pd.DataFrame({'QQQ':[100,100,100,96,95,93,92,95,91],'SPY':[100,100,100,99,99,97,96,97,98],
                    'SMH':[100,100,100,93,91,86,83,90,85],'RSP':[100,100,100,99,98,97,96,98,98],
                    'VIX':[15,16,17,30,31,32,19,17,35]},index=idx)
    f=classify_us_daily(p)
    assert f.loc[idx[3],'level']=='S'
    s=classify_us_daily(p.iloc[:7])
    pd.testing.assert_frame_equal(f.iloc[:7],s)

def test_gating_config_invalid():
    with pytest.raises(ValueError):DailyGateConfig(mode='sell_everything').validate()
    with pytest.raises(ValueError):DailyGateConfig(severe_freeze_days=-1).validate()

def _mock_target(days):
    w=pd.DataFrame(.0,index=pd.date_range('2025-12-22','2026-11-30',freq='W-MON'),columns=TARGETS)
    w.target_tech=.40;w.target_red=.35;w.target_money=.25
    return w

def _mock_run(days,events,mode,local=False):
    prior=np.array([.4,.35,0,0,.25])
    d=days.loc['2026-06-01':'2026-07-15']
    return run_daily_replay(d,_mock_target(d),prior,DailyGateConfig(mode=mode,require_local_weakness=local),
                             V10Config(),events,prior_close=d.iloc[0][['tech_open','red_open','bond_open','gold_open','money_open']].to_numpy(float))

def test_off_is_no_op(days,events):
    a=_mock_run(days,events,'off');b=_mock_run(days,events,'off',local=True)
    pd.testing.assert_series_equal(a.nav,b.nav)
    assert not (a.emergency_turnover>0).any()

def test_risk_events_never_sell_red_bond_gold_at_open(days,events):
    x=_mock_run(days,events,'halve')
    assert (x[['held_tech','held_red','held_bond','held_gold','held_money']]>=-1e-9).all().all()
    assert np.allclose(x[['held_tech','held_red','held_bond','held_gold','held_money']].sum(axis=1),1)
    assert (x.emergency_cost>=0).all()
    assert (x.emergency_turnover>=0).all()

def test_prefix_stability_no_future_shock(days,events):
    d=days.loc[:'2026-07-10']
    x=run_daily_replay(d,_mock_target(d),np.array([.4,.35,0,0,.25]),DailyGateConfig(mode='halve'),events=events)
    mutated=events.copy()
    mutated.loc[pd.Timestamp('2026-11-19'),'level']='S'
    y=run_daily_replay(d,_mock_target(d),np.array([.4,.35,0,0,.25]),DailyGateConfig(mode='halve'),events=mutated)
    pd.testing.assert_series_equal(x.nav,y.nav)

def test_frozen_source_week_cannot_see_current_cn_friday(days,events):
    x=_mock_run(days,events,'freeze')
    assert (x.loc[:'2026-06-05'].emergency_turnover==0).all()
    assert x.us_available_cn.dropna().lt(pd.DatetimeIndex([china_open_at(d) for d in x.loc[x.us_available_cn.notna()].index])).all()
