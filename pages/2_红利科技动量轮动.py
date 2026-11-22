"""Second Streamlit page: dividend / technology momentum rotation."""
from pathlib import Path
import json
from datetime import datetime,timedelta
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from history.strategies.rotation_engine import RotationParams,run_rotation_comparison,rotation_metrics,rotation_windows
from history.strategies.strategy_engine import to_weekly

ROOT=Path(__file__).resolve().parents[1]
CACHE=ROOT/'market_data';CACHE.mkdir(exist_ok=True)
st.set_page_config(page_title='红利科技｜动量轮动实验室',page_icon='🔄',layout='wide')
st.title('红利 × 科技：动量轮动与极端行情风控')
st.caption('相对动量选强势资产 → 绝对趋势限制风险 → 科技破位降低上限 → 趋势恢复后买回')

@st.cache_data(ttl=3600,show_spinner=False)
def download_price(ticker:str):
    import yfinance as yf
    fn=CACHE/(ticker.replace('^','index_').replace('.','_')+'_close.csv')
    try:
        last=(datetime.utcnow()+timedelta(days=2)).strftime('%Y-%m-%d')
        raw=yf.download(ticker,start='2015-01-01',end=last,interval='1d',auto_adjust=False,threads=False,progress=False,timeout=25)
        if raw.empty: raise ValueError('下载为空')
        if isinstance(raw.columns,pd.MultiIndex):raw.columns=raw.columns.get_level_values(0)
        s=to_weekly(raw['Close'])
        # discard current partial week
        if s.index[-1].date()>datetime.utcnow().date():s=s.iloc[:-1]
        s.to_csv(fn,header=['Close']);return s
    except Exception as e:
        if fn.exists():
            old=pd.read_csv(fn,index_col=0,parse_dates=True).iloc[:,0].astype(float)
            st.warning(f'{ticker}实时下载失败，当前使用本地缓存：{e}')
            return old
        raise RuntimeError(f'数据下载失败：{ticker}，请上传CSV。原因：{e}') from e


def parse_uploaded(upload,label):
    df=pd.read_csv(upload)
    dcol=next((c for c in df if c.lower() in ('date','datetime','日期')),None)
    pcol=next((c for c in df if c.lower() in ('close','收盘价')),None)
    if not dcol or not pcol:raise ValueError(f'{label} 需包含 Date,Close 列')
    s=pd.Series(pd.to_numeric(df[pcol],errors='coerce').to_numpy(),index=pd.to_datetime(df[dcol],errors='coerce'))
    return to_weekly(s)

with st.sidebar:
    st.subheader('行情源')
    st.caption('默认：创业板399006.SZ；中证红利价格指数000922.SH（Yahoo通常为000922.SS），不含分红再投资。')
    tech_file=st.file_uploader('创业板价格CSV（Date,Close）',type='csv',key='tech_csv')
    red_file=st.file_uploader('中证红利指数价格CSV（Date,Close）',type='csv',key='red_csv')
    red_ticker=st.text_input('红利Yahoo代码',value='000922.SS',help='若Yahoo不支持指数，请上传000922.SH的CSV。若改为红利ETF，请确保使用复权价格。')
    st.divider()
    st.subheader('动量参数')
    fast=st.slider('短期动量（周）',4,20,12,2)
    slow=st.slider('长期动量（周）',20,52,26,2)
    share=st.slider('短期动量权重',0.0,1.0,.60,.05)
    trend=st.slider('绝对趋势均线（周）',10,40,30,2)
    buffer=st.slider('切换缓冲：相对动量差（百分点）',0,12,4,1)/100
    reb=st.slider('常规再平衡间隔（周）',1,8,4,1)
    risk=st.checkbox('科技指数破位：周度风控覆盖',value=True)
    ratio=st.checkbox('启用原0.4/0.6指数比值倾斜（实验）',value=False,help='仅适合创业板与中证红利原始指数比值；ETF或改基指数不能直接沿用绝对阈值。')
    st.divider()
    cost=st.slider('单程成本（bp）',0,60,15,5)/10000
    cash=st.slider('现金年化利率',0.0,5.0,0.,.5)/100

