"""V8: Conditional re-entry into ChiNext ETF, dividend core + money ETF cash sleeve."""
from dataclasses import replace
from pathlib import Path
import sys
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import streamlit as st
ROOT=Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from history.strategies.fusion_v5 import metrics
from history.strategies.fusion_v6 import V6Config,v6_base
from history.strategies.fusion_v7 import V7Config,simulate_v7
from history.strategies.fusion_v8 import V8Config,simulate_v8,signals_v8

st.set_page_config(page_title='V8 创业板重新买回策略',page_icon='📈',layout='wide')
st.title('V8｜创业板右侧买回 × 红利核心 × 货币ETF')
st.caption('研究策略：红利做长期核心、创业板只在具备条件时投入进攻仓。不是趋势动物私有公式，不是自动下单。')
with st.sidebar:
    st.subheader('风险预算')
    core=st.slider('红利核心比例',0,50,30,5)/100
    preset=st.selectbox('创业板入场模式',['均衡试仓','谨慎试仓','只做确认突破','提高参与度'],index=0)
    presets={
        '均衡试仓':(0.12,0.38,0.52,True),
        '谨慎试仓':(0.08,0.32,0.45,True),
        '只做确认突破':(0.0,0.38,0.52,False),
        '提高参与度':(0.18,0.45,0.60,True),
    }
    v=presets[preset]
    vshape=st.checkbox('开启快速V形突破追入（历史稳健性不足）',value=False)
    relative=st.checkbox('要求科技相对红利表现强',value=True)
    equity_bps=st.slider('权益ETF单边成本（bp）',0,40,15)
    cash_bps=st.slider('货币ETF单边成本（bp）',0,15,1)
    st.divider()
    own=st.number_input('策略子账户资金（万元）',min_value=1.,value=100.,step=10.)
    own_tech=st.slider('当前创业板ETF仓位',0,100,30)
    own_red=st.slider('当前红利ETF仓位',0,100,30)

@st.cache_data
def load_prices(filename):
    return pd.read_csv(ROOT/'market_data'/filename,parse_dates=['week_start']).set_index('week_start')

