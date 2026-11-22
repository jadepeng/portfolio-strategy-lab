"""Crowding: point-in-time broad turnover and concentration proxies.

The top-5%-of-stocks concentration published by CICC cannot be reconstructed
from index aggregates. We use an explicitly named *index-basket turnover share*
with market-volume divergence, not pretend to have actual stock-level crowding.
Week labelled Monday opens is *not* signal availability. Only after the final
trading-session close is the weekly signal observable.
"""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import pandas as pd
from strategy_core.baselines.multi_asset import TARGETS

@dataclass(frozen=True)
class CrowdConfig:
    crowd_rank:float=.85
    falling_tech_share_pp:float=.015
    high_volume_factor:float=1.05
    low_volume_factor:float=.90
    tech_cap_warning:float=.28
    tech_cap_bear:float=.10
    min_red_core:float=.20
    use_top_warning:bool=True
    use_market_bear:bool=True
    use_red_transition:bool=False
    red_shift_fraction:float=.50
    prior_crowd_window:int=6


def trailing_rank(s:pd.Series,window:int=52,min_periods:int=26) ->pd.Series:
    """Each reading is evaluated against only its trailing known observations."""
    return s.rolling(window,min_periods=min_periods).apply(lambda x: float((x<=x[-1]).mean()),raw=True)


def build_factors(p:pd.DataFrame,vol:pd.DataFrame)->pd.DataFrame:
    required={'market_close','market_turn_100m','tech_turn_100m','star_turn_100m','red_turn_100m'}
    if not required.issubset(vol.columns):raise ValueError(f'no market data: {required-set(vol.columns)}')
    if not vol.index.is_monotonic_increasing or vol.index.has_duplicates or (vol[list(required)]<=0).any().any():raise ValueError('invalid market tape')
    v=vol.copy().astype(float)
    v['tech_turn_share']=v.tech_turn_100m/v.market_turn_100m
    v['double_creation_share']=(v.tech_turn_100m+v.star_turn_100m)/v.market_turn_100m
    v['red_turn_share']=v.red_turn_100m/v.market_turn_100m
    # Never label these as all A-share market turnover or a constituent-level crowding index.
    v['market_turn_ratio']=v.market_turn_100m.rolling(4,min_periods=4).mean()/v.market_turn_100m.rolling(13,min_periods=13).mean()
    v['market_ma13']=v.market_close.rolling(13,min_periods=13).mean()
    v['market_ma26']=v.market_close.rolling(26,min_periods=26).mean()
    v['market_1w_ret']=v.market_close.pct_change(1,fill_method=None)
    v['market_4w_ret']=v.market_close.pct_change(4,fill_method=None)
    v['tech_share_rank']=trailing_rank(v.tech_turn_share)
    v['double_creation_rank']=trailing_rank(v.double_creation_share)
    v['market_turn_rank']=trailing_rank(v.market_turn_100m)
    v['tech_share_high6']=v.tech_turn_share.rolling(6,min_periods=6).max()
    v['share_peak_rank6']=v.tech_share_rank.rolling(6,min_periods=6).max()
    # No index volume from the ETF itself; use 399006.SZ index-basket turnover.
    px=p[['tech','red']].reindex(v.index)
    if px.isna().any().any():raise ValueError('ETF data missing for market tape weeks')
    v['tech_week_ret']=px.tech.pct_change(fill_method=None)
    v['red_week_ret']=px.red.pct_change(fill_method=None)
    v['tech_2w_rel']=px.tech.pct_change(2,fill_method=None)-px.red.pct_change(2,fill_method=None)
    v['tech_ma4']=px.tech.rolling(4,min_periods=4).mean()
    v['red_ma4']=px.red.rolling(4,min_periods=4).mean()
    v['red_4w_ret']=px.red.pct_change(4,fill_method=None)
    # Optional fields require genuinely observed stock-level data. They are never
    # synthesized from 399006/000688 index turnover or ETF prices.
    for extra in ('top5_concentration_ratio','advancing_stock_share'):
        if extra in v:
            x=pd.to_numeric(v[extra],errors='coerce')
            if x.dropna().lt(0).any() or x.dropna().gt(1).any():raise ValueError(f'{extra} must be 0..1')
            v[extra]=x
        else:v[extra]=np.nan
    v['tech_high8']=px.tech.rolling(8,min_periods=8).max()
    v['tech_from_high8']=px.tech/v.tech_high8-1
    return v


