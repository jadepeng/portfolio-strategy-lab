"""V10 Streamlit research page: 5-asset trend rotation + portfolio vol budget.

This app never places orders; all market data are static until user uploads a
new verified 5-asset weekly CSV, and it uses next-week-close fills only.
"""
from pathlib import Path
from dataclasses import replace
import sys
import numpy as np
import pandas as pd
import streamlit as st
import plotly.graph_objects as go

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from fusion_v6 import V6Config, v6_base
from fusion_v7 import V7Config, simulate_v7
from fusion_v8 import V8Config
from fusion_v9 import V9Config, signals_v9
from fusion_v10 import (
    V10Config,signals_v10,simulate_v10,baseline_5asset_signals,
    fixed_mix_signals,results,TARGETS,PRICES,
)

st.set_page_config(page_title='V10 多资产全天候风险预算',layout='wide')
st.title('V10 · 趋势动物 × 中金式轮动 × 全天候风险预算')
st.caption('五资产：创业板ETF 159915｜红利ETF 515180｜10年国债ETF 511260｜黄金ETF 518880｜银华日利 511880')
st.info('研究用途，不自动交易。周标签为周线开盘日期，信号在该周最后交易日收盘后才确定，并假设下一周收盘成交。内置数据截至2026-11-20，不提供实时行情。')

with st.sidebar:
    st.header('V10研究参数')
    red_core=st.slider('红利核心下限',.15,.45,.30,.05)
    bond_cap=st.slider('国债仓位上限',0.,.50,.30,.05)
    gold_cap=st.slider('黄金仓位上限',0.,.30,.15,.05)
    reserve=st.slider('货币ETF最低预留',0.,.4,.08,.02)
    cap_on=st.checkbox('启用组合预测波动率上限',True)
    cap_target=st.slider('组合年化波动率上限',.06,.16,.10,.01)
    allocation=st.selectbox('国债/黄金预算分配',['capped','inverse_vol'],format_func=lambda x:{'capped':'先满足国债上限，再配置黄金','inverse_vol':'国债/黄金逆波动率风险预算'}[x])
    ma=st.slider('国债/黄金趋势均线（周）',12,40,20,2)
    momo=st.slider('动量确认周期（周）',8,26,13,1)
    deadband=st.slider('最小双边调仓阈值',.00,.24,.12,.02)
    base_fee=st.slider('权益及黄金单侧成交摩擦 (bp)',5,40,15,1)
    bond_fee=st.slider('国债单侧成交摩擦 (bp)',1,15,3,1)
    money_fee=st.slider('货币ETF单侧成交摩擦 (bp)',0,10,1,1)
    custom=st.file_uploader('上传更新后的五资产周线CSV（可选）',type=['csv'])

@st.cache_data(show_spinner=False)
def read_default():
    f=ROOT/'market_data/longbridge_5asset_fwd_2020_2026_v10.csv'
    return pd.read_csv(f,parse_dates=['week_start']).set_index('week_start')

try:
    if custom is None:prices=read_default()
    else:
        prices=pd.read_csv(custom,parse_dates=['week_start']).set_index('week_start')
        st.warning('使用用户上传数据。请核对是否为前复权周线、五只ETF的同一交易周，且历史日期不包含未来价格。')
    prices=prices.sort_index()
    assert prices.index.is_unique
    assert set(PRICES).issubset(prices.columns)

    base=replace(v6_base('week'),cost_per_side=base_fee/10000.)
    model=V6Config()
    cash=V7Config(red_core=red_core,cash_mode='money_price',money_etf_side_cost=money_fee/10000.)
    cfg=V10Config(
        bond_cap=bond_cap,gold_cap=gold_cap,reserve_money=reserve,
        portfolio_vol_brake=cap_on,portfolio_annual_vol_cap=cap_target,
        allocation=allocation,trend_weeks=ma,momentum_weeks=momo,
        execution_deadband=deadband,min_red_core=red_core,
        tech_fee=base_fee/10000.,red_fee=base_fee/10000.,
        bond_fee=bond_fee/10000.,gold_fee=base_fee/10000.,money_fee=money_fee/10000.)
    v9signals=signals_v9(prices,base,model,cash,V9Config(),V8Config())
    v10signals=signals_v10(prices,base,model,cash,cfg)
    v9=simulate_v7(prices,base,model,cash,signals=v9signals)
    v10=simulate_v10(prices,v10signals,cfg)
    # fixed allocation has no conditional market timing; may not add to 1 if revised.
    fixed=simulate_v10(prices,fixed_mix_signals(prices),cfg)
except Exception as e:
    st.error('计算失败，请检查数据周期、缺失值和参数：'+str(e));st.stop()

latest=v10.iloc[-1]
labels={'tech':'创业板','red':'红利','bond':'十年国债','gold':'黄金','money':'货币ETF'}
st.subheader('最新已知收盘信号')
a,b,c,d,e=st.columns(5)
for col,name in zip([a,b,c,d,e],labels):
    col.metric(labels[name],f'{latest["target_"+name]:.0%}')
st.caption(f'该信号使用周标签 {v10.index[-1].date()} 对应的当周收盘价格；实际应在下周再安排交易。')
if float(latest.target_tech)==0:
    st.caption('创业板仍在候选池。若V8/V9低位试仓或趋势确认触发，模型将自动从其他可调配仓位恢复科技配置。')
