"""Keep the archived strategy imports and historical documents available."""
import importlib
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]

@pytest.mark.parametrize("name", [
    "v3_engine", "trend_animal_v4", "rotation_engine",
    "fusion_v8", "fusion_v9", "fusion_v9_execution",
])
def test_legacy_module_imports_from_history(name):
    assert not (ROOT / (name + ".py")).exists()
    mod = importlib.import_module("history.strategies." + name)
    assert mod.__file__ is not None

def test_active_shared_modules_remain_importable():
    from fusion_v10 import V10Config
    from fusion_v11 import V11Config
    from v18.integrated import V18CNConfig
    assert V10Config and V11Config and V18CNConfig

@pytest.mark.parametrize("name", [
    "V5_快速使用.txt", "V6_研究纪要.md", "V7_研究与执行建议.md",
    "V8_创业板买回规则与回测结论.md", "V9_策略研究报告.md",
    "V10_五资产配置与策略优化研究报告.md", "V11_研究报告.md",
    "纪律_V4.md", "纪律_V5.md",
])
def test_report_archived_without_deletion(name):
    assert (ROOT / "history" / "reports" / name).is_file()
    assert not (ROOT / name).exists()
