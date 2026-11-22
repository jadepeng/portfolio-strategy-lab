"""Market-clock-safe intraday stops and staged reduction intents.

The key guarantee is chronology: a closed bar can only produce an order for a
LATER bar, never for the trigger bar itself. The price line uses completed
PRIOR sessions only, frozen for each current session. This engine creates
trade INTENTS, not live orders.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Literal
import math
import numpy as np
import pandas as pd

Market = Literal['CN','US']

@dataclass(frozen=True)
class StopConfig:
    market: Market = 'CN'
    confirm_bars: int = 2
    second_stage_bars: int = 4
    lookback_days: int = 20
    ma_days: int = 10
    vol_multiplier: float = 2.5
    ma_offset_vol: float = 1.0
    min_daily_sigma: float = .004
    gap_sigma: float = 1.5
    shock_pct: float = .03     # ex ante emergency line below last completed daily close
    first_stage_fraction: float = .50
    cooldown_sessions: int = 3
    side_fee: float = .0015
    slippage: float = .0010
    core_floor: float = 0.0
    intraday_minutes: int = 30
    max_order_age_minutes: int = 24*60*5

    def validate(self):
        if self.market not in ('CN','US') or not (1<=self.confirm_bars<self.second_stage_bars<=12):
            raise ValueError('invalid market or confirmation bars')
        if self.lookback_days < 12 or not 3<=self.ma_days<=self.lookback_days:
            raise ValueError('invalid moving average lookback')
        if not (0 < self.vol_multiplier<=8 and 0 <= self.ma_offset_vol<=5 and
                0 < self.min_daily_sigma<=.1 and .1<=self.gap_sigma<=10 and .01<=self.shock_pct<=.10):
            raise ValueError('invalid volatility rules')
        if not (0<self.first_stage_fraction<1 and 0<=self.core_floor<=1):
            raise ValueError('invalid fraction or floor')
        if not (0<=self.side_fee<=.05 and 0<=self.slippage<=.05):
            raise ValueError('invalid trading costs')
        if not (0<=self.cooldown_sessions<=30 and 1<=self.intraday_minutes<=60):
            raise ValueError('invalid cooldown or intraday resolution')
        return self


def stop_line(prior_daily_closes:pd.Series, cfg:StopConfig)->dict:
    """The *only* input is daily closes known before today's open."""
    cfg.validate()
    s=pd.Series(prior_daily_closes,dtype=float).dropna()
    if len(s)<cfg.lookback_days or not np.isfinite(s.to_numpy()).all() or (s<=0).any():
        raise ValueError('insufficient prior daily closes')
    s=s.iloc[-cfg.lookback_days:]
    logret=np.diff(np.log(s.to_numpy()))
    sigma=max(float(np.std(logret,ddof=1)),cfg.min_daily_sigma)
    prev=float(s.iloc[-1]);peak=float(s.max());ma=float(s.iloc[-cfg.ma_days:].mean())
    trail=peak-cfg.vol_multiplier*sigma*prev
    ma_stop=ma-cfg.ma_offset_vol*sigma*prev
    raw_trend=max(trail,ma_stop)
    shock=prev*(1-cfg.shock_pct)
    # Do not simulate a fresh sell using a stale trend stop which was already
    # breached before the replay started. The intraday shock line is new today.
    trend_prebroken=prev<=raw_trend
    chosen=shock if trend_prebroken else max(raw_trend,shock)
    return {'line':round(chosen,6),'raw_trend_line':round(raw_trend,6),
            'shock_line':round(shock,6),'trend_prebroken':bool(trend_prebroken),
            'prior_close':prev,
            'daily_sigma':sigma,'peak':peak,'ma':ma,
            'method':'max(active unbroken 20d trailing line, prior close*0.97 emergency line); stale breached trend line ignored'}


def _date_group(timestamp:pd.Timestamp, market:Market)->str:
    if timestamp.tzinfo is None:raise ValueError('timezone-aware timestamp required')
    tz='Asia/Shanghai' if market=='CN' else 'America/New_York'
    return timestamp.tz_convert(tz).strftime('%Y-%m-%d')


def _valid_regular_bar(timestamp:pd.Timestamp, market:Market)->bool:
    t=timestamp.tz_convert('Asia/Shanghai' if market=='CN' else 'America/New_York')
    hm=t.hour*60+t.minute
    if market=='US':return 9*60+30<=hm<=15*60+30 and (hm-(9*60+30))%30==0
    return hm in (570,600,630,660,780,810,840,870)


