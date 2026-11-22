# 在线行情与策略驾驶舱

在项目根目录运行：

```powershell
python -m pip install -r requirements-open-source.txt
python scripts/serve_dashboard.py
```

或双击 `run_dashboard_windows.bat`，访问 http://127.0.0.1:8765 。关闭启动窗口或按 Ctrl+C 停止服务。Linux 使用 `./run_dashboard_linux.sh`。

## 数据到页面的链路

`efinance / yfinance / 可选长桥 → 统一行情校验 → V18 / V20 周频模型 + V22 完整K线观察 + bt参考 → 本地HTTP接口 → 原七页面驾驶舱`

- 页面每15秒查询一次后台；后台默认每30秒采集报价与近期30分钟K线，每900秒重新下载历史并重算策略。请求串行合并，浏览器关闭后后台仍采集；页面“暂停刷新”只暂停该页面。
- A股：159915、515180、511260、518880、511880。美股：QQQ、BIL、SPY、SMH、RSP。
- 自动选择：配置长桥进程环境变量时优先长桥；否则已安装 efinance 时使用 efinance 获取 A股、yfinance 获取美股；未安装时使用 yfinance 公共源。
- `--provider public|efinance|longbridge` 可显式选源；`--port 8766` 更换端口。选定源失败会显示故障，不跨数据商拼接复权历史。
- 带时间戳的公共分钟收盘是观察报价，不承诺交易所级实时行情；每张卡片显示来源、源数据时间、数据年龄和过期状态。休市和过期不会更新成虚假的当前价格。

## 策略与数据口径

- 在线重算重新读取整段同源历史，不拼接到冻结研究净值。切回“冻结历史研究”可复核原 V22 截图口径。
- 周频模型沿用 V18/V20，排除未完成日线与交易周。A股默认下一周收盘模拟，美股下一周开盘模拟，二者账户独立。
- 周五A股已收盘、美股本周尚未完整收盘时，保留上一份A股模型，等待完整跨市场输入，避免旧映射器在缺少当周美股行时误用两周前的风险上下文。
- A股 V18 还需要中证全指、创业板指、科创50和中证红利的指数成交额。使用明确行情 ID 获取，不用 ETF 成交量代替；任一缺失时暂停新的 A股重算，美股仍可独立更新。
- 交易日历可由 `exchange-calendars` 核验。未安装或覆盖范围不足时显示“交易日历未核验”；保守模式直到当地周五正常收盘后才允许该周进入计算，不宣称交易日已核验。
- V22 使用不复权日线和不复权30分钟线；周频模型使用调整后的价格。Eastmoney分钟线的结束时间转为引擎要求的开始时间；已结束且经过5秒缓冲才进入确认。
- 盘中模块仅观察一级破位确认。没有真实持仓与成交回报，不推断实际成交、第二档执行或跨日冷却，也不把页面情景仓位当成真实账户。
- 历史稳健性审计、bootstrap、隔夜事件映射和V22事件回放仍保留原截点，不伪装成在线更新的统计结果。

## 独立开源回测

安装上述依赖后，在线历史重算会额外调用 `pmorissette/bt`，在“稳健性审计”页面显示独立参考。也可离线使用原研究价格运行：

```powershell
python scripts/backtest_open_source.py
```

输出 `research_outputs/bt_independent_reference.json`。参考明确使用**零费用、每周调仓、次周收盘成交**；初始化假设与研究引擎一致，之后权重滞后一周。它不复刻 V18 换手死区、优先减仓或 US 次周开盘，因此不能直接据收益差宣称策略改善。未安装 bt 时明确显示未运行，不生成参考收益。

## 可选长桥

```powershell
python -m pip install -r requirements-live.txt
# 通过进程环境设置 LONGBRIDGE_APP_KEY / LONGBRIDGE_APP_SECRET / LONGBRIDGE_ACCESS_TOKEN
python scripts/serve_dashboard.py --provider longbridge
```

SDK只创建 QuoteContext；不创建交易上下文。凭据仅由服务器进程读取，不进入网页、接口或缓存。行情权限依账户实际订阅；A股指数成交额仍通过公共指数接口获取。

## 缓存、接口与验证

- `GET /api/dashboard`：研究快照、在线模型、报价、观察信号、源时间与错误状态。
- `GET /api/status`：简要健康信息。
- `POST /api/refresh`：合并刷新请求；手动请求可触发历史重算，至少间隔30秒。
- 最后一次成功响应原子保存到 `market_data/live_cache/last_good.json`。重启恢复时显示缓存状态，源时间戳保持不变。失败时保留最后数据并标注失败。
- 服务只监听本机、只暴露指定接口与网页，不开放工作目录文件；刷新请求检查同源。无需数据库、Node服务、Redis或交易平台。

## 个人交易管理系统（PTMS）扩展

本地驾驶舱已从只读研究面板升级为双账户个人交易管理系统，新增 4 个交易视图（决策与调仓台、持仓与账本、策略指标雷达、交易复盘中心）：

- **持久化账本与风控状态**：所有资金流水、买卖成交、对账校准、决策票据、V22 盘中移动止损与冷却锁及周/日复盘笔记均保存于 `market_data/personal_trading.db`（SQLite WAL 模式，随 `market_data/` 默认被 `.gitignore` 忽略）。
- **周频与日内非对称仲裁**：周频 V18 负责攻防再平衡，日内 V22 30 分钟完整 K 线仅负责有效跌破分级减仓并触发 3 个交易日冷却期；冷却期内周频加仓自动拦截并转入现金管理资产（`511880.SH` / `BIL.US`）。A 股 ETF 自动遵循 100 股整数手与 T+0/T+1 制度约束并保护长期核心底仓。
- **PTMS 接口**：
  - `GET /api/ptms/state`：获取双账户持仓投影、全量策略指标、盘中止损状态与可执行调仓单。
  - `GET /api/ptms/review?account_id=CN_MAIN`：计算剔除出入金干扰的时间加权实盘净值 (TWR)、滑点 bp 与纪律遵守率。
  - `GET /api/ptms/export?account_id=CN_MAIN&format=markdown|json`：导出 Markdown 复盘报告或 JSON 账本备份。
  - `POST /api/ptms/ledger/{init,event,reconcile}`、`POST /api/ptms/account/config`、`POST /api/ptms/decisions/{create,execute,skip}`、`POST /api/ptms/stops/reset`、`POST /api/ptms/review/note`（仅限本机同源 JSON 请求）。

```powershell
python -m pytest tests/test_ptms.py tests/test_live_dashboard.py -q
python -m pytest tests -q
node --check web/v21.js
node --check web/ptms.js
node tests/dashboard_smoke.cjs
node tests/ptms_smoke.cjs
```

本次开发环境不允许外部DNS与TCP访问，新增可选包未能在线安装；行情端到端和真实 bt 库执行须在可联网环境完成。本地测试验证原策略重算、时间隔离、缺失数据、故障标记、HTTP处理与并发合并；可选 bt 实库测试在安装依赖后自动执行。
