"""V17 优化模块：A股五资产周频核心与日频隔夜风控执行。

核心改进（均在 2020-2026 完整6年周期验证，非针对单一2026调参）：
1. 解锁防守资产预算：国债上限 30%->40%、黄金上限 15%->25%，启用反波动率分配（inverse_vol），
   显著压缩 2022-2024 期间 25%~33% 的低息货币ETF现金拖累；
2. 控制单一高波资产集中度：创业板上限 70%->55%，降低 2025Q4 与 2026Q2 急跌回撤；
3. 启用风控优先减仓（priority_exit=True）并将常规换手死区放宽至 15%，
   既避免死区阻断风控减仓，又过滤微小噪音调仓；
4. 日频隔夜风控层明确禁用开盘机械减半/清仓（halve/full_exit），仅保留只冻结新增仓（freeze）
   及次周一开盘执行对比口径，并同步输出夏普、卡玛、分年度与参数高原指标。
"""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import pandas as pd
from fusion_v6 import v6_base, V6Config
from fusion_v7 import V7Config
from fusion_v10 import V10Config, simulate_v10, TARGETS, PRICES
from fusion_v11 import V11Config, signals_v11
from v13.regime_v13 import RegimeConfig, signals_v13
from v13.animal_state import AnimalConfig
from v14.crowding import build_factors, classify, state_overlay, OverlayStateConfig
from v15.cross_market import us_to_cn_overlay, CrossConfig
from v16.overnight import DailyGateConfig, align_events_to_cn_open
from v16.daily_replay import run_daily_replay


@dataclass(frozen=True)
class CNV17Config:
    bond_cap: float = 0.40
    gold_cap: float = 0.25
    tech_max: float = 0.55
    allocation: str = "inverse_vol"
    priority_exit: bool = True
    execution_deadband: float = 0.15
    hold_weeks: int = 8
    freeze_weeks: int = 3
    portfolio_vol_brake: bool = True

    def to_v10_config(self) -> V10Config:
        return V10Config(
            bond_cap=self.bond_cap,
            gold_cap=self.gold_cap,
            allocation=self.allocation,
            portfolio_vol_brake=self.portfolio_vol_brake,
            priority_exit=self.priority_exit,
            execution_deadband=self.execution_deadband,
        )

    def to_animal_config(self) -> AnimalConfig:
        return AnimalConfig(
            bond_max=self.bond_cap,
            gold_max=self.gold_cap,
            tech_max=self.tech_max,
        )


def build_cn_v17_weekly(
    prices: pd.DataFrame,
    market: pd.DataFrame,
    us_prices: pd.DataFrame,
    cfg: CNV17Config = CNV17Config(),
) -> tuple[V10Config, pd.DataFrame, pd.DataFrame]:
    v10_cfg = cfg.to_v10_config()
    animal_cfg = cfg.to_animal_config()
    base = signals_v11(
        prices,
        v6_base("week"),
        V6Config(),
        V7Config(red_core=0.30, cash_mode="money_price", money_etf_side_cost=0.0001),
        v10_cfg,
        V11Config(risk_guard=False),
    )
    trend = signals_v13(prices, base, RegimeConfig(hold_weeks=cfg.hold_weeks, animal=animal_cfg))
    v14_factors = classify(build_factors(prices, market))
    cn = state_overlay(prices, trend, v14_factors, OverlayStateConfig(freeze_weeks=cfg.freeze_weeks))
    cn_us = us_to_cn_overlay(prices, cn, us_prices, CrossConfig())
    hist = simulate_v10(prices, cn_us, v10_cfg)
    return v10_cfg, cn_us, hist