p=RotationParams(fast_weeks=fast,slow_weeks=slow,fast_share=share,trend_weeks=trend,switch_buffer=buffer,rebalance_weeks=reb,trade_cost=cost,cash_rate=cash,use_risk_gate=risk,use_ratio_tilt=ratio)
if slow<=fast:st.error('长期动量周期必须大于短期周期。');st.stop()
try:
    tech=parse_uploaded(tech_file,'创业板') if tech_file else download_price('399006.SZ')
    red=parse_uploaded(red_file,'中证红利') if red_file else download_price(red_ticker)
    # Avoid silently treating an equity stock or a differently scaled ETF as the CSI Dividend price index.
    if red_file is None and red_ticker == '000922.SS' and not (1000 < red.median() < 20000):
        raise ValueError('000922.SS下载值不符合中证红利指数点位量级，请确认是否取到了错误证券并改用官方指数CSV。')
    if ratio and (red_file is None and red_ticker!='000922.SS'):
        st.warning('当前红利数据可能不是原始中证红利指数，0.4/0.6绝对比值阈值可能无效。')
    comparison=run_rotation_comparison(tech,red,p)
    model=comparison['当前参数方案'];last=model.iloc[-1]
    d1,d2,d3,d4=st.columns(4)
    d1.metric('下周科技战术权重',f"{last['next_tech']:.0%}")
    d2.metric('下周红利战术权重',f"{last['next_red']:.0%}")
    d3.metric('下周现金',f"{last['next_cash']:.0%}")
    d4.metric('指数比值（创业板/中证红利）',f"{last['ratio']:.3f}")
    st.caption(f"最后一根已完成周线：{last.name.date()} ｜ 科技12/26周动量 {last['tech_momentum']:.1%}，红利12/26周动量 {last['red_momentum']:.1%} ｜ {last['reason']}")
    st.warning('上述100%现金等仅指模型所管理的战术轮动资金，不是对全部家庭资产或长期核心仓的清仓指令。')
    st.subheader('完整历史：四种可比策略')
    comp=[]
    for label,res in comparison.items(): comp.append({'策略':label,**rotation_metrics(res)})
    st.dataframe(pd.DataFrame(comp).set_index('策略').style.format({'区间收益率':'{:+.1f}%','年化收益率':'{:+.1f}%','最大回撤':'{:+.1f}%','年化波动':'{:.1f}%','累计换手':'{:.1f}','平均科技仓位':'{:.1f}%','平均红利仓位':'{:.1f}%'}),use_container_width=True)
    fig=go.Figure()
    color=['#2563eb','#b45309','#64748b','#16a34a','#0f766e','#7c3aed','#dc2626']
    for (name,res),co in zip(comparison.items(),color):fig.add_trace(go.Scatter(x=res.index,y=res['nav']/res['nav'].iloc[0],name=name,mode='lines',line={'color':co,'width':2.3}))
    fig.update_layout(height=420,hovermode='x unified',legend={'orientation':'h'},margin={'l':10,'r':10,'t':20,'b':10},yaxis_title='净值（初始1）')
    st.plotly_chart(fig,use_container_width=True)
    left,right=st.columns(2)
    with left:
        st.subheader('模型仓位轨迹')
        z=go.Figure()
        for col,name,color in [('next_tech','科技','#2563eb'),('next_red','红利','#b45309'),('next_cash','现金','#64748b')]:z.add_trace(go.Scatter(x=model.index,y=model[col]*100,name=name,line={'color':color},stackgroup='one'))
        z.update_layout(height=320,margin={'l':10,'r':10,'t':20,'b':10},yaxis_title='目标仓位（%）',yaxis_range=[0,100],legend={'orientation':'h'})
        st.plotly_chart(z,use_container_width=True)
    with right:
        st.subheader('回撤曲线')
        z=go.Figure()
        for (name,res),co in zip(comparison.items(),color):z.add_trace(go.Scatter(x=res.index,y=(res.nav/res.nav.cummax()-1)*100,name=name,line={'color':co}))
        z.update_layout(height=320,margin={'l':10,'r':10,'t':20,'b':10},yaxis_title='回撤（%）',legend={'orientation':'h'})
        st.plotly_chart(z,use_container_width=True)
    st.subheader('历史股灾压力测试')
    stress=[]
    for name,(start,end) in rotation_windows().items():
        for strat,res in comparison.items():
            m=rotation_metrics(res,start,end)
            stress.append({'事件':name,'策略':strat,'收益':m['区间收益率'],'最大回撤':m['最大回撤']})
    st.dataframe(pd.DataFrame(stress).style.format({'收益':'{:+.1f}%','最大回撤':'{:+.1f}%'}),use_container_width=True,hide_index=True)
    st.subheader('决策日志与当前持仓差额')
    logs=model.loc[((model.next_tech.diff().abs().fillna(0)>1e-6)|(model.next_red.diff().abs().fillna(0)>1e-6))]
    st.dataframe(logs[['tech','red','ratio','relative_momentum','next_tech','next_red','next_cash','reason']].tail(40).iloc[::-1],use_container_width=True)
    a,b,c=st.columns(3)
    budget=a.number_input('本轮动战术资金预算（万元）',min_value=0.,value=100.,step=10.)
    current_tech=b.number_input('当前科技战术持仓（万元）',min_value=0.,value=70.,step=5.)
    current_red=c.number_input('当前红利战术持仓（万元）',min_value=0.,value=20.,step=5.)
    delta_tech=budget*last['next_tech']-current_tech
    delta_red=budget*last['next_red']-current_red
    st.markdown(f'理论目标：**科技 {budget*last["next_tech"]:.1f}万，红利 {budget*last["next_red"]:.1f}万，现金 {budget*last["next_cash"]:.1f}万**。相对当前：科技 **{delta_tech:+.1f}万**，红利 **{delta_red:+.1f}万**。')
    st.caption('这是风险预算的模拟差额，不建议因错过此前信号就一次性市价完成全部差额。保留核心仓、处理未实现盈利及税费。')
    st.download_button('下载轮动信号及净值CSV',model.to_csv(index_label='week_ending').encode('utf-8-sig'),file_name='hongli_tech_rotation_signals.csv',mime='text/csv')
    with st.expander('重要限制：阅读后再使用模型仓位'):
        st.markdown('''- 仅使用指数价格，**中证红利000922是价格指数，不包含股息再投资**；若要公平比较红利长期收益，应更换成中证红利全收益H00922或者复权ETF净值。\n- 只在已完成的周线收盘产生信号。回测按照收盘后立即以该价格换仓，实际下一交易日成交会存在价格跳空、流动性和滑点。\n- 相对动量用短/长回看收益加权；绝对趋势同时要求30周均线上方且动量为正。常规每4周更新，风控每周允许降仓。\n- 指数原始比值阈值仅作**实验**，不建议在ETF或不同起始基日指数之间直接复用。\n- 当前的两个市场是创业板与中证红利**指数代理**，真实组合中的半导体、工业富联、纳指、红利ETF表现可能不同。\n- 2016年之前的中证红利行情在当前已核验周线中不可用，2015股灾不属于本组合轮动回测。\n- 使用同一批样本查看参数并反复调优会过拟合。建议再使用全收益指数完成滚动样本外检验。''')
except Exception as e:
    st.error(f'当前无法重新下载或计算：{e}')
    st.info('你可以上传创业板及中证红利的两份Date,Close CSV。下方是此前核验的静态历史结果，不随滑块变化。')
    snap=ROOT/'verified_rotation_results.json'
    if snap.exists():st.json(json.loads(snap.read_text(encoding='utf-8')))
