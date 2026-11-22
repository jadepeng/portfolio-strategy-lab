"""Portfolio-side adapter: execute risk cuts into existing strategy cash bucket.

Accepted stop intents only LIMIT increases in risk until a later approved entry.
No buy/sell to a broker is performed here. Currency accounts remain separate.
"""
from __future__ import annotations
from typing import Mapping
import numpy as np
from history.v22.intraday import StopIntent

CN_MAP={'159915.SZ':'tech','515180.SH':'red','518880.SH':'gold'}
US_MAP={'QQQ.US':'qqq'}


def allocate_stop_cuts(current:Mapping[str,float], actions:list[StopIntent],
                       *, market:str)->dict[str,float]:
    cash='money' if market=='CN' else 'bil'
    required={'tech','red','bond','gold','money'} if market=='CN' else {'qqq','bil'}
    if not required.issubset(current):raise ValueError('missing account assets')
    if abs(sum(float(current[k]) for k in required)-1)>1e-6:raise ValueError('portfolio must sum to one')
    if min(float(current[k]) for k in required)<0:raise ValueError('negative holdings')
    out={k:float(current[k]) for k in required}
    for act in actions:
        if act.market!=market:raise ValueError('cross-currency allocation prohibited')
        key=(CN_MAP if market=='CN' else US_MAP).get(act.instrument)
        if key is None:raise ValueError('unsupported asset')
        want=min(out[key],act.target_weight)
        if want < -1e-9:raise ValueError('invalid requested reduction')
        out[cash]+=out[key]-want;out[key]=want
    if abs(sum(out.values())-1)>1e-7:raise AssertionError('budget not conserved')
    return out


def clip_weekly_risk_additions(target:Mapping[str,float],current:Mapping[str,float],
                               held_frozen_assets:set[str],*,market:str)->dict[str,float]:
    """While a stop cooldown is active, a WEEKLY signal cannot undo the cut."""
    cash='money' if market=='CN' else 'bil'
    out={k:float(v) for k,v in target.items()}
    if abs(sum(out.values())-1)>1e-6:raise ValueError('weekly target not normalized')
    for k in held_frozen_assets:
        if k==cash or k not in out or k not in current:raise ValueError('invalid frozen asset')
        amt=max(0.,out[k]-float(current[k]));out[k]-=amt;out[cash]+=amt
    if abs(sum(out.values())-1)>1e-7:raise AssertionError('capital not conserved')
    return out
