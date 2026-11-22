"""V14: informative and opt-in research, not investment advice or actual orders."""
from __future__ import annotations
from dataclasses import replace
from pathlib import Path
import sys
import streamlit as st
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from fusion_v6 import v6_base,V6Config
from fusion_v7 import V7Config
from fusion_v10 import V10Config,simulate_v10,results,TARGETS
from fusion_v11 import V11Config,signals_v11
from v13.regime_v13 import RegimeConfig,signals_v13
from v14.market_snapshot import load_weekly
from v14.crowding import CrowdConfig,OverlayStateConfig,build_factors,classify,state_overlay,overlay_signals

st.set_page_config(page_title='V14 市场成交量与拥挤度趋势',layout='wide')
st.title('V14 · 市场量能 × 拥挤度 × 趋势纪律')
st.caption('仅为历史研究。成交额来自中证全指/创业板指/科创50/中证红利的指数成分股口径；不是逐只股票的真实成交集中度。周末收盘形成信号，下一周收盘模拟成交。')

@st.cache_data(show_spinner=False)
def load():
    p=pd.read_csv(ROOT/'market_data/verified_5asset_weekly_v13.csv',parse_dates=['week_start']).set_index('week_start')
    v=load_weekly()
    c=V10Config(portfolio_vol_brake=True)
    b=signals_v11(p,v6_base('week'),V6Config(),V7Config(red_core=.30,cash_mode='money_price',money_etf_side_cost=.0001),c,V11Config(risk_guard=False))
    return p,v,c,signals_v13(p,b,RegimeConfig(hold_weeks=8))

p,v,c,base=load()
st.sidebar.header('风险预警参数')
freeze=st.sidebar.slider('预警后暂缓新增科技仓（周）',min_value=1,max_value=5,value=3,step=1)
vol_factor=st.sidebar.slider('高量能阈值：4周/13周均成交额',1.00,1.20,1.05,.01)
rank=st.sidebar.slider('指数篮子拥挤历史分位数',.65,.95,.85,.05)
use_market=st.sidebar.checkbox('市场熊市结构限制新增科技仓',value=True)
mode=st.sidebar.radio('量价背离后的动作',('暂停新增（V14建议）','直接压低科技仓（反面实验）'),index=0)

cc=CrowdConfig(high_volume_factor=vol_factor,crowd_rank=rank)
f=classify(build_factors(p,v),cc)
if mode.startswith('暂停'):
    sig=state_overlay(p,base,f,OverlayStateConfig(freeze_weeks=freeze,require_market_confirmation=use_market))
else:
    sig=overlay_signals(p,base,f,CrowdConfig(high_volume_factor=vol_factor,crowd_rank=rank,use_market_bear=use_market))
base_run=simulate_v10(p,base,c)
run=simulate_v10(p,sig,c)
s0=results(base_run.nav);s1=results(run.nav)

def yr(x,year):
    t=x.loc[str(year)]
    prev=x.loc[x.index<t.index[0]].nav.iloc[-1]
    return 100*(t.nav.iloc[-1]/prev-1)

last=f.iloc[-1]
a,b,cx,dy=st.columns(4)
a.metric('市场状态（最近完整周）',str(last.market_regime))
b.metric('市场周均日成交额',f'{last.market_turn_100m/10000:.2f} 万亿元')
c.metric('创业板成分股成交占比',f'{last.tech_turn_share:.1%}')
dy.metric('市场4周/13周量能',f'{last.market_turn_ratio:.2f} 倍')

st.subheader('同口径历史对照')
cols=st.columns(4)
cols[0].metric('2026年收益',f'{yr(run,2026):+.2f}%',delta=f'{yr(run,2026)-yr(base_run,2026):+.2f}个百分点')
cols[1].metric('2024年收益',f'{yr(run,2024):+.2f}%')
cols[2].metric('全样本累计',f'{s1["total"]:+.2f}%',delta=f'{s1["total"]-s0["total"]:+.2f}个百分点')
cols[3].metric('最大回撤',f'{s1["mdd"]:.2f}%',delta=f'{s1["mdd"]-s0["mdd"]:+.2f}个百分点',delta_color='normal')

st.subheader('当前五资产模型目标')
labels=['创业板ETF 159915','红利ETF 515180','10年国债ETF 511260','黄金ETF 518880','货币ETF 511880']
current=pd.DataFrame({'资产':labels,'目标权重':sig.loc[p.index[-1],list(TARGETS)].to_numpy(float)})
st.dataframe(current.assign(目标权重=current.目标权重.map(lambda x:f'{x:.1%}')),hide_index=True,use_container_width=True)

st.subheader('市场成交额及科技占比')
window=f.loc['2026':].copy()
window['市场成交额（万亿元）']=window.market_turn_100m/10000
window['创业板占比（%）']=window.tech_turn_share*100
window['科创50+创业板占比（%）']=window.double_creation_share*100
st.line_chart(window[['市场成交额（万亿元）']],height=220)
st.line_chart(window[['创业板占比（%）','科创50+创业板占比（%）']],height=220)
st.caption('科创50+创业板两指数篮子成交占比，并非全部科创板与创业板的真实成交额份额。指数篮子成分会变动。')

st.subheader('2026年预警与交易纪律')
warn=f.loc['2026'].loc[lambda x:x.top_warning]
st.dataframe(warn[['market_regime','market_turn_100m','tech_turn_share','market_turn_ratio','volume_exhaustion','crowd_rollover']],use_container_width=True)
changed=(sig[list(TARGETS)]-base[list(TARGETS)]).abs().sum(axis=1)>.001
st.dataframe(sig.loc[changed & (sig.index.year==2026),list(TARGETS)+(['v14_reason'] if 'v14_reason' in sig else ['v14_effect'])],use_container_width=True)

st.subheader('累计净值与对照')
eq=pd.DataFrame({'V13':base_run.nav/base_run.nav.iloc[0],'V14':run.nav/run.nav.iloc[0]})
st.line_chart(eq,height=280)
st.download_button('导出信号与模拟交易',data=run.to_csv(index_label='week_start').encode('utf-8-sig'),file_name='V14_weekly_backtest.csv',mime='text/csv')
st.info('注意：默认参数是在2026年已知行情下研究出来的；不能将2026年利润改善视为独立样本外的可重复盈利概率。加入两市总成交额、个股前5%成交集中度和真实市场广度后仍需要重新验证。')
