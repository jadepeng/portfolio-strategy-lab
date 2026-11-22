"""V10: regime-aware, no-leverage multi-asset risk-budget overlay on V9.

Assets: tech/ChiNext ETF, red/dividend ETF, 10Y CN government bond ETF,
physical-gold ETF, cash/money ETF. All are adjusted-close proxies.
This is an independent research model, not CICC ARES or Bridgewater implementation.

Week t close -> signal[t]; signal[t] trades only at week t+1 close.
No same-bar fill; initial benchmark holdings are assumed pre-established without an
entry commission. Live brokerage execution is NOT performed.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
import numpy as np
import pandas as pd
from fusion_v5 import FusionConfig
from fusion_v6 import V6Config
from fusion_v7 import V7Config
from history.strategies.fusion_v8 import V8Config
from history.strategies.fusion_v9 import V9Config, signals_v9

ASSETS=('tech','red','bond','gold','money')
PRICES=('tech','red','bond_511260_adj','gold_518880_adj','cash_511880_adj')
TARGETS=('target_tech','target_red','target_bond','target_gold','target_money')


@dataclass(frozen=True)
class V10Config:
    bond_cap:float=.30
    gold_cap:float=.15
    reserve_money:float=.08
    trend_weeks:int=20
    momentum_weeks:int=13
    slow_momentum_weeks:int=26
    volatility_weeks:int=26
    allocation:str='capped'     # capped or inverse_vol, not ex-post optimized
    portfolio_annual_vol_cap:float=.10
    portfolio_vol_brake:bool=False
    min_red_core:float=.30
    signal_cash_hurdle:bool=True
    execution_rebalance_weeks:int=1  # 1 = all signals, optional 4-week execution experiment
    execution_deadband:float=.12      # same 12% turnover deadband as V9; 0 disables
    urgent_cut_pp:float=.04
    priority_exit:bool=False  # experimental; off to reproduce V9 execution
    tech_fee:float=.0015
    red_fee:float=.0015
    bond_fee:float=.0003
    gold_fee:float=.0015
    money_fee:float=.0001


def validate(prices:pd.DataFrame,c:V10Config):
    if not set(PRICES).issubset(prices.columns):
        raise ValueError(f'五资产行情需包含{PRICES}')
    x=prices.loc[:,PRICES]
    if x.isna().any().any() or (~np.isfinite(x.to_numpy())).any() or (x<=0).any().any():
        raise ValueError('价格必须为正，且不得缺失')
    if len(x)<max(35,c.slow_momentum_weeks,c.volatility_weeks)+10:
        raise ValueError('样本未完成预热')
    if not prices.index.is_monotonic_increasing or prices.index.has_duplicates:
        raise ValueError('日期必须严格递增且无重复')
    if c.allocation not in ('capped','inverse_vol'):
        raise ValueError('allocation must be capped/inverse_vol')
    if not (5<=c.trend_weeks<=52 and 4<=c.momentum_weeks<=52 and
            8<=c.slow_momentum_weeks<=78 and 12<=c.volatility_weeks<=52):
        raise ValueError('信号窗口不合法')
    if not (0<=c.bond_cap<=.5 and 0<=c.gold_cap<=.3 and
            0<=c.reserve_money<=.5 and 0<=c.min_red_core<=.5):
        raise ValueError('仓位上下限不合法')
    if not (.03<=c.portfolio_annual_vol_cap<=.30):
        raise ValueError('目标波动率区间不合法')
    if not (c.execution_rebalance_weeks>=1 and 0<=c.execution_deadband<=.6
            and 0<=c.urgent_cut_pp<=.5):
        raise ValueError('调仓频率或阈值非法')
    for f in ('tech_fee','red_fee','bond_fee','gold_fee','money_fee'):
        if not 0<=getattr(c,f)<.03:raise ValueError('交易成本非法')


def _capped_inverse_vol(available:float,elig:list[bool],vol:list[float],caps:list[float]):
    # Water-filling through iterative cap: invite equal ex-ante independent risk
    # from bond/gold before considering correlations with the rest of holdings.
    chosen=np.zeros(2,float)
    active=[i for i in range(2) if elig[i] and caps[i]>0]
    remaining=available
    for _ in range(3):
        if not active or remaining<=1e-12:break
        scores=np.array([1/max(.005,vol[i]) for i in active])
        shares=scores/scores.sum()*remaining
        left=[]
        for i,w in zip(active,shares):
            if w>=caps[i]-chosen[i]-1e-12:
                put=max(0.,caps[i]-chosen[i]);chosen[i]+=put;remaining-=put
            else:left.append(i)
        if len(left)==len(active):
            for i,w in zip(active,shares):chosen[i]+=w
            remaining=0
            break
        active=left
    return chosen


def signals_v10(prices:pd.DataFrame,base:FusionConfig,model:V6Config,
                cash:V7Config,c:V10Config=V10Config(),
                v9:V9Config=V9Config(),v8:V8Config=V8Config()) ->pd.DataFrame:
    validate(prices,c)
    if base.frequency!='week':raise ValueError('V10 使用周频信号')
    if c.min_red_core>cash.red_core+1e-10:
        raise ValueError('V10最低红利核心不得高于V9设定的核心')
    f=signals_v9(prices,base,model,cash,v9,v8).copy()
    returns=prices.loc[:,PRICES].pct_change(fill_method=None)
    asset_vol=returns.rolling(c.volatility_weeks,min_periods=c.volatility_weeks).std(ddof=1)*np.sqrt(52)
    money=prices.cash_511880_adj
    money_l=money.pct_change(c.momentum_weeks,fill_method=None)
    for name,col in (('bond','bond_511260_adj'),('gold','gold_518880_adj')):
        series=prices[col]
        f[name+'_momentum_fast']=series.pct_change(c.momentum_weeks,fill_method=None)
        f[name+'_momentum_slow']=series.pct_change(c.slow_momentum_weeks,fill_method=None)
        f[name+'_trend']=series>series.rolling(c.trend_weeks,min_periods=c.trend_weeks).mean()
        f[name+'_eligible']= f[name+'_trend'] & (f[name+'_momentum_fast']>(money_l if c.signal_cash_hurdle else 0)) & (f[name+'_momentum_slow']>0)
        f[name+'_vol']=asset_vol[col]
    weights=[];risks=[];reasons=[]
    for i in range(len(f)):
        t=float(f.target_tech.iloc[i]); r=float(f.target_red.iloc[i]); money_budget=max(0,1-t-r)
        free=max(0,money_budget-c.reserve_money)
        elig=[bool(f.bond_eligible.iloc[i]),bool(f.gold_eligible.iloc[i])]
        vols=[float(f.bond_vol.iloc[i]),float(f.gold_vol.iloc[i])]
        if c.allocation=='capped':
            b=min(c.bond_cap,free) if elig[0] else 0.
            g=min(c.gold_cap,max(0,free-b)) if elig[1] else 0.
            # Do not force bond allocation when only gold passes its trend gate.
        else:
            b,g=_capped_inverse_vol(free,elig,vols,[c.bond_cap,c.gold_cap])
        w=np.array([t,r,b,g,max(0,1-t-r-b-g)],float)
        uncapped_ann_vol=np.nan
        post_ann_vol=np.nan
        risk_scale=1.
        if i>=c.volatility_weeks:
            local=returns.iloc[max(1,i-c.volatility_weeks+1):i+1]
            if len(local)==c.volatility_weeks and local.notna().all().all():
                S=np.cov(local.to_numpy().T,ddof=1)*52
                # shrink half the off-diagonal correlations toward zero
                S=.5*S+.5*np.diag(np.diag(S))
                uncapped_ann_vol=float(np.sqrt(max(0,w@S@w)))
                if c.portfolio_vol_brake and uncapped_ann_vol>c.portfolio_annual_vol_cap:
                    # Preserve a bounded minimum red core, scale every non-core risk asset.
                    anchor=np.array([0,min(r,c.min_red_core),0,0,1-min(r,c.min_red_core)],float)
                    delta=w-anchor
                    low,high=0.,1.
                    for _ in range(35):
                        mid=(low+high)/2
                        q=anchor+mid*delta
                        sig=float(np.sqrt(max(0,q@S@q)))
                        if sig<=c.portfolio_annual_vol_cap:low=mid
                        else:high=mid
                    risk_scale=low
                    w=anchor+risk_scale*delta
                post_ann_vol=float(np.sqrt(max(0,w@S@w)))
        if (w< -1e-10).any() or abs(sum(w)-1)>1e-8 or w[1]<c.min_red_core-1e-8:
            raise AssertionError('五资产资金预算或红利核心约束错误')
        weights.append(w)
        risks.append((uncapped_ann_vol,post_ann_vol,risk_scale))
        reasons.append(('国债' if elig[0] else '国债不合格')+'/'+('黄金' if elig[1] else '黄金不合格') +
                       ('；组合波动风险缩放' if risk_scale<1-1e-5 else ''))
    f[list(TARGETS)]=pd.DataFrame(weights,index=f.index,columns=list(TARGETS))
    f[['uncapped_portfolio_vol','estimated_portfolio_vol','portfolio_risk_multiplier']]=pd.DataFrame(risks,index=f.index)
    f['v10_reason']=reasons
    return f


def simulate_v10(prices:pd.DataFrame,signals:pd.DataFrame,
                 c:V10Config=V10Config(),execution:bool=True)->pd.DataFrame:
    validate(prices,c)
    if len(prices)!=len(signals) or not prices.index.equals(signals.index):
        raise ValueError('信号与价格日期必须完全一致')
    targets=signals.loc[:,TARGETS].to_numpy(float)
    if np.any(targets<-1e-10) or not np.allclose(targets.sum(axis=1),1):
        raise ValueError('非法五资产目标')
    px=prices.loc[:,PRICES].to_numpy(float)
    # Distinct fee on each bought/sold asset. Only actual executed trades cost money.
    fees=np.array([c.tech_fee,c.red_fee,c.bond_fee,c.gold_fee,c.money_fee])
    holdings=targets[0].copy()
    rows=[]
    for i in range(len(px)):
        if i>0:
            holdings*=px[i]/px[i-1]
        gross=float(holdings.sum())
        current=holdings/gross
        desired=targets[i-1] if i>0 else targets[0]
        decision='signal' if i>0 else 'first holding (assumed)'
        if execution and i>0:
            tech_decrease=float(current[0]-desired[0]);red_decrease=float(current[1]-desired[1])
            risk_exit=(tech_decrease>c.urgent_cut_pp or red_decrease>c.urgent_cut_pp
                       or (desired[0]==0 and current[0]>0.01))
            normal_trade=bool(i%c.execution_rebalance_weeks==0)
            risk_exit=risk_exit and c.priority_exit
            if not risk_exit and not normal_trade:
                desired=current
                decision='cycle skip'
            elif not risk_exit and np.abs(current-desired).sum()<c.execution_deadband:
                desired=current
                decision='turnover skip'
            elif risk_exit:
                decision='urgent reduce'
        trade=np.abs(current-desired)
        cost_rate=float(trade@fees)
        nav=gross*(1-cost_rate)
        holdings=np.asarray(desired,float)*nav
        rows.append((nav,float(trade.sum()),gross*cost_rate,*desired,decision))
    out=signals.copy()
    out[['nav','turnover','fee','held_tech','held_red','held_bond','held_gold','held_money','execution_reason']]=pd.DataFrame(rows,index=signals.index)
    return out


def baseline_5asset_signals(v9signals:pd.DataFrame)->pd.DataFrame:
    """Precisely represent the V9 tech/red/money allocation in five dimensions."""
    f=v9signals.copy()
    f['target_bond']=0.;f['target_gold']=0.;f['target_money']=f.target_cash
    return f


def fixed_mix_signals(prices:pd.DataFrame,w=(.20,.25,.25,.10,.20))->pd.DataFrame:
    d={k:np.full(len(prices),v) for k,v in zip(TARGETS,w)}
    return pd.DataFrame(d,index=prices.index)


def results(nav:pd.Series):
    nav=nav.astype(float)
    ww=nav.pct_change(fill_method=None).dropna()
    duration=(nav.index[-1]-nav.index[0]).days/365.25
    peak=nav.cummax()
    dd=(nav/peak-1)
    rolling=nav.pct_change(52,fill_method=None).dropna()
    return dict(total=100*(nav.iloc[-1]/nav.iloc[0]-1),cagr=100*((nav.iloc[-1]/nav.iloc[0])**(1/duration)-1),
                mdd=100*float(dd.min()),vol=100*float(ww.std(ddof=1))*np.sqrt(52),
                sharpe=float(ww.mean()*52/(ww.std(ddof=1)*np.sqrt(52))) if ww.std()>0 else float('nan'),
                positive_52w=100*float((rolling>0).mean()) if len(rolling) else None,
                n_weeks=len(nav))
