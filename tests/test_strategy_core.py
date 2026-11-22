"""Architecture invariants for the independently maintained production strategy core.

Historical versions must remain reproducible, but live execution must not import them.
"""
from __future__ import annotations

import ast
from dataclasses import asdict
import importlib
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
CORE_MODULES = (
    "animal_state", "regime", "crowding", "market_snapshot", "cross_market",
    "us_snapshot", "us_strategy", "daily_replay", "overnight",
    "cn_strategy", "us_trend", "portfolio", "advisory", "public_rules",
    "metrics", "analytics", "allocation", "intraday",
)
ARCHIVED_VERSIONS = ("v13", "v14", "v15", "v16", "v17", "v18", "v19", "v20", "v21", "v22")
VERIFIED_RESULTS = (
    "verified_fusion_v5_results.json", "verified_fusion_v6_results.json",
    "verified_fusion_v7_results.json", "verified_fusion_v8_results.json",
    "verified_fusion_v9_results.json", "verified_fusion_v10_results.json",
    "verified_fusion_v11_results.json", "verified_results.json",
    "verified_rotation_results.json", "verified_trend_animal_v4_results.json",
)


@pytest.mark.parametrize("module", CORE_MODULES)
def test_current_core_module_imports(module):
    loaded = importlib.import_module("strategy_core." + module)
    assert loaded.__file__ is not None
    assert Path(loaded.__file__).resolve().is_relative_to(ROOT / "strategy_core")


@pytest.mark.parametrize("version", ARCHIVED_VERSIONS)
def test_historical_package_is_archived(version):
    assert not (ROOT / version).exists()
    archive = ROOT / "history" / version
    assert archive.is_dir()
    assert importlib.import_module("history." + version).__file__


@pytest.mark.parametrize("filename", VERIFIED_RESULTS)
def test_legacy_verified_output_moved(filename):
    assert not (ROOT / filename).exists()
    assert (ROOT / "history" / "verification" / filename).is_file()


def _imports(path: Path):
    parsed = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(parsed):
        if isinstance(node, ast.ImportFrom) and node.module:
            yield node.module
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name


def test_production_core_has_no_archival_or_version_imports():
    production = list((ROOT / "strategy_core").rglob("*.py"))
    production += [ROOT / "live" / "engine.py", ROOT / "live" / "decisions.py"]
    production += [ROOT / "scripts" / "build_v21_dashboard.py"]
    assert production
    errors = []
    for path in production:
        for name in _imports(path):
            if name == "history" or name.startswith("history.") or any(
                name == version or name.startswith(version + ".") for version in ARCHIVED_VERSIONS
            ):
                errors.append((str(path.relative_to(ROOT)), name))
    assert not errors, errors


def test_archived_versions_do_not_import_current_strategy():
    errors = []
    for version in ARCHIVED_VERSIONS:
        for path in (ROOT / "history" / version).rglob("*.py"):
            for name in _imports(path):
                if name == "strategy_core" or name.startswith("strategy_core."):
                    errors.append((str(path.relative_to(ROOT)), name))
    assert not errors, errors


def test_current_baseline_defaults_match_frozen_v10_v11():
    from strategy_core.baselines.fusion_v10 import V10Config as CurrentV10
    from history.strategies.fusion_v10 import V10Config as ArchivedV10
    from strategy_core.baselines.fusion_v11 import V11Config as CurrentV11
    from history.strategies.fusion_v11 import V11Config as ArchivedV11
    assert asdict(CurrentV10()) == asdict(ArchivedV10())
    assert asdict(CurrentV11()) == asdict(ArchivedV11())


def test_intraday_stop_matches_frozen_version_on_deterministic_prices():
    from strategy_core.intraday import StopConfig, stop_line
    from history.v22.intraday import StopConfig as OldConfig, stop_line as old_stop_line

    prices = pd.Series(np.linspace(100.0, 118.0, 32), dtype=float)
    assert stop_line(prices, StopConfig()) == old_stop_line(prices, OldConfig())


def test_current_execution_contract_exports_are_available():
    from strategy_core.portfolio import V18CNConfig, build_cn_v18_weekly, build_us_v18
    from strategy_core.advisory import run_cn_v20, run_us_v20
    from strategy_core.public_rules import V20Config
    from strategy_core.intraday import IntradayStopEngine
    from strategy_core.allocation import clip_weekly_risk_additions

    assert all((V18CNConfig, build_cn_v18_weekly, build_us_v18, run_cn_v20,
                run_us_v20, V20Config, IntradayStopEngine, clip_weekly_risk_additions))
