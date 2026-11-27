"""Stable, version-independent API for current portfolio decisions.

Research snapshots and historical models live under history/.  Production callers
should only import this facade rather than implementation modules or old versions.
"""
from .portfolio import ChinaPortfolioConfig, USPortfolioConfig, build_china_portfolio_weekly, build_us_portfolio
from .public_rules import PublicRuleConfig
from .advisory import run_advisory_cn, run_advisory_us, evaluate_china_rule_blend, evaluate_us_rule_blend
from .analytics import (
    verify_market_history, describe, equity_curve, yearly, latest_weights,
    execution_gate, frozen_signal_record, sha256_file,
)
from .market_snapshot import load_weekly
from .us_snapshot import load_us_prices, load_us_open
from .intraday import StopConfig, IntradayStopEngine, normalize_bars, stop_line
from .allocation import clip_weekly_risk_additions
from .risk_parity_sleeve import RiskParityConfig, blend_targets, RP40_SHARE
from .fee_calibration import calibrate_from_store, MIN_FILLS as FEE_MIN_FILLS
from .baselines.multi_asset import simulate_multi_asset

__all__ = [
    "ChinaPortfolioConfig", "USPortfolioConfig", "PublicRuleConfig",
    "build_china_portfolio_weekly", "build_us_portfolio",
    "run_advisory_cn", "run_advisory_us", "evaluate_china_rule_blend", "evaluate_us_rule_blend",
    "verify_market_history", "describe", "equity_curve", "yearly", "latest_weights",
    "execution_gate", "frozen_signal_record", "sha256_file",
    "load_weekly", "load_us_prices", "load_us_open",
    "StopConfig", "IntradayStopEngine", "normalize_bars", "stop_line", "clip_weekly_risk_additions",
    "RiskParityConfig", "blend_targets", "RP40_SHARE",
    "calibrate_from_store", "FEE_MIN_FILLS", "simulate_multi_asset",
]
