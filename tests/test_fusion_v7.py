from pathlib import Path
from dataclasses import replace
import sys
import numpy as np
import pandas as pd
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from history.strategies.fusion_v6 import V6Config,v6_base
from history.strategies.fusion_v5 import metrics
from history.strategies.fusion_v7 import V7Config,signals_v7,simulate_v7

@pytest.fixture(scope='module')
def prices():
 p=pd.read_csv(ROOT/'market_data/longbridge_etf_fwd_2020_2026_v7_money.csv',parse_dates=['week_start']).set_index('week_start')
 assert p.index.is_monotonic_increasing and len(p)==313
 return p

def test_money_data_looks_plausible(prices):
 p=prices.cash_511880_adj
 assert p.min()>80 and p.max()<120
 assert abs(p.iloc[0]-91.272)<1e-8
 assert abs(p.iloc[-1]-100.991)<1e-8
 assert (p.pct_change().dropna().abs()<.01).all()

def test_no_future_signal_leak(prices):
 base=v6_base('week'); c=V7Config(red_core=.25,red_extra=.2,red_qualify='strict')
 a=signals_v7(prices,base,V6Config(),c)
 new=prices.copy();new.loc[new.index[-1],['tech','red']]*=1.15
 b=signals_v7(new,base,V6Config(),c)
 cols=['target_tech','target_red','target_cash','v7_red_add']
 np.testing.assert_allclose(a[cols].iloc[:-1].to_numpy(),b[cols].iloc[:-1].to_numpy())

def test_budget_and_floor(prices):
 cfg=V7Config(red_core=.4,red_extra=.2)
 s=signals_v7(prices,v6_base('week'),V6Config(),cfg)
 assert (s[['target_tech','target_red','target_cash']]>=0).all().all()
 assert np.allclose(s[['target_tech','target_red','target_cash']].sum(axis=1),1)
 assert (s.target_red>=.4-1e-9).all()

def test_future_money_price_only_affects_future_nav(prices):
 base=v6_base('week'); cfg=V7Config()
 a=simulate_v7(prices,base,V6Config(),cfg)
 changed=prices.copy();changed.loc[changed.index[-1],'cash_511880_adj']*=1.03
 b=simulate_v7(changed,base,V6Config(),cfg)
 np.testing.assert_allclose(a.nav.iloc[:-1],b.nav.iloc[:-1])
 assert a.nav.iloc[-1]!=b.nav.iloc[-1]

def test_money_proxy_better_than_idle_in_observed_sample(prices):
 c=v6_base('week');model=V6Config()
 mm=simulate_v7(prices,c,model,V7Config(cash_mode='money_price'))
 no=simulate_v7(prices,c,model,V7Config(cash_mode='cash_zero'))
 assert mm.nav.iloc[-1]>no.nav.iloc[-1]
 assert metrics(mm)['累计收益%']>metrics(no)['累计收益%']

def test_no_extra_tilt_when_disabled(prices):
 a=signals_v7(prices,v6_base('week'),V6Config(),V7Config(red_extra=0))
 b=signals_v7(prices,v6_base('week'),V6Config(),V7Config(red_extra=.2,red_qualify='off'))
 np.testing.assert_allclose(a[['target_tech','target_red','target_cash']],b[['target_tech','target_red','target_cash']])

def test_money_fee_reduces_net_value(prices):
 base=v6_base('week');model=V6Config()
 a=simulate_v7(prices,base,model,V7Config(money_etf_side_cost=0))
 b=simulate_v7(prices,base,model,V7Config(money_etf_side_cost=.002))
 assert a.nav.iloc[-1]>b.nav.iloc[-1]

def test_cash_price_required(prices):
 with pytest.raises(ValueError,match='511880'):
  simulate_v7(prices.drop(columns=['cash_511880_adj']),v6_base('week'),V6Config(),V7Config())

def test_zero_cash_and_fixed_annual_rate(prices):
 c=v6_base('week');m=V6Config()
 z=simulate_v7(prices,c,m,V7Config(cash_mode='cash_zero'))
 y=simulate_v7(prices,c,m,V7Config(cash_mode='assumed_yield',assumed_cash_annual=.01))
 assert y.nav.iloc[-1]>z.nav.iloc[-1]

@pytest.mark.parametrize('core',[0.,.1,.2,.4,.5])
def test_core_levels_no_leverage(prices,core):
 v=simulate_v7(prices,v6_base('week'),V6Config(),V7Config(red_core=core))
 assert np.allclose(v[['held_tech','held_red','held_cash']].sum(axis=1),1)
 assert (v[['held_tech','held_red','held_cash']]>=-1e-10).all().all()
