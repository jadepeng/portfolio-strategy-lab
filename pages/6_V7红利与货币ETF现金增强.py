"""V7 strategy lab: dividend equity as core, money ETF as investable cash sleeve."""
from dataclasses import replace
from pathlib import Path
import sys
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import streamlit as st
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from history.strategies.fusion_v5 import metrics
from history.strategies.fusion_v6 import V6Config,v6_base
from history.strategies.fusion_v7 import V7Config,simulate_v7,signals_v7,eval_cases
st.set_page_config(page_title='V7 红利主仓 + 货币ETF',layout='wide',page_icon='💵')
st.title('V7｜红利主仓 · 货币ETF现金增强 · 趋势交易')
st.caption('数据截至2026年11月20日；指标源于可交易ETF历史周收盘价格。这里的“趋势动物”是公开理念的透明代理，不是私有公式。')
with st.sidebar:
    st.subheader('组合结构与资金管理')
    core=st.slider('红利常驻核心占策略资金',0,50,30,5)/100
    money_mode=st.selectbox('现金资金处理方式',['money_price','assumed_yield','cash_zero'],index=0,
                            format_func=lambda x:{'money_price':'511880前复权价格代理','assumed_yield':'固定现金年化假设','cash_zero':'闲置现金不计息'}[x])
    annual=st.slider('固定假设的年化收益率',0.,3.,1.,.1)/100
    money_fee=st.slider('货币ETF单边交易成本（bp）',0,20,1,1)/10000
    stock_fee=st.slider('科技/红利ETF单边交易成本（bp）',0,40,15,1)/10000
    st.divider()
    st.caption('研究开关：红利动量追买在当前样本未带来稳健增益，默认关闭')
    extra=st.slider('红利强势时额外增仓上限',0,30,0,5)/100
    confirmation=st.radio('红利增仓确认',['strict','medium'],format_func=lambda x:'严格：6周+26周+30周均线' if x=='strict' else '宽松：26周+30周均线')
    own=st.number_input('策略子账户资金（万元）',min_value=1.,max_value=100000.,value=100.,step=10.)
    own_red=st.slider('实际红利仓位',0,100,20,5)
    own_tech=st.slider('实际科技仓位',0,100,60,5)
