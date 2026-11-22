"""V16 | independent A+US strategies and causal US overnight risk to A-share next open."""
from pathlib import Path
import sys
import numpy as np
import pandas as pd
import streamlit as st
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from history.strategies.fusion_v6 import v6_base,V6Config
from history.strategies.fusion_v7 import V7Config
from history.strategies.fusion_v10 import V10Config,simulate_v10,PRICES,TARGETS,results
from history.strategies.fusion_v11 import V11Config,signals_v11
from v13.regime_v13 import RegimeConfig,signals_v13
from v13.daily_ohlc_2026 import load as load_cn_daily
from v14.market_snapshot import load_weekly
from v14.crowding import build_factors,classify,state_overlay,OverlayStateConfig
from v15.us_snapshot import load_us_prices,load_us_open
from v15.us_strategy import USConfig,us_signals,simulate_us
from v15.cross_market import us_to_cn_overlay,CrossConfig
from v16.overnight import DailyGateConfig,load_event_snapshot,align_events_to_cn_open
from v16.daily_replay import run_daily_replay,annual_stats

st.set_page_config(page_title='V16 | 美股隔夜风控+A股日频执行',layout='wide')
st.title('V16 · A股/美股双策略 + 美股隔夜风险日频联动')
st.caption('A股、美元美股分开计价。美国纽约16:00收盘后，最早下一个中国交易日09:30执行风控；原周频策略不改变。')

@st.cache_data(show_spinner=False)
def initial_data():
    p=pd.read_csv(ROOT/'market_data/verified_5asset_weekly_v13.csv',parse_dates=['week_start']).set_index('week_start')
    c=V10Config(portfolio_vol_brake=True)
    original=signals_v11(p,v6_base('week'),V6Config(),V7Config(red_core=.30,cash_mode='money_price',money_etf_side_cost=.0001),c,V11Config(risk_guard=False))
    regime=signals_v13(p,original,RegimeConfig(hold_weeks=8))
    cn=state_overlay(p,regime,classify(build_factors(p,load_weekly())),OverlayStateConfig(freeze_weeks=3))
    us=load_us_prices()
    cn_us=us_to_cn_overlay(p,cn,us,CrossConfig())
    return p,c,cn,cn_us,us,load_us_open(),load_cn_daily(),load_event_snapshot()

p,cncfg,cn_v14,cn_v15,us,us_open,daily,events=initial_data()
with st.sidebar:
    st.header('日频美股风控')
    cn_base=st.radio('A股周频基准',['V15：已有美股周频闸门','V14：纯A股五资产'],index=0)
    mode_label=st.selectbox('美股隔夜日频动作',['不干预（基准）','仅冻结新增科技仓','A股本地走弱才冻结新增仓（研究候选）','美股严重风险立即减半科技','中美同时走弱才减半科技','极端退出：卖出全部科技（实验）'],index=0)
    mode_table={
        '不干预（基准）':('off',False),
        '仅冻结新增科技仓':('freeze',False),
        'A股本地走弱才冻结新增仓（研究候选）':('freeze',True),
        '美股严重风险立即减半科技':('halve',False),
        '中美同时走弱才减半科技':('halve',True),
        '极端退出：卖出全部科技（实验）':('full_exit',False),
    }
    selection,local=mode_table[mode_label]
    moderate=st.slider('中等预警冻结交易日',1,10,3)
    severe=st.slider('严重预警冻结交易日',1,12,5)
    lookback=st.slider('A股短期均线确认天数',3,15,5)
    slip=st.slider('紧急交易额外单边摩擦成本（基点）',0,30,5)
    st.divider()
    st.header('美股独立策略')
    core=st.slider('美股QQQ长期核心比例',0,100,30,5)/100

weekly=cn_v15 if cn_base.startswith('V15') else cn_v14
full=simulate_v10(p,weekly,cncfg)
prev=full.loc[full.index<daily.index[0]].iloc[-1]
w0=prev[[f'held_{k}' for k in ('tech','red','bond','gold','money')]].to_numpy(float)
priorclose=p.loc[prev.name,list(PRICES)].to_numpy(float)
base_daily=run_daily_replay(daily,weekly,w0,DailyGateConfig(mode='off'),cncfg,events,priorclose)
daily_cfg=DailyGateConfig(mode=selection,require_local_weakness=local,cn_lookback=lookback,moderate_freeze_days=moderate,severe_freeze_days=severe,emergency_fee_slippage=slip/10000)
selected=run_daily_replay(daily,weekly,w0,daily_cfg,cncfg,events,priorclose)
stats0,stat1=annual_stats(base_daily.nav),annual_stats(selected.nav)
model=USConfig(qqq_core=core,fill_mode='next_week_open')
us_s=us_signals(us,model)
us_sim=simulate_us(us,us_s,model,us_open)

