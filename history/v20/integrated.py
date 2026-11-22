"""V20 V18-frozen benchmark alongside independent public-rule TAA candidates."""
from __future__ import annotations
from dataclasses import replace
import pandas as pd
from history.strategies.fusion_v10 import TARGETS,simulate_v10
from history.v15.us_strategy import USConfig,simulate_us
from history.v18.integrated import V18CNConfig,V18USConfig,build_cn_v18_weekly,build_us_v18
from .public_rules import V20Config,candidate_cn,candidate_us,blend_signals


def run_cn_v20(prices:pd.DataFrame,market:pd.DataFrame,us_prices:pd.DataFrame,
               c:V20Config=V20Config()):
    c.validate()
    basecfg,baseline,baseledger=build_cn_v18_weekly(prices,market,us_prices,V18CNConfig())
    pub=candidate_cn(prices,c)
    sig=blend_signals(baseline,pub,tuple(TARGETS),c.cn_blend)
    cfg=replace(basecfg,**{k:getattr(basecfg,k)*c.cn_fee_multiplier for k in
       ('tech_fee','red_fee','bond_fee','gold_fee','money_fee')})
    # All variants use the identical V18 execution conventions.
    if c.cn_blend==0 and c.cn_fee_multiplier==1:
        ledger=baseledger
    else:
        ledger=simulate_v10(prices,sig,cfg)
    return {'baseline_signal':baseline,'public_signal':pub,'signal':sig,
            'baseline_ledger':baseledger,'ledger':ledger,'config':cfg}


def run_us_v20(prices:pd.DataFrame,opens:pd.DataFrame,c:V20Config=V20Config()):
    c.validate()
    cf=V18USConfig(qqq_core=c.us_core,fill_mode=c.us_fill_mode)
    modern,baseline_ledger=build_us_v18(prices,cf,opens)
    pub=candidate_us(prices,c)
    sig=blend_signals(modern,pub,('target_qqq','target_bil'),c.us_blend)
    simconf=USConfig(qqq_core=c.us_core,fill_mode=c.us_fill_mode,
            rebalance_threshold=cf.rebalance_threshold,
            qqq_side_fee=cf.qqq_side_fee*c.us_fee_multiplier,
            bil_side_fee=cf.bil_side_fee*c.us_fee_multiplier)
    if c.us_blend==0 and c.us_fee_multiplier==1:
        ledger=baseline_ledger
    else:
        ledger=simulate_us(prices,sig,simconf,opens)
    return {'baseline_signal':modern,'public_signal':pub,'signal':sig,
            'baseline_ledger':baseline_ledger,'ledger':ledger,'config':simconf}


def run_advisory_cn(prices,market,us_prices,c:V20Config=V20Config()):
    from .public_rules import advisory_gate_cn
    basecfg,baseline,baseledger=build_cn_v18_weekly(prices,market,us_prices,V18CNConfig())
    signal=advisory_gate_cn(prices,baseline,c)
    ledger=simulate_v10(prices,signal,basecfg)
    return baseline,signal,baseledger,ledger


def run_advisory_us(prices,opens,c:V20Config=V20Config()):
    from .public_rules import advisory_gate_us
    cf=V18USConfig(qqq_core=c.us_core,fill_mode=c.us_fill_mode)
    baseline,baseledger=build_us_v18(prices,cf,opens)
    signal=advisory_gate_us(prices,baseline,c)
    simconf=USConfig(qqq_core=c.us_core,fill_mode=c.us_fill_mode,
        rebalance_threshold=cf.rebalance_threshold,
        qqq_side_fee=cf.qqq_side_fee,bil_side_fee=cf.bil_side_fee)
    return baseline,signal,baseledger,simulate_us(prices,signal,simconf,opens)
