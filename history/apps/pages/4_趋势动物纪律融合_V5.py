"""V5 research page: executable rules, daily stress check, weekly history, ablations."""
from dataclasses import replace
from pathlib import Path
import sys
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

ROOT=Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from history.strategies.fusion_v5 import (FusionConfig, weekly_config, create_signals, simulate,
                       metrics, compare, STAGES)
from history.strategies.trend_animal_v4 import TrendAnimalV4Config, build_temperature_signals

st.set_page_config(page_title='V5 趋势动物纪律融合',layout='wide',page_icon='🧭')
st.title('V5｜趋势温度 × 红利科技动量 × 日频风控')
st.caption('趋势动物公开理念的独立、透明代理模型，不是Nick私有算法或其实际交易。以风险控制为优先目标；不保证优于买入持有。')

with st.sidebar:
    st.header('研究参数')
    period=st.selectbox('历史数据粒度',['2026日线：1/8—11/20','2016—2026周线'],index=0)
    daily=period.startswith('2026')
    cost=st.slider('单边调仓成本 / bp',0,50,15,5)/10000
    buffer=st.slider('相对动量切换缓冲 / %',0.,15.,2.5 if daily else 6.,.5)/100
    trial=st.slider('首次试仓比例 / %',10,50,25,5)/100
    red_early=st.checkbox('实验：红利相对强度快速试仓',False,
         help='在红利长周期未转强时，仅凭短期相对强势试仓；当前样本未证实增加收益')
    vol=st.checkbox('启用波动率降仓',True)
    momentum=st.checkbox('启用红利科技相对动量',True)
    gradual=st.checkbox('盈利后分步加仓',True)
    shock=st.checkbox('急跌/移动止损',True)
    foreign=st.checkbox('科创50领先弱势代理',True,help='中国科创50，不是韩国KSP200，不是真正跨市场领先指标')
    crowd=st.checkbox('创业板成交量+乖离代理',True,help='并非科技板块成交额占比或实际资金拥挤度')
    upload=st.file_uploader('可选：上传你的价格CSV',type=['csv'],help='日线列 date,tech,red；周线列 week_start,tech,red，允许附加market、growth_leader_proxy和tech_volume_million')

if daily:
    config=FusionConfig()
    fpath=ROOT/'market_data'/'longbridge_daily_2026.csv'
    ix='date'
else:
    config=weekly_config()
    fpath=ROOT/'market_data'/'longbridge_2016_2026_tech_dividend_prices.csv'
    ix='week_start'
config=replace(config,cost_per_side=cost,relative_buffer=buffer,trial=trial,
    use_red_early=red_early,use_vol_control=vol,use_relative=momentum,
    use_gradual_entry=gradual,use_shock=shock,use_trailing=shock,
    use_leader_proxy=foreign,use_crowding_proxy=crowd)

