"""V22 intraday risk tests, including chronology and gap counterexamples."""
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
from datetime import timedelta
import numpy as np
import pandas as pd
import pytest
from history.v22.intraday import (StopConfig,IntradayStopEngine,stop_line,normalize_bars,
                          make_daily_lines,simulate_asset)


def t(day='2026-11-18',hh='09:30',tz='Asia/Shanghai'):
    return pd.Timestamp(f'{day} {hh}',tz=tz)


def engine(**kwargs):
    return IntradayStopEngine('515180.SH',StopConfig(**kwargs))


def test_line_excludes_current_session_and_uses_known_history_only():
    x=pd.Series(np.linspace(100,120,23))
    base=stop_line(x[:-1],StopConfig())
    revised=stop_line(x[:-1],StopConfig())
    assert base==revised
    assert stop_line(x,StopConfig())['line']!=base['line']


def test_intrabar_wick_does_not_trigger_without_closed_bar():
    e=engine();e.session_start('2026-11-18',100,105,.01)
    e.on_closed_bar(t(),105,102,prev_close=105,sigma=.01)
    assert e.s.pending_stage==0
    e.on_closed_bar(t(hh='10:00'),102,101,prev_close=105,sigma=.01)
    assert e.s.pending_stage==0


def test_two_closes_below_triggers_only_next_bar_open():
    e=engine();e.session_start('2026-11-18',100,105,.01)
    e.on_closed_bar(t(),101,99,prev_close=105,sigma=.01)
    assert not e.on_next_bar_open(t(hh='10:00'),99,.5,session_seq=0)
    e.on_closed_bar(t(hh='10:00'),99,98,prev_close=105,sigma=.01)
    assert e.s.pending_stage==1
    intent=e.on_next_bar_open(t(hh='10:30'),97,.44,session_seq=0)
    assert intent is not None and intent.stage==1
    assert intent.current_weight==.44 and intent.target_weight==pytest.approx(.22)
    assert intent.assumed_fill_price<97
    assert intent.execute_at.endswith('10:30:00+08:00')
    assert intent.signal_bar_end.endswith('10:30:00+08:00') # signal is only known once 10:00-10:30 bar finishes


def test_core_floor_and_stage_two_reduce_remaining_tactical_only():
    e=engine(core_floor=.20,first_stage_fraction=.5)
    e.session_start('2026-11-18',100,105,.01)
    for hh,c in [('09:30',99),('10:00',98)]:
        e.on_closed_bar(t(hh=hh),100,c,prev_close=105,sigma=.01)
    first=e.on_next_bar_open(t(hh='10:30'),98,.44,session_seq=0)
    assert first.target_weight==pytest.approx(.32)
    e.on_closed_bar(t(hh='10:30'),98,96,prev_close=105,sigma=.01)
    e.on_closed_bar(t(hh='11:00'),96,95,prev_close=105,sigma=.01)
    second=e.on_next_bar_open(t(hh='13:00'),95,.32,session_seq=0)
    assert second.stage==2 and second.target_weight==pytest.approx(.20)
    assert not e.can_add_risk(2)
    assert e.can_add_risk(4)


def test_gap_open_requires_first_bar_confirmation_no_fill_at_open():
    e=engine();e.session_start('2026-11-18',100,105,.01)
    assert not e.on_next_bar_open(t(),95,.44,session_seq=0)
    e.on_closed_bar(t(),94,93,prev_close=105,sigma=.01)
    assert e.s.pending_stage==1
    assert e.on_next_bar_open(t(hh='10:00'),92,.44,session_seq=0)


def test_failed_confirmation_reset():
    e=engine();e.session_start('2026-11-18',100,105,.01)
    for hh,c in [('09:30',99),('10:00',101),('10:30',99)]:
        e.on_closed_bar(t(hh=hh),100,c,prev_close=105,sigma=.01)
    assert e.s.pending_stage==0


