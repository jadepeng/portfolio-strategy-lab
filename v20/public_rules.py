"""Independent benchmark and signal-ensemble research grounded in public ideas.

This module is NOT a reproduction of any manager's private portfolio or trades.
- Faber: 40-week SMA approximation to 10-month trend, on weekly adjusted prices.
- Antonacci: absolute return hurdle versus local cash and cross-asset ranking.
- Alpha Architect: exclude most recent 4 weeks from 26-week momentum to reduce
  short-lived reversal/chasing; note this is a *shorter-horizon proxy* for 12-2.
- E Da: only the published qualitative idea of separately managed core vs
  tactical sleeve is used; NO synthetic valuation/PE/PB signal is fabricated.
All signals stamped end-of-week t. Existing simulators execute t+1.
"""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import pandas as pd
from fusion_v10 import TARGETS, PRICES


@dataclass(frozen=True)
class V20Config:
    cn_blend: float = .25  # research-only fixed candidate; live default remains V18
    us_blend: float = .25
    trend_weeks: int = 40
    momentum_weeks: int = 26
    skip_recent_weeks: int = 4
    top_n: int = 2
    red_core: float = .20
    us_core: float = .30
    cn_fee_multiplier: float = 1.0
    us_fee_multiplier: float = 1.0
    us_fill_mode: str = 'next_week_open'

    def validate(self):
        if not 0 <= self.cn_blend <= 1 or not 0 <= self.us_blend <= 1:
            raise ValueError('blend weight must be 0..1')
        if not (26 <= self.trend_weeks <= 52 and 13 <= self.momentum_weeks <= 52):
            raise ValueError('long-term windows out of range')
        if not 1 <= self.skip_recent_weeks <= 8 or self.skip_recent_weeks >= self.momentum_weeks:
            raise ValueError('skip_recent_weeks invalid')
        if not 1 <= self.top_n <= 4 or not 0 <= self.red_core <= .3 or not 0 <= self.us_core <= 1:
            raise ValueError('caps/core invalid')
        if not .1 <= self.cn_fee_multiplier <= 10 or not .1 <= self.us_fee_multiplier <= 10:
            raise ValueError('fee factor invalid')
        if self.us_fill_mode not in ('next_week_open','next_week_close'):
            raise ValueError('execution mode invalid')


def _validate_frame(p: pd.DataFrame, columns):
    if not set(columns).issubset(p.columns):
        raise ValueError(f'missing prices: {set(columns) - set(p.columns)}')
    if not p.index.is_monotonic_increasing or p.index.has_duplicates:
        raise ValueError('invalid chronology')
    x=p[list(columns)]
    if not np.isfinite(x.to_numpy(dtype=float)).all() or (x<=0).any().any():
        raise ValueError('invalid prices')


def candidate_cn(prices: pd.DataFrame, c: V20Config=V20Config()) -> pd.DataFrame:
    """A capped, no-leverage, top-2 public-rule dual momentum TAA benchmark.

    Not ex-post tuned. Qualification: close > 40w MA and skip-4 momentum above
    money. Only two independent positive-momentum assets may be held at once.
    Start with a 20% red long-term sleeve; remaining capital competes, and all
    unconstrained capital goes to local money ETF. No borrowing or shorting.
    """
    c.validate(); _validate_frame(prices,PRICES)
    x=pd.DataFrame({a:prices[p] for a,p in zip(('tech','red','bond','gold','money'),PRICES)},index=prices.index)
    ma=x.rolling(c.trend_weeks,min_periods=c.trend_weeks).mean()
    mom=x.shift(c.skip_recent_weeks)/x.shift(c.momentum_weeks)-1
    cash=x.money.shift(c.skip_recent_weeks)/x.money.shift(c.momentum_weeks)-1
    # Risk limits were specified before comparison; not optimum-fitted to 2026.
    caps={'tech':.45,'red':.55,'bond':.35,'gold':.20}
    rows=[]
    for i,date in enumerate(x.index):
        rank=[]
        for k in caps:
            eligible=(np.isfinite(ma.at[date,k]) and np.isfinite(mom.at[date,k])
                    and np.isfinite(cash.at[date]) and x.at[date,k] > ma.at[date,k]
                    and mom.at[date,k] > cash.at[date])
            if eligible:rank.append((float(mom.at[date,k]-cash.at[date]),k))
        rank.sort(key=lambda kv: (-kv[0],kv[1]))
        selected=[k for _,k in rank[:c.top_n]]
        w={k:0. for k in ('tech','red','bond','gold','money')}
        w['red']=c.red_core
        budget=1.-c.red_core
        # Fixed-sleeve allocation up to asset cap, in descending momentum order.
        for k in selected:
            increment=min(budget,caps[k]-w[k])
            w[k]+=increment;budget-=increment
        w['money']=1-sum(w[k] for k in caps)
        rows.append({**{f'target_{k}':w[k] for k in w},'ranked_eligible':','.join(selected),
                     'selected_assets':len(selected)})
    out=pd.DataFrame(rows,index=x.index)
    _check_target(out,('target_tech','target_red','target_bond','target_gold','target_money'))
    return out