cn_tab,us_tab,events_tab=st.tabs(['🇨🇳 A股日频风控回测','🇺🇸 美股独立趋势策略','🌙 隔夜事件与时间核验'])
with cn_tab:
    a,b,c,d=st.columns(4)
    a.metric('2026 A股回测收益',f'{stat1["annual_pct"]:+.2f}%',delta=f'{stat1["annual_pct"]-stats0["annual_pct"]:+.2f}个百分点')
    b.metric('2026最大回撤',f'{stat1["mdd_pct"]:.2f}%')
    c.metric('紧急执行天数',int((selected.emergency_turnover>0).sum()))
    d.metric('美国事件被A股观察到',len(align_events_to_cn_open(daily.index,events)))
    st.line_chart(pd.DataFrame({'基准周频+日线':base_daily.nav,'当前日频风控':selected.nav},index=daily.index),height=320)
    st.caption('A股周频目标来自上一完整交易周，周频正常调仓在本周最后交易日收盘执行；美股急跌引发的额外减仓在下一A股开盘执行。两种模式使用完全相同的底层价格和周频目标。')
    b0,b1=st.columns(2)
    with b0:
        st.subheader('最新A股周线目标')
        st.dataframe(pd.DataFrame({'资产':['创业板159915','红利515180','国债511260','黄金518880','货币511880'],'目标':[float(weekly.iloc[-1][x]) for x in TARGETS]}).assign(目标=lambda x:x.目标.map(lambda z:f'{z:.1%}')),hide_index=True,use_container_width=True)
    with b1:
        st.subheader('2026日频对照')
        st.dataframe(pd.DataFrame([{'模式':'周频基准','收益%':stats0['annual_pct'],'最大回撤%':stats0['mdd_pct']},{'模式':mode_label,'收益%':stat1['annual_pct'],'最大回撤%':stat1['mdd_pct']}]).round(2),hide_index=True,use_container_width=True)
    changes=selected.loc[selected.reason!='不调仓',['us_source_session','us_event_level','local_cn_weakness','held_tech','held_red','held_money','emergency_turnover','weekly_turnover','reason']]
    st.subheader('决策日志（最后50条）')
    st.dataframe(changes.tail(50),use_container_width=True)
    st.download_button('下载V16逐日持仓与交易CSV',selected.to_csv(index_label='date').encode('utf-8-sig'),'V16_A_daily_ledger.csv')

with us_tab:
    st.subheader('独立的美股30周纳指趋势策略')
    a,b,c=st.columns(3)
    us_year=us_sim.loc['2026'];prior_us=us_sim.loc[us_sim.index<us_year.index[0]].iloc[-1]
    a.metric('2026模拟收益',f'{100*(us_year.nav.iloc[-1]/prior_us.nav-1):+.2f}%')
    b.metric('QQQ目标仓',f'{us_s.target_qqq.iloc[-1]:.0%}')
    c.metric('QQQ最近收盘价',f'${us.QQQ.iloc[-1]:.2f}')
    st.line_chart(pd.DataFrame({'美股策略净值':us_sim.nav/us_sim.nav.iloc[0],'QQQ买入持有复权价':us.QQQ/us.QQQ.iloc[0]}),height=300)
    st.caption('美股30周均线在美股周末收盘时形成信号，下一美股交易周开盘执行；QQQ与BIL美元账户独立，不与A股收益直接相加。')
    st.download_button('下载美股周频交易明细',us_sim.to_csv(index_label='week_start').encode('utf-8-sig'),'V16_US_weekly.csv')

with events_tab:
    st.subheader('纽约16:00 → 北京次日09:30')
    mapped=align_events_to_cn_open(daily.index,events)
    st.dataframe(mapped.tail(30),use_container_width=True)
    st.caption('已取得的2026年US快照只保存中等与严重预警事件，不包含其余每天的完整行情。实际监控必须使用全部美股日线收盘价重新分类，不得将缺失数据当作安全。')
    last_us=events.iloc[-1]
    st.info(f'美股最后严重风险事件：{events.index[-1].date()} 16:00美东，折合北京时间{last_us.available_at_cn}。它不可能影响2026-11-20当天A股开盘；只能在之后的A股交易时段被观察。')
    st.warning('2026年样本内发现的改进可能是事后拟合。直接按照美股跌幅半仓/清仓，在本样本中显著降低收益。不能依据一次回测就自动下单。')
    st.download_button('导出隔夜风险事件CSV',events.to_csv(index_label='us_session').encode('utf-8-sig'),'V16_US_daily_risk_events.csv')
