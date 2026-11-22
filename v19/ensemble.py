"""V19 stable 50/50 model blending, with NO parameter fit against 2026.

The China and U.S. model candidates are V15 and V18 signals already in the
codebase. Blending is performed at SIGNAL time (end of week t); trades use
week t+1, with explicit transaction costs. The source signals are not mutated.
"""
from dataclasses import dataclass, replace
import numpy as np
import pandas as pd
from fusion_v10 import TARGETS, V10Config, simulate_v10
from v18.integrated import V18CNConfig, V18USConfig, build_cn_v18_weekly, build_us_v18
from v15.us_strategy import USConfig, us_signals, simulate_us
from fusion_v6 import v6_base, V6Config
from fusion_v7 import V7Config
from fusion_v11 import V11Config, signals_v11
from v13.regime_v13 import RegimeConfig, signals_v13
from v14.crowding import build_factors, classify, state_overlay, OverlayStateConfig
from v15.cross_market import us_to_cn_overlay, CrossConfig

@dataclass(frozen=True)
class V19Config:
    cn_v18_share: float = 0.50
    us_v18_share: float = 0.50
    us_core: float = 0.30
    cn_extra_fee_multiple: float = 1.0
    us_extra_fee_multiple: float = 1.0
    us_fill_mode: str = 'next_week_open'

    def validate(self):
        for x in (self.cn_v18_share, self.us_v18_share, self.us_core):
            if not 0 <= x <= 1:
                raise ValueError('ensemble weight/core must be between 0 and 1')
        for x in (self.cn_extra_fee_multiple, self.us_extra_fee_multiple):
            if not 0.1 <= x <= 10:
                raise ValueError('fee multiplier invalid')
        if self.us_fill_mode not in ('next_week_open', 'next_week_close'):
            raise ValueError('unsupported fill mode')


def _blend(signal_a, signal_b, cols, fraction):
    if not signal_a.index.equals(signal_b.index):
        raise ValueError('source signal calendars differ')
    if not set(cols).issubset(signal_a) or not set(cols).issubset(signal_b):
        raise ValueError('missing allocation column')
    wa = signal_a[list(cols)].to_numpy(float)
    wb = signal_b[list(cols)].to_numpy(float)
    blended = (1 - fraction) * wa + fraction * wb
    if not np.all(np.isfinite(blended)) or (blended < -1e-10).any() or not np.allclose(blended.sum(axis=1), 1):
        raise AssertionError('invalid blended budget')
    out = signal_b.copy()
    out[list(cols)] = blended
    return out


def cn_v15_reference(prices, market, us_prices):
    """Reproduce baseline V15 signals from the frozen code, not a variant approximator."""
    c = V10Config(portfolio_vol_brake=True)
    baseline = signals_v11(prices, v6_base('week'), V6Config(),
        V7Config(red_core=.30,cash_mode='money_price',money_etf_side_cost=.0001),
        c, V11Config(risk_guard=False))
    regime = signals_v13(prices, baseline, RegimeConfig(hold_weeks=8))
    overlaid = state_overlay(prices, regime, classify(build_factors(prices, market)),
        OverlayStateConfig(freeze_weeks=3))
    return us_to_cn_overlay(prices, overlaid, us_prices, CrossConfig())


def build_cn_v19(prices, market, us_prices, cfg=V19Config()):
    cfg.validate()
    old = cn_v15_reference(prices, market, us_prices)
    v18conf, modern, _ = build_cn_v18_weekly(prices,market,us_prices)
    signal = _blend(old, modern, TARGETS, cfg.cn_v18_share)
    cf = replace(v18conf, **{k: getattr(v18conf,k)*cfg.cn_extra_fee_multiple
       for k in ('tech_fee','red_fee','bond_fee','gold_fee','money_fee')})
    ledger = simulate_v10(prices, signal, cf)
    signal['v19_v18_share'] = cfg.cn_v18_share
    return old, modern, signal, ledger


def build_us_v19(prices, opens, cfg=V19Config()):
    cfg.validate()
    oldconf = USConfig(qqq_core=cfg.us_core,fill_mode=cfg.us_fill_mode)
    old = us_signals(prices,oldconf)
    conf = V18USConfig(qqq_core=cfg.us_core,fill_mode=cfg.us_fill_mode)
    modern,_ = build_us_v18(prices, conf, opens)
    signal = _blend(old, modern, ('target_qqq','target_bil'), cfg.us_v18_share)
    c = USConfig(qqq_core=cfg.us_core, fill_mode=cfg.us_fill_mode,
        rebalance_threshold=conf.rebalance_threshold,
        qqq_side_fee=conf.qqq_side_fee*cfg.us_extra_fee_multiple,
        bil_side_fee=conf.bil_side_fee*cfg.us_extra_fee_multiple)
    ledger = simulate_us(prices, signal, c, opens)
    signal['v19_v18_share'] = cfg.us_v18_share
    return old, modern, signal, ledger