def classify(f:pd.DataFrame,c:CrowdConfig=CrowdConfig())->pd.DataFrame:
    if not (0<=c.tech_cap_bear<=c.tech_cap_warning<=1 and 0<c.falling_tech_share_pp<.20 and 0<c.min_red_core<.7):raise ValueError('invalid caps')
    v=f.copy()
    v['bull_expansion']=(v.market_close>v.market_ma13)&(v.market_ma13>v.market_ma26)&(v.market_turn_ratio>=1)
    v['market_bear_structure']=(v.market_close<v.market_ma13)&(v.market_close<v.market_ma26)&(v.market_turn_ratio<1)&(v.market_4w_ret<0)
    v['bear_weak_volume']=v.market_bear_structure&(v.market_turn_ratio<c.low_volume_factor)
    v['panic_selloff']=(v.market_close<v.market_ma13)&(v.market_1w_ret<=-.045)&(v.market_turn_ratio>1)
    # Qualifying price weakness is essential. High volume by itself is often bullish.
    v['price_rollover']=(v.tech_week_ret<-.012)&(v.tech_ma4>0)&(v.tech_week_ret<v.red_week_ret)
    v['index_proxy_crowd_rollover']=(v.share_peak_rank6>=c.crowd_rank)&(v.tech_share_high6-v.tech_turn_share>=c.falling_tech_share_pp)&v.price_rollover
    # 46% comes from CICC's top 5% stock turnover concentration *definition*.
    # It does not apply to index-basket shares; user-supplied market data only.
    v['real_stock_congestion_warning']=(v.top5_concentration_ratio>=.46)&v.price_rollover
    v['crowd_rollover']=v.index_proxy_crowd_rollover|v.real_stock_congestion_warning
    v['volume_exhaustion']=(v.market_turn_ratio>=c.high_volume_factor)&(v.market_turn_rank>=.72)&v.price_rollover&(v.tech_from_high8>=-.12)
    v['top_warning']=v.crowd_rollover|v.volume_exhaustion
    v['red_takeover']=(v.red_4w_ret>0)&(v.tech_2w_rel<-.025)&(v.red_ma4>0)&(v.red_week_ret>0)
    labels=np.select([v.panic_selloff.fillna(False),v.market_bear_structure.fillna(False),v.top_warning.fillna(False),v.bull_expansion.fillna(False)],['恐慌放量下跌','偏熊市（缩量破位）','顶部量价背离','放量牛市'],default='震荡或修复')
    v['market_regime']=labels
    return v


def overlay_signals(p:pd.DataFrame,base:pd.DataFrame,factors:pd.DataFrame,c:CrowdConfig=CrowdConfig())->pd.DataFrame:
    """Risk-only overlay on any EnsembleRisk/Regime strategy. Does not predict a literal top."""
    if not p.index.equals(base.index):raise ValueError('strategy input date mismatch')
    if not set(TARGETS).issubset(base):raise ValueError('missing target columns')
    f=base.copy()
    aligned=factors.reindex(p.index)
    for name in ('market_close','market_turn_ratio','tech_turn_share','double_creation_share','market_regime','top_warning','bear_weak_volume','red_takeover','crowd_rollover','volume_exhaustion','bull_expansion'):
        f[name]=aligned[name]
    origin=f[list(TARGETS)].to_numpy(dtype=float)
    out=origin.copy()
    effect=[]
    for i in range(len(out)):
        row=aligned.iloc[i]
        if pd.isna(row.market_close):effect.append('数据缺失：保留原策略，未假定市场情况');continue
        warning=bool(row.top_warning) if c.use_top_warning else False
        bear=bool(row.bear_weak_volume or row.panic_selloff) if c.use_market_bear else False
        if not (warning or bear):effect.append('无叠加交易，原策略决定');continue
        current=out[i].copy()
        cap=c.tech_cap_bear if bear else c.tech_cap_warning
        delta=max(0,current[0]-cap)
        current[0]-=delta
        # Only rotate to red when its own trend is positive, otherwise money.
        add_to_red=min(delta*c.red_shift_fraction,max(0,.80-current[1])) if c.use_red_transition and bool(row.red_takeover) else 0.
        current[1]+=add_to_red
        current[4]+=delta-add_to_red
        if abs(current.sum()-1)>1e-8 or min(current)<-1e-9:raise AssertionError('Crowding budget violation')
        out[i]=current
        effect.append(f'{"熊市/急跌" if bear else "顶部量价背离"};科技减少{delta:.1%}，红利增加{add_to_red:.1%}')
    f[list(TARGETS)]=out
    f['market_effect']=effect
    return f

