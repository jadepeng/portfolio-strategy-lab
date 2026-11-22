"""Semantic-only production interface and numerical parity with frozen research.

Tests deliberately import versioned names ONLY from the immutable history tree.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
VERSION_SYMBOL = re.compile(r"v(?:[5-9]|1[0-9]|2[0-2])", re.IGNORECASE)


@pytest.mark.parametrize("folder", ("strategy_core", "live"))
def test_production_python_symbols_have_no_revision_numbers(folder):
    """Current modules, APIs, classes, field accessors and local names use semantics."""
    offending = []
    for path in (ROOT / folder).rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        identifiers = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                identifiers.append(node.name)
            elif isinstance(node, ast.Name):
                identifiers.append(node.id)
            elif isinstance(node, ast.Attribute):
                identifiers.append(node.attr)
            elif isinstance(node, ast.arg):
                identifiers.append(node.arg)
            elif isinstance(node, ast.ImportFrom):
                identifiers.extend([node.module or ""] + [a.name for a in node.names])
            elif isinstance(node, ast.Import):
                identifiers.extend(a.name for a in node.names)
        for name in identifiers:
            if VERSION_SYMBOL.search(name):
                offending.append((str(path.relative_to(ROOT)), name))
        if path.is_relative_to(ROOT / "strategy_core") and VERSION_SYMBOL.search(path.stem):
            offending.append((str(path.relative_to(ROOT)), "versioned module filename"))
    assert not offending, offending


def test_public_production_facade_is_semantic():
    from strategy_core import api
    expected = {
        "ChinaPortfolioConfig", "USPortfolioConfig", "PublicRuleConfig",
        "build_china_portfolio_weekly", "build_us_portfolio",
        "evaluate_china_rule_blend", "evaluate_us_rule_blend",
        "IntradayStopEngine", "StopConfig", "clip_weekly_risk_additions",
    }
    assert expected.issubset(set(api.__all__))
    assert all(getattr(api, name, None) is not None for name in expected)
    assert not any(VERSION_SYMBOL.search(name) for name in api.__all__)


@pytest.fixture(scope="module")
def source_data():
    from strategy_core.api import load_weekly, load_us_prices, load_us_open
    cn = pd.read_csv(ROOT / "market_data" / "verified_5asset_weekly_v13.csv",
                     parse_dates=["week_start"]).set_index("week_start")
    return cn, load_weekly(), load_us_prices(), load_us_open()


def test_portfolio_economic_outputs_match_frozen_historical_engines(source_data):
    from history.v18.integrated import build_cn_v18_weekly, build_us_v18
    from strategy_core.api import build_china_portfolio_weekly, build_us_portfolio
    from history.strategies.fusion_v10 import TARGETS

    cn, market, us, us_open = source_data
    _, old_cn_signal, old_cn_ledger = build_cn_v18_weekly(cn, market, us)
    _, new_cn_signal, new_cn_ledger = build_china_portfolio_weekly(cn, market, us)
    pd.testing.assert_frame_equal(old_cn_signal[list(TARGETS)],
                                  new_cn_signal[list(TARGETS)], check_exact=True)
    pd.testing.assert_series_equal(old_cn_ledger["nav"], new_cn_ledger["nav"], check_exact=True)

    old_us_signal, old_us_ledger = build_us_v18(us, opens=us_open)
    new_us_signal, new_us_ledger = build_us_portfolio(us, opens=us_open)
    pd.testing.assert_frame_equal(
        old_us_signal[["target_qqq", "target_bil"]],
        new_us_signal[["target_qqq", "target_bil"]], check_exact=True)
    pd.testing.assert_series_equal(old_us_ledger["nav"], new_us_ledger["nav"], check_exact=True)

    for frame in (new_cn_signal, new_us_signal):
        assert not [c for c in frame.columns if VERSION_SYMBOL.search(c)]


def test_public_rule_blends_match_frozen_historical_engines(source_data):
    from history.v20.integrated import run_cn_v20, run_us_v20
    from history.v20.public_rules import V20Config
    from strategy_core.api import (
        evaluate_china_rule_blend, evaluate_us_rule_blend, PublicRuleConfig,
    )
    from history.strategies.fusion_v10 import TARGETS

    cn, market, us, us_open = source_data
    old_cn = run_cn_v20(cn, market, us, V20Config(cn_blend=.25))
    new_cn = evaluate_china_rule_blend(
        cn, market, us, PublicRuleConfig(cn_blend=.25))
    pd.testing.assert_frame_equal(
        old_cn["signal"][list(TARGETS)], new_cn["signal"][list(TARGETS)],
        check_exact=True)
    pd.testing.assert_series_equal(
        old_cn["ledger"]["nav"], new_cn["ledger"]["nav"], check_exact=True)

    old_us = run_us_v20(us, us_open, V20Config(us_blend=.25))
    new_us = evaluate_us_rule_blend(us, us_open, PublicRuleConfig(us_blend=.25))
    pd.testing.assert_frame_equal(
        old_us["signal"][["target_qqq", "target_bil"]],
        new_us["signal"][["target_qqq", "target_bil"]], check_exact=True)
    pd.testing.assert_series_equal(
        old_us["ledger"]["nav"], new_us["ledger"]["nav"], check_exact=True)


def test_production_live_indicators_use_semantic_output_keys():
    from live.engine import extract_cn_indicators
    src = (ROOT / "live" / "engine.py").read_text(encoding="utf-8")
    web = (ROOT / "web" / "ptms.js").read_text(encoding="utf-8")
    for name in ("allocation_reason", "regime_active", "regime_explanation",
                 "market_warning", "market_reason"):
        assert repr(name) in src
    assert "cn.portfolio.allocation_reason" in web
    assert "cn.portfolio.market_warning" in web
    assert not re.search(r"cn\.portfolio\.v(?:[5-9]|1[0-9]|2[0-2])", web)
