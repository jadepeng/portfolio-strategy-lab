"""V17 优化模块：美股多周期趋势 + 危机波动率刹车 + 迟滞过滤。

严格因果约束：
- 仅使用截至第 t 周收盘的已知价格计算信号；
- 交易在第 t+1 周开盘（或收盘敏感性口径）执行，无任何未来函数；
- 保留原 V15 费率与资金守恒校验。
"""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import pandas as pd
from v15.us_strategy import USConfig, us_factors, simulate_us


@dataclass(frozen=True)
class USV17Config:
    qqq_core: float = 0.30
    ma_windows: tuple[int, ...] = (10, 20, 30)
    hysteresis: float = 0.005       # 0.5% 上下迟滞带，减少均线反复打脸
    vol_window: int = 13            # 13周实现波动率窗口
    vol_cap: float = 0.24           # 危机波动率上限：仅当波动率 > 24% 时按比例降仓
    min_vol_scale: float = 0.30     # 波动率缩放下限
    rebalance_threshold: float = 0.20  # 双边换手死区（约10%单边权重变化才调仓）
    qqq_side_fee: float = 0.001
    bil_side_fee: float = 0.0002
    fill_mode: str = "next_week_open"

    def validate(self) -> None:
        if not (0 <= self.qqq_core <= 1):
            raise ValueError("qqq_core must be in [0, 1]")
        if not self.ma_windows or any(w < 4 or w > 52 for w in self.ma_windows):
            raise ValueError("ma_windows out of range")
        if not (0 <= self.hysteresis <= 0.05):
            raise ValueError("hysteresis out of range")
        if not (0.08 <= self.vol_cap <= 0.50 and 0.10 <= self.min_vol_scale <= 1.0):
            raise ValueError("vol parameters invalid")
        if not (0 <= self.rebalance_threshold <= 0.40):
            raise ValueError("rebalance_threshold invalid")


def us_signals_v17(p: pd.DataFrame, cfg: USV17Config = USV17Config()) -> pd.DataFrame:
    cfg.validate()
    f = us_factors(p)
    scores = []
    for w in cfg.ma_windows:
        ma = p.QQQ.rolling(w, min_periods=w).mean()
        st = np.zeros(len(p), dtype=float)
        cur = 0.0
        for i in range(len(p)):
            if pd.isna(ma.iloc[i]):
                cur = 0.0
            elif cur == 0.0 and p.QQQ.iloc[i] > ma.iloc[i] * (1.0 + cfg.hysteresis):
                cur = 1.0
            elif cur == 1.0 and p.QQQ.iloc[i] < ma.iloc[i] * (1.0 - cfg.hysteresis):
                cur = 0.0
            st[i] = cur
        scores.append(pd.Series(st, index=p.index))

    trend_score = sum(scores) / len(scores)
    ret = p.QQQ.pct_change(fill_method=None)
    rvol = ret.rolling(cfg.vol_window, min_periods=cfg.vol_window).std(ddof=1) * np.sqrt(52)
    vol_mult = (cfg.vol_cap / rvol).clip(lower=cfg.min_vol_scale, upper=1.0).fillna(1.0)

    tactical = trend_score * vol_mult
    weight_qqq = (cfg.qqq_core + (1.0 - cfg.qqq_core) * tactical).clip(lower=cfg.qqq_core, upper=1.0)

    f["us_v17_trend_score"] = trend_score
    f["us_v17_realized_vol"] = rvol
    f["us_v17_vol_mult"] = vol_mult
    f["target_qqq"] = weight_qqq
    f["target_bil"] = 1.0 - weight_qqq
    f["us_trend_decision"] = np.where(
        trend_score >= 0.99, "多周期强势持有",
        np.where(trend_score > 0.01, "多周期分档减仓", "多周期趋势防守")
    )
    return f


def simulate_us_v17(
    p: pd.DataFrame,
    cfg: USV17Config = USV17Config(),
    opens: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    sig = us_signals_v17(p, cfg)
    base_cfg = USConfig(
        qqq_core=cfg.qqq_core,
        qqq_side_fee=cfg.qqq_side_fee,
        bil_side_fee=cfg.bil_side_fee,
        rebalance_threshold=cfg.rebalance_threshold,
        fill_mode=cfg.fill_mode,
    )
    sim = simulate_us(p, sig, base_cfg, opens)
    return sig, sim
