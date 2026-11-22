# 历史验证文件

原先位于仓库根目录的 `verified_*.json` 研究结果快照统一保存在此目录。历史复现脚本中针对这些文件的读写路径已同步修改；这里不是当前在线策略的实时行情来源。

历史价格解码工具可从仓库根目录运行：

```bash
python history/verification/decode_verified_prices.py
```