try:
    prices=load_prices('longbridge_etf_fwd_2020_2026_v7_money.csv')
    index_prices=load_prices('longbridge_2016_2026_tech_dividend_prices.csv')
    base=replace(v6_base('week'),cost_per_side=equity_bps/10000)
    model=V6Config()
    cash=V7Config(red_core=core,cash_mode='money_price',money_etf_side_cost=cash_bps/10000)
    reb=V8Config(probe_weight=min(v[0],1-core),confirmed_weight=min(v[1],1-core),winning_weight=min(v[2],1-core),
                 enable_probe=v[3],enable_v_reentry=vshape,enable_relative=relative)
    c=simulate_v8(prices,base,model,cash,reb)
    baseline=simulate_v7(prices,base,model,cash)
    last=c.iloc[-1]
    w=st.columns(4)
    for col,lab,val in zip(w,['创业板目标','红利目标','货币ETF目标','创业板回补状态'],
                [f'{last.target_tech:.0%}',f'{last.target_red:.0%}',f'{last.target_cash:.0%}',str(last.v8_reentry_state)]):
        col.metric(lab,val)
    st.caption('最后价格及信号是2026年11月20日收盘；CSV内行名为交易周起始日。实际回测按下一周收盘价成交。')
    st.info('科技目标为0%仅代表目前不满足模型条件，**不是永久排除创业板ETF**。红利核心和货币ETF分别承担长期配置与等待买回的任务。')
    tabs=st.tabs(['当前买回条件','V7与V8绩效对照','历史买回日志','实际仓位过渡','策略约束'])
    with tabs[0]:
        p=prices.tech
        indicators={
            '最新创业板ETF收盘':p.iloc[-1],
            '4周均线':p.rolling(4).mean().iloc[-1],
            '10周均线':p.rolling(10).mean().iloc[-1],
            '30周均线':p.rolling(30).mean().iloc[-1],
            '近4周收益%':(p.iloc[-1]/p.iloc[-5]-1)*100,
            '近6周相对红利收益（百分点）':((p.iloc[-1]/p.iloc[-7]-1)-(prices.red.iloc[-1]/prices.red.iloc[-7]-1))*100,
            '26周高点回撤%':(p.iloc[-1]/p.iloc[-26:].max()-1)*100,
            '近12周最低价反弹%':(p.iloc[-1]/p.iloc[-12:].min()-1)*100,
        }
        st.dataframe(pd.DataFrame({'指标':list(indicators),'当前数值':list(indicators.values())}),use_container_width=True,hide_index=True)
        st.markdown('**买回路径 A｜底部企稳试仓**：最近26周回撤至少12%，从12周低点反弹至少7.5%，连续两周站上4周均线，短期动量与相对红利强弱改善。首先分配12%的策略资金。')
        st.markdown('**买回路径 B｜趋势转强**：站上10周和30周均线，6周与12周动量均为正，并且近6周相对红利超额收益超过2个百分点。先把科技目标提高至38%，盈利并进一步确认后最多至52%。')
        st.caption('快速V形突破支路（突破此前8周高点、近4周涨幅9%以上等）仅做实验；默认关闭。ETF价格仅供策略研究，具体订单应依据交易日实际成交条件执行。')
    with tabs[1]:
        st.dataframe(pd.DataFrame([{'策略':'V7 红利核心+货币ETF',**metrics(baseline)},
                                   {'策略':'V8 增加创业板买回',**metrics(c)}]).set_index('策略'),use_container_width=True)
        fig=go.Figure()
        for label,x in [('V7',baseline),('V8',c)]:
            fig.add_trace(go.Scatter(x=x.index+pd.Timedelta(days=4),y=x.nav/x.nav.iloc[0],name=label))
        fig.update_layout(title='可交易ETF历史净值（起点=1）',height=420,yaxis_title='净值',hovermode='x unified')
        st.plotly_chart(fig,use_container_width=True)
        idxcash=replace(cash,cash_mode='assumed_yield',assumed_cash_annual=.01)
        idxb=simulate_v7(index_prices,base,model,idxcash)
        idxv=simulate_v8(index_prices,base,model,idxcash,reb)
        st.subheader('样本覆盖与稳健性')
        st.dataframe(pd.DataFrame([
            {'样本':'2020—2026复权ETF','策略':'V7',**metrics(baseline)},
            {'样本':'2020—2026复权ETF','策略':'V8',**metrics(c)},
            {'样本':'2016—2026价格指数（现金假定1%）','策略':'V7',**metrics(idxb)},
            {'样本':'2016—2026价格指数（现金假定1%）','策略':'V8',**metrics(idxv)},
        ]),use_container_width=True,hide_index=True)
        st.warning('两个样本口径不同：指数价格不含红利现金分配，而ETF前复权数据是收益代理。长期样本若变差，不得声称新回补规则已经稳定有效。2020年2—3月疫情急跌未包含在ETF样本中。')
    with tabs[2]:
        prev=c.v8_reentry_state.shift(1).fillna('off')
        events=c.loc[(c.v8_reentry_state!=prev)|(c.v8_increment>.05),
           ['tech','red','v8_reentry_state','v8_increment','target_tech','target_red','target_cash','v8_reason']]
        st.dataframe(events.iloc[::-1].head(90),use_container_width=True)
        st.download_button('导出完整逐周交易日志',c.to_csv(index_label='week_start').encode('utf-8-sig'),
            file_name='V8_创业板买回_回测日志.csv',mime='text/csv')
    with tabs[3]:
        if own_tech+own_red>100:st.error('实际仓位之和超过100%，请修正')
        else:
            target=np.array([last.target_tech,last.target_red,last.target_cash])*own
            actual=np.array([own_tech,own_red,100-own_tech-own_red])/100*own
            st.dataframe(pd.DataFrame({'资产':['创业板ETF','红利ETF','货币ETF/现金'],'当前(万元)':actual,
                     '模型目标(万元)':target,'差额(万元)':target-actual}),use_container_width=True,hide_index=True)
            st.caption('该差额只是规划工具，不能替代长期核心仓与战术仓拆分，更不是立即全额成交指令。')
    with tabs[4]:
        st.markdown('''
**风险闸门**：创业板单周跌幅超过预设阈值、连续两周跌破4周均线、从建仓价回撤超过8.5%且跌破6周线、或从建仓后最高价回撤超过12%且跌破6周线时，关闭额外买回仓，并设置至少一个完整周线观察间隔。

**三条原则**：① 红利核心仓位不会被买回规则挪走；② 创业板交易必须从小仓试探或趋势确认开始，不在单次大跌当天强制抄底；③ 所有信号只使用当时或更早数据，周末确认后一周收盘模拟交易。

**费用口径**：权益和货币ETF分别按买卖每侧费率；回测未包含汇率、税务和基金申赎特殊规则，货币ETF前复权行情未逐笔对账红利。结果不是未来收益承诺。
''')
except Exception as ex:
    st.exception(ex)
