# 当前策略核心（后续迭代唯一入口）

本目录与历史研究代码**彻底解耦**，提供给 `live/engine.py`、`live/decisions.py` 和最新看板的稳定策略接口。

## 模块布局

| 层 | 组件 | 用途 |
| --- | --- | --- |
| 基础模型 | `baselines/fusion_v5.py`—`fusion_v11.py` | 现行模型所需基础信号与五资产模拟器的**独立副本** |
| 市场感知 | `market_snapshot.py`、`us_snapshot.py`、`crowding.py`、`cross_market.py`、`overnight.py` | A 股和美股数据、成交额拥挤度与跨市场门控 |
| 趋势与分配 | `animal_state.py`、`regime.py`、`cn_strategy.py`、`us_strategy.py`、`us_trend.py`、`portfolio.py`、`daily_replay.py` | 多周期趋势、五资产组合、周频/日频策略 |
| 风控与建议 | `public_rules.py`、`advisory.py`、`intraday.py`、`allocation.py` | 双市场建议、30 分钟盘中减仓、仓位约束 |
| 绩效分析 | `metrics.py`、`analytics.py` | 归因、风险指标和研究看板 |

## 维护约定

- **只在本目录修改未来策略逻辑。** V13–V22 原始版本固定存入 `history/vXX` 供历史复现，不要修改那些文件来影响线上策略。
- 新增核心能力请将 `live/` 调用保持在稳定、可测试接口；如需改变签名，同步更新 `tests/test_strategy_core.py`、在线回归用例及前端快照。
- 禁止引入 `history.*`、根目录 `v13.*`—`v22.*` 依赖；已经引入的 V5–V11 基类通过 `strategy_core.baselines` 访问。
- 历史策略并不因改目录而改善表现。新版本必须独立验证、记录数据来源、避免前视偏差，并在上线前比对组合输出与风控行为。

```bash
python -m pytest tests/test_strategy_core.py tests/test_live_dashboard.py tests/test_ptms.py tests/test_v22.py -q
```
