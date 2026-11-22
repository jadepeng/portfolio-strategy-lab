# 现行量化策略核心（唯一维护入口）

**生产策略不使用研究版本号命名。** `history/` 保留原 V1—V22 名字作为冻结研究来源，现行策略使用业务职责命名，后续改进直接在这里进行。

## 稳定入口

```python
from strategy_core.api import (
    ChinaPortfolioConfig, USPortfolioConfig, PublicRuleConfig,
    build_china_portfolio_weekly, build_us_portfolio,
    evaluate_china_rule_blend, evaluate_us_rule_blend,
    StopConfig, IntradayStopEngine, clip_weekly_risk_additions,
)

cn_cfg = ChinaPortfolioConfig()
us_cfg = USPortfolioConfig()
```

生产调用方（`live/engine.py`、`live/decisions.py`）和生成看板的工具统一引用 `strategy_core.api`，避免与内部模块布局耦合。当前策略核心不允许导入 `history.*`。

## 模块职责

| 模块 | 责任 |
| --- | --- |
| `api.py` | 对外稳定语义接口，避免现行客户端绑定研究版本号 |
| `portfolio.py` | 中美独立的多资产组合构建 |
| `cn_strategy.py`、`us_trend.py`、`us_strategy.py` | 中国与美国交易信号、趋势状态、周频重平衡 |
| `regime.py`、`crowding.py`、`cross_market.py`、`overnight.py` | 趋势状态、量价拥挤、隔夜事件与跨市场风险 |
| `daily_replay.py` | 日频回放，保持因果顺序与次期开盘/收盘约束 |
| `public_rules.py`、`advisory.py` | 可配置公开规则建议与双重否决门控 |
| `intraday.py`、`allocation.py` | 盘中有效跌破后的分档减仓和新增风险约束 |
| `analytics.py`、`metrics.py` | 可审计统计指标、信号历史和研究输出 |
| `market_snapshot.py`、`us_snapshot.py` | 离线核验行情读取 |
| `baselines/` | `trend_rotation`、`dividend_overlay`、`cash_sleeve`、`growth_reentry`、`momentum_risk`、`multi_asset`、`ensemble_guard` |

## 命名和兼容规则

- 新增类、函数、模块、数据列都使用**业务含义**命名，禁止生产代码出现 `V17Config`、`fusion_v10`、`v14_warning` 等研发版本号。
- 冻结研究结果的原始标识（例如历史 CSV、研究报告、研究数据快照）继续保留，以维持出处和可复现性。
- 这次属于**破坏旧字段名的生产接口重构**，`live` 输出字段、当前 Web/测试已经同步。外部使用旧 HTTP 属性（例如 `portfolio.v10_reason`）的代码需要按[迁移表](../docs/STRATEGY_API.md)更新。
- 算法默认参数、阈值、执行时间和交易成本保持不变；数值一致性以冻结研究版本的目标仓位、NAV 对照测试作为准入条件。

## 测试

```bash
python -m pytest tests/test_strategy_naming.py tests/test_strategy_core.py -q
python -m pytest tests -q
node tests/ptms_smoke.cjs
```