def normalize_bars(bars:pd.DataFrame,cfg:StopConfig)->pd.DataFrame:
    """Accept tz-aware 30-minute open-time index and numeric open/close.

    The source's 15:00 CN duplicate or US extended-hours bars are excluded;
    missing bars are NOT filled. Price path between bars is unknown.
    """
    cfg.validate()
    if not isinstance(bars.index,pd.DatetimeIndex) or bars.index.tz is None:
        raise ValueError('intraday timestamps must carry market timezone')
    if bars.index.has_duplicates or not bars.index.is_monotonic_increasing:
        raise ValueError('duplicate/out-of-order bars')
    if not {'open','close'}.issubset(bars.columns):raise ValueError('open/close required')
    if not np.isfinite(bars[['open','close']].to_numpy(float)).all() or (bars[['open','close']]<=0).any().any():
        raise ValueError('nonpositive, missing or nonfinite bar prices')
    if cfg.intraday_minutes==30:
        return bars.loc[[_valid_regular_bar(x,cfg.market) for x in bars.index]].copy()
    return bars.copy()


@dataclass
class StopState:
    """Per account + instrument state; must be persisted across market sessions."""
    active_line: float|None=None
    session: str|None=None
    below_count: int=0
    stage: int=0
    pending_stage: int=0
    pending_issued_at:pd.Timestamp|None=None
    pending_reason:str=''
    pending_line:float|None=None
    stop_session:str|None=None
    cooldown_until_session:int=-1
    gap_qualified:bool=False
    last_completed:pd.Timestamp|None=None
    previous_bar:pd.Timestamp|None=None


@dataclass(frozen=True)
class StopIntent:
    instrument:str
    market:Market
    signal_bar_end:str
    execute_at:str
    stage:int
    current_weight:float
    target_weight:float
    protected_floor:float
    line:float
    assumed_fill_price:float
    estimated_cost_pct:float
    reason:str


