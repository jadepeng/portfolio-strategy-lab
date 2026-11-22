# 历史版本归档（只读研究快照）

此目录存放不再作为当前在线交易执行入口的旧版模型和研究资料。

- `strategies/`：V1—V11 的双资产、趋势动物、红利/科技轮动、五资产策略及执行实验；
- `v13/`—`v22/`：V13–V22 原始版本模块，已统一修正**归档版本内部**的导入路径为 `history.vXX.*`；
- `reports/`：V4–V22 各期研究报告；
- `verification/`：根目录旧 `verified_*.json` 快照及价格数据解码工具；
- `apps/`：原根目录的 Streamlit 研究应用，通过 `run_linux.sh` 或 `run_windows.bat` 启动。

## 核心原则

1. **历史版本之间只相互依赖历史版本**，并可引用 `history.strategies.*` 基类。它们不得回引 `strategy_core`。
2. **线上执行代码禁止依赖 `history`**：当前模型实现位于 [`../strategy_core/`](../strategy_core/README.md)，其基础模块已复制成为独立的 `strategy_core.baselines`，不随历史文件而变。
3. 历史复现工具继续保持在 `scripts/`，但导入 `history.vXX`；当前看板生成脚本和 `live/` 导入 `strategy_core`。
4. `market_data/`、`research_outputs/` 目录保留原位，避免破坏历史复现与静态快照；根目录旧版本的验证 JSON 迁往 `history/verification/`。

## 验证

```bash
python -m pytest tests -q
python scripts/reproduce_v22.py
python scripts/reproduce_v11.py
```

归档只是结构拆分，不代表重新优化、验证策略收益或允许自动真实交易。