try:
    prices=pd.read_csv(upload if upload else fpath,parse_dates=[ix]).set_index(ix).sort_index()
    if prices.index.has_duplicates:raise ValueError('价格表含重复日期')
    results=compare(prices,config)
    chosen=results['V5完整版']
    if not daily:
        # Re-run V4 under exactly the same next-bar-close execution convention.
        prior=build_temperature_signals(prices,TrendAnimalV4Config())
        results['V4同执行口径']=simulate(prices,prior,config)
    comparison = [(k,metrics(v)) for k,v in results.items()]
    last=chosen.iloc[-1]
    st.info('所有策略统一按 **t收盘形成信号 → t+1收盘成交** 计算；短时间急跌会产生执行延迟。模型目标仓位仅对应独立的战术资金池。')
    st.caption(f'数据最后标签：{chosen.index[-1].date()}｜{len(chosen)}条真实收盘价格｜红利指数不含分红再投；现金利率暂按0。'+('' if daily else ' 周线索引代表“该周起始日”，该行实际是该周最后一个交易日收盘价，不是周一已经可用的信号。'))
    m1,m2,m3,m4,m5=st.columns(5)
    m1.metric('科技温度',str(last.tech_temperature));m2.metric('红利温度',str(last.red_temperature))
    m3.metric('科技目标',f'{last.target_tech:.0%}');m4.metric('红利目标',f'{last.target_red:.0%}')
    m5.metric('现金目标',f'{last.target_cash:.0%}')
    st.caption('信号原因：'+str(last.reason)+'；当周日线已有涨跌已发生，不能把历史回测中的提前减仓效果移植到当前实际持仓。')

    tabs=st.tabs(['历史绩效','策略资金曲线','因子消融','关键节点与交易日志','当前仓位过渡'])
    with tabs[0]:
        if daily:
            options={'本轮：2026/5—11月':('2026-05-01','2026-11-20'),'7月科技退潮以来':('2026-06-30','2026-11-20'),'2026可用全区间':(None,None)}
        else:
            options={'2016—2026全样本':(None,None),'2016—2022历史检验':('2016-01-11','2022-12-31'),
                     '2023—2026回顾性分段':('2023-01-01','2026-11-20'),
                     '2018下跌':('2018-01-01','2018-12-31'),
                     '2020V形反转':('2020-02-01','2020-08-31'),
                     '2022熊市':('2022-01-01','2022-12-31'),
                     '2026本轮':('2026-09-28','2026-11-20')}
        selected=st.selectbox('观察区间',list(options),index=0)
        s,e=options[selected]
        table=[{'策略':name,**metrics(d,s,e)} for name,d in results.items() if metrics(d,s,e)]
        st.dataframe(pd.DataFrame(table).set_index('策略'),use_container_width=True)
        st.caption('最大回撤、收益等为价格指数模拟，不含分红、税、跟踪误差；结果并非独立样本外验证。')
    with tabs[1]:
        fig=go.Figure()
        for name in ['科技买入持有','红利买入持有','50/50四周再平衡','V5完整版']+(['V4同执行口径'] if not daily else []):
            df=results[name];fig.add_trace(go.Scatter(x=df.index,y=df.nav/df.nav.iloc[0],mode='lines',name=name))
        fig.update_layout(height=410,yaxis_title='净值（初始=1）',hovermode='x unified')
        st.plotly_chart(fig,use_container_width=True)
        fig2=go.Figure()
        for x,label in [('held_tech','科技'),('held_red','红利'),('held_cash','现金')]:
            fig2.add_trace(go.Scatter(x=chosen.index,y=chosen[x]*100,name=label,stackgroup='one'))
        fig2.update_layout(height=300,yaxis_title='真实已执行目标权重 %',yaxis_range=[0,100])
        st.plotly_chart(fig2,use_container_width=True)
    with tabs[2]:
        st.subheader('因子增量收益：逐项删除或切换')
        base=metrics(results['V5完整版']);rows=[]
        for name in ['去掉波动率控制','去掉相对动量','不分步入场','去掉急跌与移动止损',
                     '去掉领先代理','去掉拥挤代理','红利早试仓实验']:
            now=metrics(results[name]);rows.append({'对照版本':name,
                '累计收益差值（百分点，V5减对照）':round(base['累计收益%']-now['累计收益%'],2),
                '最大回撤改善（百分点）':round(base['最大回撤%']-now['最大回撤%'],2),
                '对照累计收益%':now['累计收益%'],'对照最大回撤%':now['最大回撤%']})
        st.dataframe(pd.DataFrame(rows).set_index('对照版本'),use_container_width=True)
        st.caption('差值为该历史样本的模型消融，不等于因子未来预测能力；“领先/拥挤”即使产生观察信号，也可能因为别的规则先降仓而不改变净值。')
    with tabs[3]:
        st.subheader('实际执行交易记录')
        log=chosen[chosen.turnover>.08].copy()
        cols=['tech','red','tech_temperature','red_temperature','leader','reason','held_tech','held_red','held_cash','turnover','fee']
        st.dataframe(log[cols].iloc[::-1].head(80),use_container_width=True)
        if daily:
            st.subheader('2026年关键日：科技退潮及红利转强')
            days=['2026-06-18','2026-06-25','2026-06-30','2026-07-01','2026-07-02','2026-07-03','2026-07-08','2026-08-03','2026-10-22','2026-11-20']
            st.dataframe(chosen.loc[chosen.index.astype(str).isin(days),
                ['tech','red','tech_temperature','red_temperature','target_tech','target_red','held_tech','held_red','reason']],use_container_width=True)
        st.download_button('导出V5全部价格、因子、交易与净值 CSV',chosen.to_csv(index_label=ix).encode('utf-8-sig'),
            file_name='fusion_v5_ledger.csv',mime='text/csv')
    with tabs[4]:
        st.subheader('实际仓位与模型目标：仅战术子账户')
        capital=st.number_input('战术资金规模（万元）',min_value=1.,value=100.,step=10.)
        tech_real=st.slider('当前科技战术仓（占战术资金）',0,100,65,5)
        red_real=st.slider('当前红利战术仓（占战术资金）',0,100,25,5)
        if tech_real+red_real>100:st.warning('当前科技仓+红利仓超过100%，请修正输入。')
        else:
            target_t=float(last.target_tech)*capital;target_r=float(last.target_red)*capital
            st.markdown(f'按模型计算的科技目标 **{target_t:.1f} 万元**、红利目标 **{target_r:.1f} 万元**。当前科技战术仓约 **{capital*tech_real/100:.1f} 万元**、红利战术仓约 **{capital*red_real/100:.1f} 万元**。')
            st.caption('这些是研究模型与实际仓位的缺口，不是立即市价清仓指令。历史上本应执行的卖出，今天不能不计交易价格补做；需结合长期底仓、持仓成本、流动性和税费分批处理。')
        st.caption('可设置总资产风险预算，核心长期仓位不得被战术信号自动卖出；本工具不执行交易。')
except Exception as exc:
    st.error(f'无法计算V5，请检查输入价格序列及参数：{exc}')
