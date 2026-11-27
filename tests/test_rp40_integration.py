"""rp40 live-integration invariants."""
import numpy as np
import pandas as pd
import pytest

from strategy_core.api import blend_targets, RP40_SHARE, build_china_portfolio_weekly, load_weekly, load_us_prices
from strategy_core.baselines.multi_asset import simulate_multi_asset, TARGETS
from strategy_core.analytics import latest_weights
from live.store import VALID_STRATEGY_VERSIONS, MARKET_ONLY_VERSIONS
from live.service import NO_FALLBACK_VERSIONS
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope='module')
def cn_run():
    cn = pd.read_csv(ROOT / 'market_data' / 'verified_5asset_weekly_v13.csv',
                     parse_dates=['week_start']).set_index('week_start')
    cfg, sig, hist = build_china_portfolio_weekly(cn, load_weekly(), load_us_prices())
    return cn, cfg, sig, hist


def test_blend_conserves_capital_and_is_causal(cn_run):
    cn, cfg, sig, _ = cn_run
    b = blend_targets(cn, sig, RP40_SHARE)
    assert np.allclose(b[list(TARGETS)].sum(axis=1), 1) and (b[list(TARGETS)] >= -1e-9).all().all()
    cut = blend_targets(cn.iloc[:200], sig.iloc[:200], RP40_SHARE)
    pd.testing.assert_frame_equal(b.iloc[:200][list(TARGETS)], cut[list(TARGETS)])


def test_share_zero_reproduces_v18(cn_run):
    cn, cfg, sig, hist = cn_run
    h0 = simulate_multi_asset(cn, blend_targets(cn, sig, 0.0), cfg)
    np.testing.assert_allclose(h0.nav.to_numpy(), hist.nav.to_numpy(), rtol=1e-12)


def test_rp40_improves_risk_and_cost(cn_run):
    cn, cfg, sig, hist = cn_run
    h = simulate_multi_asset(cn, blend_targets(cn, sig, RP40_SHARE), cfg)
    dd = lambda n: float((n / n.cummax() - 1).min())
    assert dd(h.nav) > dd(hist.nav)              # shallower drawdown
    assert h.fee.sum() < hist.fee.sum()          # lower trading cost
    w = latest_weights(blend_targets(cn, sig, RP40_SHARE), TARGETS)
    assert abs(sum(w.values()) - 1) < 1e-6


def test_rp40_registered_cn_only_and_never_falls_back():
    assert 'rp40' in VALID_STRATEGY_VERSIONS
    assert MARKET_ONLY_VERSIONS['rp40'] == 'CN'
    assert 'rp40' in NO_FALLBACK_VERSIONS
