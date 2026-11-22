"""Optional bt portfolio reference; explicitly different from V18 execution.

    This is a zero-fee, every-week, next-CLOSE reference for BOTH markets.
    U.S. V18 normally fills next OPEN. Deadbands, priority exits, V22 intraday
    stops and currency conversion are not silently approximated here.
"""
from __future__ import annotations

import importlib.util
import numpy as np
import pandas as pd

CN_PRICES = {'tech': 'tech', 'red': 'red', 'bond_511260_adj': 'bond',
             'gold_518880_adj': 'gold', 'cash_511880_adj': 'money'}


def effective_weights(prices, signal, market):
    keys = list(CN_PRICES.values()) if market == 'CN' else ['qqq', 'bil']
    columns = ['target_'+k for k in keys]
    if not prices.index.equals(signal.index) or signal.index.has_duplicates:
        raise ValueError('price/signal dates must match exactly')
    targets = signal[columns].copy()
    targets.columns = keys
    if not np.isfinite(targets.to_numpy()).all() or (targets < 0).any().any() or not np.allclose(targets.sum(axis=1), 1):
        raise ValueError('invalid reference portfolio weights')
    # First row is the same assumed initial allocation as the research engine;
    # every subsequent close uses only the previous completed week's signal.
    effective = targets.shift(1)
    effective.iloc[0] = targets.iloc[0]
    return effective


def run_bt_reference(prices, signal, market):
    if importlib.util.find_spec('bt') is None:
        return {'state': 'not_installed', 'message': '安装 requirements-open-source.txt 后启用 bt 多资产对照'}
    import bt
    weights = effective_weights(prices, signal, market)
    px = prices.rename(columns=CN_PRICES) if market == 'CN' else prices.rename(columns=str.lower)
    px = px[weights.columns].astype(float)
    if not np.isfinite(px.to_numpy()).all() or (px <= 0).any().any():
        raise ValueError('invalid bt prices')
    name = market+'_bt_next_close_reference'
    strategy = bt.Strategy(name, [bt.algos.WeighTarget(weights), bt.algos.Rebalance()])
    test = bt.Backtest(strategy, px, initial_capital=1_000_000, integer_positions=False,
                       commissions=lambda quantity, price: 0.0, progress_bar=False)
    result = bt.run(test)
    nav = result.prices[name].reindex(prices.index) / 100
    if nav.isna().any() or (nav <= 0).any():
        raise ValueError('invalid bt reference NAV')
    return {'state': 'available', 'engine': 'pmorissette/bt', 'market': market,
            'fill': 'next_week_close', 'fee': 0, 'rebalance': 'every_week_no_deadband',
            'cumulative_pct': float((nav.iloc[-1]/nav.iloc[0]-1)*100),
            'max_dd_pct': float((nav/nav.cummax()-1).min()*100),
            'history': [{'d': str(t.date()), 'nav': float(v)} for t, v in nav.items()],
            'note': '零费用、每周调仓、次周收盘的独立参考；未复刻V18换手死区/优先减仓，US成交时点也不同，不是同口径优劣结论。'}