def test_no_cn_close_fake_bar_or_us_overnight_fills():
    idx=pd.DatetimeIndex([t(hh=h) for h in ('14:00','14:30','15:00')])
    x=pd.DataFrame({'open':[2,2,2],'close':[2,2,2]},index=idx)
    assert len(normalize_bars(x,StopConfig()))==2
    us_idx=pd.DatetimeIndex([t(hh=h,tz='America/New_York') for h in ('08:30','09:30','15:30','16:00')])
    us=pd.DataFrame({'open':[100]*4,'close':[100]*4},index=us_idx)
    assert len(normalize_bars(us,StopConfig(market='US')))==2


def test_unknown_timezone_rejected():
    a=pd.DataFrame({'open':[1],'close':[1]},index=[pd.Timestamp('2026-11-18')])
    with pytest.raises(ValueError,match='timezone'):
        normalize_bars(a,StopConfig())


def test_out_of_order_bars_rejected():
    a=pd.DataFrame({'open':[1,1],'close':[1,1]},index=[t(hh='10:00'),t()])
    with pytest.raises(ValueError,match='order'):
        normalize_bars(a,StopConfig())


def test_monotonic_line_during_open_position():
    e=engine();assert e.session_start('2026-11-18',100,105,.01)==100
    assert e.session_start('2026-11-19',98,102,.01)==100
    assert e.session_start('2026-11-20',103,106,.01)==103


def test_missing_intraday_bar_does_not_fake_consecutive_confirmation():
    e=engine();e.session_start('2026-11-18',100,105,.01)
    e.on_closed_bar(t(),101,99,prev_close=105,sigma=.01)
    e.on_closed_bar(t(hh='11:00'),98,97,prev_close=105,sigma=.01)
    assert e.s.pending_stage==0


def test_previous_session_event_fills_next_trading_bar_not_earlier():
    e=engine();e.session_start('2026-11-18',100,105,.01)
    e.on_closed_bar(t(hh='14:00'),101,99,prev_close=105,sigma=.01)
    e.on_closed_bar(t(hh='14:30'),99,98,prev_close=105,sigma=.01)
    assert e.s.pending_stage==1
    e.session_start('2026-11-19',99,102,.01)
    v=e.on_next_bar_open(t('2026-11-19'),96,.4,session_seq=1)
    assert v is not None and v.signal_bar_end.startswith('2026-11-18')


def test_replay_with_complete_bar_history_and_accounting():
    cfg=StopConfig(core_floor=.20)
    days=pd.bdate_range('2026-09-01',periods=34)
    vals=[];ix=[]
    for i,d in enumerate(days):
        price=100+i*.4 if i<28 else 80-(i-28)
        for hh in ['09:30','10:00','10:30','11:00','13:00','13:30','14:00','14:30']:
            ix.append(pd.Timestamp(str(d.date())+' '+hh,tz='Asia/Shanghai'))
            vals.append([price,price-.05])
    bars=pd.DataFrame(vals,columns=['open','close'],index=pd.DatetimeIndex(ix))
    led,orders=simulate_asset(bars,'tech',cfg)
    assert len(led)>0 and (led.nav>0).all()
    assert len(orders)>0 and (orders.target_weight>=.2-1e-8).all()
    assert all(pd.Timestamp(x.execute_at)>=pd.Timestamp(x.signal_bar_end) for x in orders.itertuples())


def test_bad_config_or_missing_history_rejected():
    with pytest.raises(ValueError):StopConfig(confirm_bars=4,second_stage_bars=4).validate()
    with pytest.raises(ValueError):stop_line(pd.Series([1.,1.]),StopConfig())

from history.v22.allocation import allocate_stop_cuts,clip_weekly_risk_additions

def test_allocation_preserves_cash_and_other_assets():
    e=engine(core_floor=.2);e.session_start('2026-11-18',100,105,.01)
    for hh,c in [('09:30',99),('10:00',98)]:e.on_closed_bar(t(hh=hh),100,c,prev_close=105,sigma=.01)
    action=e.on_next_bar_open(t(hh='10:30'),97,.44,session_seq=0)
    x=allocate_stop_cuts({'tech':0,'red':.44,'bond':.35,'gold':0,'money':.21},[action],market='CN')
    assert x==pytest.approx({'tech':0,'red':.32,'bond':.35,'gold':0,'money':.33})
    y=clip_weekly_risk_additions({'tech':0,'red':.44,'bond':.35,'gold':0,'money':.21},x,{'red'},market='CN')
    assert y['red']==pytest.approx(.32) and y['money']==pytest.approx(.33)


