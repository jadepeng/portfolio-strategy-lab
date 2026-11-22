"""Run bt against frozen research inputs, without a market data download."""
from pathlib import Path
import json
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import pandas as pd
from live.backtest import run_bt_reference
from strategy_core.market_snapshot import load_weekly
from strategy_core.us_snapshot import load_us_prices, load_us_open
from strategy_core.portfolio import build_cn_v18_weekly, build_us_v18


def main():
    cn = pd.read_csv(ROOT/'market_data'/'verified_5asset_weekly_v13.csv', parse_dates=['week_start']).set_index('week_start')
    us, op = load_us_prices(), load_us_open()
    _, csig, _ = build_cn_v18_weekly(cn, load_weekly(), us)
    usig, _ = build_us_v18(us, opens=op)
    results = {m: run_bt_reference(p, s, m) for m, p, s in [('CN', cn, csig), ('US', us, usig)]}
    if not all(r['state'] == 'available' for r in results.values()):
        print('bt is not installed. Run: python -m pip install -r requirements-open-source.txt')
        return 2
    output = ROOT/'research_outputs'/'bt_independent_reference.json'
    output.write_text(json.dumps(results, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    print('Saved:', output)
    for market, result in results.items():
        print(market, 'next close, zero fee reference:', round(result['cumulative_pct'], 3), '%')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
