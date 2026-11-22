from pathlib import Path
from dataclasses import replace
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pandas as pd
import numpy as np
import pytest
from fusion_v6 import v6_base,V6Config
from fusion_v7 import V7Config,simulate_v7
from fusion_v8 import V8Config,signals_v8,simulate_v8

@pytest.fixture(scope='module')
def prices():
 return pd.read_csv(Path(__file__).resolve().parents[1]/'market_data/longbridge_etf_fwd_2020_2026_v7_money.csv',parse_dates=['week_start']).set_index('week_start')

@pytest.fixture(scope='module')
def ctx():
 return (v6_base('week'),V6Config(),V7Config(red_core=.3))

def test_budget_preserves_core(prices,ctx):
 s=signals_v8(prices,*ctx,V8Config(enable_v_reentry=False))
 assert np.allclose(s[['target_tech','target_red','target_cash']].sum(axis=1),1)
 assert (s.target_red>=.3-1e-9).all()
 assert (s[['target_tech','target_red','target_cash']]>=-1e-8).all().all()

def test_reentry_is_real_not_permanent_zero(prices,ctx):
 s=signals_v8(prices,*ctx,V8Config(enable_v_reentry=False))
 assert (s.v8_increment>.05).sum()>1
 assert s.target_tech.iloc[-1]==0

def test_no_future_signal_information(prices,ctx):
 c=V8Config(enable_v_reentry=False)
 before=signals_v8(prices,*ctx,c)
 p=prices.copy();p.loc[p.index[-1],['tech','red']]*=1.5
 after=signals_v8(p,*ctx,c)
 np.testing.assert_allclose(before[['target_tech','target_red','target_cash']].iloc[:-1],after[['target_tech','target_red','target_cash']].iloc[:-1],atol=1e-10)

def test_previous_bar_prices_govern_current_pnl(prices,ctx):
 c=V8Config(enable_v_reentry=False)
 before=simulate_v8(prices,*ctx,c)
 changed=prices.copy();changed.loc[changed.index[-1],'cash_511880_adj']*=1.1
 after=simulate_v8(changed,*ctx,c)
 np.testing.assert_allclose(before.nav.iloc[:-1],after.nav.iloc[:-1],atol=1e-10)
 assert before.nav.iloc[-1]!=after.nav.iloc[-1]

def test_early_reentry_toggles(prices,ctx):
 a=signals_v8(prices,*ctx,V8Config(enable_probe=True,enable_v_reentry=False))
 b=signals_v8(prices,*ctx,V8Config(enable_probe=False,enable_v_reentry=False))
 assert (a[['target_tech','target_red','target_cash']]-b[['target_tech','target_red','target_cash']]).abs().sum().sum()>0

def test_v_shape_is_optional(prices,ctx):
 a=signals_v8(prices,*ctx,V8Config(enable_v_reentry=True))
 b=signals_v8(prices,*ctx,V8Config(enable_v_reentry=False))
 assert (a[['target_tech','target_red','target_cash']]-b[['target_tech','target_red','target_cash']]).abs().sum().sum()>0

def test_invalid_exposure_rejected(prices,ctx):
 with pytest.raises(ValueError):signals_v8(prices,*ctx,V8Config(confirmed_weight=.9))

@pytest.mark.parametrize('core',[.2,.3,.4])
def test_all_core_levels_valid(prices,ctx,core):
 cfg=replace(ctx[2],red_core=core)
 w=signals_v8(prices,ctx[0],ctx[1],cfg,V8Config(enable_v_reentry=False,winning_weight=min(.52,1-core)))
 assert np.allclose(w[['target_tech','target_red','target_cash']].sum(axis=1),1)
 assert (w.target_red>=core-1e-9).all()

def test_transaction_cost_penalizes_return(prices,ctx):
 a=simulate_v8(prices,*ctx,V8Config(enable_v_reentry=False))
 b=simulate_v8(prices,replace(ctx[0],cost_per_side=.005),ctx[1],ctx[2],V8Config(enable_v_reentry=False))
 assert a.nav.iloc[-1]>b.nav.iloc[-1]
