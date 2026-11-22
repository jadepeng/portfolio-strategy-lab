"""V18 | Gemini V17 audit, separate CN/US strategies and causal daily risk overlay."""
from __future__ import annotations
from dataclasses import replace
from pathlib import Path
import sys
import numpy as np
import pandas as pd
import streamlit as st
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from fusion_v10 import TARGETS,PRICES,results
from v14.market_snapshot import load_weekly
from v15.us_snapshot import load_us_prices,load_us_open
from v15.us_strategy import USConfig,us_signals,simulate_us
from v13.daily_ohlc_2026 import load as load_cn_daily
from v16.overnight import DailyGateConfig,load_event_snapshot
from v17.cn_v17 import CNV17Config,build_cn_v17_weekly,run_cn_daily_replay_v17
from v18.integrated import V18CNConfig,V18USConfig,build_cn_v18_weekly,build_us_v18

st.set_page_config(page_title='V18 中美双策略与Gemini融合审计',layout='wide')
st.title('V18｜Gemini融合、A股五资产与美股多周期趋势')
st.caption('历史行情快照截至2026-11-20。A股、美股互为独立账户；跨市场只对A股创业板新增仓进行约束。研究工具不提供自动下单。')
st.info('建议将V16原策略作为冻结基准：V18分散参数为研究候选；日频美股联动默认关闭。避免因为2026年回测表现而在实盘中频繁追改参数。')

@st.cache_data(show_spinner=False)
def load_sources():
    cn=pd.read_csv(ROOT/'market_data/verified_5asset_weekly_v13.csv',parse_dates=['week_start']).set_index('week_start')
    return cn,load_weekly(),load_us_prices(),load_us_open(),load_cn_daily(),load_event_snapshot()

cn,market,us,us_open,cn_day,events=load_sources()

with st.sidebar:
    st.header('配置实验（仅历史研究）')
    st.subheader('A股')
    bond=st.select_slider('国债最高仓位',options=[.30,.35,.40],value=.35,format_func=lambda v:f'{v:.0%}')
    gold=st.select_slider('黄金最高仓位',options=[.15,.20,.25],value=.20,format_func=lambda v:f'{v:.0%}')
    alloc=st.selectbox('防御资产分配',options=['inverse_vol','capped'],format_func=lambda s:'反波动率' if s=='inverse_vol' else '优先国债（上限配置）')
    st.subheader('美股')
    us_core=st.select_slider('QQQ长期底仓', options=[x/100 for x in range(0,71,10)], value=.30, format_func=lambda v:f'{v:.0%}')
    ma_selection=st.selectbox('QQQ趋势均线组',options=['10/20/30','13/26/39'],index=0)
    us_vol=st.select_slider('波动率风险上限',options=[x/100 for x in range(16,33,2)],value=.24,format_func=lambda v:f'{v:.0%}')
    st.subheader('A股日频联动')
    daily_mode=st.selectbox('美股隔夜风控',options=['off','freeze'],index=0,format_func=lambda s:'默认：关闭' if s=='off' else '实验：A股转弱后冻结新增仓')
    fill=st.selectbox('A股周频目标执行时间',options=['next_friday_close','next_monday_open'],index=0,format_func=lambda s:'下一周最后交易日收盘' if s=='next_friday_close' else '下一周首交易日开盘')

cfg=V18CNConfig(bond_cap=bond,gold_cap=gold,allocation=alloc)
cn_cfg,signals,cn_hist=build_cn_v18_weekly(cn,market,us,cfg)
us_cfg=V18USConfig(qqq_core=us_core,ma_windows=tuple(int(x) for x in ma_selection.split('/')),vol_cap=us_vol)
us_signals_new,us_hist=build_us_v18(us,us_cfg,us_open)

def perf_year(nav,year='2026'):
    yearrows=nav.loc[year]
    prev=nav.loc[nav.index<yearrows.index[0]].iloc[-1]
    return 100*(yearrows.iloc[-1]/prev-1)

cn_tab,us_tab,daily_tab,logs_tab=st.tabs(['A股五资产','美股QQQ/BIL','隔夜日频联动','数据与交易记录'])

with cn_tab:
    st.subheader('A股：V14趋势动物＋Gemini防御资产风险预算')
    a,b,c,d=st.columns(4)
    c_stats=results(cn_hist.nav)
    a.metric('本模型累计收益',f'{c_stats["total"]:+.2f}%')
    b.metric('历史最大回撤',f'{c_stats["mdd"]:.2f}%')
    c.metric('2026年模拟收益',f'{perf_year(cn_hist.nav):+.2f}%')
    d.metric('周频调仓次数',int((cn_hist.turnover>0).sum()))
    st.line_chart((cn_hist.nav/cn_hist.nav.iloc[0]).rename('A股策略净值'),height=300)
    table=pd.DataFrame({'资产':['创业板159915','红利515180','国债511260','黄金518880','货币511880'],'目标':[float(signals.iloc[-1][k]) for k in TARGETS]})
    table['目标']=table['目标'].map(lambda v:f'{v:.0%}')
    st.dataframe(table,hide_index=True,use_container_width=True)
    st.caption('国债和黄金不是静态全天候配比：只有各自相对货币ETF有趋势优势时才可配置。趋势动物模型在极端风险状态下可能将红利核心降至20%。')
    st.markdown('#### 固定历史对照（相同数据与费用）')
    st.dataframe(pd.read_csv(ROOT/'research_outputs/V18_CN_comparison.csv'),use_container_width=True,hide_index=True)

