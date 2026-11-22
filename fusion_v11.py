"""V11 research candidate: horizon ensemble and two-window risk guard.
Uses V10 signals and execution, no look-ahead. All risk targets are forecasts.
"""
from dataclasses import dataclass, replace
import numpy as np
import pandas as pd
from fusion_v10 import V10Config, signals_v10, simulate_v10, TARGETS, PRICES

@dataclass(frozen=True)
class V11Config:
    ensemble: bool = True
    risk_guard: bool = True
    risk_cap: float = .10
    risk_windows: tuple = (13, 52)
    covariance_shrink: float = .50
    # Same V10 default; urgent exits can be researched separately.
    priority_exit: bool = False


def _cov(x, shrink):
    s = np.cov(x, rowvar=False, ddof=1)*52
    return (1-shrink)*s+shrink*np.diag(np.diag(s))


def _vol(w,s):
    return float(np.sqrt(max(0,w@s@w)))


def risk_project(w, covariances, cap, core=.30):
    """Prefer preserving red core, but allow it to shrink if needed.
    Use convexity of volatility along each segment; cash proxy is not riskless.
    Returns the feasible target and whether the cash-only anchor itself fails.
    """
    w=np.asarray(w,float)
    money=np.array([0,0,0,0,1.])
    if not covariances:return w.copy(),False
    def worst(x):return max(_vol(x,s) for s in covariances)
    if worst(w)<=cap:return w.copy(),False
    anchor=np.array([0,min(core,w[1]),0,0,1-min(core,w[1])])
    if worst(anchor)>cap:
        if worst(money)>cap:return money,True
        lo,hi=0.,1.
        for _ in range(40):
            mid=(lo+hi)/2
            if worst(money+mid*(anchor-money))<=cap:lo=mid
            else:hi=mid
        return money+lo*(anchor-money),False
    lo,hi=0.,1.
    for _ in range(40):
        mid=(lo+hi)/2
        if worst(anchor+mid*(w-anchor))<=cap:lo=mid
        else:hi=mid
    return anchor+lo*(w-anchor),False


def signals_v11(prices, base, model, cash, v10=None, cfg=V11Config()):
    if v10 is None:v10=V10Config(portfolio_vol_brake=True)
    if not (.03<=cfg.risk_cap<=.30 and 0<=cfg.covariance_shrink<=1):raise ValueError('invalid V11 risk configuration')
    if not cfg.risk_windows or any(not isinstance(w,int) or w<8 for w in cfg.risk_windows):raise ValueError('invalid risk windows')
    # Ensemble combines target allocations instead of choosing the best retrospectively.
    horizons=[(12,8),(v10.trend_weeks,v10.momentum_weeks),(30,26)] if cfg.ensemble else [(v10.trend_weeks,v10.momentum_weeks)]
    frames=[signals_v10(prices,base,model,cash,replace(v10,trend_weeks=a,momentum_weeks=b)) for a,b in horizons]
    out=frames[len(frames)//2].copy()
    w=np.mean([f[list(TARGETS)].to_numpy(float) for f in frames],axis=0)
    out['bond_signal_agreement']=np.mean([f.bond_eligible.to_numpy(float) for f in frames],axis=0)
    out['gold_signal_agreement']=np.mean([f.gold_eligible.to_numpy(float) for f in frames],axis=0)
    out['pre_guard_red']=w[:,1]
    px=prices.loc[:,PRICES].pct_change(fill_method=None).to_numpy(float)
    post=[];pre=[];infeasible=[]
    for i in range(len(w)):
        covs=[_cov(px[i-n+1:i+1],cfg.covariance_shrink) for n in cfg.risk_windows if i>=n]
        pre.append(max((_vol(w[i],s) for s in covs),default=np.nan))
        fail=False
        if cfg.risk_guard:w[i],fail=risk_project(w[i],covs,cfg.risk_cap,core=v10.min_red_core)
        post.append(max((_vol(w[i],s) for s in covs),default=np.nan));infeasible.append(fail)
    out[list(TARGETS)]=w
    out['pre_guard_vol']=pre;out['worst_window_vol']=post;out['infeasible_cash_anchor']=infeasible
    out['core_override']=out.target_red<v10.min_red_core-1e-8
    return out


def simulate_v11(prices,signals,v10=None,cfg=V11Config()):
    if v10 is None:v10=V10Config(portfolio_vol_brake=True)
    return simulate_v10(prices,signals,replace(v10,priority_exit=cfg.priority_exit))
