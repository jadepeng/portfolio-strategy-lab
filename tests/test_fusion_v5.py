from dataclasses import replace
from pathlib import Path
import sys
import numpy as np
import pandas as pd
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from fusion_v5 import FusionConfig,weekly_config,create_signals,simulate,compare,metrics


def daily():
    return pd.read_csv(ROOT/'market_data'/'longbridge_daily_2026.csv',parse_dates=['date']).set_index('date')


def weekly():
    return pd.read_csv(ROOT/'market_data'/'longbridge_2016_2026_tech_dividend_prices.csv',parse_dates=['week_start']).set_index('week_start')


def test_verified_daily_close_samples():
    d=daily()
    assert len(d)==210
    assert d.index[0].strftime('%Y-%m-%d')=='2026-01-08'
    assert d.index[-1].strftime('%Y-%m-%d')=='2026-11-20'
    assert d.loc['2026-07-02','tech']==4017.27
    assert d.loc['2026-07-02','red']==5154.94
    assert d.loc['2026-11-20','tech']==2212.13
    assert d.loc['2026-11-20','red']==5529.78


def test_signal_causality_future_data_does_not_change_past():
    d=daily();c=FusionConfig();a=create_signals(d,c)
    shifted=d.copy();shifted.loc[shifted.index[-1],'tech']*=1.1
    b=create_signals(shifted,c)
    for name in ['target_tech','target_red','target_cash','tech_stage','red_stage']:
        assert np.allclose(a[name].iloc[:-1],b[name].iloc[:-1],equal_nan=True)


def test_next_bar_fill_timing_and_no_return_leakage():
    d=daily();c=FusionConfig();sig=create_signals(d,c)
    r=simulate(d,sig,c)
    date=pd.Timestamp('2026-07-02');i=r.index.get_loc(date)
    assert sig.iloc[i]['target_tech']==0.0
    assert r.iloc[i+1]['held_tech']==0.0 # signal 7/2 fills at close 7/3
    assert np.isclose(r.iloc[i+1]['nav']/r.iloc[i]['nav'],
        (1+r.iloc[i]['held_tech']*(d.iloc[i+1].tech/d.iloc[i].tech-1)
          +r.iloc[i]['held_red']*(d.iloc[i+1].red/d.iloc[i].red-1))
          *(1-r.iloc[i+1].turnover*c.cost_per_side),rtol=1e-12)


def test_weights_sum_and_nonnegative_weekly_daily():
    for d,c in [(daily(),FusionConfig()),(weekly(),weekly_config())]:
        s=create_signals(d,c)
        weights=s[['target_tech','target_red','target_cash']].to_numpy()
        assert np.allclose(weights.sum(axis=1),1)
        assert (weights>=0).all() and (weights<=1).all()
        r=simulate(d,s,c)
        assert (r['nav']>0).all()
        assert (r[['held_tech','held_red','held_cash']]>=0).all().all()


def test_no_trade_cost_higher_nav_for_same_signals():
    d=daily();c=FusionConfig();s=create_signals(d,c)
    a=simulate(d,s,replace(c,cost_per_side=0))
    b=simulate(d,s,replace(c,cost_per_side=.0015))
    assert a.nav.iloc[-1]>b.nav.iloc[-1]
    assert (b.fee>=0).all()


def test_buy_and_hold_is_true_buy_and_hold():
    d=daily();c=FusionConfig();s=create_signals(d,c)
    r=simulate(d,s,c,'tech')
    assert np.isclose(r.nav.iloc[-1],d.tech.iloc[-1]/d.tech.iloc[0])
    assert r.turnover.sum()==0


def test_cash_is_zero_return_when_no_holdings():
    d=daily();c=FusionConfig()
    s=create_signals(d,c)
    s[['target_tech','target_red','target_cash']]=[0.,0.,1.]
    r=simulate(d,s,c)
    assert np.isclose(r.nav.iloc[-1],1.)  # cash earns 0 and never trades


def test_daily_red_early_is_optional_not_mandatory():
    d=daily();c=FusionConfig()
    base=compare(d,c)['V5完整版']
    early=compare(d,replace(c,use_red_early=True))['V5完整版']
    assert not np.isclose(base.nav.iloc[-1],early.nav.iloc[-1])


def test_missing_data_and_duplicate_dates_rejected():
    d=daily();d.loc[d.index[20],'tech']=np.nan
    with pytest.raises(ValueError):create_signals(d,FusionConfig())
    d=daily();d=pd.concat([d,d.iloc[[0]]]).sort_index()
    with pytest.raises(ValueError):create_signals(d,FusionConfig())


def test_backtest_full_weekly_and_daily_reproducible():
    for d,c in [(daily(),FusionConfig()),(weekly(),weekly_config())]:
        a=compare(d,c)['V5完整版']
        b=compare(d,c)['V5完整版']
        assert np.array_equal(a.nav.to_numpy(),b.nav.to_numpy())
        assert metrics(a)['最大回撤%']<=0