@dataclass(frozen=True)
class OverlayStateConfig:
    """Crowding-B: fear/confirmation state machine, rather than hard crowd sell."""
    freeze_weeks:int=3
    frozen_new_entry_cap:float=.12
    require_market_confirmation:bool=True
    allow_red_takeover:bool=False
    red_shift_fraction:float=.50
    confirm_exit:bool=False
    confirm_exit_cap:float=.10
    min_red_core:float=.20


def state_overlay(p:pd.DataFrame,base:pd.DataFrame,factors:pd.DataFrame,c:OverlayStateConfig=OverlayStateConfig())->pd.DataFrame:
    if not p.index.equals(base.index):raise ValueError('unaligned')
    if not 0<=c.freeze_weeks<=12 or not 0<=c.frozen_new_entry_cap<=.5:raise ValueError('state invalid')
    f=base.copy();observed=factors.reindex(p.index)
    w=f[list(TARGETS)].to_numpy(float).copy()
    alert_until=-1
    consecutive_good=0
    reasons=[];warnings=[]
    for i in range(len(w)):
        row=observed.iloc[i]
        if pd.isna(row.market_close):reasons.append('market feed not started');warnings.append(False);continue
        warning=bool(row.top_warning)
        if warning:alert_until=max(alert_until,i+c.freeze_weeks)
        rising=bool(row.tech_week_ret>0 and p.tech.iloc[i]>row.tech_ma4 and row.market_close>row.market_ma13)
        consecutive_good=consecutive_good+1 if rising else 0
        if consecutive_good>=2:alert_until=-1
        # In a weak broad tape, volume itself is not a bearish proof.
        weak=bool(row.market_close<row.market_ma13 and row.market_close<row.market_ma26 and row.market_turn_ratio<1)
        freeze=bool(i<=alert_until or (c.require_market_confirmation and weak))
        b=w[i].copy();target=float(b[0]);prev=float(w[i-1,0]) if i>0 else 0.
        newcap=max(prev,c.frozen_new_entry_cap) if freeze else 1.
        b[0]=min(b[0],newcap)
        reason=[]
        if warning:reason.append('volume/price warning')
        if freeze and target>b[0]+1e-10:reason.append(f'freeze add {target:.1%}->{b[0]:.1%}')
        if c.confirm_exit and bool(row.price_rollover) and bool(row.tech_2w_rel<-.04) and bool(row.market_1w_ret<-.015):
            b[0]=min(b[0],c.confirm_exit_cap);reason.append('confirmed technology trend failure')
        diff=target-b[0]
        buyred=0.
        if c.allow_red_takeover and bool(row.red_takeover) and diff>0:
            buyred=min(diff*c.red_shift_fraction,max(0,.80-b[1]))
        b[1]+=buyred
        b[4]+=diff-buyred
        if np.any(b< -1e-8) or abs(b.sum()-1)>1e-8:raise AssertionError('risk budget')
        w[i]=b
        reasons.append('; '.join(reason) if reason else 'unmodified')
        warnings.append(warning)
    f[list(TARGETS)]=w
    f['market_warning']=warnings
    f['market_reason']=reasons
    return f