class IntradayStopEngine:
    """Research implementation: 2 closed 30m bars below prior-day line;
    fill next regular bar open (with slippage). Next stage after 4 closes.
    Open-gap override: first bar closes below line and open <= line-1.5*sigma,
    fill the next bar. Never sell retroactively at the pierced line price.
    """
    def __init__(self,symbol:str,cfg:StopConfig):
        self.symbol=symbol
        self.cfg=cfg.validate()
        self.s=StopState()

    def session_start(self,day:str,line:float,prev_close:float,sigma:float):
        if not np.isfinite([line,prev_close,sigma]).all() or min(line,prev_close,sigma)<=0:
            raise ValueError('invalid prior-day line')
        st=self.s
        if st.session!=day:
            st.session=day;st.below_count=0;st.gap_qualified=False;st.previous_bar=None
            st.active_line=max(float(line),st.active_line or float(line))
        return st.active_line

    def on_closed_bar(self,t:pd.Timestamp,op:float,close:float,*,prev_close:float,sigma:float,
                      known_at:pd.Timestamp|None=None):
        """Timestamp t labels bar start. Known-at time must be after bar end."""
        st=self.s
        if known_at is not None:
            if known_at.tzinfo is None or known_at<t+pd.Timedelta(minutes=self.cfg.intraday_minutes):
                raise ValueError('bar is not fully closed or observation time missing timezone')
        if st.session!=_date_group(t,self.cfg.market):raise ValueError('call session_start first')
        if st.last_completed is not None and t<=st.last_completed:raise ValueError('duplicate/stale bar')
        if min(op,close)<=0 or not np.isfinite([op,close]).all():raise ValueError('bad prices')
        line=st.active_line
        first=st.previous_bar is None
        if not first:
            minutes=(t-st.previous_bar).total_seconds()/60
            localprev=st.previous_bar.tz_convert('Asia/Shanghai' if self.cfg.market=='CN' else 'America/New_York')
            localnow=t.tz_convert('Asia/Shanghai' if self.cfg.market=='CN' else 'America/New_York')
            legal_cn_lunch=(self.cfg.market=='CN' and (localprev.hour,localprev.minute)==(11,0) and (localnow.hour,localnow.minute)==(13,0))
            if minutes>self.cfg.intraday_minutes*1.1 and not legal_cn_lunch:
                st.below_count=0
        if first and op <= line-self.cfg.gap_sigma*sigma*prev_close:
            st.gap_qualified=True
        if close<line:
            st.below_count+=1
        else:
            st.below_count=0
        proposed=0
        if st.stage<1 and st.pending_stage<1 and (
                st.below_count>=self.cfg.confirm_bars or
                (first and st.gap_qualified and close<line)):
            proposed=1
        elif st.stage==1 and st.pending_stage==0 and st.below_count>=self.cfg.second_stage_bars:
            proposed=2
        if proposed:
            st.pending_stage=proposed
            st.pending_line=line
            st.pending_issued_at=t+pd.Timedelta(minutes=self.cfg.intraday_minutes)
            st.pending_reason='跳空破位确认' if first and st.gap_qualified else f'连续{st.below_count}根盘中收盘确认破位'
        st.previous_bar=t;st.last_completed=t
        return {'session':st.session,'line':line,'observed_close':float(close),
                'below_bars':st.below_count,'pending':st.pending_stage,
                'reason':st.pending_reason if proposed else ''}

    def on_next_bar_open(self,t:pd.Timestamp,price:float,weight:float,*,session_seq:int,
                         sellable_weight:float|None=None)->StopIntent|None:
        """Called BEFORE processing this bar close. Order cannot fill on trigger bar."""
        st=self.s
        if st.pending_stage==0:return None
        if st.pending_issued_at is None or t<st.pending_issued_at:
            return None
        if (t-st.pending_issued_at)>pd.Timedelta(minutes=self.cfg.max_order_age_minutes):
            st.pending_stage=0;st.pending_issued_at=None;return None
        if not (np.isfinite(price) and price>0 and 0<=weight<=1):raise ValueError('invalid order inputs')
        floor=self.cfg.core_floor
        excess=max(0.,weight-floor)
        stage=st.pending_stage
        target=max(floor, weight-excess*(self.cfg.first_stage_fraction if stage==1 else 1.))
        issued_line=st.pending_line if st.pending_line is not None else st.active_line
        signal_bar_end=st.pending_issued_at  # actual close time of the bar that confirmed the breach
        cut=weight-target
        if sellable_weight is not None:
            if not np.isfinite(sellable_weight) or not 0<=sellable_weight<=weight+1e-6:
                raise ValueError('invalid broker sellable inventory')
            if sellable_weight+1e-8<cut:
                # Do not invent execution when CN inventory/T+1, a halt or
                # a trading-limit condition prevents the planned risk cut.
                # Pending intent stays until availability or expiry.
                return None
        st.pending_stage=0;st.pending_issued_at=None;st.pending_line=None;st.stage=max(st.stage,stage)
        st.cooldown_until_session=max(st.cooldown_until_session,session_seq+self.cfg.cooldown_sessions)
        if cut<1e-8:return None
        fee=(weight-target)*(self.cfg.side_fee+self.cfg.slippage)
        return StopIntent(self.symbol,self.cfg.market,
            signal_bar_end.isoformat() if signal_bar_end is not None else '',
            t.isoformat(),stage,float(weight),float(target),float(floor),
            float(issued_line),float(price*(1-self.cfg.slippage)),float(fee),st.pending_reason)

    def can_add_risk(self,session_seq:int)->bool:
        return session_seq>self.s.cooldown_until_session and self.s.pending_stage==0

    def reset_on_new_entry(self):
        """Only after an approved new WEEKLY trend entry with cooldown expired."""
        st=self.s
        st.active_line=None;st.stage=0;st.below_count=0;st.pending_stage=0
        st.pending_issued_at=None;st.pending_line=None;st.pending_reason='';st.gap_qualified=False


