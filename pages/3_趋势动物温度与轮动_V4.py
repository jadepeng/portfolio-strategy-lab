"""Interactive V4 trend-temperature / red-tech rotation research page."""
from pathlib import Path
import sys
from dataclasses import replace
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from history.strategies.trend_animal_v4 import TrendAnimalV4Config,backtest_v4,get_stage_snapshot,STAGES
from history.strategies.v3_engine import V3Config,run_v3,metrics

st.set_page_config(page_title='趋势动物×红利科技 V4',page_icon='🌡️',layout='wide')
st.title('V4｜趋势动物温度 × 红利科技轮动')
st.caption('趋势温度定时机；波动率归一后的相对强度选筹；先小仓试错，盈利加仓，危险信号先退出。不是趋势动物 Pro 私有公式的复刻。')

with st.sidebar:
    st.header('数据与实验设置')
    st.caption('默认使用本包的经核验历史指数周收盘价，离线也能计算。')
    data_file=st.file_uploader('上传自有周线：week_start,tech,red',type='csv',key='v4_prices')
    short=st.slider('短期动量周数',6,20,12,2)
    long=st.slider('长期动量周数',20,52,26,2)
    trend=st.slider('长期均线周数',18,42,30,2)
    buffer=st.slider('强弱切换缓冲',0.00,.30,.10,.02)
    first=st.slider('温转热试仓上限',.15,.60,.35,.05)
    stop=st.slider('科技移动止损回撤',.08,.25,.14,.01)
    stop_red=st.slider('红利移动止损回撤',.06,.20,.10,.01)
    cool=st.slider('止损冷却周数',1,10,4,1)
    risk=st.checkbox('启用危险/移动止损',True)
    champagne=st.checkbox('启用沸腾期部分兑现',True)
    cost=st.slider('名义成交单边成本（bp）',0,40,15,5)/10000

cfg=TrendAnimalV4Config(mom_short=short,mom_long=long,long_ma=trend,strength_buffer=buffer,enter_fraction=first,
 tech_trail_stop=stop,dividend_trail_stop=stop_red,cooldown_weeks=cool,risk_overlay=risk,
 champagne_overlay=champagne,cost_rate=cost)

try:
    if data_file:
        df=pd.read_csv(data_file,parse_dates=['week_start']).set_index('week_start')
    else:
        df=pd.read_csv(ROOT/'market_data/longbridge_2016_2026_tech_dividend_prices.csv',parse_dates=['week_start']).set_index('week_start')
    if long<=short:raise ValueError('长期动量周期必须大于短期')
    a=backtest_v4(df,cfg)
    v3=run_v3(df,V3Config(cost_rate=cost),compare=True)
    comparisons={n:v3[n] for n in ('科技持有','红利持有','50/50再平衡','V3多因子防御')}
    comparisons['V4温度+选筹+止损']=a
    comparisons['V4不含肥尾兑现']=backtest_v4(df,replace(cfg,champagne_overlay=False))
    comparisons['V4不含危险/移动止损']=backtest_v4(df,replace(cfg,risk_overlay=False))

    snap=get_stage_snapshot(a)
    st.caption(f'最后完整周线：{snap["date"]}｜中证红利为价格指数（不含股息再投）｜本页仅限战术资金')
    m1,m2,m3,m4,m5=st.columns(5)
    m1.metric('科技趋势温度',snap['tech'],help=snap['tech_season']);m2.metric('红利趋势温度',snap['dividend'],help=snap['dividend_season'])
    m3.metric('科技目标',f'{snap["tech_weight"]:.0%}')
    m4.metric('红利目标',f'{snap["red_weight"]:.0%}')
    m5.metric('现金目标',f'{snap["cash_weight"]:.0%}')
    st.info('当前动作：'+snap['reason']+'。仓位百分比仅作用于战术轮动子账户，不是全部家庭金融资产的卖出指令。')

    windows={'全样本':(None,None),'开发期2016—2022':('2016-01-11','2022-12-26'),
        '留出期2023—2026':('2023-01-02','2026-11-20'),
        '2018下跌':('2018-01-01','2018-12-31'),
        '2020 V形反转':('2020-02-03','2020-08-31'),
        '2022熊市':('2022-01-01','2022-12-31'),
        '2026本轮':('2026-09-28','2026-11-20')}
    period=st.selectbox('绩效观察区间',list(windows),index=0)
    start,end=windows[period]
    table=[]
    for name,r in comparisons.items():
        v=metrics(r,start,end)
        if v is not None:table.append({'策略':name,**v})
    st.subheader('绩效：多策略并排比较')
    st.dataframe(pd.DataFrame(table).set_index('策略'),use_container_width=True)
    fig=go.Figure()
    for name,r in comparisons.items():
        q=r.loc[start:end] if start or end else r
        fig.add_trace(go.Scatter(x=q.index,y=q.nav/q.nav.iloc[0],name=name,mode='lines'))
    fig.update_layout(height=410,yaxis_title='区间起点净值=1',hovermode='x unified',legend={'orientation':'h'})
    st.plotly_chart(fig,use_container_width=True)

    left,right=st.columns(2)
    with left:
        st.subheader('科技与红利趋势温度（0冻—6沸）')
        tt=go.Figure()
        for key,name in [('tech_temp_id','创业板'),('red_temp_id','中证红利')]:
            tt.add_trace(go.Scatter(x=a.index,y=a[key],mode='lines',name=name,line_shape='hv'))
        tt.update_layout(height=320,yaxis=dict(tickvals=list(range(7)),ticktext=STAGES,range=[-.5,6.5]),legend={'orientation':'h'},hovermode='x unified')
        st.plotly_chart(tt,use_container_width=True)
    with right:
        st.subheader('实际目标配置轨迹')
        z=go.Figure()
        for key,name in [('hold_tech','科技'),('hold_red','红利'),('hold_cash','现金')]:
            z.add_trace(go.Scatter(x=a.index,y=a[key]*100,name=name,stackgroup='one'))
        z.update_layout(height=320,yaxis_title='权重%',yaxis_range=[0,100],hovermode='x unified')
        st.plotly_chart(z,use_container_width=True)

    st.subheader('历史交易决策日志')
    trades=a[a.turnover>.08][['tech','red','tech_temperature','red_temperature','tech_strength','red_strength',
                            'tech_champagne','red_champagne','tech_danger','red_danger',
                            'hold_tech','hold_red','hold_cash','turnover','reason']]
    st.dataframe(trades.tail(50).iloc[::-1],use_container_width=True)
    st.download_button('下载全部周度趋势、信号和净值CSV',a.to_csv(index_label='week_start').encode('utf-8-sig'),
                       file_name='trend_animal_v4_weekly_signals.csv',mime='text/csv')
    st.markdown('''**使用边界：** 趋势温度是根据收盘价、均线、收益率与历史波动率构造的代理指标。
    缺乏真实成交量、个股参与率、情绪以及Nick小程序的原始参数，因此**不是原版温度**。
    所有策略都基于周收盘价重新分配，信号在该周结束后决定下一周持仓；忽略次日开盘价跳空。
    中证红利为价格指数，股息未回投；实际操作需换成可交易ETF复权净值。样本外区间被展示后再针对它调参数，会进一步造成数据挖掘。''')
except Exception as exc:
    st.error(f'无法运行V4回测：{exc}')
    st.caption('请检查CSV包含week_start、tech、red及至少110个共同有效周数据。')
