"""V22: selected-event intraday guard visualization, not a live order screen."""
from __future__ import annotations
from pathlib import Path
import json
import streamlit as st
import pandas as pd

ROOT=Path(__file__).resolve().parents[3]
RESULTS=ROOT/'research_outputs'/'V22_intraday_research_snapshot.json'
st.set_page_config(page_title='V22盘中减仓线',layout='wide')
st.title('V22｜盘中有效跌破与分级减仓')
st.caption('离线研究快照截至2026年11月20日；真实执行需要独立的分钟行情、可卖库存、券商下单和状态持久化。')
if not RESULTS.exists():
    st.error('请先运行 `python scripts/reproduce_v22.py`，生成事件回测快照。')
    st.stop()
z=json.loads(RESULTS.read_text(encoding='utf-8'))
assets={x['symbol']:x for x in z['next_session_candidates']}
labels={'515180.SH':'红利ETF','159915.SZ':'创业板ETF','QQQ.US':'纳指100 QQQ'}
asset=st.selectbox('标的',list(labels),format_func=lambda x:f'{labels[x]} ({x})')
a=assets[asset]
cols=st.columns(4)
cols[0].metric('前一日收盘',f"{a['prior_close']:.3f}")
cols[1].metric('下一个交易日候选减仓线',f"{a['line']:.3f}")
cols[2].metric('候选线相对前收盘',f"{(a['line']/a['prior_close']-1)*100:+.2f}%")
cols[3].metric('模型目标仓位',f"{a['baseline_target_weight']*100:.1f}%")
st.info('减仓线在前一交易日收盘之后计算；两根30分钟完整收盘价确认破位，下一根可交易K线开盘成交。大幅跳空可缩短至一根确认。')
weight=st.slider('假设当前持仓（策略子账户）',0.,100.,float(a['baseline_target_weight']*100),1.)/100
floor=st.slider('战略核心仓保留下限（策略子账户）',0.,100.,float(a['core_floor']*100),1.)/100
floor=min(floor,weight)
first=weight-.5*max(0,weight-floor)
c1,c2,c3=st.columns(3)
c1.metric('当前假设仓位',f'{weight*100:.1f}%')
c2.metric('第一档减仓后',f'{first*100:.1f}%')
c3.metric('第二档减仓后',f'{floor*100:.1f}%')
st.caption('参数调整只用于算术情景，不重新计算历史事件收益；未发送任何订单。')
lines=pd.DataFrame(z['lines']);subset=lines[lines.symbol==asset].set_index('date')
trade_csv=ROOT/'research_outputs'/f"V22_{asset.replace('.', '_')}_event_ledger.csv"
if trade_csv.exists():
    bars=pd.read_csv(trade_csv,parse_dates=['datetime']).set_index('datetime')
    st.subheader('真实30分钟事件窗口')
    st.line_chart(bars[['close','stop_line']])
    st.dataframe(bars[['close','stop_line','held_weight','stage','price_kind']].tail(25),use_container_width=True)
st.subheader('模拟减仓意图')
orders=pd.DataFrame(z['intents'])
if len(orders):
    st.dataframe(orders[orders.instrument==asset],use_container_width=True)
st.subheader('不同标的的短窗口实验')
st.dataframe(pd.DataFrame(z['samples']),use_container_width=True)
st.warning('仅为选择性事件样本，不能外推出2026全年收益。红利窗口减仓效果为负；QQQ窗口采用100%初始QQQ、30%底仓，并非当前实际模型配置。')
with st.expander('策略实现与实盘风险'):
    st.markdown('''
    - T+1和可卖份额：国内权益ETF需要券商账户可卖数量确认，不允许假设当日买入即可当日卖出。
    - 下一根K线开盘成交是模型假设；价格跳空、跌停或流动性不足会造成更差的执行。
    - 中美分别使用Asia/Shanghai、America/New_York时区，仅在正规交易时段处理，不使用未收完的K线。
    - 冷却期内不得通过普通周频策略立刻买回；必须在后续趋势重新确认后允许入场。
    - 网页与当前代码不连接券商，不实时监控、不执行订单。
    ''')
