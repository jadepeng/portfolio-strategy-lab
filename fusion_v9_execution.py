"""Experimental V9 transaction deadband with a hard de-risk exception.

Unlike a symmetric turnover threshold, a technology exit or a material
reduction in a signaled equity target is NEVER ignored just because it is small.
This experimental execution layer is off by default; it was tuned after seeing
2026 and is not an out-of-sample performance improvement.
"""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import pandas as pd
from fusion_v5 import FusionConfig
from fusion_v6 import V6Config
from fusion_v7 import V7Config
from fusion_v8 import V8Config
from fusion_v9 import V9Config, signals_v9


@dataclass(frozen=True)
class V9ExecutionConfig:
    ordinary_turnover_deadband: float = .20
    forced_equity_reduction: float = .04
    force_zero_technology_exit: bool = True


def simulate_v9_execution(prices: pd.DataFrame, base: FusionConfig,
                          model: V6Config, cash: V7Config,
                          risk: V9Config = V9Config(),
                          exec_cfg: V9ExecutionConfig = V9ExecutionConfig(),
                          v8: V8Config = V8Config()) -> pd.DataFrame:
    if not .02 <= exec_cfg.ordinary_turnover_deadband <= .6:
        raise ValueError('换手死区需在2%—60%双边换手之间')
    if not .005 <= exec_cfg.forced_equity_reduction <= .25:
        raise ValueError('强制减仓阈值需在0.5—25个百分点之间')
    sig=signals_v9(prices,base,model,cash,risk,v8).copy()
    if cash.cash_mode == 'money_price' and 'cash_511880_adj' not in prices:
        raise ValueError('货币ETF的前复权价格缺失')
    hold=sig[['target_tech','target_red','target_cash']].iloc[0].to_numpy(dtype=float).copy()
    records=[]
    for i in range(len(sig)):
        if i:
            # P&L first, on previous already-filled allocations.
            old=prices.iloc[i-1];now=prices.iloc[i]
            creturn=(float(now.cash_511880_adj)/float(old.cash_511880_adj)) if cash.cash_mode=='money_price' else (1+cash.assumed_cash_annual)**(1/52) if cash.cash_mode=='assumed_yield' else 1.
            hold*=np.array([float(now.tech)/float(old.tech),float(now.red)/float(old.red),creturn])
        gross=float(hold.sum())
        held=hold/gross
        target=sig[['target_tech','target_red','target_cash']].iloc[i-1 if i else 0].to_numpy(float)
        last_signal=sig[['target_tech','target_red','target_cash']].iloc[max(0,i-2)].to_numpy(float)
        reduction=(last_signal[:2]-target[:2])
        hard=bool(i>0 and (np.any(reduction>=exec_cfg.forced_equity_reduction)
                      or (exec_cfg.force_zero_technology_exit and target[0]<1e-9 and held[0]>.005)))
        turn=float(np.abs(held-target).sum()) if i else 0.
        if i and not hard and turn<exec_cfg.ordinary_turnover_deadband:
            target=held;turn=0.
        traded=np.abs(held-target)
        fee=gross*(base.cost_per_side*(traded[0]+traded[1])
                   +(cash.money_etf_side_cost*traded[2] if cash.cash_mode=='money_price' else 0))
        nav=gross-fee
        hold=target*nav
        records.append((nav,turn,fee,target[0],target[1],target[2],hard,
                        '硬退出/减仓优先' if hard else ('小额变动暂缓' if turn==0 and i else '常规调仓')))
    sig[['nav','turnover','fee','held_tech','held_red','held_cash','v9_exec_forced','v9_exec_reason']]=pd.DataFrame(records,index=sig.index)
    return sig
