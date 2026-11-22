# 网页启动与托管

当前入口分为两种：

| 入口 | 行为 |
|---|---|
| 根目录 `run_dashboard_windows.bat` 或 `python scripts/serve_dashboard.py` | 启动本地动态服务，访问 http://127.0.0.1:8765 ，自动获取行情、重算策略并刷新网页 |
| 直接打开 `web/index.html` | 可查看内嵌历史研究；提示启动行情服务，不把冻结快照当成实时行情 |

推荐先运行 `python -m pip install -r requirements-open-source.txt`，安装 efinance、bt 与交易日历等可选组件。也可沿用现有 requirements.txt 的公共 yfinance 源，未安装的功能明确显示状态。

GitHub Pages / Cloudflare Pages 只能提供本目录的静态文件，不能运行 Python 行情采集或策略计算。单独上传 index.html 不会获得实时数据。当前动态服务只监听本机；如需公开部署，需要单独部署并适配受控后端，而非把行情凭据嵌入网页。

源模板为 `template.html`、`v21.css`、`v21.js`。动态服务直接读取这些源文件；重建离线交付网页使用 `python scripts/build_v22_dashboard.py`。原 `build_v21_dashboard.py` 用于复核 V21 研究快照，随后再运行 V22 构建保持首页为最新版。

完整说明见 [运行说明](../docs/LIVE_DASHBOARD.md) 和 [开源组件选型](../docs/OPEN_SOURCE_STACK.md)。