with us_tab:
    st.subheader('美股：多周期趋势迟滞＋13周波动率刹车')
    old=USConfig(qqq_core=us_core,fill_mode='next_week_open')
    base_us=simulate_us(us,us_signals(us,old),old,us_open)
    c0,c1,c2,c3=st.columns(4)
    c0.metric('QQQ目标权重',f'{us_signals_new.target_qqq.iloc[-1]:.0%}')
    c1.metric('2026年V18',f'{perf_year(us_hist.nav):+.2f}%')
    c2.metric('2026年30周基准',f'{perf_year(base_us.nav):+.2f}%')
    c3.metric('波动率缩放',f'{us_signals_new.us_v17_vol_mult.iloc[-1]:.2f}倍')
    st.line_chart(pd.DataFrame({'V18趋势集成':us_hist.nav/us_hist.nav.iloc[0], '30周基准':base_us.nav/base_us.nav.iloc[0]}),height=320)
    st.caption('美股信号使用本周收盘价生成，下周第一个交易日开盘执行；30%核心仓不受趋势择时完全清空。美股美元账户不与A股人民币账户直接合并回测。')
    st.markdown('#### 固定历史执行与风险对照')
    st.dataframe(pd.read_csv(ROOT/'research_outputs/V18_US_cost_execution_sensitivity.csv').query('core == @us_core') if us_core in (0.,.3) else pd.read_csv(ROOT/'research_outputs/V18_US_robustness.csv'),hide_index=True,use_container_width=True)

with daily_tab:
    st.subheader('2026年：美股隔夜预警 × A股价格本地确认')
    prev=cn_hist.loc[cn_hist.index<cn_day.index[0]].iloc[-1]
    pweight=prev[[f'held_{k}' for k in ('tech','red','bond','gold','money')]].to_numpy(float)
    previous_close=cn.loc[prev.name,list(PRICES)].to_numpy(float)
    dl=run_cn_daily_replay_v17(cn_day,signals,pweight,cn_cfg,events,previous_close,
        DailyGateConfig(mode=daily_mode,require_local_weakness=True),exec_timing=fill)
    pbase=run_cn_daily_replay_v17(cn_day,signals,pweight,cn_cfg,events,previous_close,
        DailyGateConfig(mode='off'),exec_timing=fill)
    a,b,c=st.columns(3)
    a.metric('联动模式收益',f'{(dl.nav.iloc[-1]-1)*100:+.2f}%')
    b.metric('相同成交口径基准',f'{(pbase.nav.iloc[-1]-1)*100:+.2f}%')
    c.metric('联动带来的差异',f'{(dl.nav.iloc[-1]-pbase.nav.iloc[-1])*100:+.2f}个百分点')
    st.line_chart(pd.DataFrame({'当前模式':dl.nav,'不联动基准':pbase.nav}),height=320)
    st.warning('周五收盘与周一开盘不是等价的成交假设。冻结模式在2026年的优势对执行时间敏感，因此默认不自动启用。')
    st.download_button('下载A股日频模拟CSV',dl.to_csv(index_label='date').encode('utf-8-sig'),'V18_A_daily_replay.csv')

with logs_tab:
    st.subheader('审计与重现实验')
    st.write('A股：周线313条；美股：周线313条；日频2026年有交易所实际日历。')
    st.dataframe(cn_hist.loc[cn_hist.turnover>0,['nav','turnover','fee','target_tech','target_red','target_bond','target_gold','target_money']].tail(40),use_container_width=True)
    st.download_button('下载A股周频完整账本',cn_hist.to_csv(index_label='week_start').encode('utf-8-sig'),'V18_CN_weekly.csv')
    st.download_button('下载美股周频完整账本',us_hist.to_csv(index_label='week_start').encode('utf-8-sig'),'V18_US_weekly.csv')
    for filename in ['V18_CN_robustness.csv','V18_US_robustness.csv','V18_CN_cost_stress.csv']:
        f=ROOT/'research_outputs'/filename
        if f.exists():
            st.download_button(f'下载 {filename}',f.read_bytes(),filename)
    st.caption('可用命令：python scripts/reproduce_v17.py；python scripts/research_v18.py；python scripts/reproduce_v18.py；python -m pytest -q。')