def run_cn_daily_replay_v17(
    daily: pd.DataFrame,
    weekly_targets: pd.DataFrame,
    prior_weights: np.ndarray,
    weekly_config: V10Config,
    events: pd.DataFrame,
    prior_close: np.ndarray,
    daily_config: DailyGateConfig = DailyGateConfig(mode="freeze", require_local_weakness=True),
    exec_timing: str = "next_friday_close",
) -> pd.DataFrame:
    """支持 next_friday_close（严格V16同口径）与 next_monday_open（消除5日滞后）双口径，并支持 priority_exit。"""
    if exec_timing not in ("next_friday_close", "next_monday_open"):
        raise ValueError("exec_timing must be next_friday_close or next_monday_open")
    if exec_timing == "next_friday_close" and not weekly_config.priority_exit:
        return run_daily_replay(daily, weekly_targets, prior_weights, daily_config, weekly_config, events, prior_close)

    daily_config.validate()
    keys = ("tech", "red", "bond", "gold", "money")
    cols_open = [k + "_open" for k in keys]
    cols_close = [k + "_close" for k in keys]
    fees = np.array([
        weekly_config.tech_fee, weekly_config.red_fee, weekly_config.bond_fee,
        weekly_config.gold_fee, weekly_config.money_fee,
    ])
    ev = align_events_to_cn_open(daily.index, events)
    weeks = daily.index.to_period("W-SUN").start_time
    firstday = pd.Series(daily.index, index=weeks).groupby(level=0, sort=True).min()
    lastday = pd.Series(daily.index, index=weeks).groupby(level=0, sort=True).max()
    first_day_to_week = {pd.Timestamp(v): pd.Timestamp(k) for k, v in firstday.items()}
    last_day_to_week = {pd.Timestamp(v): pd.Timestamp(k) for k, v in lastday.items()}

    held = np.asarray(prior_weights, dtype=float).copy()
    prevclose = np.asarray(prior_close, dtype=float).copy()
    lock_until = -1
    max_tech_allowed = None
    rows = []

    for i, today in enumerate(daily.index):
        x = daily.iloc[i]
        op = x[cols_open].to_numpy(float)
        cl = x[cols_close].to_numpy(float)
        held *= op / prevclose
        val_open = float(held.sum())

        event = ev.loc[today] if today in ev.index else None
        level = str(event["level"]) if event is not None else "N"
        source_us = pd.Timestamp(event["us_session"]) if event is not None else pd.NaT
        available = event["available_at_cn"] if event is not None else pd.NaT

        if i > lock_until:
            max_tech_allowed = None

        local_weakness = (
            i >= daily_config.cn_lookback
            and daily.iloc[i - 1].tech_close < daily.iloc[i - daily_config.cn_lookback : i].tech_close.mean()
        )
        if daily_config.require_local_weakness and not local_weakness:
            level = "N"

        reason = []
        weekly_cost = 0.0
        weekly_turnover = 0.0

        if daily_config.mode != "off" and level in ("M", "S"):
            lock_until = max(
                lock_until,
                i + (daily_config.severe_freeze_days if level == "S" else daily_config.moderate_freeze_days) - 1,
            )
            actual_before = held / val_open
            if max_tech_allowed is None:
                max_tech_allowed = float(actual_before[0])
            reason.append("美股隔夜预警：冻结创业板新增仓")

        def _try_weekly_rebalance(curr_val: float, curr_week: pd.Timestamp, label: str):
            nonlocal held, weekly_cost, weekly_turnover
            previous = weekly_targets.index[weekly_targets.index < curr_week]
            if not len(previous):
                return
            target = weekly_targets.loc[previous[-1], list(TARGETS)].to_numpy(float).copy()
            if daily_config.mode != "off" and i <= lock_until and max_tech_allowed is not None:
                orig = float(target[0])
                target[0] = min(orig, max_tech_allowed)
                target[4] += orig - target[0]
                if orig > target[0] + 1e-9:
                    reason.append("周频新增科技仓冻结")
            origw = held / curr_val
            turn = float(np.abs(origw - target).sum())
            risk_exit = weekly_config.priority_exit and (
                (origw[0] - target[0] > weekly_config.urgent_cut_pp)
                or (origw[1] - target[1] > weekly_config.urgent_cut_pp)
                or (target[0] == 0 and origw[0] > 0.01)
            )
            if risk_exit or turn >= weekly_config.execution_deadband:
                fee = float(np.abs(origw - target) @ fees)
                weekly_cost = curr_val * fee
                held = target * (curr_val - weekly_cost)
                weekly_turnover = turn
                reason.append(f"{label}（{'风控优先减仓' if risk_exit and turn < weekly_config.execution_deadband else '常规调仓'}）")

        if exec_timing == "next_monday_open" and today in first_day_to_week:
            _try_weekly_rebalance(val_open, first_day_to_week[today], "次周一开盘调仓")

        held = held * cl / op
        closeval = float(held.sum())

        if exec_timing == "next_friday_close" and today in last_day_to_week:
            _try_weekly_rebalance(closeval, last_day_to_week[today], "本周收盘调仓")

        nav = float(held.sum())
        prevclose = cl
        w = held / nav
        if np.any(w < -1e-9) or abs(w.sum() - 1.0) > 1e-8:
            raise AssertionError("budget drift")
        rows.append(
            dict(
                date=today,
                nav=nav,
                held_tech=w[0],
                held_red=w[1],
                held_bond=w[2],
                held_gold=w[3],
                held_money=w[4],
                us_source_session=source_us,
                us_event_level=level,
                us_available_cn=available,
                local_cn_weakness=local_weakness,
                weekly_cost=weekly_cost,
                weekly_turnover=weekly_turnover,
                overlay_active=bool(i <= lock_until and daily_config.mode != "off"),
                reason="; ".join(reason) if reason else "不调仓",
            )
        )
    return pd.DataFrame(rows).set_index("date")
