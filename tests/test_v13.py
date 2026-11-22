"""V13 reproducibility and anti-leakage tests; research-only."""
from pathlib import Path
import sys
import numpy as np
import pandas as pd
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from history.strategies.fusion_v6 import v6_base,V6Config
from history.strategies.fusion_v7 import V7Config
from history.strategies.fusion_v10 import V10Config,simulate_v10,TARGETS
from history.strategies.fusion_v11 import V11Config,signals_v11
from history.v13.regime_v13 import RegimeConfig,signals_v13
from history.v13.daily_ohlc_2026 import load
from history.v13.daily_state_v13 import DailyExitConfig,simulate

@pytest.fixture(scope='module')
def test_data():
    p=pd.read_csv(ROOT/'market_data/verified_5asset_weekly_v13.csv',parse_dates=['week_start']).set_index('week_start')
    c=V10Config(portfolio_vol_brake=True)
    s=signals_v11(p,v6_base('week'),V6Config(),V7Config(red_core=.30,cash_mode='money_price',money_etf_side_cost=.0001),c,V11Config(risk_guard=False))
    return p,c,s


def test_daily_calendar_and_prices_align_to_weekly(test_data):
    p,_,_=test_data;d=load()
    assert len(d)==213 and d.index[0]==pd.Timestamp('2026-01-05')
    assert pd.Timestamp('2026-02-16') not in p.index
    assert pd.Timestamp('2026-02-16') not in d.index
    wk=d.index-pd.to_timedelta(d.index.weekday,unit='D')
    for a,b in [('tech_close','tech'),('red_close','red'),('bond_close','bond_511260_adj'),('gold_close','gold_518880_adj'),('money_close','cash_511880_adj')]:
        assert np.allclose(d.groupby(wk).last()[a],p.loc['2026',b],atol=.001)


def test_portfolio_weights_normalized(test_data):
    p,_,s=test_data
    x=signals_v13(p,s,RegimeConfig(hold_weeks=12))
    w=x[list(TARGETS)].to_numpy(float)
    assert np.all(w>=-1e-10) and np.allclose(w.sum(axis=1),1)
    assert (x.target_tech<=.70+1e-10).all()


def test_no_future_data_in_signal(test_data):
    p,_,s=test_data
    x=signals_v13(p,s,RegimeConfig(hold_weeks=12))
    altered=p.copy();altered.loc['2026-08-24':,'tech']*=1.7
    s2=signals_v11(altered,v6_base('week'),V6Config(),V7Config(red_core=.30,cash_mode='money_price',money_etf_side_cost=.0001),V10Config(portfolio_vol_brake=True),V11Config(risk_guard=False))
    y=signals_v13(altered,s2,RegimeConfig(hold_weeks=12))
    assert np.allclose(x.loc[:'2026-08-17',list(TARGETS)],y.loc[:'2026-08-17',list(TARGETS)])


def test_historical_baseline_unchanged(test_data):
    p,c,s=test_data
    s_before=s[list(TARGETS)].copy(deep=True)
    _=signals_v13(p,s,RegimeConfig(hold_weeks=12))
    pd.testing.assert_frame_equal(s[list(TARGETS)],s_before)
    f=simulate_v10(p,s,c)
    y=100*(f.loc['2026'].nav.iloc[-1]/f.loc[:'2025'].nav.iloc[-1]-1)
    assert abs(y-.8639672)<.005


def test_2026_next_open_guard_reproducible(test_data):
    p,c,s=test_data
    t=signals_v13(p,s,RegimeConfig(hold_weeks=12))
    w=simulate_v10(p,t,c)
    no,f0=simulate(p,t,w,DailyExitConfig(use_stop=False))
    guarded,f1=simulate(p,t,w,DailyExitConfig(shock=-.07,dd_stop=.08,profit_lock=True))
    assert abs(f0-7.51305)<.03
    assert abs(f1-10.27778)<.03
    assert guarded.index[0]==pd.Timestamp('2026-01-05') and guarded.index[-1]==pd.Timestamp('2026-11-20')
    assert guarded.loc[:'2026-05-15','profit_locked'].sum()==0
    assert guarded[guarded.profit_locked].index[0]==pd.Timestamp('2026-05-18')
    assert (guarded[['tech','red','bond','gold','money']].sum(axis=1)-1).abs().max()<1e-8


def test_daily_future_prices_cannot_change_prior_nav(test_data,monkeypatch):
    from v13 import daily_state_v13
    p,c,s=test_data
    t=signals_v13(p,s,RegimeConfig(hold_weeks=12))
    w=simulate_v10(p,t,c)
    original=load()
    out,_=simulate(p,t,w,DailyExitConfig(shock=-.07,dd_stop=.08,profit_lock=True))
    distorted=original.copy()
    mask=distorted.index>=pd.Timestamp('2026-09-01')
    for col in distorted.columns:distorted.loc[mask,col]*=1.02
    monkeypatch.setattr(daily_state_v13,'load',lambda:distorted)
    altered,_=simulate(p,t,w,DailyExitConfig(shock=-.07,dd_stop=.08,profit_lock=True))
    assert np.allclose(out.loc[:'2026-08-31','nav'],altered.loc[:'2026-08-31','nav'])


def test_invalid_regime_grid_is_not_silently_fitted(test_data):
    p,c,s=test_data
    with pytest.raises(ValueError):signals_v13(p,s,RegimeConfig(hold_weeks=9))
