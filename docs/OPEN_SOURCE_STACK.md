# GitHub轻量行情与回测组件选型（2026-11-22）

针对本项目“A股五资产 + 美股QQQ/BIL + 周频轮动 + 30分钟破位观察”，选择组件组合，不引入完整实盘交易平台。

| 项目 | 适合承担的部分 | 本项目决定 | 关键边界 |
|---|---|---|---|
| [efinance](https://github.com/Micro-sheep/efinance) | 国内股票/场内ETF日线、复权、分钟行情 | 已增加可选A股适配器 | 使用明确的市场ID；公共Eastmoney接口可能限流/变化，并非独立于Eastmoney的备用源 |
| [yfinance](https://github.com/ranaroussi/yfinance) | QQQ/BIL/SPY/SMH/RSP历史和近期分钟数据 | 沿用并接入动态重算 | 可能延迟；Yahoo数据使用条件独立于软件Apache-2.0许可证 |
| [bt](https://github.com/pmorissette/bt) | 多资产目标权重、组合再平衡、组合绩效 | 新增独立参考引擎和页面展示 | MIT；依赖ffn等，轻于完整交易平台；当前参考不冒充同口径V18复制 |
| [AKShare](https://github.com/akfamily/akshare) | 更广的国内指数、ETF、宏观和基本面数据 | 后续按需扩展，当前不加入核心依赖 | 数据范围更广，接口和依赖也更多；近期接口命名变化需要固定版本后联调 |
| [backtesting.py](https://github.com/kernc/backtesting.py) | 单个可交易资产的进出场与可视化 | 当前不选为五资产主引擎 | 官方指南说明不以多资产组合再平衡为主要能力；AGPL-3.0 |
| [vectorbt](https://github.com/polakowo/vectorbt) | 大规模向量化参数研究 | 当前不加入运行时 | Numba等增加依赖；Apache-2.0附Commons Clause，区别于无附加条件的Apache-2.0 |

## 已落实的整合

1. `live/market.py`：统一资产标识、源时间、价格口径、市场时区、行情校验、独立市场失败。
2. `live/efinance_provider.py`：ETF明确市场ID、raw/qfq分离、分钟结束标签转开始标签；为该库的HTTPS客户端加超时与证书验证。
3. `live/engine.py`：直接复用已有V18/V20/V22；保护未完成日/周/K线，在线历史完整重算。
4. `live/backtest.py`：bt零费次周收盘独立参考；未安装/失败时显示真实状态。
5. `live/service.py`：合并后台刷新、原子缓存、数据时效与API；原网页显示最新模型及报价并保留研究模式。

真实行情采集不等于策略每天调仓。报价每30秒轮询，周频策略仍只在完整周收盘后更新目标；风险观察只使用完整30分钟K线。

## 上游核对资料

- [efinance API与复权参数](https://efinance.readthedocs.io/en/latest/_modules/efinance/stock/getter.html)
- [efinance明确行情ID路径](https://github.com/Micro-sheep/efinance/blob/main/efinance/common/getter.py)
- [efinance MIT许可](https://github.com/Micro-sheep/efinance/blob/main/LICENSE)
- [yfinance数据历史接口](https://ranaroussi.github.io/yfinance/reference/yfinance.price_history.html)
- [bt官方项目与组合算法](https://github.com/pmorissette/bt)
- [backtesting.py单资产边界](https://github.com/kernc/backtesting.py/blob/master/doc/examples/Quick%20Start%20User%20Guide.py)
- [AKShare接口命名变更](https://github.com/akfamily/akshare)
- [vectorbt许可](https://github.com/polakowo/vectorbt/blob/master/LICENSE.md)
- [长桥SDK只读行情API](https://longbridge.github.io/openapi/python/reference_all/)

当前环境网络受限，新增可选依赖安装与真实行情请求尚未通过端到端验证。原策略引擎和离线/故障路径可本地验证；安装可选依赖后才会运行对应上游库，不能把“写好适配代码”当成“已完成联网验证”。
