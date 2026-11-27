import numpy as np
import pandas as pd
import pytest

from strategy_core.risk_parity_sleeve import (RiskParityConfig, risk_parity_targets,
                                              simulate_risk_parity, blend_sleeves, RP_PRICES)
from strategy_core.robustness import deflated_sharpe, sharpe_calmar
from strategy_core.us_vol_target import build_us_vol_target_signals


def _prices(n=120, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range('2021-01-04', periods=n, freq='W-MON')
    vols = [.04, .03, .005, .02, .0002]
    cols = list(RP_PRICES) + ['cash_511880_adj']
    return pd.DataFrame({c: 100 * np.cumprod(1 + rng.normal(.001, v, n)) for c, v in zip(cols, vols)}, index=idx)


def test_rp_weights_sum_and_cap():
    w = risk_parity_targets(_prices(), RiskParityConfig(max_weight=.5))
    assert np.allclose(w.sum(1), 1) and (w.to_numpy() <= .5 + 1e-9).all()


def test_rp_is_causal():
    p = _prices()
    full = risk_parity_targets(p)
    cut = risk_parity_targets(p.iloc[:80])
    pd.testing.assert_frame_equal(full.iloc[:80], cut)


def test_rp_future_shock_does_not_change_past_nav():
    p = _prices(); q = p.copy(); q.iloc[-1] *= .5
    a, b = simulate_risk_parity(p).nav, simulate_risk_parity(q).nav
    pd.testing.assert_series_equal(a.iloc[:-1], b.iloc[:-1])


def test_blend_bounds():
    p = _prices(); n = simulate_risk_parity(p).nav
    pd.testing.assert_series_equal(blend_sleeves(n, n, .4), n / n.iloc[0] * n.iloc[0], check_names=False, rtol=1e-9)
    with pytest.raises(ValueError):
        blend_sleeves(n, n, 1.5)


def test_dsr_monotone_in_trials():
    n = simulate_risk_parity(_prices()).nav
    assert deflated_sharpe(n, 10)['dsr'] >= deflated_sharpe(n, 500)['dsr']
    assert set(sharpe_calmar(n)) >= {'sharpe', 'calmar', 'sortino'}


def test_us_vol_target_no_leverage():
    from strategy_core.us_snapshot import load_us_prices
    s = build_us_vol_target_signals(load_us_prices())
    assert s.target_qqq.between(0, 1).all() and np.allclose(s.target_qqq + s.target_bil, 1)