def test_core_floor_preserved_in_us_and_currency_no_pooling():
    e=IntradayStopEngine('QQQ.US',StopConfig(market='US',core_floor=.30))
    e.session_start('2026-11-18',100,105,.01)
    ts=lambda hh:t(hh=hh,tz='America/New_York')
    for hh,c in [('09:30',99),('10:00',98)]:e.on_closed_bar(ts(hh),100,c,prev_close=105,sigma=.01)
    x=e.on_next_bar_open(ts('10:30'),97,.3,session_seq=1)
    assert x is None # 30% core protected; no tactical position exists
    with pytest.raises(ValueError):clip_weekly_risk_additions({'qqq':.3,'bil':.7},{'qqq':.3,'bil':.7},{'red'},market='US')

def test_incomplete_bar_cannot_trigger_even_if_close_is_below_stop():
    e=engine();e.session_start('2026-11-18',100,105,.01)
    with pytest.raises(ValueError,match='fully closed'):
        e.on_closed_bar(t(),101,95,prev_close=105,sigma=.01,
                        known_at=t()+pd.Timedelta(minutes=12))
    assert e.s.pending_stage==0
    e.on_closed_bar(t(),101,95,prev_close=105,sigma=.01,
                    known_at=t()+pd.Timedelta(minutes=30))


def test_unsettled_cn_equity_etf_inventory_cannot_be_sold_at_unsupported_fill():
    e=engine(core_floor=.2);e.session_start('2026-11-18',100,105,.01)
    for hh,close in [('09:30',99),('10:00',98)]:
        e.on_closed_bar(t(hh=hh),101,close,prev_close=105,sigma=.01)
    assert e.on_next_bar_open(t(hh='10:30'),97,.44,session_seq=0,sellable_weight=0) is None
    assert e.s.pending_stage==1
    execution=e.on_next_bar_open(t(hh='11:00'),96,.44,session_seq=0,sellable_weight=.30)
    assert execution and execution.target_weight==pytest.approx(.32)

def test_official_china_close_is_used_for_next_day_line_not_phantom_1500_execution():
    from history.v13.daily_ohlc_2026 import load
    from history.v22.intraday import make_daily_lines
    cn=load()
    x=pd.read_csv(ROOT/'market_data'/'v22_verified_30m_event_samples.csv')
    r=x[x.symbol=='515180.SH'].copy()
    r.index=pd.DatetimeIndex(pd.to_datetime(r.datetime_local).dt.tz_localize('Asia/Shanghai'))
    frame=r[['open','close']]
    z=make_daily_lines(frame,StopConfig(),cn.red_close)
    assert z['2026-11-20']['prior_close']==pytest.approx(cn.loc['2026-11-19','red_close'])
    assert z['2026-11-20']['prior_close']!=pytest.approx(float(frame.loc[frame.index.date==pd.Timestamp('2026-11-19').date()].close.iloc[-1]))


def test_v22_snapshot_uses_real_event_window_not_misleading_full_year():
    import json
    p=ROOT/'research_outputs'/'V22_intraday_research_snapshot.json'
    if not p.exists():pytest.skip('research not yet generated')
    d=json.loads(p.read_text(encoding='utf-8'))
    assert len(d['samples'])==3
    assert len(d['intents'])==6
    assert all(2<=s['observed_sessions']<=4 for s in d['samples'])
    assert all('not complete history' in s['source'] for s in d['samples'])
    assert d['samples'][1]['delta_pp'] < 0 or any(s['delta_pp']<0 for s in d['samples'])


def test_v22_web_is_offline_and_includes_7th_nav_and_risk_data():
    p=ROOT/'web'/'V22_盘中减仓线与双市场策略驾驶舱.html'
    if not p.exists():pytest.skip('web not yet generated')
    txt=p.read_text(encoding='utf-8')
    assert 'view-intraday' in txt and 'data-view="intraday"' in txt
    assert 'V22' in txt and 'next_session_candidates' in txt
    assert 'https://cdn.' not in txt
    assert len(txt)>150000
