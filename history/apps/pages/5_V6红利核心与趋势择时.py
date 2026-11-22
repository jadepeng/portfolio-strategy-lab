"""V6: separate structural dividend allocation from risk-managed tactical sleeve."""
from dataclasses import replace
from pathlib import Path
import sys
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import streamlit as st

ROOT=Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from history.strategies.fusion_v5 import FusionConfig, weekly_config, create_signals, simulate, metrics
from history.strategies.fusion_v6 import V6Config, v6_base, signal_v6, simulate_v6

st.set_page_config(page_title='V6 红利底仓与趋势风控',layout='wide',page_icon='🛡️')
st.title('V6｜红利底仓 × 趋势动物纪律 × 红利科技轮动')
st.caption('核心与战术拆分是本轮主要改善来源；非趋势动物 Pro 私有公式，亦非对未来收益的预测。')
choices={
 '2016—2026：创业板 / 中证红利价格指数':('market_data/longbridge_2016_2026_tech_dividend_prices.csv','week_start','week'),
 '2020—2026：159915 / 515180 前复权ETF':('market_data/longbridge_etf_fwd_2020_2026_v6.csv','week_start','week'),
 '2026：创业板 / 中证红利日线':('market_data/longbridge_daily_2026.csv','date','day')}
with st.sidebar:
 st.header('策略与数据')
 mode=st.selectbox('回测样本',list(choices))
 data_file,datefield,frequency=choices[mode]
 core=st.slider('战略红利常驻比例（策略子账户）',0,40,20,5)/100
 trial=st.slider('战术首次试仓比例（仅战术部分）',10,40,20,5)/100
 deadband=st.slider('换仓死区：最低双边权重变化',4,30,12,2)/100
 fee=st.slider('单边成交成本 / 基点',0,50,15,5)/10000
 st.divider()
 st.caption('下列新增因子是待验证的实验功能，默认关闭')
 do_rotate=st.checkbox('红利短期相对强势后提前试仓',False)
 do_smooth=st.checkbox('增加信号确认与仓位迟滞',False)
 do_vol=st.checkbox('科技异常波动附加降仓',False)
 do_speed=st.checkbox('科技快速回撤附加降仓',False)
 custom=st.file_uploader('上传价格数据（可选）',type=['csv'])

