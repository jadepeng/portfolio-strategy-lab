"""Historical strategy implementations must live under history/ without breaking current imports."""
import importlib
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LEGACY = [
    "strategy_engine", "v3_engine", "trend_animal_v4", "rotation_engine",
    "fusion_v5", "fusion_v6", "fusion_v7", "fusion_v8", "fusion_v9",
    "fusion_v9_execution", "fusion_v10", "fusion_v11",
]

@pytest.mark.parametrize("name", LEGACY)
def test_legacy_module_imports_from_history(name):
    assert not (ROOT / (name + ".py")).exists()
    mod = importlib.import_module("history.strategies." + name)
    assert mod.__file__ is not None

def test_active_strategy_chains_import_after_archive():
    from history.strategies.fusion_v10 import V10Config
    from history.strategies.fusion_v11 import V11Config
    from history.v18.integrated import V18CNConfig as ArchivedV18CNConfig
    from strategy_core.portfolio import V18CNConfig
    from strategy_core.advisory import run_cn_v20
    assert V10Config and V11Config and V18CNConfig and ArchivedV18CNConfig and run_cn_v20

def test_no_legacy_root_imports_remain():
    legacy = "|".join(re.escape(name) for name in LEGACY)
    pattern = re.compile(r"^\s*(?:from\s+(?:" + legacy + r")\s+import|import\s+(?:" + legacy + r")\b)", re.MULTILINE)
    offenders = []
    for path in ROOT.rglob("*.py"):
        if ".git" in path.parts:
            continue
        if pattern.search(path.read_text(encoding="utf-8")):
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []

@pytest.mark.parametrize("name", [
    "V5_快速使用.txt", "V6_研究纪要.md", "V7_研究与执行建议.md",
    "V8_创业板买回规则与回测结论.md", "V9_策略研究报告.md",
    "V10_五资产配置与策略优化研究报告.md", "V11_研究报告.md",
    "纪律_V4.md", "纪律_V5.md",
    "V14_成交量拥挤度牛熊与趋势纪律研究报告.md",
    "V15_中美双策略与跨市场风险研究报告.md",
    "V16_美股隔夜与A股日频风控研究报告.md",
    "V18_中美双策略_Gemini融合审计与后续优化.md",
    "V19_中美双市场稳健性与模型集成研究报告.md",
    "V20_公开量化策略融合与稳健性审计报告.md",
    "V21_中美双市场策略可视化与前瞻研究报告.md",
    "V13_坚决进出场和年度收益保护研究报告.md",
])
def test_report_archived_without_deletion(name):
    assert (ROOT / "history" / "reports" / name).is_file()
    assert not (ROOT / name).exists()
    if name.startswith("V13_"):
        assert not (ROOT / "reports" / name).exists()
