"""V15 US and China separate strategies with a lagged U.S.-to-China risk gate."""
from __future__ import annotations
from pathlib import Path
from dataclasses import replace
import sys
import pandas as pd
import streamlit as st
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from history.strategies.fusion_v6 import v6_base,V6Config
from history.strategies.fusion_v7 import V7Config
from history.strategies.fusion_v10 import V10Config,simulate_v10,results,TARGETS
from history.strategies.fusion_v11 import V11Config,signals_v11
from history.v13.regime_v13 import RegimeConfig,signals_v13
from history.v14.market_snapshot import load_weekly
from history.v14.crowding import build_factors,classify,state_overlay,OverlayStateConfig
from history.v15.us_snapshot import load_us_prices,load_us_open
from history.v15.us_strategy import USConfig,us_signals,simulate_us
from history.v15.cross_market import CrossConfig,us_to_cn_overlay

st.set_page_config(page_title='V15 | 中美双策略与跨市场风险',layout='wide')
st.title('V15 · 中美两套独立策略 + 美股影响A股风险闸门')
st.caption('人民币A股策略与美元美股策略独立计价、独立回测；不把两种货币收益直接相加。两市周线标签是周一，但信号只在各自周五收盘后才可知。')

@st.cache_data(show_spinner=False)
def data():
    p=pd.read_csv(ROOT/'market_data/verified_5asset_weekly_v13.csv',parse_dates=['week_start']).set_index('week_start')
    cnconfig=V10Config(portfolio_vol_brake=True)
    base=signals_v11(p,v6_base('week'),V6Config(),V7Config(red_core=.30,cash_mode='money_price',money_etf_side_cost=.0001),cnconfig,V11Config(risk_guard=False))
    regime=signals_v13(p,base,RegimeConfig(hold_weeks=8))
    market=classify(build_factors(p,load_weekly()))
    v14=state_overlay(p,regime,market,OverlayStateConfig(freeze_weeks=3))
    return p,v14,market,cnconfig,load_us_prices(),load_us_open()

cn_prices,cn_v14,market,cncfg,us_prices,us_opens=data()

with st.sidebar:
    st.header('策略设置')
    core=st.slider('美股长期QQQ核心仓',0,100,30,5)/100
    us_fill=st.radio('美股成交假设',['下一周开盘（主口径）','下一周收盘（敏感性）'],index=0)
    gate=st.checkbox('启用美股对A股科技仓的风险闸门',value=True)
    local=st.checkbox('若A股明确放量牛市，允许不受美股拖累',value=False)
    st.caption('美股风险信息滞后一整个完整美股周：避免使用中国周五收盘时仍未发生的美股周五价格。')

ucc=USConfig(qqq_core=core,fill_mode='next_week_open' if us_fill.startswith('下一周开盘') else 'next_week_close')
us_s=us_signals(us_prices,ucc)
us_run=simulate_us(us_prices,us_s,ucc,us_opens)
cn_s=us_to_cn_overlay(cn_prices,cn_v14,us_prices,CrossConfig(enabled=gate,require_cn_local_weakness=local))
cn_base_run=simulate_v10(cn_prices,cn_v14,cncfg)
cn_run=simulate_v10(cn_prices,cn_s,cncfg)

def yearret(frame,year):
    z=frame.loc[str(year)]
    prev=frame.loc[frame.index<z.index[0]].nav
    return 100*(z.nav.iloc[-1]/prev.iloc[-1]-1)

tab_cn,tab_us,tab_cross=st.tabs(['🇨🇳 A股五资产','🇺🇸 美股纳指趋势','🔁 跨市场联动与核验'])
with tab_cn:
    st.subheader('A股 · V14五资产 + 可选美股风险闸门')
    st.caption('创业板159915、红利515180、国债511260、黄金518880、货币511880。周五收盘发信号，下一周收盘成交（原V14口径）。')
    a,b,c,d=st.columns(4)
    a.metric('2026年收益（含联动）',f'{yearret(cn_run,2026):+.2f}%',delta=f'{yearret(cn_run,2026)-yearret(cn_base_run,2026):+.2f}百分点')
    b.metric('历史累计',f'{results(cn_run.nav)["total"]:+.2f}%')
    c.metric('最大回撤',f'{results(cn_run.nav)["mdd"]:.2f}%')
    d.metric('最近A股量能结构',str(market.iloc[-1].market_regime))
    labels=['创业板159915','红利515180','10年国债511260','黄金518880','货币511880']
    st.dataframe(pd.DataFrame({'资产':labels,'目标比例':cn_s.iloc[-1][list(TARGETS)].values}).assign(目标比例=lambda x:x.目标比例.map(lambda z:f'{z:.1%}')),hide_index=True,use_container_width=True)
    nav=pd.DataFrame({'V14原版':cn_base_run.nav/cn_base_run.nav.iloc[0],'V15含美股闸门':cn_run.nav/cn_run.nav.iloc[0]})
    st.line_chart(nav,height=300)
    st.download_button('导出A股信号CSV',cn_s.to_csv(index_label='week_start').encode('utf-8-sig'),'V15_CN_signals.csv')
    st.download_button('导出A股回测CSV',cn_run.to_csv(index_label='week_start').encode('utf-8-sig'),'V15_CN_backtest.csv')

