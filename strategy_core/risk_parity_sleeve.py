"""Risk-parity sleeve: a low-parameter, causal companion to the V18 CN tactical model.

Motivation (see docs/OPTIMIZATION_REPORT.md):
- On 2020-10..2026-11 weekly data, a 4-asset trailing inverse-vol portfolio
  has Sharpe 1.44 / MDD -5.4% vs V18 Sharpe 0.99 / MDD -8.1%; correlation 0.71.
- Blending 30-50% of capital into this sleeve raises Sharpe in BOTH sub-periods
  (2020-23 and 2024-26) and cuts MDD by 2-3pp, with only 2 free parameters.

Strict causality: weights for week t use returns up to week t-1 close and are
held over week t (same convention as simulate_multi_asset's signal[t-1]).
"""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import pandas as pd

RP_PRICES = ('tech', 'red', 'bond_511260_adj', 'gold_518880_adj')
CASH = 'cash_511880_adj'
FEES = np.array([.0015, .0015, .0003, .0015])


@dataclass(frozen=True)
class RiskParityConfig:
    vol_weeks: int = 26
    deadband: float = 0.10       # total abs weight change needed to rebalance
    max_weight: float = 0.60     # bond inverse-vol can otherwise dominate
    fee_multiple: float = 1.0

    def validate(self) -> None:
        if not (8 <= self.vol_weeks <= 78):
            raise ValueError('vol_weeks must be 8..78')
        if not (0 <= self.deadband <= .5 and .25 <= self.max_weight <= 1):
            raise ValueError('invalid deadband / max_weight')


def _cap(w: np.ndarray, cap: float) -> np.ndarray:
    w = w.copy()
    for _ in range(10):
        over = w > cap + 1e-12
        if not over.any():
            break
        excess = float((w[over] - cap).sum())
        w[over] = cap
        free = ~over
        if not free.any():
            break
        w[free] += excess * w[free] / w[free].sum()
    return w


def risk_parity_targets(prices: pd.DataFrame, cfg: RiskParityConfig = RiskParityConfig()) -> pd.DataFrame:
    cfg.validate()
    ret = prices.loc[:, RP_PRICES].pct_change(fill_method=None)
    vol = ret.rolling(cfg.vol_weeks, min_periods=cfg.vol_weeks).std(ddof=1)
    rows = []
    for i in range(len(prices)):
        v = vol.iloc[i].to_numpy(float)
        if not np.isfinite(v).all() or (v <= 0).any():
            w = np.full(len(RP_PRICES), 1 / len(RP_PRICES))
        else:
            w = _cap((1 / v) / (1 / v).sum(), cfg.max_weight)
        rows.append(w)
    out = pd.DataFrame(rows, index=prices.index, columns=['rp_' + c for c in RP_PRICES])
    assert np.allclose(out.sum(axis=1), 1) and (out >= -1e-12).all().all()
    return out


def simulate_risk_parity(prices: pd.DataFrame, cfg: RiskParityConfig = RiskParityConfig()) -> pd.DataFrame:
    targets = risk_parity_targets(prices, cfg).to_numpy(float)
    px = prices.loc[:, RP_PRICES].to_numpy(float)
    fees = FEES * cfg.fee_multiple
    held = targets[0].copy()
    rows = []
    for i in range(len(px)):
        if i > 0:
            held *= px[i] / px[i - 1]
        gross = float(held.sum())
        cur = held / gross
        desired = targets[i - 1] if i > 0 else targets[0]   # signal from previous close
        trade = np.abs(cur - desired)
        if i > 0 and trade.sum() < cfg.deadband:
            desired, trade = cur, np.zeros_like(cur)
        cost = gross * float(trade @ fees)
        nav = gross - cost
        held = desired * nav
        rows.append((nav, float(trade.sum()), cost, *desired))
    cols = ['nav', 'turnover', 'fee'] + ['held_' + c for c in RP_PRICES]
    return pd.DataFrame(rows, index=prices.index, columns=cols)


def blend_sleeves(tactical_nav: pd.Series, rp_nav: pd.Series, rp_share: float = 0.4,
                  rebalance_weeks: int = 13) -> pd.Series:
    """Capital blend of two sleeves with quarterly re-balancing (drift in between)."""
    if not tactical_nav.index.equals(rp_nav.index):
        raise ValueError('sleeve dates mismatch')
    if not 0 <= rp_share <= 1:
        raise ValueError('rp_share must be 0..1')
    a = tactical_nav.pct_change(fill_method=None).fillna(0).to_numpy()
    b = rp_nav.pct_change(fill_method=None).fillna(0).to_numpy()
    va, vb = 1 - rp_share, rp_share
    out = []
    for i in range(len(a)):
        va *= 1 + a[i]; vb *= 1 + b[i]
        tot = va + vb
        out.append(tot)
        if i % rebalance_weeks == 0:
            va, vb = tot * (1 - rp_share), tot * rp_share
    return pd.Series(out, index=tactical_nav.index, name='nav')


CN_TARGETS = ('target_tech', 'target_red', 'target_bond', 'target_gold', 'target_money')
RP40_SHARE = 0.40


def blend_targets(prices: pd.DataFrame, tactical: pd.DataFrame, share: float = RP40_SHARE,
                  cfg: RiskParityConfig = RiskParityConfig()) -> pd.DataFrame:
    """Blend V18 weekly targets with risk-parity targets at WEIGHT level.

    Unlike blend_sleeves (capital split), the blended target is executed by the
    same simulate_multi_asset / live ticket path: one deadband, one fee model,
    priority_exit and intraday stop clipping all keep working unchanged.
    Backtest 2020-10..2026-11: Sharpe 0.99 -> 1.12, MDD -8.1% -> -5.9%, fees 8.6% -> 5.0%.
    """
    if not 0 <= share <= 1:
        raise ValueError('share must be 0..1')
    if not tactical.index.equals(prices.index):
        raise ValueError('tactical signal dates mismatch')
    rp = risk_parity_targets(prices, cfg).to_numpy(float)
    rp5 = np.column_stack([rp, np.zeros(len(rp))])          # RP sleeve holds no money ETF
    out = tactical.copy()
    out[list(CN_TARGETS)] = (1 - share) * tactical[list(CN_TARGETS)].to_numpy(float) + share * rp5
    arr = out[list(CN_TARGETS)].to_numpy(float)
    if (arr < -1e-9).any() or not np.allclose(arr.sum(axis=1), 1):
        raise AssertionError('blended targets do not conserve capital')
    out['rp_share'] = share
    for c, k in zip(RP_PRICES, ('tech', 'red', 'bond', 'gold')):
        out['rp_target_' + k] = rp[:, RP_PRICES.index(c)]
    base_reason = tactical.get('cross_market_reason', pd.Series('', index=tactical.index)).astype(str)
    out['cross_market_reason'] = base_reason + f'；{int(share*100)}%风险平价底仓混合'
    return out
