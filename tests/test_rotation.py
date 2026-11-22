import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pandas as pd
import numpy as np
from dataclasses import replace
from history.strategies.rotation_engine import RotationParams,align_close,make_rotation_signals,run_rotation_comparison,rotation_metrics

D=pd.date_range('2018-01-05',periods=160,freq='W-FRI')

def example():
    # Positive/breaking tech trend; independently moving dividends.
    tech=pd.Series(100*np.cumprod(np.r_[1,np.full(79,1.012),np.full(80,.965)]),index=D)
    red=pd.Series(100*np.cumprod(np.r_[1,np.full(79,1.003),np.full(80,1.002)]),index=D)
    return tech,red

def test_alignment_excludes_missing_dates():
    t,r=example();q=align_close(t,r.drop(r.index[4]));assert q.shape[0]==159
    assert q.index[4]==D[5]

def test_no_lookahead():
    t,r=example();price=align_close(t,r);p=RotationParams()
    a=make_rotation_signals(price,p)
    changed=price.copy();changed.iloc[-1,0]*=2
    b=make_rotation_signals(changed,p)
    assert np.allclose(a.next_tech.iloc[:-1],b.next_tech.iloc[:-1]);assert np.allclose(a.next_red.iloc[:-1],b.next_red.iloc[:-1])

def test_weight_budget_no_leverage():
    t,r=example();p=RotationParams(use_ratio_tilt=True)
    a=make_rotation_signals(align_close(t,r),p)
    w=a[['next_tech','next_red','next_cash']]
    assert np.allclose(w.sum(axis=1),1.)
    assert (w>=-1e-9).all().all()
    assert (w<=1+1e-9).all().all()

def test_benchmark_growth_no_fees():
    t,r=example();p=RotationParams(trade_cost=0.)
    runs=run_rotation_comparison(t,r,p)
    x=runs['科技买入持有']
    assert np.isclose(x.nav.iloc[-1],t.iloc[-1]/t.iloc[0])
    assert x.turnover.sum()==0

def test_rebalance_applies_after_current_period():
    t,r=example();p=RotationParams(trade_cost=0.)
    result=run_rotation_comparison(t,r,p)['动量+破位风控']
    # Each period gross before the current signal change comes from previous target.
    for i in range(2,len(result)):
        prior=result.iloc[i-1];current=result.iloc[i]
        expected=(prior.held_next_tech*t.iloc[i]/t.iloc[i-1]
                  +prior.held_next_red*r.iloc[i]/r.iloc[i-1]
                  +prior.held_next_cash)*prior.nav
        assert np.isclose(expected,current.nav,rtol=1e-10)

def test_cash_out_after_both_long_downtrends():
    t,r=example();r=pd.Series(100*np.cumprod(np.r_[1,np.full(79,1.002),np.full(80,.96)]),index=D)
    result=run_rotation_comparison(t,r,RotationParams())['动量+破位风控']
    assert np.isclose(result.iloc[-1].next_cash,1.)

def test_cost_reduces_nav():
    t,r=example()
    cheap=run_rotation_comparison(t,r,RotationParams(trade_cost=0.))['动量+破位风控']
    expensive=run_rotation_comparison(t,r,RotationParams(trade_cost=.003))['动量+破位风控']
    assert expensive.nav.iloc[-1]<cheap.nav.iloc[-1]

def test_strategy_metrics_finite():
    t,r=example()
    result=run_rotation_comparison(t,r,RotationParams())
    for value in result.values():
        assert np.isfinite(rotation_metrics(value)['区间收益率'])
