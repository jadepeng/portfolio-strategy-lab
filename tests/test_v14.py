import numpy as np
import pandas as pd
import pytest
from v14.market_snapshot import load_weekly
from v14.crowding import CrowdConfig,OverlayStateConfig,build_factors,classify,state_overlay,trailing_rank
from fusion_v10 import TARGETS,simulate_v10,V10Config

@pytest.fixture(scope='module')
def p():
    return pd.read_csv('market_data/verified_5asset_weekly_v13.csv',parse_dates=['week_start']).set_index('week_start')

@pytest.fixture(scope='module')
def market(p):
    return load_weekly()

@pytest.fixture(scope='module')
def facts(p,market):
    return classify(build_factors(p,market))


def test_market_snapshot_integrity(market):
    assert len(market)==150
    assert market.index[0]==pd.Timestamp('2023-12-25')
    assert market.index[-1]==pd.Timestamp('2026-11-16')
    assert market.iloc[-1].market_turn_100m==13508
    assert market.iloc[-1].market_close==4745
    assert (market.loc[:,market.columns.str.contains('turn')] > 0).all().all()


def test_index_basket_shares_have_valid_values(facts):
    assert facts.tech_turn_share.between(0,1).all()
    assert facts.double_creation_share.between(0,1).all()
    assert facts.red_turn_share.between(0,1).all()


def test_market_current_not_bull(facts):
    now=facts.iloc[-1]
    assert now.market_regime == '偏熊市（缩量破位）'
    assert now.market_turn_ratio<1
    assert now.market_close<now.market_ma13
    assert now.market_close<now.market_ma26


def test_high_turnover_alone_does_not_flag_top(facts):
    assert not bool(facts.loc['2026-06-15'].top_warning)
    assert bool(facts.loc['2026-06-29'].volume_exhaustion)


def test_trailing_rank_does_not_read_future():
    x=pd.Series([2,2,3,2,9,1,2,8,9,3,4,7]*10,dtype=float)
    full=trailing_rank(x,26,10)
    shorter=trailing_rank(x.iloc[:61],26,10)
    pd.testing.assert_series_equal(full.iloc[:61],shorter)


def test_one_time_weekly_signal_ordering(p,facts):
    # A synthetic one-period target switch cannot benefit from same-week returns.
    xs=p.iloc[:50]
    signal=pd.DataFrame(index=xs.index)
    for col in TARGETS:signal[col]=0.
    signal['target_money']=1.
    signal.loc[xs.index[20]:,'target_money']=0.
    signal.loc[xs.index[20]:,'target_tech']=1.
    sim=simulate_v10(xs,signal,V10Config(execution_deadband=0),execution=True)
    assert sim.loc[xs.index[20],'held_money']==1
    assert sim.loc[xs.index[21],'held_tech']==1


def test_state_budget_and_no_new_buy_in_freeze(p,facts):
    s=pd.DataFrame(index=p.index)
    for col in TARGETS:s[col]=0.
    s['target_money']=1.
    s.loc['2026-05-01':,'target_tech']=.7
    s.loc['2026-05-01':,'target_money']=.3
    out=state_overlay(p,s,facts,OverlayStateConfig(freeze_weeks=3))
    v=out[list(TARGETS)].to_numpy()
    assert np.allclose(v.sum(axis=1),1)
    assert (v>=-1e-10).all()
    assert (out.loc['2026-06-29','target_tech'] <= .7)


def test_future_rows_do_not_change_past_overlay(p,facts):
    s=pd.DataFrame(index=p.index)
    for col in TARGETS:s[col]=0.
    s['target_money']=1.
    s.loc['2025-01-01':,'target_tech']=.3
    s.loc['2025-01-01':,'target_money']=.7
    cfg=OverlayStateConfig(freeze_weeks=3)
    full=state_overlay(p,s,facts,cfg)
    prefix=p.index.get_loc(pd.Timestamp('2026-08-10'))+1
    short=state_overlay(p.iloc[:prefix],s.iloc[:prefix],facts.iloc[:max(0,prefix-facts.index.searchsorted(p.index[0]))],cfg)
    pd.testing.assert_frame_equal(full.loc[short.index,list(TARGETS)],short[list(TARGETS)])


def test_missing_market_data_does_not_impute_bull(p,facts):
    s=pd.DataFrame(index=p.index)
    for col in TARGETS:s[col]=0.
    s['target_money']=1.
    out=state_overlay(p,s,facts)
    pd.testing.assert_frame_equal(out.loc[p.index<facts.index[0],list(TARGETS)],s.loc[p.index<facts.index[0],list(TARGETS)])


def test_parameter_validation(p,facts):
    s=pd.DataFrame(0.,index=p.index,columns=TARGETS)
    s['target_money']=1.
    with pytest.raises(ValueError):state_overlay(p,s,facts,OverlayStateConfig(freeze_weeks=100))
    with pytest.raises(ValueError):classify(facts,CrowdConfig(tech_cap_warning=.1,tech_cap_bear=.5))


def test_price_volume_factors_prefix_consistency(p,market):
    full=classify(build_factors(p,market))
    prefix=market.index.get_loc(pd.Timestamp('2026-07-13'))+1
    short=classify(build_factors(p.loc[:market.index[prefix-1]],market.iloc[:prefix]))
    cols=['market_turn_ratio','market_close','tech_turn_share','tech_share_rank','top_warning','bull_expansion','bear_weak_volume']
    pd.testing.assert_frame_equal(full.loc[short.index,cols],short[cols])


def test_actual_concentration_never_invented(p,market):
    f=build_factors(p,market)
    assert f.top5_concentration_ratio.isna().all()
    assert not classify(f).real_stock_congestion_warning.any()


def test_external_top5_concentration_requires_price_confirmation(p,market):
    augmented=market.copy()
    augmented['top5_concentration_ratio']=np.nan
    augmented.loc['2026-06-15','top5_concentration_ratio']=.55
    augmented.loc['2026-06-29','top5_concentration_ratio']=.49
    f=classify(build_factors(p,augmented))
    assert not f.loc['2026-06-15','real_stock_congestion_warning']
    assert f.loc['2026-06-29','real_stock_congestion_warning']
    augmented.loc['2026-06-15','top5_concentration_ratio']=55
    with pytest.raises(ValueError):build_factors(p,augmented)
