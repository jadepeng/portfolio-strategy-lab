"""Interactive weekly risk strategy laboratory. Run: streamlit run app.py"""
from pathlib import Path
import io
import json
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from history.strategies.strategy_engine import Params, to_weekly, prepare_rate, make_signals, backtest, metrics, run_comparison, test_windows

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / 'market_data'
DATA_DIR.mkdir(exist_ok=True)

st.set_page_config(page_title='中美股市｜风险策略实验室', page_icon='📉', layout='wide')
st.markdown('''<style>
.block-container {max-width: 1420px; padding-top: 1.4rem;}
h1 {font-weight:740;letter-spacing:-.6px;}
div[data-testid="stMetric"] {border:1px solid #dedee3;border-radius:12px;padding:14px;}
.small {font-size:13px;color:#666;}
</style>''', unsafe_allow_html=True)
st.title('中美股市 · 风险策略实验室')
st.caption('宏观背离预警 → 趋势破位降仓 → 深度回撤防守 → 趋势修复买回 ｜ 周线收盘决策，下周执行')

@st.cache_data(ttl=3600, show_spinner=False)
def download_weekly(ticker: str) -> pd.Series:
    import yfinance as yf
    p = DATA_DIR / (ticker.replace('^','index_').replace('.','_') + '.csv')
    try:
        end = (datetime.utcnow() + timedelta(days=2)).strftime('%Y-%m-%d')
        df = yf.download(ticker, start='2009-12-01', end=end, interval='1d', auto_adjust=False,
                         progress=False, threads=False, timeout=25)
        if df.empty:
            raise ValueError('下载数据为空')
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        s = pd.to_numeric(df['Close'], errors='coerce').dropna()
        raw_last = pd.Timestamp(s.index.max()).tz_localize(None) if pd.Timestamp(s.index.max()).tzinfo else pd.Timestamp(s.index.max())
        weekly = to_weekly(s)
        # A partial active week is not a completed weekly signal.
        if raw_last.weekday() < 4 and pd.Timestamp.utcnow().tz_localize(None).date() <= (raw_last + pd.Timedelta(days=4-raw_last.weekday())).date():
            weekly = weekly.iloc[:-1]
        weekly.to_csv(p, header=['close'])
        return weekly
    except Exception as err:
        if p.exists():
            z = pd.read_csv(p, index_col=0, parse_dates=True)
            if not z.empty:
                return z.iloc[:,0].astype(float)
        raise RuntimeError(f'无法获得 {ticker} 数据：{err}') from err


def upload_weekly(upload, name):
    """Optional user CSV: Date,Close; daily input will be aggregated to Friday weeks."""
    df = pd.read_csv(upload)
    dc = next((c for c in df if str(c).lower() in ('date','datetime','日期')), None)
    vc = next((c for c in df if str(c).lower() in ('close','收盘价')), None)
    if dc is None or vc is None:
        raise ValueError(f'{name} CSV需要 Date,Close 两列')
    return to_weekly(pd.Series(pd.to_numeric(df[vc],errors='coerce').values,
                               index=pd.to_datetime(df[dc],errors='coerce')))

with st.sidebar:
    st.subheader('行情与回测设置')
    market = st.selectbox('策略标的', ['纳斯达克100（^NDX）', '创业板指数（399006.SZ）'])
    start = st.date_input('完整回测起始日期', pd.Timestamp('2015-01-01').date(),
                          min_value=pd.Timestamp('2011-01-01').date())
    st.divider()
    st.subheader('风险参数')
    warn_dd = st.slider('一级预警：滚动高点回撤', 3, 15, 8, 1) / 100
    defense_dd = st.slider('二级防御：滚动高点回撤', 7, 22, 12, 1) / 100
    extreme_dd = st.slider('三级极端：滚动高点回撤', 12, 35, 20, 1) / 100
    bps = st.slider('单程调仓成本（bp）', 0, 60, 15, 5)
    cash_pct = st.slider('现金年化收益假设', 0.0, 5.0, 0.0, 0.5)
    st.divider()
    st.subheader('美债背离指标')
    if market.startswith('纳斯达克'):
        macro = st.radio('选用', ['TLT代理', '真实10Y收益率', '不使用'], index=0)
        tlt_thr = st.slider('TLT八周跌幅阈值', 2.0, 12.0, 4.5, 0.5)
        yield_bp = st.slider('10年美债八周收益率上升（bp）', 15, 100, 35, 5)
        rate_file = st.file_uploader('如选真实10Y：上传FRED DGS10 CSV', type='csv', key='rate')
    else:
        macro, tlt_thr, yield_bp, rate_file = '不使用', 4.5, 35, None
        st.caption('创业板模型暂不使用美国利率作为直接减仓信号。')
    st.divider()
    st.subheader('数据源')
    uploaded = st.file_uploader('上传指数CSV（Date, Close），可替代Yahoo下载', type='csv')
    st.caption('默认为Yahoo Finance自动获取；离线时支持本地缓存或上传CSV。')

