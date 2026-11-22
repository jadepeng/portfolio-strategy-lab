"""V7: V6 signals with a tradable cash sleeve and conservative dividend tilts.

This is an *explicit* research model, NOT the proprietary 趋势动物 formula.
At bar t use data through t; execute at bar t+1 close. Cash sleeve can be
zero-yield deposit, fixed-yield modeling assumption, or verified 511880
forward-adjusted ETF market price proxy. Money-market ETFs are not risk-free.

Costs differ per traded instrument: equities are charged per equity buy/sell,
money ETF charges an optional secondary-market spread/commission per buy/sell.
Uninvested cash has no transaction fee. This re-costs V6 baselines consistently.
"""
from __future__ import annotations
from dataclasses import dataclass, replace
from typing import Optional
import numpy as np
import pandas as pd
from strategy_core.baselines.fusion_v5 import FusionConfig, metrics
from strategy_core.baselines.fusion_v6 import V6Config, signal_v6, v6_base

@dataclass(frozen=True)
class V7Config:
    red_core: float = .20
    red_extra: float = .0
    red_qualify: str = 'strict'  # strict: >=30wk MA, +26w momentum, +6w momentum; medium ignores 6w
    cash_mode: str = 'money_price'  # money_price, cash_zero, assumed_yield
    assumed_cash_annual: float = .01
    etf_cash_annual_cost: float = 0.
    money_etf_side_cost: float = .0001
    # When money_etf_side_cost is too optimistic, stress test up to .001.


def configure_v7(base: FusionConfig, model: V6Config, p: V7Config):
    if not 0 <= p.red_core <= .5 or not 0 <= p.red_extra <= .7 or p.red_core+p.red_extra> .85:
        raise ValueError('红利核心及动态加仓范围不合法')
    if p.red_qualify not in ('strict','medium','off'):
        raise ValueError('未知的红利趋势确认方式')
    if p.cash_mode not in ('cash_zero','money_price','assumed_yield'):
        raise ValueError('未知现金模式')
    if not -.1 < p.assumed_cash_annual < .4 or not 0 <= p.money_etf_side_cost < .02:
        raise ValueError('现金收益及费用参数非法')
    return replace(model,red_core=p.red_core)


def signals_v7(prices:pd.DataFrame, base: FusionConfig, model: V6Config,p: V7Config) ->pd.DataFrame:
    c=configure_v7(base, model,p)
    df=signal_v6(prices,base,c).copy()
    if p.red_extra<=0 or p.red_qualify=='off':
        df['v7_red_add']=0.
        return df
    win_s=6 if base.frequency=='week' else 30
    win_l=26 if base.frequency=='week' else 120
    win_ma=30 if base.frequency=='week' else 120
    red=prices.red
    positive=(red>red.rolling(win_ma,min_periods=win_ma).mean()) & (red.pct_change(win_l,fill_method=None)>0)
    if p.red_qualify=='strict':positive&=(red.pct_change(win_s,fill_method=None)>0)
    # Shift no lookahead: signal row t based on close[t], simulator next bar fill.
    cash=df.target_cash.to_numpy(dtype=float)
    inc=np.where(positive.to_numpy(dtype=bool),np.minimum(p.red_extra,cash),0.)
    df['target_red']=df.target_red.to_numpy(float)+inc
    df['target_cash']=cash-inc
    df['v7_red_add']=inc
    assert np.allclose(df[['target_tech','target_red','target_cash']].sum(axis=1),1)
    return df


