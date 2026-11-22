"""V19 signal mix and execution-invariance regression tests."""
from functools import lru_cache
from dataclasses import replace
import numpy as np
import pandas as pd
import pytest
from fusion_v10 import TARGETS
from v14.market_snapshot import load_weekly
from v15.us_snapshot import load_us_prices,load_us_open
from v19.ensemble import V19Config,build_cn_v19,build_us_v19,_blend

@lru_cache(maxsize=1)
def data():
    p=pd.read_csv('market_data/verified_5asset_weekly_v13.csv',parse_dates=['week_start']).set_index('week_start')
    return p,load_weekly(),load_us_prices(),load_us_open()
@lru_cache(maxsize=3)
def models_cn(share):
    p,m,u,_=data()
    return build_cn_v19(p,m,u,V19Config(cn_v18_share=share))
@lru_cache(maxsize=3)
def models_us(share):
    _,_,u,o=data()
    return build_us_v19(u,o,V19Config(us_v18_share=share))

def test_cn_sum_nonnegative_and_cap():
    _,_,s,h=models_cn(.5)
    x=s[list(TARGETS)].to_numpy(float)
    assert (x>=-1e-10).all()
    np.testing.assert_allclose(x.sum(axis=1),1,atol=1e-11)
    assert (h.nav>0).all()

def test_cn_signal_endpoints_exact():
    p,m,u,_=data()
    old,new,mixed,_=models_cn(.5)
    _,_,onlyold,_=models_cn(0.)
    _,_,onlynew,_=models_cn(1.)
    np.testing.assert_allclose(onlyold[list(TARGETS)],old[list(TARGETS)],atol=1e-12)
    np.testing.assert_allclose(onlynew[list(TARGETS)],new[list(TARGETS)],atol=1e-12)
    np.testing.assert_allclose(mixed[list(TARGETS)],.5*(old[list(TARGETS)]+new[list(TARGETS)]),atol=1e-12)

def test_cn_latest_is_flat_tech_and_ensemble_is_not_auto_liquidation():
    _,_,s,_=models_cn(.5)
    np.testing.assert_allclose(s[list(TARGETS)].iloc[-1].to_numpy(float),[0.,.44,.325,0.,.235],atol=1e-9)

def test_us_endpoints_interpolation_and_core_floor():
    old,new,mix,h=models_us(.5)
    _,_,a,_=models_us(0.)
    _,_,b,_=models_us(1.)
    np.testing.assert_allclose(a[['target_qqq','target_bil']],old[['target_qqq','target_bil']],atol=1e-12)
    np.testing.assert_allclose(b[['target_qqq','target_bil']],new[['target_qqq','target_bil']],atol=1e-12)
    np.testing.assert_allclose(mix.target_qqq,.5*(old.target_qqq+new.target_qqq),atol=1e-12)
    assert mix.target_qqq.min()>=.30-1e-10 and mix.target_qqq.max()<=1.+1e-10
    assert (h.nav>0).all()

def test_us_tplusone_signal_and_funding():
    _,_,u,_=data()
    _,_,s,h=models_us(.5)
    assert (h.source_signal_week.iloc[1:].to_numpy()==u.index[:-1].to_numpy()).all()
    np.testing.assert_allclose(h.held_qqq+h.held_bil,1,atol=1e-12)

@pytest.mark.parametrize('kwargs',[{'cn_v18_share':-1},{'cn_v18_share':1.1},{'us_v18_share':-0.1},{'us_v18_share':2},{'us_core':1.5},{'cn_extra_fee_multiple':0},{'us_extra_fee_multiple':11},{'us_fill_mode':'same_week'}])
def test_bad_config_rejected(kwargs):
    with pytest.raises(ValueError):V19Config(**kwargs).validate()

def test_blend_reject_different_calendar():
    a=pd.DataFrame({'x':[1],'y':[0]},index=pd.DatetimeIndex(['2026-10-01']))
    b=a.copy();b.index=pd.DatetimeIndex(['2026-10-02'])
    with pytest.raises(ValueError):_blend(a,b,['x','y'],.5)

def test_cn_future_close_does_not_rewrite_past_targets():
    p,m,u,_=data()
    _,_,sig,_=models_cn(.5)
    changed=p.copy();changed.iloc[-1,0]*=1.2
    _,_,s2,_=build_cn_v19(changed,m,u,V19Config())
    np.testing.assert_allclose(sig[list(TARGETS)].iloc[:-1],s2[list(TARGETS)].iloc[:-1],atol=1e-12,rtol=0)

def test_us_future_close_does_not_rewrite_past_targets():
    _,_,u,o=data()
    _,_,s,_=models_us(.5)
    changed=u.copy();changed.iloc[-1,changed.columns.get_loc('QQQ')]*=1.5
    _,_,s2,_=build_us_v19(changed,o,V19Config())
    np.testing.assert_allclose(s.target_qqq.iloc[:-1],s2.target_qqq.iloc[:-1],atol=1e-12,rtol=0)
