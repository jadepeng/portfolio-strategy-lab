"""V21: provenance-first strategy audit, execution robustness and forward-signal ledger.

No newly optimized trading rule. Published V18 frozen baselines and V20 advisory
candidate are preserved. The promotion gate is deliberately conservative and
cannot be used to infer prospective profitability from tuning-exposed years.
"""
from __future__ import annotations
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any
import numpy as np
import pandas as pd
from history.v20.metrics import metrics

RESEARCH_ASOF = '2026-11-20'
MARKETS = ('CN', 'US')


def verify_market_history(prices: pd.DataFrame, *, label: str) -> dict:
    if not isinstance(prices.index, pd.DatetimeIndex) or prices.index.has_duplicates:
        raise ValueError(f'{label}: non-unique/non-datetime index')
    if not prices.index.is_monotonic_increasing or len(prices) < 52:
        raise ValueError(f'{label}: invalid order or insufficient observations')
    if prices.isna().any().any():
        raise ValueError(f'{label}: missing market values')
    a=prices.select_dtypes(include=[np.number])
    if len(a.columns) and not np.isfinite(a.to_numpy()).all():
        raise ValueError(f'{label}: non-finite market prices')
    if len(a.columns) and (a<=0).any().any():
        raise ValueError(f'{label}: nonpositive market prices')
    if not (prices.index.weekday==0).all():
        raise ValueError(f'{label}: weekly labels must be Monday week starts')
    return {'market':label,'rows':len(prices),'from':str(prices.index[0].date()),
            'last_week_start':str(prices.index[-1].date()),
            'duplicate_weeks':int(prices.index.duplicated().sum()),'missing_values':int(prices.isna().sum().sum())}


def describe(ledger:pd.DataFrame) -> dict:
    if 'nav' not in ledger:raise ValueError('missing NAV')
    if ledger.nav.isna().any() or (ledger.nav<=0).any():raise ValueError('invalid NAV')
    return metrics(ledger)


def equity_curve(ledger:pd.DataFrame, *, cols:tuple[str,...]) -> list[dict]:
    nav=ledger.nav.astype(float)
    peak=nav.cummax()
    weekly=nav.pct_change(fill_method=None)
    output=[]
    for i,(date,row) in enumerate(ledger.iterrows()):
        r={'d':str(date.date()),'nav':round(float(row['nav']),6),
           'dd':round(float(nav.iloc[i]/peak.iloc[i]-1)*100,3),
           'ret':round(float(weekly.iloc[i])*100,3) if i>0 else None,
           'turnover':round(float(row.get('turnover',0)),4),
           'fee':round(float(row.get('fee',row.get('cost',0))),6)}
        for c in cols:r[c]=round(float(row[c]),5)
        output.append(r)
    return output


def yearly(ledger:pd.DataFrame) -> list[dict]:
    n=ledger.nav.astype(float)
    out=[]
    for year,period in n.groupby(n.index.year):
        i=n.index.get_loc(period.index[0]);start=n.iloc[i-1] if i else n.iloc[0]
        out.append({'year':int(year), 'pct':round(100*(float(period.iloc[-1]/start)-1),3),
                    'partial':int(year) in (2020,2026)})
    return out


def latest_weights(signal:pd.DataFrame, keys:tuple[str,...]) -> dict[str,float]:
    w={s.replace('target_',''):round(float(signal.iloc[-1][s]),6) for s in keys}
    if min(w.values())<-1e-9 or abs(sum(w.values())-1)>1e-6:
        raise ValueError('target weights do not conserve capital')
    return w


def execution_gate(stress:pd.DataFrame, baseline:dict, candidate:dict, *, market:str,
                   bootstrap_interval:tuple[float,float]|None=None) -> dict:
    """Fixed governance: require positive CI bound *and* 2x fee/alternate fill robustness.

    These are research qualification rules only; even a pass does not establish
    independent out-of-sample validity. Never silently promote a strategy.
    """
    if market not in MARKETS:raise ValueError('invalid market')
    improvement=round(float(candidate['max_dd_pct']-baseline['max_dd_pct']),3)
    # A less-negative MDD yields positive improvement percentage points.
    positive=bootstrap_interval is not None and bootstrap_interval[0]>0
    if market=='US':
        part=stress[(stress.market=='US')&(stress.version=='guarded')&
                    (stress.trend_weeks==40)&(stress.momentum_weeks==26)]
        cost=part[(part.fill=='next_week_open')&(part.fee_multiple==2)]
        close=part[(part.fill=='next_week_close')&(part.fee_multiple==1)]
        has_stress=len(cost)==1 and len(close)==1
        checks={'drawdown_improved':improvement>=2,
                'bootstrap_excludes_zero':positive,
                'double_fee_positive_cumulative':bool(has_stress and cost.iloc[0].cumulative_pct>0),
                'alternative_fill_positive_cumulative':bool(has_stress and close.iloc[0].cumulative_pct>0)}
    else:
        checks={'drawdown_improved':improvement>=2,'bootstrap_excludes_zero':positive,
                'double_fee_positive_cumulative':False,'alternative_fill_positive_cumulative':False}
    return {'market':market,'promotion_ready':all(checks.values()),
            'checks':checks,'drawdown_improvement_pp':improvement,
            'status':'仅供前瞻跟踪（未通过晋级门槛）' if not all(checks.values()) else '历史检查通过；仍须前瞻验证'}


def frozen_signal_record(market: str, cutoff: str, source_week: str,
                         weights:dict[str,float], assumptions:dict[str,Any],
                         *, source_sha:str) -> dict:
    """Deterministic auditable signal record; no fabricated creation timestamp."""
    if market not in MARKETS:raise ValueError('invalid market')
    if not source_sha or len(source_sha)!=64:raise ValueError('SHA256 required')
    if not np.isclose(sum(weights.values()),1.,atol=1e-6):raise ValueError('weights not normalized')
    record={'version':'V21-RESEARCH-ONLY','market':market,'cutoff':cutoff,
            'source_week_start':source_week,'target_weights':weights,
            'assumptions':assumptions,'source_sha256':source_sha,
            'record_type':'research_snapshot_not_submitted_order'}
    material=json.dumps(record,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')
    record['record_sha256']=hashlib.sha256(material).hexdigest()
    return record


def sha256_file(path: str|Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