def simulate_v7(prices:pd.DataFrame,base:FusionConfig, model:V6Config,p:V7Config,
                signals:Optional[pd.DataFrame]=None,mode='strategy')->pd.DataFrame:
    """Compare cash/no-cash portfolios using identical instrument fee conventions.

    'cash_zero': no return and no cash trading cost; 'assumed_yield': fixed
    annualized cash rate and zero cash transaction cost; 'money_price': 511880
    adjusted prices and cash ETF trading cost on both sides. The adjusted-price
    proxy assumes the provider's adjustment captures distributions correctly.
    """
    if signals is None:signals=signals_v7(prices,base,model,p)
    if mode=='strategy':targets=signals[['target_tech','target_red','target_cash']].to_numpy(float)
    elif mode=='tech':targets=np.tile([1.,0.,0.],(len(signals),1))
    elif mode=='red':targets=np.tile([0.,1.,0.],(len(signals),1))
    elif mode=='half':targets=np.tile([.5,.5,0.],(len(signals),1))
    else:raise ValueError('bad mode')
    if p.cash_mode=='money_price' and ('cash_511880_adj' not in prices or prices.cash_511880_adj.isna().any() or (prices.cash_511880_adj<=0).any()):
        raise ValueError('511880总收益代理价格列缺失')
    configure_v7(base,model,p)
    if len(prices)!=len(signals):raise ValueError('signals length mismatch')
    if base.frequency=='day':bars=252
    else:bars=52
    hold=np.array(targets[0],dtype=float)
    report=[]
    for i,(_,r) in enumerate(signals.iterrows()):
        if i:
            old=signals.iloc[i-1]
            cash_growth=(float(prices.cash_511880_adj.iloc[i])/float(prices.cash_511880_adj.iloc[i-1])) if p.cash_mode=='money_price' else (1+p.assumed_cash_annual)**(1/bars) if p.cash_mode=='assumed_yield' else 1
            # Data may contain missing cash_511880_adj for cash_zero/yield modes; never accidentally use it.
            hold *= np.array([float(r.tech)/float(old.tech),float(r.red)/float(old.red),cash_growth])
        gross=float(hold.sum())
        desired=np.array(targets[i-1] if i else targets[0],dtype=float)
        if mode in ('tech','red'):desired=hold/gross
        if mode=='half' and i%(20 if base.frequency=='day' else 4)!=0:desired=hold/gross
        abs_side=np.abs(hold/gross-desired)
        turnover=float(abs_side.sum()) if i else 0
        if mode=='strategy' and turnover<base.min_rebalance:desired=hold/gross;abs_side[:]=0;turnover=0
        # CashETF purchase/sale has a spread/commission; cash itself is uncharged.
        equity_cost=base.cost_per_side*(abs_side[0]+abs_side[1])
        money_cost=p.money_etf_side_cost*abs_side[2] if p.cash_mode=='money_price' else 0
        fees=gross*(equity_cost+money_cost)
        nav=gross-fees
        hold=desired*nav
        report.append((nav,turnover,fees,desired[0],desired[1],desired[2],
                       equity_cost*gross,money_cost*gross))
    f=signals.copy()
    f[['nav','turnover','fee','held_tech','held_red','held_cash','fee_equity','fee_money']]=pd.DataFrame(report,index=f.index)
    return f


def eval_cases(prices:pd.DataFrame,fee_bps=15,cash_spread_bps=1):
    base=replace(v6_base('week'),cost_per_side=fee_bps/10000)
    cfg=V6Config()
    runs={}
    for name,core,extra,qual,mode in [
       ('V6空仓收益0%',.2,0.,'off','cash_zero'),
       ('V6红利20%+511880',.2,0.,'off','money_price'),
       ('红利30%+511880',.3,0.,'off','money_price'),
       ('红利40%+511880',.4,0.,'off','money_price'),
       ('红利50%+511880',.5,0.,'off','money_price'),
       ('红利20%+严格动量最多加20%+511880',.2,.2,'strict','money_price'),
       ('红利20%+中期趋势最多加20%+511880',.2,.2,'medium','money_price'),
       ('V6红利20%+假定货币利率1.0%',.2,0.,'off','assumed_yield')]:
        p=V7Config(red_core=core,red_extra=extra,red_qualify=qual,cash_mode=mode,money_etf_side_cost=cash_spread_bps/10000)
        runs[name]=simulate_v7(prices,base,cfg,p)
    return runs