st.write('**可执行判断：** '+str(latest.v10_reason))
col1,col2,col3=st.columns(3)
col1.metric('组合预测年化波动（当期权重）',f'{float(latest.estimated_portfolio_vol):.1%}' if pd.notna(latest.estimated_portfolio_vol) else '预热中')
col2.metric('国债趋势条件','通过' if latest.bond_eligible else '未通过')
col3.metric('黄金趋势条件','通过' if latest.gold_eligible else '未通过')

s=[('V9原版',v9),('五资产固定配置',fixed),('V10动态五资产',v10)]
comparison=pd.DataFrame([{'组合':name,'累计收益%':results(f.nav)['total'],'年化收益%':results(f.nav)['cagr'],
                          '最大回撤%':results(f.nav)['mdd'],'年化波动%':results(f.nav)['vol'],
                          '滚动52周正收益窗口%':results(f.nav)['positive_52w'],
                          '双边换手累计':f.turnover.sum()} for name,f in s]).set_index('组合')
st.subheader('与原V9基准的公平对比')
st.dataframe(comparison.style.format('{:.2f}'),use_container_width=True)
st.caption('滚动52周窗口高度重叠，其正收益占比不是未来胜率预测；ETF前复权行情尚未逐笔核查分红分配。')

l,r=st.columns(2)
with l:
    fig=go.Figure()
    for name,f in s:
        fig.add_trace(go.Scatter(x=f.index,y=f.nav/f.nav.iloc[0],name=name,mode='lines'))
    fig.update_layout(title='累计净值（初始=1）',height=400,yaxis_title='累计净值',hovermode='x unified')
    st.plotly_chart(fig,use_container_width=True)
with r:
    fig=go.Figure()
    for name,f in s:
        dd=f.nav/f.nav.cummax()-1
        fig.add_trace(go.Scatter(x=f.index,y=dd*100,name=name,mode='lines'))
    fig.update_layout(title='历史回撤',height=400,yaxis_title='回撤 %',hovermode='x unified')
    st.plotly_chart(fig,use_container_width=True)

st.subheader('动态仓位与年表')
fig=go.Figure()
for name in labels:
    fig.add_trace(go.Scatter(x=v10.index,y=v10['held_'+name]*100,stackgroup='one',name=labels[name],mode='lines'))
fig.update_layout(height=400,yaxis_title='实际模拟仓位%',hovermode='x unified')
st.plotly_chart(fig,use_container_width=True)
periods=[]
for year in sorted(set(v10.index.year)):
    dfv10=v10[v10.index.year==year];dfv9=v9[v9.index.year==year]
    if len(dfv10)<2:continue
    periods.append({'年份':year,'V9累计收益%':results(dfv9.nav)['total'],
                    'V10累计收益%':results(dfv10.nav)['total'],
                    'V10当年最大回撤%':results(dfv10.nav)['mdd']})
st.dataframe(pd.DataFrame(periods).set_index('年份').style.format('{:.2f}'),use_container_width=True)

st.subheader('当前实际仓位与目标差额')
st.caption('手工输入你在该策略子账户上的真实比例；不读取证券账户，不执行下单。')
cols=st.columns(5)
actual={}
for name,col in zip(labels,cols):
    actual[name]=col.number_input(labels[name]+'实际%',min_value=0.,max_value=100.,value=0.,step=5.)/100
if sum(actual.values())>1.00000001:
    st.warning('输入仓位相加超过100%，请核实。')
else:
    st.dataframe(pd.DataFrame([{'资产':labels[name],
        '模型目标%':100*float(latest['target_'+name]),
        '当前实际%':100*actual[name],
        '差额(百分点)':100*(float(latest['target_'+name])-actual[name])} for name in labels]).set_index('资产').style.format('{:.1f}'),use_container_width=True)

with st.expander('风险模型、数据质量与局限性',expanded=False):
    st.markdown('''
1. **不复制中金ARES/桥水全天候私有算法。** V10为公开方法启发的、无杠杆可解释代理模型；不是严格的各资产边际风险贡献相等的风险平价。
2. **周末已知信息产生信号，下一周周线收盘价模拟成交**；并非周初价格，更不是次日开盘真实成交。交易手续费和价差用单侧bp近似。
3. **5只ETF复权价格**取自数据商，已包含其公司行动调整口径，但尚未独立核对黄金/货币/国债的分红再投与指数全收益。
4. **控制预测波动率≠保证未来波动率或回撤**。模型对26周协方差矩阵使用50%向对角线收缩；极端情况下，30%红利底仓单独就可能超过风险上限。
5. **所有指标是2026年已知行情之后设计的**。不能把2023—2026分段当作真正盲测。市场上行、下行和股债同跌时都会存在损失。
6. 历史回测**并未模拟买卖盘口、交易所临停、实际申赎费用、折溢价突变以及税务影响**。货币ETF有低概率净值/流动性风险。
''')

with st.expander('导出可复查数据和交易日志',expanded=False):
    st.download_button('下载V10全部周度交易明细CSV',data=v10.to_csv(index_label='week_start').encode('utf-8-sig'),file_name='v10_weekly_trades.csv',mime='text/csv')
    st.download_button('下载五只ETF周度价格CSV',data=prices.to_csv(index_label='week_start').encode('utf-8-sig'),file_name='v10_market_prices.csv',mime='text/csv')
