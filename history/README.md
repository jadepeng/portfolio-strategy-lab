# 历史策略与研究资料归档

此目录统一保存**非当前默认交易入口的历史策略实现**；移动文件不改变交易参数、历史结果或实时决策规则。

- `strategies/`：原始双资产 `strategy_engine.py`、V3、V4 趋势动物与轮动引擎，以及 V5/V6/V7/V8/V9/V10/V11 融合策略和 V9 执行实验。
- `reports/`：V4/V5 交易纪律、V5—V11 和 V13—V21 研究报告。

所有旧版实现从根目录移走（不保留根目录影子副本）；复现与当前模块的导入统一改为 `history.strategies.<module>`。例：

```python
from history.strategies.fusion_v10 import V10Config
from history.strategies.fusion_v11 import V11Config
```

## 验证及历史复现

在仓库根目录运行：

```bash
python -m pytest tests -q
python scripts/reproduce_v9.py
python scripts/reproduce_v11.py
```

**运行中依赖的边界**：现行 V18/V20 在线重算仍经 V13—V17 使用历史基类，因此 `v13/`—`v22/` 与 `live/` 保留原位（它们不是可直接卸载的死代码）；旧版 `pages/` 和 `scripts/` 保留入口以避免破坏已有使用方式。

**数据边界**：`verified_*.json`、`market_data/`、`research_outputs/` 的路径均不更改，以保证历史复现文件检索和当前网页快照读取。归档不构成新的收益验证或盲测。
