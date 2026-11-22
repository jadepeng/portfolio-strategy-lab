"""V16: US-market-close to China-next-open risk overlay.

The signal is UTC/ET time stamped; NO US event can affect a Chinese opening
before its New York 16:00 close. This is a *risk-only*, China-tech tactical
module. It never converts USD holdings to RMB or touches core red/bond/gold.

Historical 2026 US snapshot: only the observed **moderate/severe events** are
retained; non-event sessions are not recorded. No inference that a missing day
means an explicitly measured zero signal. On actual operations, feed the pure
`classify_us_daily` function a COMPLETE US daily close table and store its
close timestamps; avoid using the sparse event history for live decisions.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import time
from pathlib import Path
import pandas as pd
import numpy as np
from zoneinfo import ZoneInfo

_US=ZoneInfo('America/New_York')
_CN=ZoneInfo('Asia/Shanghai')

@dataclass(frozen=True)
class DailyGateConfig:
    mode: str='freeze'           # off / freeze / halve / full_exit
    moderate_freeze_days: int=3
    severe_freeze_days: int=5
    min_cut_pp: float=.015
    require_local_weakness:bool=False
    cn_lookback:int=5
    emergency_fee_slippage:float=.0005  # EXTRA cost per 1 RMB sold/bought, added to ETF fees

    def validate(self):
        if self.mode not in ('off','freeze','halve','full_exit'):raise ValueError('mode')
        if not 0<=self.moderate_freeze_days<=20 or not 0<=self.severe_freeze_days<=20:raise ValueError('cooldown')
        if not 3<=self.cn_lookback<=30:raise ValueError('CN lookback')
        if not 0<=self.min_cut_pp<=.25 or not 0<=self.emergency_fee_slippage<=.03:raise ValueError('risk threshold/cost')


def classify_us_daily(p:pd.DataFrame)->pd.DataFrame:
    """Return US daily risk events, using only the last two US closes at t.

    Must receive complete, sorted **US** sessions for QQQ/SPY/SMH/RSP/VIX;
    ETF prices consistent adjustment, VIX as index points. This logic is
    intentionally simple. No future US/A price is used.
    """
    need=['QQQ','SPY','SMH','RSP','VIX']
    if not set(need).issubset(p.columns):raise ValueError('missing market price')
    if p.index.has_duplicates or not p.index.is_monotonic_increasing or (p[need]<=0).any().any() or p[need].isna().any().any():raise ValueError('invalid US daily price')
    out=pd.DataFrame(index=p.index)
    for k in need[:-1]:out[k.lower()+'_return_pct']=p[k].pct_change(fill_method=None)*100
    out['vix_close']=p.VIX.astype(float)
    q=out.qqq_return_pct;sp=out.spy_return_pct;semi=out.smh_return_pct;v=out.vix_close
    severe=(q<=-2.5)|((semi<=-4.5)&(q<=-1))|((v>=30)&(q<=-1.5))|((sp<=-2.5)&(q<=-1.5))
    moderate=(q<=-1.8)|((semi<=-3.5)&(q<0))|((v>=27)&(q<0))
    out['level']=np.select([severe,moderate],['S','M'],default='N')
    return out


def close_available_cn(session:pd.Timestamp)->pd.Timestamp:
    """NY 16:00 observed in China; automatically handles NY DST."""
    d=pd.Timestamp(session).date()
    return pd.Timestamp.combine(d,time(16)).tz_localize(_US).tz_convert(_CN)


def china_open_at(day:pd.Timestamp)->pd.Timestamp:
    return pd.Timestamp.combine(pd.Timestamp(day).date(),time(9,30)).tz_localize(_CN)


def load_event_snapshot(path:Path|None=None)->pd.DataFrame:
    if path is None:path=Path(__file__).resolve().parents[2]/'market_data'/'us_daily_risk_events_2026_v16.csv'
    v=pd.read_csv(path,parse_dates=['us_session']).set_index('us_session')
    if len(v)<20 or not v.index.is_monotonic_increasing or v.index.has_duplicates:raise ValueError('event snapshot invalid')
    if not v.level.isin(['M','S']).all():raise ValueError('only actual warning events expected')
    v['available_at_cn']=[close_available_cn(d) for d in v.index]
    return v


def align_events_to_cn_open(cn_days:pd.DatetimeIndex,events:pd.DataFrame)->pd.DataFrame:
    """Latest observed US *event* only; never use a not-yet-closed session.

    No persistence here: whether a prior event is still in force is delegated
    to the daily risk state machine. Absent an event, no new event today.
    """
    if cn_days.has_duplicates or not cn_days.is_monotonic_increasing:raise ValueError('invalid Chinese daily calendar')
    if 'available_at_cn' not in events or events.index.has_duplicates:raise ValueError('invalid US events')
    ev=events.rename_axis('us_session').reset_index()
    # Map every US event to the FIRST Chinese session starting AFTER that close.
    opens=[china_open_at(x) for x in cn_days]
    slots=np.searchsorted(pd.DatetimeIndex(opens).asi8,pd.DatetimeIndex(ev.available_at_cn).asi8,side='right')
    mapped=[]
    for j,record in ev.iterrows():
        i=int(slots[j])
        if i<len(cn_days):mapped.append((cn_days[i],record['us_session'],record['level'],record['available_at_cn']))
        else:mapped.append((pd.NaT,record['us_session'],record['level'],record['available_at_cn']))
    ret=pd.DataFrame(mapped,columns=['cn_trade_day','us_session','level','available_at_cn'])
    ret=ret.dropna(subset=['cn_trade_day'])
    # Several US sessions may fall inside one Chinese holiday window.
    # Preserve the worst observed state, without losing its original US time.
    ret['rank']=ret.level.map({'M':1,'S':2})
    ret=ret.sort_values(['cn_trade_day','rank','available_at_cn']).groupby('cn_trade_day',sort=True).tail(1)
    return ret.drop(columns='rank').set_index('cn_trade_day').sort_index()