p = Params(warn_dd,defense_dd,extreme_dd,tlt_thr/100,float(yield_bp),bps/10000,macro,cash_pct/100)
sym = '^NDX' if market.startswith('纳斯达克') else '399006.SZ'

if p.use_macro == '真实10Y收益率' and rate_file is None:
    st.warning('你选择了真实10年期美债收益率，但尚未上传DGS10 CSV。为避免用TLT冒充利率，当前暂时关闭美债背离信号。')
    p = Params(warn_dd,defense_dd,extreme_dd,tlt_thr/100,float(yield_bp),bps/10000,'不使用',cash_pct/100)

try:
    close = upload_weekly(uploaded, sym) if uploaded else download_weekly(sym)
    tlt = download_weekly('TLT') if p.use_macro == 'TLT代理' else None
    rate = prepare_rate(pd.read_csv(rate_file)) if rate_file is not None else None
    if len(close) < 60:
        raise ValueError('可用历史数据少于60周，无法进行稳定均线测试。')
    runs = run_comparison(close,tlt,rate,p)
    latest = runs['三级风控'].iloc[-1]
    price = close.iloc[-1]
    latest_label = str(close.index[-1].date())
    st.caption(f'最新完成的周线：{latest_label}｜指数收盘 {price:,.2f}｜数据截止日由本地下载或上传文件决定')
    c1,c2,c3,c4 = st.columns(4)
    c1.metric('下周战术仓目标',f"{latest['next_target']:.0%}")
    c2.metric('本周已执行模拟仓位',f"{latest['weight']:.0%}")
    c3.metric('26周高点回撤', f"{latest['drawdown26']:.1%}")
    c4.metric('风险判定',latest['reason'])
    if p.use_macro == 'TLT代理':
        st.info('宏观项使用TLT八周价格变化做代理，不是10年期国债收益率；TLT受久期、期限溢价与分红因素影响。')
    st.divider()
    st.subheader('全周期：收益与回撤对比')
    comp=[]
    for name,x in runs.items():
        d=metrics(x,str(start), '2099-12-31')
        comp.append({'策略':name,**d})
    df=pd.DataFrame(comp).set_index('策略')
    show=df.copy()
    for c in ['区间收益率','年化收益率','最大回撤','年化波动率','平均仓位']:
        show[c]=show[c].map(lambda x: f'{x:+.1f}%' if pd.notna(x) else '—')
    show['累计换手']=show['累计换手'].map(lambda x: f'{x:.2f}')
    st.dataframe(show,use_container_width=True)

    fig=go.Figure()
    cols={'买入持有':'#64748b','30周均线':'#14b8a6','三级风控':'#2563eb'}
    for name,x in runs.items():
        q=x.loc[str(start):]
        if len(q)>1:
            y=q['nav']/q['nav'].iloc[0]
            fig.add_trace(go.Scatter(x=q.index,y=y,name=name,mode='lines',line={'color':cols[name], 'width':2.5}))
    fig.update_layout(height=420,margin=dict(l=10,r=10,t=12,b=10),legend=dict(orientation='h',y=1.08),
                      xaxis_title='',yaxis_title='净值（起始 = 1）',hovermode='x unified')
    st.plotly_chart(fig,use_container_width=True)

    left,right=st.columns(2)
    with left:
        st.subheader('回撤曲线')
        f=go.Figure()
        for name,x in runs.items():
            q=x.loc[str(start):]
            if len(q)>1:
                nav=q['nav']; dd=(nav/nav.cummax()-1)*100
                f.add_trace(go.Scatter(x=q.index,y=dd,mode='lines',name=name,line={'color':cols[name]}))
        f.update_layout(height=330,margin=dict(l=10,r=10,t=12,b=10),yaxis_title='从区间内高点回撤（%）',xaxis_title='',legend=dict(orientation='h'),hovermode='x unified')
        st.plotly_chart(f,use_container_width=True)
    with right:
        st.subheader('历史模拟仓位')
        q=runs['三级风控'].loc[str(start):]
        f=go.Figure(go.Scatter(x=q.index,y=q['weight']*100,mode='lines',line_shape='hv',name='三级风控',line={'color':'#2563eb'}))
        f.update_layout(height=330,margin=dict(l=10,r=10,t=12,b=10),yaxis=dict(range=[-5,105],title='权益仓位（%）'),xaxis_title='')
        st.plotly_chart(f,use_container_width=True)
    st.divider()
    st.subheader('历史股灾压力测试')
    stress=[]
    for period,(f,t) in test_windows().items():
        if period=='完整样本':continue
        row={'事件':period}
        for name,x in runs.items():
            m=metrics(x,f,t)
            row[name+' 收益']=m['区间收益率']
            row[name+' 最大回撤']=m['最大回撤']
        stress.append(row)
    stress_df=pd.DataFrame(stress).set_index('事件')
    st.dataframe(stress_df.style.format('{:+.1f}%',na_rep='—'),use_container_width=True)

    st.subheader('调仓与风险信号日志')
    r=runs['三级风控'].copy()
    s=r[(r['next_target'] != r['next_target'].shift(1)) & (r.index>=pd.Timestamp(start))]
    log=s[['close','drawdown26','next_target','reason']].tail(60).iloc[::-1].copy()
    log.columns=['指数收盘','近26周回撤','下一周目标仓位','触发原因']
    st.dataframe(log,use_container_width=True)
    out=r[['close','ma10','ma30','drawdown26','next_target','weight','turnover','nav','reason']]
    st.download_button('下载完整信号与回测CSV',data=out.to_csv(index_label='week_ending').encode('utf-8-sig'),file_name=f'{sym.replace("^", "")}_strategy_backtest.csv',mime='text/csv')

    st.divider()
    st.subheader('将模型仓位转换为实际减仓金额')
    st.caption('只对“战术仓预算”执行25%等目标，不直接等同于总资产的25%；战略核心仓不在计算之内。')
    x1,x2,x3=st.columns(3)
    with x1:
        budget=st.number_input('战术仓原始预算（万元）',min_value=0.0,value=100.0,step=10.0)
    with x2:
        current=st.number_input('目前已投入战术仓（万元）',min_value=0.0,value=100.0,step=10.0)
    with x3:
        core=st.number_input('另行保留的长期核心仓（万元）',min_value=0.0,value=100.0,step=10.0)
    target_cash=budget*latest['next_target']
    sell=max(0,current-target_cash)
    st.markdown(f'**模型目标战术仓 {target_cash:.1f} 万元；目前超出目标 {sell:.1f} 万元。** 核心仓 {core:.1f} 万元未计入减仓指令。')
    st.warning('模型建议的减仓额是按历史上持续执行信号推导的目标差额。若之前连续没有减仓，不应机械将全部差额当作下一笔市价单；应结合流动性、实际持仓、税费与个人风险承受能力制定过渡计划。')
    with st.expander('规则及限制（建议阅读）'):
        st.markdown('''
- **信号与执行：**每周收盘以后计算，下一周的周度收益使用上周已经得知的目标仓位；避免同一根K线收盘后回头成交。
- **一级（75%）：**跌破10周均线或距离近26周高点回撤达到阈值；美债背离也可以使目标最高为75%。
- **二级（50%）：**跌破30周均线或回撤达到二级阈值。
- **三级（25%）：**回撤达到三级阈值，或跌破30周均线且回撤16%以上。
- **回补：**站回10周均线、四周收益转正且回撤小于16%，允许目标回到50%；站稳10周和30周线、四周收益为正、回撤小于10%，恢复100%。
- **美债代理：**TLT八周价格跌幅达到设定值，且纳指距26周高点6%以内、近八周收益不低于-1%，目标仓位上限75%。真实10年收益率模式使用八周上升bp阈值。
- **财务假设：**指数价格收益，默认现金0%收益、单程成交成本15bp，不包含股息、ETF跟踪误差、税费、汇率、期货、杠杆、限价无法成交；每周按目标比例交易。不把纳指与创业板回测直接相加成实际多币种账户收益。
- **模型风险：**参数基于已经发生的历史经验构造，并非样本外确认的最优值；均线在震荡市可能多次失败，2020式V形反转可能踏空，跳空和跌停无法被周线精细模拟。
''')
except Exception as e:
    st.error(f'无法计算最新回测：{e}')
    st.info('可上传历史CSV（Date,Close）进行本地运行。下面提供已核验的2026-11-20历史回测快照；该快照不会跟随参数调整。')
    verified_path=ROOT/'verified_results.json'
    if verified_path.exists():
        verified=json.loads(verified_path.read_text(encoding='utf-8'))
        st.json(verified)

st.caption('研究工具，不构成自动委托交易指令。以完整实际持仓和可承受回撤决定最终风险预算。')
