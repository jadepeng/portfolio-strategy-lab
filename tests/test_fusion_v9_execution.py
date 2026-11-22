from pathlib import Path
from dataclasses import replace
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np, pandas as pd, pytest
from history.strategies.fusion_v6 import V6Config,v6_base
from history.strategies.fusion_v7 import V7Config
from history.strategies.fusion_v9_execution import simulate_v9_execution,V9ExecutionConfig

@pytest.fixture(scope='module')
def sample():
 return pd.read_csv(Path(__file__).resolve().parents[1]/'market_data/longbridge_etf_fwd_2020_2026_v7_money.csv',parse_dates=['week_start']).set_index('week_start')

def test_execution_budget_and_forced_decreases(sample):
 x=simulate_v9_execution(sample,v6_base('week'),V6Config(),V7Config(red_core=.3))
 assert (x.v9_exec_forced).sum()>2
 assert (x.held_tech>=0).all() and (x.held_red>=0).all() and (x.held_cash>=0).all()
 np.testing.assert_allclose(x[['held_tech','held_red','held_cash']].sum(axis=1),1)

def test_fee_increases_reduce_nav(sample):
 a=simulate_v9_execution(sample,v6_base('week'),V6Config(),V7Config(red_core=.3))
 b=simulate_v9_execution(sample,replace(v6_base('week'),cost_per_side=.003),V6Config(),V7Config(red_core=.3,money_etf_side_cost=.0005))
 assert a.nav.iloc[-1]>b.nav.iloc[-1]

def test_invalid_deadband_rejected(sample):
 with pytest.raises(ValueError):
  simulate_v9_execution(sample,v6_base('week'),V6Config(),V7Config(red_core=.3),exec_cfg=V9ExecutionConfig(ordinary_turnover_deadband=2.))

def test_future_data_does_not_change_prior_nav(sample):
 b=v6_base('week');m=V6Config();cash=V7Config(red_core=.3)
 before=simulate_v9_execution(sample,b,m,cash)
 changed=sample.copy();changed.loc[changed.index[-1],['tech','red','cash_511880_adj']]*=1.1
 after=simulate_v9_execution(changed,b,m,cash)
 np.testing.assert_allclose(before.nav.iloc[:-1].values,after.nav.iloc[:-1].values,atol=1e-12)