try:
    base=pd.read_csv(custom or ROOT/data_file,parse_dates=[datefield]).set_index(datefield).sort_index()
    c=v6_base(frequency)
    c=replace(c,trial=trial,min_rebalance=deadband,cost_per_side=fee)
    model=V6Config(red_core=core,enable_defensive_rotation=do_rotate,enable_hysteresis=do_smooth,
                   round_weight=.05 if do_smooth else 0,enable_vol_guard=do_vol,
                   enable_drawdown_speed=do_speed)
    latest=simulate_v6(base,c,model)
    original_c=weekly_config() if frequency=='week' else FusionConfig()
    original_c=replace(original_c,cost_per_side=fee)
    old=simulate(base,create_signals(base,original_c),original_c)
    no_core=simulate_v6(base,c,replace(model,red_core=0))
    price_signal=create_signals(base,c)
    tech_only=simulate(base,price_signal,c,'tech')
    red_only=simulate(base,price_signal,c,'red')
    half=simulate(base,price_signal,c,'half')
    runs={'V5（原版）':old,'V6（无战略底仓）':no_core,'V6（当前设置）':latest,'科技买入持有':tech_only,'红利买入持有':red_only,'50/50定期再平衡':half}
    last=latest.iloc[-1]
    st.info('收盘信号在下一根K线收盘价成交，禁止使用未来价格。战略红利默认20%，只属于 **V6策略子账户**，不代表整个家庭资产都要买20%红利。')
    k=st.columns(5)
    k[0].metric('科技目标',f'{last.target_tech:.0%}')
    k[1].metric('红利目标',f'{last.target_red:.0%}')
    k[2].metric('现金目标',f'{last.target_cash:.0%}')
    k[3].metric('科技温度',str(last.tech_temperature))
    k[4].metric('红利温度',str(last.red_temperature))
    st.caption('最后信号日期：'+str(last.name.date()) + ('（周线索引是周一，价格为该周末收盘）' if frequency=='week' else '')+'。当下目标不等于下一交易日不计成本立即清仓。')
    tabs=st.tabs(['回测总览','净值与仓位','风险与因子','稳定性与成本','实际持仓过渡','交易明细与下载'])
    with tabs[0]:
        windows={'全部可用历史':(None,None), '2022熊市':('2022-01-01','2022-12-31'),
                 '2023—2026回顾性分段':('2023-01-01','2026-11-20'),'2026本轮':('2026-09-28','2026-11-20')}
        if frequency=='week' and base.index[0].year<2018:
            windows.update({'2016—2022开发期':('2016-01-11','2022-12-31'),'2018股灾':('2018-01-01','2018-12-31'),
                            '2020V形反转':('2020-02-01','2020-08-31')})
        if frequency=='day':windows.update({'2026年5月后':('2026-05-01','2026-11-20'),
                                            '2026科技退潮后':('2026-06-30','2026-11-20')})
        window=st.selectbox('观察期',list(windows))
        start,end=windows[window]
        table=[]
        for n,df in runs.items():
            m=metrics(df,start,end)
            if m:table.append({'策略':n,**m})
        st.dataframe(pd.DataFrame(table).set_index('策略'),use_container_width=True)
        if 'ETF' in mode:st.caption('ETF为Longbridge前复权周线，实际交易仍可能存在分红调整、交易滑点、折溢价和成交时点差异。')
        else:st.caption('指数为价格指数，红利未计入分红再投资；收益不能直接等同真实ETF。')
    with tabs[1]:
        fig=go.Figure()
        for n,df in runs.items():
            if n=='50/50定期再平衡' or n=='V6（无战略底仓）':continue
            fig.add_trace(go.Scatter(x=df.index,y=df.nav/df.nav.iloc[0],mode='lines',name=n))
        fig.update_layout(height=440,title='相同成交假设下的累计净值',yaxis_title='起点 = 1',hovermode='x unified')
        st.plotly_chart(fig,use_container_width=True)
        figure2=go.Figure()
        for col,label in [('held_tech','科技'),('held_red','红利'),('held_cash','现金')]:
            figure2.add_trace(go.Scatter(x=latest.index,y=latest[col]*100,mode='lines',stackgroup='one',name=label))
        figure2.update_layout(height=300,yaxis_range=[0,100],title='V6实际持仓分布',yaxis_title='%')
        st.plotly_chart(figure2,use_container_width=True)
    with tabs[2]:
        st.subheader('最新因子状态')
        show=['tech_momentum','red_momentum','relative','red_short_rel',
              'tech_vol','red_vol','tech_dd','red_dd','tech_stage','red_stage','v6_red_eligible']
        st.dataframe(pd.DataFrame({'指标':show,'数值':[last.get(x,None) for x in show]}),hide_index=True)
        st.markdown('**默认执行纪律：**相对动量选强势资产，温度和趋势决定试仓与退出，波动率限制风险，盈利后分批加仓；不满足战术入场条件时，80%战术部分可以保持现金。')
        st.caption('科创50、成交量拥挤代理和短期红利提前切换尚未显示稳定增量，默认不增加额外下单信号。')
    with tabs[3]:
        st.subheader('不同红利底仓比例下的历史敏感性')
        if st.button('执行仓位敏感性与费用测试',type='primary'):
            rows=[]
            for x in [0.,.1,.2,.3,.4]:
                z=simulate_v6(base,c,replace(model,red_core=x))
                allm=metrics(z)
                future=metrics(z,'2023-01-01',None) if frequency=='week' else metrics(z,'2026-06-30',None)
                rows.append({'红利常驻':f'{x:.0%}','累计收益%':allm['累计收益%'],
                             '最大回撤%':allm['最大回撤%'],'后段回撤%':future['最大回撤%'] if future else None,
                             '显著调仓周/日':allm['显著调仓次数']})
            st.dataframe(pd.DataFrame(rows),hide_index=True,use_container_width=True)
            fees=[]
            for bp in [5,15,30,50]:
                cc=replace(c,cost_per_side=bp/10000)
                z=simulate_v6(base,cc,model)
                fees.append({'单边成本':f'{bp}bp',**metrics(z)})
            st.dataframe(pd.DataFrame(fees).set_index('单边成本'),use_container_width=True)
            st.caption('只用于历史稳定性检验，不能据最大累计收益选择参数；红利底仓会在红利下跌期增加损失。')
        else:st.caption('点击按钮运行，不改变默认模型参数。')
    with tabs[4]:
        st.subheader('实际持仓与目标差额：仅策略子账户')
        capital=st.number_input('策略子账户资金（万元）',min_value=1.0,value=100.,step=10.)
        actual_t=st.slider('现在的科技仓位',0,100,65,5)
        actual_r=st.slider('现在的红利仓位',0,100,15,5)
        if actual_t+actual_r>100:st.error('两类仓位之和不能超过100%')
        else:
            current=np.array([actual_t,actual_r])/100*capital
            desired=np.array([last.target_tech,last.target_red])*capital
            diff=desired-current
            chart=pd.DataFrame({'资产':['科技','红利'],'目前万元':current,'模型目标万元':desired,'差额万元':diff})
            st.dataframe(chart,hide_index=True,use_container_width=True)
            st.caption('注意：这是研究模型的仓位缺口，并非即时卖出指令。首次引入模型时需要将历史已错过的信号与现有仓位分开处理，先核算整体风险敞口。')
    with tabs[5]:
        cols=['tech','red','tech_temperature','red_temperature','target_tech','target_red','target_cash','held_tech','held_red','turnover','fee','reason','v6_reason']
        log=latest[latest.turnover>.08]
        st.dataframe(log[cols].iloc[::-1].head(120),use_container_width=True)
        st.download_button('导出V6完整交易台账CSV',latest.to_csv(index_label=datefield).encode('utf-8-sig'),
                           file_name='fusion_v6_ledger.csv',mime='text/csv')
except Exception as exc:
    st.error(f'V6读取或回测失败：{exc}')
    st.exception(exc)
