import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from dataclasses import replace
import numpy as np
import pandas as pd
from history.strategies.trend_animal_v4 import TrendAnimalV4Config,build_temperature_signals,backtest_v4,STAGES

D=pd.date_range('2016-01-04',periods=170,freq='W-MON')
def sample():
    a=100*np.cumprod(np.r_[1,np.full(64,1.022),np.full(50,.965),np.full(55,1.027)])
    b=100*np.cumprod(np.r_[1,np.full(64,1.003),np.full(50,.993),np.full(55,1.006)])
    return pd.DataFrame({'tech':a,'red':b},index=D)

def test_future_price_does_not_change_past_targets():
    x=sample();c=TrendAnimalV4Config()
    a=build_temperature_signals(x,c)
    y=x.copy();y.iloc[-1,0]*=2.5;y.iloc[-1,1]*=.8
    b=build_temperature_signals(y,c)
    for k in ['target_tech','target_red','target_cash','tech_temperature','red_temperature']:
        assert (a[k].iloc[:-1].to_numpy()==b[k].iloc[:-1].to_numpy()).all()

def test_portfolio_weight_constraints():
    m=backtest_v4(sample(),TrendAnimalV4Config())
    tgt=m[['target_tech','target_red','target_cash']]
    assert np.allclose(tgt.sum(axis=1),1)
    assert (tgt>=-1e-10).all().all()
    assert (tgt<=1.00000001).all().all()
    assert set(m['tech_temperature']).issubset(set(STAGES))

def test_no_looking_at_next_week_return():
    x=sample();c=TrendAnimalV4Config(cost_rate=0)
    a=backtest_v4(x,c)
    for i in range(1,len(x)):
        r=a.iloc[i];p=a.iloc[i-1]
        expected=p.nav*(p.hold_tech*x.tech.iloc[i]/x.tech.iloc[i-1]
              +p.hold_red*x.red.iloc[i]/x.red.iloc[i-1]+p.hold_cash)
        assert np.isclose(r.nav,expected,rtol=1e-10)

def test_cost_reduces_terminal_nav():
    low=backtest_v4(sample(),TrendAnimalV4Config(cost_rate=0))
    high=backtest_v4(sample(),TrendAnimalV4Config(cost_rate=.003))
    assert high.nav.iloc[-1]<low.nav.iloc[-1]

def test_v4_cash_when_both_enter_downtrend():
    x=sample();x.loc[x.index[-30]:,'tech']*=np.linspace(1,.2,30)
    x.loc[x.index[-30]:,'red']*=np.linspace(1,.2,30)
    v=backtest_v4(x,TrendAnimalV4Config())
    assert np.isclose(v.iloc[-1].target_cash,1)

def test_soft_factor_ablation():
    x=sample();base=backtest_v4(x,TrendAnimalV4Config())
    other=backtest_v4(x,replace(TrendAnimalV4Config(),champagne_overlay=False))
    assert len(base)==len(other)==170
