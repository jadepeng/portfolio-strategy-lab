# 旧版策略与研究资料归档

这里存放不再作为当前 V18—V22 默认交易策略入口的早期策略与研究成果：

- `strategies/`：V3 回测、V4 趋势动物、红利科技轮动、V8/V9 策略、V9 执行实验。
- `reports/`：V4/V5 交易纪律与 V5—V11 历史研究报告。

历史策略仍可以从仓库根目录复现：

```bash
python -m pytest tests/test_history_layout.py tests/test_trend_animal_v4.py tests/test_rotation.py tests/test_fusion_v8.py tests/test_fusion_v9.py tests/test_fusion_v9_execution.py -q
python scripts/reproduce_v9.py
```

旧代码的导入方式改为 `from history.strategies.fusion_v9 import V9Config` 等。

**仍留在根目录的共享依赖**：`strategy_engine.py`、`fusion_v5.py`、`fusion_v6.py`、`fusion_v7.py`、`fusion_v10.py`、`fusion_v11.py`，因为现行 V18—V22 回测、策略页或模型集成仍引用它们。现行 `v13/`—`v22/` 和 `live/` 保留原位。本次也不移动 `verified_*.json`，以保持现有历史数据读取路径。