def candidate_us(prices: pd.DataFrame, c:V20Config=V20Config()) ->pd.DataFrame:
    """Faber-style trend + absolute momentum; keep optional QQQ long-term core."""
    c.validate();_validate_frame(prices,('QQQ','BIL'))
    q=prices.QQQ;b=prices.BIL
    ma=q.rolling(c.trend_weeks,min_periods=c.trend_weeks).mean()
    mom=q.shift(c.skip_recent_weeks)/q.shift(c.momentum_weeks)-1
    cash=b.shift(c.skip_recent_weeks)/b.shift(c.momentum_weeks)-1
    passed=q.gt(ma)&mom.gt(cash)&ma.notna()&mom.notna()&cash.notna()
    out=pd.DataFrame(index=prices.index)
    out['target_qqq']=np.where(passed,1.0,c.us_core)
    out['target_bil']=1-out.target_qqq
    out['faber_trend']=q.gt(ma).fillna(False)
    out['absolute_momentum']=mom.gt(cash).fillna(False)
    _check_target(out,('target_qqq','target_bil'))
    return out


def _check_target(signal:pd.DataFrame,columns):
    w=signal[list(columns)].to_numpy(float)
    if not np.isfinite(w).all() or (w< -1e-10).any() or not np.allclose(w.sum(axis=1),1.,atol=1e-10):
        raise AssertionError('portfolio weights must sum to one and be nonnegative')


def blend_signals(baseline:pd.DataFrame, benchmark:pd.DataFrame,
                  cols:tuple[str,...], fraction:float)->pd.DataFrame:
    if not 0<=fraction<=1:raise ValueError('invalid fraction')
    if not baseline.index.equals(benchmark.index):raise ValueError('signal dates mismatched')
    out=baseline.copy()
    out[list(cols)]=(1-fraction)*baseline[list(cols)].to_numpy(float)+fraction*benchmark[list(cols)].to_numpy(float)
    _check_target(out,cols)
    return out


def advisory_gate_cn(prices:pd.DataFrame,baseline:pd.DataFrame,c:V20Config=V20Config()):
    """Narrow *increase-only* overlay, not a trailing stop or new market timer.

    When an asset fails BOTH 40w trend AND 26w skip4 excess cash momentum,
    restrict an upward exposure step. Never force a sell on this overlay.
    Maintain the original animal-style probe up to 12% for growth.
    This is deliberately frozen prior to inspecting its backtest results.
    """
    c.validate();_validate_frame(prices,PRICES)
    if not prices.index.equals(baseline.index):raise ValueError('calendar mismatch')
    x=pd.DataFrame({a:prices[p] for a,p in zip(('tech','red','bond','gold','money'),PRICES)},index=prices.index)
    ma=x.rolling(c.trend_weeks,min_periods=c.trend_weeks).mean()
    mom=x.shift(c.skip_recent_weeks)/x.shift(c.momentum_weeks)-1
    cash=x.money.shift(c.skip_recent_weeks)/x.money.shift(c.momentum_weeks)-1
    out=baseline.copy()
    changes=[];labels=[]
    for i,date in enumerate(x.index):
        reasons=[]
        if i:
            for k in ('tech','red','gold'):
                col=f'target_{k}'
                proposed=float(out.at[date,col]);last=float(out.iloc[i-1][col])
                fail=(np.isfinite(ma.at[date,k]) and np.isfinite(mom.at[date,k]) and
                      np.isfinite(cash.at[date]) and x.at[date,k]<ma.at[date,k] and
                      mom.at[date,k]<cash.at[date])
                if fail and proposed>last+1e-9:
                    floor={'tech':.12,'red':c.red_core,'gold':0.}[k]
                    revised=min(proposed,max(floor,last))
                    reduction=proposed-revised
                    if reduction>1e-12:
                        out.at[date,col]=revised
                        out.at[date,'target_money']+=reduction
                        reasons.append(k)
        changes.append(bool(reasons));labels.append(','.join(reasons))
    out['public_warning_restricted']=changes
    out['public_warning_assets']=labels
    _check_target(out,tuple(TARGETS))
    return out


def advisory_gate_us(prices:pd.DataFrame,baseline:pd.DataFrame,c:V20Config=V20Config()):
    """US increase-only warning. Leaves 30% core and V18 exit discipline intact."""
    c.validate();_validate_frame(prices,('QQQ','BIL'))
    if not prices.index.equals(baseline.index):raise ValueError('calendar mismatch')
    m=prices.QQQ.rolling(c.trend_weeks,min_periods=c.trend_weeks).mean()
    qmom=prices.QQQ.shift(c.skip_recent_weeks)/prices.QQQ.shift(c.momentum_weeks)-1
    cmom=prices.BIL.shift(c.skip_recent_weeks)/prices.BIL.shift(c.momentum_weeks)-1
    out=baseline.copy();warn=[]
    for i,date in enumerate(prices.index):
        fail=(np.isfinite(m.iloc[i]) and np.isfinite(qmom.iloc[i]) and
              np.isfinite(cmom.iloc[i]) and prices.QQQ.iloc[i]<m.iloc[i] and qmom.iloc[i]<cmom.iloc[i])
        increase=i>0 and float(out.target_qqq.iloc[i])>float(out.target_qqq.iloc[i-1])+1e-9
        if fail and increase:
            prev=float(out.target_qqq.iloc[i-1]);new=max(c.us_core,prev)
            if new<float(out.target_qqq.iloc[i]):
                out.at[date,'target_qqq']=new;out.at[date,'target_bil']=1-new
                warn.append(True)
            else:warn.append(False)
        else:warn.append(False)
    out['public_warning_restricted']=warn
    _check_target(out,('target_qqq','target_bil'))
    return out
