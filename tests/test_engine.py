import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd
from strategy_engine import Params, make_signals, backtest, metrics

D = pd.date_range('2015-01-02', periods=80, freq='W-FRI')

def test_constant_price_has_zero_return_and_no_trades():
    close = pd.Series(np.full(80,100.0),index=D)
    sig = make_signals(close,None,None,Params(use_macro='不使用'), '三级风控')
    result=backtest(sig,Params(use_macro='不使用'),'三级风控')
    assert np.isclose(result['nav'].iloc[-1],1)
    assert np.isclose(result['turnover'].sum(),0)

def test_weekly_weight_is_lagged():
    prices = [100.0]*40 + [97,92,85,80,75] + [70]*35
    close = pd.Series(prices,index=D)
    p=Params(use_macro='不使用')
    sig=make_signals(close,None,None,p,'三级风控')
    result=backtest(sig,p,'三级风控')
    assert result['weight'].iloc[0] == 1.0
    assert np.allclose(result['weight'].iloc[1:].to_numpy(),result['next_target'].iloc[:-1].to_numpy())
    assert result['next_target'].min() == .25

def test_benchmark_is_full_exposure():
    close = pd.Series(np.linspace(100,200,80),index=D)
    sig=make_signals(close,None,None,Params(),'买入持有')
    r=backtest(sig,Params(),'买入持有')
    assert np.isclose(r['weight'].mean(),1)
    assert np.isclose(r['turnover'].sum(),0)
    assert np.isclose(r['nav'].iloc[-1],2)

def test_macro_can_reduce_exposure_not_create_future_info():
    close = pd.Series(np.linspace(100,130,80),index=D)
    bond = pd.Series([105.0]*72 + [90.0]*8,index=D)
    p=Params(use_macro='TLT代理')
    sig=make_signals(close,bond,None,p,'三级风控')
    assert sig['next_target'].iloc[-1] <= .75
    changed=close.copy()
    changed.iloc[-1]*=2
    sig2=make_signals(changed,bond,None,p,'三级风控')
    assert np.allclose(sig['next_target'].iloc[:-1],sig2['next_target'].iloc[:-1])