try:
    path=ROOT/'market_data/longbridge_etf_fwd_2020_2026_v7_money.csv'
    prices=pd.read_csv(path,parse_dates=['week_start']).set_index('week_start')
    base=replace(v6_base('week'),cost_per_side=stock_fee)
    model=V6Config()
    cfg=V7Config(red_core=core,red_extra=extra,red_qualify=confirmation,cash_mode=money_mode,
                 assumed_cash_annual=annual,money_etf_side_cost=money_fee)
    current=simulate_v7(prices,base,model,cfg)
    idle=simulate_v7(prices,base,model,replace(cfg,cash_mode='cash_zero'))
    latest=current.iloc[-1]
    st.info('决策对象是策略子账户；**“现金”指可配置的防守资金，不含生活应急金**。货币ETF低风险但不保本，二级市场买卖存在折溢价与价差。')
    cols=st.columns(4)
    for col,lab,value in zip(cols,['目标科技ETF','目标红利ETF','目标货币ETF/现金','最新红利温度'],
                              [f'{latest.target_tech:.1%}',f'{latest.target_red:.1%}',f'{latest.target_cash:.1%}',str(latest.red_temperature)]):
        col.metric(lab,value)
    st.caption(f'最后信号对应 {(latest.name+pd.Timedelta(days=4)).date()} 周五收盘（数据行索引为周一 {latest.name.date()}），下一周收盘模拟执行；不是周一盘中已获知的信号。')
    red_prices=prices.red; tech_prices=prices.tech
    red_w6=red_prices.iloc[-1]/red_prices.iloc[-7]-1;red_w26=red_prices.iloc[-1]/red_prices.iloc[-27]-1
    tech_w26=tech_prices.iloc[-1]/tech_prices.iloc[-27]-1
    st.caption(f'当前指数代理：红利6周{red_w6:+.1%}、26周{red_w26:+.1%}；科技26周{tech_w26:+.1%}。红利相对较强，但短期动量仍偏弱，不能仅凭历史高收益追涨。')
    tabs=st.tabs(['绩效与风险','历史行情压力测试','当前仓位与迁移','逐周交易日志','数据说明'])
    with tabs[0]:
        variations=eval_cases(prices,fee_bps=int(round(stock_fee*10000)),cash_spread_bps=int(round(money_fee*10000)))
        comparisons={'当前自定义':current,'同仓位闲置现金':idle,
                     '20%红利＋货币ETF':variations['V6红利20%+511880'],
                     '40%红利＋货币ETF':variations['红利40%+511880'],
                     '50%红利＋货币ETF':variations['红利50%+511880']}
        # Buy-and-hold red equity benchmark is intentionally visible; tactical cash drags returns in long red bull runs.
        comparisons['红利ETF一直持有']=simulate_v7(prices,base,model,V7Config(cash_mode='cash_zero'),mode='red')
        table=[]
        for name,r in comparisons.items():table.append({'策略':name,**metrics(r)})
        st.dataframe(pd.DataFrame(table).set_index('策略'),use_container_width=True)
        fig=go.Figure()
        for name,r in comparisons.items():
            fig.add_trace(go.Scatter(x=r.index,y=r.nav/r.nav.iloc[0],name=name,mode='lines'))
        fig.update_layout(height=440,title='统一收盘成交与费用口径下的净值比较',yaxis_title='起点=1',hovermode='x unified')
        st.plotly_chart(fig,use_container_width=True)
        m=metrics(current);n=metrics(idle)
        st.caption(f'本次现金收益处理相对闲置现金的历史累计差异：{m["累计收益%"]-n["累计收益%"]:+.2f} 个百分点；不是未来保底收益。')
    with tabs[1]:
        data=[]
        windows={'全样本 2020—2026':(None,None),'2022熊市':('2022-01-01','2022-12-31'),
                 '2023—2026回顾分段':('2023-01-01',None),'2026年至今':('2026-01-01',None),
                 '2026年6月后':('2026-06-01',None)}
        for period,(start,end) in windows.items():
            for name,r in [('自定义',current),('同仓位闲置现金',idle)]:
                m=metrics(r,start,end)
                data.append({'样本':period,'策略':name,'收益%':m['累计收益%'],'最大回撤%':m['最大回撤%'],
                             '累计双边换手':m['累计双边换手']})
        st.dataframe(pd.DataFrame(data),use_container_width=True,hide_index=True)
        st.warning('2023—2026年不是独立样本外。由于2026年下跌已被模型研究者看到，所有择时结论仅用于研究，不应直接优化到历史最优参数。')
    with tabs[2]:
        if own_red+own_tech>100:
            st.error('实际红利与科技仓位之和不能超过100%。')
        else:
            desired=np.array([latest.target_tech,latest.target_red,latest.target_cash])*own
            actual=np.array([own_tech,own_red,100-own_tech-own_red])/100*own
            st.dataframe(pd.DataFrame({'资产':['科技ETF','红利ETF','货币ETF/现金'],'现有金额(万元)':actual,
                '模型目标金额(万元)':desired,'差额(万元)':desired-actual}),use_container_width=True,hide_index=True)
            st.caption('这是模型差额，不是立即全部按市价成交的建议。首次接管高仓位，应按多笔过渡并分别核对长期核心与战术资产。')
    with tabs[3]:
        st.dataframe(current[['tech','red','target_tech','target_red','target_cash','nav','turnover',
                              'fee_equity','fee_money','v6_reason','v7_red_add']].iloc[::-1].head(120),use_container_width=True)
        st.download_button('导出逐周台账',current.to_csv(index_label='week_start').encode('utf-8-sig'),
                           file_name='V7_money_ETF_weekly_ledger.csv',mime='text/csv')
    with tabs[4]:
        st.markdown('''
**现金收益模型差异**：511880的前复权周收盘行情是收益代理；每年分红、交易折溢价和税费没有逐笔核验。
511990的二级市场报价在100元附近，收益通常不表现为价格连续上涨，不能直接用报价涨跌代替总回报。

**交易假设**：周收盘信号→下一周收盘价模拟成交；权益ETF手续费/滑点以每侧bp配置；货币ETF另按每侧bp配置。下一个交易日的成交价、流动性、佣金豁免、资金可用与可取状态可能不同。

**重要对照**：页面内“闲置现金”与“货币ETF”采用相同的权益手续费模型，修正了V6旧回测对未投资现金也扣除交易费用的过于保守算法。因此新版结果不能不加标注地直接与历史V6成绩比较。''')
        st.dataframe(prices.tail(15),use_container_width=True)
except Exception as err:
    st.exception(err)