def make_daily_lines(bars:pd.DataFrame,cfg:StopConfig,
                     daily_context:pd.Series|None=None)->dict[str,dict]:
    """Daily lines available from the PREVIOUS day's last completed close.
    Market-local grouping, not UTC date. Uses only observed regular trading bars.
    """
    f=normalize_bars(bars,cfg)
    dates=pd.Series([_date_group(x,cfg.market) for x in f.index],index=f.index)
    close_by_day=f['close'].groupby(dates).last()
    context={}
    if daily_context is not None:
        if not isinstance(daily_context.index,pd.DatetimeIndex) or daily_context.index.has_duplicates:
            raise ValueError('daily context must use unique daily dates')
        for day,v in daily_context.items():
            if not np.isfinite(v) or v<=0:raise ValueError('invalid daily context')
            context[str(day.date())]=float(v)
    # Historical daily observations are never interpreted as today's current close.
    for day,v in close_by_day.items():
        context.setdefault(day,float(v))  # authoritative daily auction close takes precedence
    out={}
    for day in close_by_day.index:
        known=[(d,v) for d,v in context.items() if d<day]
        known.sort()
        if len(known)<cfg.lookback_days:continue
        prior=pd.Series([v for _,v in known[-cfg.lookback_days:]])
        out[day]=stop_line(prior,cfg)
    return out


def simulate_asset(bars:pd.DataFrame,symbol:str,cfg:StopConfig,*,start_weight:float=1.,
                   daily_context:pd.Series|None=None)->tuple[pd.DataFrame,pd.DataFrame]:
    """Single-instrument+cash research illustration; not the full V18 portfolio.

    Starts fully invested on the first day with sufficient 20-day history.
    A stop reduction remains in cash through the end of snapshot; does NOT
    forecast a buy-back. This prevents inflated 'avoid the crash' claims.
    """
    bar=normalize_bars(bars,cfg)
    lines=make_daily_lines(bar,cfg,daily_context)
    eng=IntradayStopEngine(symbol,cfg)
    true_daily={str(d.date()):float(p) for d,p in daily_context.items()} if daily_context is not None else {}
    rows=[];deals=[];current_day='';dseq=-1
    start_price=None
    units=None;cash=1-start_weight
    prev_close=None
    bar_dates=[_date_group(t,cfg.market) for t in bar.index]
    for bar_index,(t,x) in enumerate(bar.iterrows()):
        day=_date_group(t,cfg.market)
        if day not in lines:continue
        if day!=current_day:
            current_day=day;dseq+=1
            z=lines[day]
            eng.session_start(day,z['line'],z['prior_close'],z['daily_sigma'])
            # A stop decision from a previous closed bar may fill at this open.
        op=float(x['open']);cl=float(x['close'])
        if start_price is None:
            start_price=op;units=start_weight/start_price
        nav_before=units*op+cash
        if nav_before<=0:raise ValueError('bad NAV')
        w=units*op/nav_before
        intent=eng.on_next_bar_open(t,op,w,session_seq=dseq)
        if intent:
            to_sell=(intent.current_weight-intent.target_weight)*nav_before/op
            proceeds=to_sell*op*(1-cfg.slippage-cfg.side_fee)
            units-=to_sell;cash+=proceeds
            deals.append({**intent.__dict__,'day':day,'exposure_reduced_pp':100*(intent.current_weight-intent.target_weight)})
        eng.on_closed_bar(t,op,cl,prev_close=lines[day]['prior_close'],sigma=lines[day]['daily_sigma'])
        nav=units*cl+cash
        rows.append({'datetime':t,'day':day,'nav':nav,'base_nav':start_weight*cl/start_price+1-start_weight,
                     'held_weight':units*cl/nav,'stop_line':eng.s.active_line,'close':cl,
                     'below_bars':eng.s.below_count,'stage':eng.s.stage,'pending':eng.s.pending_stage,
                     'price_kind':'intraday_30m_close'})
        if cfg.market=='CN' and (bar_index==len(bar)-1 or bar_dates[bar_index+1]!=day):
            # The China 15:00 auction-price print is NOT a new executable
            # 30-minute bar; it only revalues shares at official daily close.
            official=true_daily.get(day)
            if official is not None:
                final_value=units*official+cash
                settlement=pd.Timestamp(day+' 15:00',tz='Asia/Shanghai')
                rows.append({'datetime':settlement,'day':day,'nav':final_value,
                             'base_nav':start_weight*official/start_price+1-start_weight,
                             'held_weight':units*official/final_value,
                             'stop_line':eng.s.active_line,'close':official,
                             'below_bars':eng.s.below_count,'stage':eng.s.stage,
                             'pending':eng.s.pending_stage,'price_kind':'settlement_only_no_fill'})
    return pd.DataFrame(rows).set_index('datetime'),pd.DataFrame(deals)
