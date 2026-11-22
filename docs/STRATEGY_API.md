# 策略核心接口命名约定及迁移

## 原则

生产运行路径固定为 `live/ → strategy_core.api → strategy_core/*`。历史版本代码只在 `history/` 下，不会被线上模块导入。研发版本仅作为**历史出处**存在；当前软件配置和函数接口由业务意义表达，不能再用研发流水号表达能力。

## 核心配置与接口

| 冻结版本原名 | 现行名称 |
| --- | --- |
| `CNV17Config` | `ChinaTrendConfig`（内部） |
| `USV17Config` | `USTrendConfig`（内部） |
| `V18CNConfig` | `ChinaPortfolioConfig` |
| `V18USConfig` | `USPortfolioConfig` |
| `V20Config` | `PublicRuleConfig` |
| `signals_v13` | `build_regime_signals` |
| `build_cn_v17_weekly` | `build_china_trend_weekly` |
| `run_cn_daily_replay_v17` | `replay_china_trend_daily` |
| `us_signals_v17` | `build_us_trend_signals` |
| `simulate_us_v17` | `simulate_us_trend` |
| `build_cn_v18_weekly` | `build_china_portfolio_weekly` |
| `build_us_v18` | `build_us_portfolio` |
| `run_cn_v20` | `evaluate_china_rule_blend` |
| `run_us_v20` | `evaluate_us_rule_blend` |

原先被复制到现行核心的 `fusion_v5`～`fusion_v11` 现为七个职责明确的基础模块：`trend_rotation`、`dividend_overlay`、`cash_sleeve`、`growth_reentry`、`momentum_risk`、`multi_asset`、`ensemble_guard`。配置类和信号生成函数对应重命名为 `TrendRotationConfig`、`DividendOverlayConfig`、`CashSleeveConfig`、`GrowthReentryConfig`、`MomentumRiskConfig`、`MultiAssetConfig`、`EnsembleRiskConfig`，以及 `build_*_signals`、`simulate_*`。

## 运行时信号列和 HTTP 输出调整

已同步迁移生产计算和 Web 消费端，**没有保留旧字段别名**，从而让以后重构不会继续依赖版本命名：

| 旧列或属性 | 现行列或属性 |
| --- | --- |
| `v6_red_eligible` | `dividend_eligible` |
| `v8_reentry_state` | `reentry_state` |
| `v8_probe_signal` / `v8_confirm_signal` | `reentry_probe_signal` / `reentry_confirm_signal` |
| `v9_tech_vs_red_6w` | `tech_vs_dividend_6w` |
| `v10_reason` | `allocation_reason` |
| `v13_regime_active` / `v13_explanation` | `regime_active` / `regime_explanation` |
| `v14_warning` / `v14_reason` | `market_warning` / `market_reason` |
| `v14_market_regime` | `market_regime` |
| `us_v17_trend_score` / `us_v17_realized_vol` | `us_trend_score` / `us_realized_vol` |
| `us_v17_vol_mult` | `us_vol_mult` |

其它基类辅助字段遵循相同语义前缀：`v6_reason → dividend_reason`、`v7_red_add → dividend_add`、`v8_* → reentry_*`、`v9_* → risk_*`、`v14_effect → market_effect`。

**注意：** 这是内部 Python 公共 API 和现行 HTTP 字段的重大命名迁移。如果有独立客户端消费旧 API，需要同批升级。历史归档源码、研究 JSON 和冻结 HTML 文件仍沿用原版本标识，不应修改以冒充新回测结果。

## 等价验证

`tests/test_strategy_naming.py` 用 `history.v18`、`history.v20` 的原实现与新接口在同一冻结行情上比较中美目标仓位、组合净值及公开规则融合结果，测试使用精确相等，不放宽为近似收益。持续运行静态检查，防止现行核心再引入带版本号的类、函数或模块。

```bash
python -m pytest tests/test_strategy_naming.py -q
```