with tab_us:
    st.subheader('美股 · QQQ核心仓 + 30周趋势战术仓')
    st.caption('独立美元账户。QQQ跌破30周均线时战术仓退至BIL；长期核心仓由左侧调整。成交时点可用实际周开盘/收盘行情切换验证。')
    a,b,c,d=st.columns(4)
    a.metric('2026年收益',f'{yearret(us_run,2026):+.2f}%')
    b.metric('历史累计',f'{results(us_run.nav)["total"]:+.2f}%')
    c.metric('最大回撤',f'{results(us_run.nav)["mdd"]:.2f}%')
    d.metric('QQQ近周价格',f'${us_prices.QQQ.iloc[-1]:.2f}')
    st.dataframe(pd.DataFrame({'资产':['QQQ / QQQM 纳指核心与趋势','BIL 短期国债'],'目标比例':[us_s.target_qqq.iloc[-1],us_s.target_bil.iloc[-1]]}).assign(目标比例=lambda x:x.目标比例.map(lambda z:f'{z:.1%}')),hide_index=True,use_container_width=True)
    st.line_chart(pd.DataFrame({'美股动态策略':us_run.nav/us_run.nav.iloc[0],'QQQ价格指数（复权，未计费）':us_prices.QQQ/us_prices.QQQ.iloc[0]}),height=300)
    st.dataframe(us_s.tail(16)[['qqq_10w','qqq_30w','us_risk_state','target_qqq','target_bil']],use_container_width=True)
    st.download_button('导出美股回测CSV',us_run.to_csv(index_label='week_start').encode('utf-8-sig'),'V15_US_backtest.csv')

with tab_cross:
    st.subheader('跨市场风险如何进入A股决策')
    st.markdown('**QQQ/SPY中期下跌、QQQ急跌、半导体相对走弱** → 只暂停A股创业板新增战术仓；红利、国债、黄金不因美股信号被自动卖出。被限制的科技预算进入人民币货币ETF。')
    st.caption('先滞后美国完整周线一周，然后才用于中国当前周线。没有数据的早期窗口维持V14原信号，绝不虚构美股拥挤度或VIX。')
    last=cn_s.iloc[-1]
    a,b,c=st.columns(3)
    a.metric('A股本周使用的美国数据周',str(last.us_source_week.date()) if pd.notna(last.us_source_week) else '暂无')
    b.metric('滞后美股状态',str(last.us_risk_state_lagged))
    c.metric('本周被阻止新增创业板仓',f'{last.cross_market_tech_cut:.1%}')
    affected=cn_s.loc[cn_s.cross_market_tech_cut>1e-9,['us_source_week','us_risk_state_lagged','cross_market_tech_cut','target_tech','target_red','target_money','cross_market_reason']]
    st.dataframe(affected.tail(25),use_container_width=True)
    st.dataframe(pd.DataFrame({'对照':['V14 不考虑美股','V15 加入美股'],
         '2024年收益':[yearret(cn_base_run,2024),yearret(cn_run,2024)],
         '2025年收益':[yearret(cn_base_run,2025),yearret(cn_run,2025)],
         '2026年收益':[yearret(cn_base_run,2026),yearret(cn_run,2026)],
         '全样本累计':[results(cn_base_run.nav)['total'],results(cn_run.nav)['total']]}),hide_index=True,use_container_width=True)
    st.warning('A股与美股不能按两套净值简单加权形成一个真实人民币组合回测。后续如需总资产配置，必须加入USD/CNY逐日汇率、换汇成本、税务和两市交易日历。所有结果仍属于2026年已知行情后的研究性模拟。')
