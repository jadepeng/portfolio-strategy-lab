"""V9 research dashboard: ChiNext re-entry, dividends core, money ETF buffer."""
from dataclasses import replace
from pathlib import Path
import sys
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import streamlit as st
ROOT=Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from history.strategies.fusion_v5 import metrics
from history.strategies.fusion_v6 import v6_base, V6Config
from history.strategies.fusion_v7 import V7Config, simulate_v7
from history.strategies.fusion_v8 import V8Config, simulate_v8
from history.strategies.fusion_v9 import V9Config, simulate_v9
from history.strategies.fusion_v9_execution import V9ExecutionConfig, simulate_v9_execution

st.set_page_config(page_title='V9 稳健回补与风险预算',page_icon='🧭',layout='wide')
st.title('V9｜创业板有条件买回 × 红利核心 × 货币ETF')
st.caption('研究版：原V8买回路径 + 科技相对强弱风险预算 + 科技波动率预算。不预测顶部、不自动下单，也不等于趋势动物私有信号。')
with st.sidebar:
    st.header('策略配置')
    core = st.slider('红利核心仓位',20,45,30,5)/100
    st.caption('核心仓不随择时信号清空，但会承受红利市场的下跌。')
    mode = st.radio('V9因子组合',['动量＋波动预算','仅相对动量','仅波动率','V8原始买回'],index=0)
    relative_cap=st.slider('科技弱于红利时的科技仓位上限',15,50,32,1)/100
    vol_target=st.slider('科技年化波动率预算',20,60,30,5)/100
    experimental_exec=st.checkbox('实验：减少常规换手，风险减仓优先（未经样本外验证）',value=False)
    cost_equity=st.slider('权益ETF买卖单侧成本 bp',0,60,15,5)
    cost_money=st.slider('货币ETF买卖单侧成本 bp',0,20,1)
    st.divider()
    st.subheader('实际仓位核对（策略子账户）')
    funds=st.number_input('策略子账户资金（万元）',min_value=1.,value=100.,step=10.)
    own_tech=st.slider('当前创业板仓位%',0,100,25)
    own_red=st.slider('当前红利仓位%',0,100,35)

@st.cache_data

def prices_load(filename):
    return pd.read_csv(ROOT/'market_data'/filename,parse_dates=['week_start']).set_index('week_start')

try:
    p=prices_load('longbridge_etf_fwd_2020_2026_v7_money.csv')
    pidx=prices_load('longbridge_2016_2026_tech_dividend_prices.csv')
    base=replace(v6_base('week'),cost_per_side=cost_equity/10000)
    model=V6Config();cash=V7Config(red_core=core,cash_mode='money_price',money_etf_side_cost=cost_money/10000)
    cfg=V9Config(weak_relative_cap=relative_cap,target_annual_vol=vol_target,
                 use_relative_brake=mode in ('动量＋波动预算','仅相对动量'),
                 use_volatility_brake=mode in ('动量＋波动预算','仅波动率'))
    v8=V8Config()
    b7=simulate_v7(p,base,model,cash)
    b8=simulate_v8(p,base,model,cash,v8)
    v9=simulate_v8(p,base,model,cash,v8) if mode=='V8原始买回' else simulate_v9(p,base,model,cash,cfg,v8)
    if experimental_exec and mode!='V8原始买回':
        v9=simulate_v9_execution(p,base,model,cash,cfg,V9ExecutionConfig(),v8)
    if mode=='V8原始买回':
        v9=v9.assign(v9_tech_vs_red_6w=p.tech.pct_change(6)-p.red.pct_change(6),
                     v9_tech_annual_vol=p.tech.pct_change().rolling(12).std()*np.sqrt(52.),
                     v9_reason='未启用V9风险约束',v9_risk_override=False)
    last=v9.iloc[-1]
    last_week_finish=(p.index[-1]+pd.Timedelta(days=4)).strftime('%Y-%m-%d')
    st.caption('行情样本：2020-10—2026-11，159915创业板ETF / 515180红利ETF / 511880银华日利。最近信号日：'+last_week_finish+'。')
    k=st.columns(4)
    for col,name,v in zip(k,['创业板目标','红利目标','货币ETF目标','创业板温度'],
                          [f'{last.target_tech:.0%}',f'{last.target_red:.0%}',f'{last.target_cash:.0%}',last.tech_temperature]):col.metric(name,v)
    st.info('科技仓位为0只代表当前不符合回补条件，**不是永久放弃创业板ETF**。V8的试仓、趋势确认、盈利加仓仍存在；V9只控制过高科技风险。')
    tab=st.tabs(['创业板何时可以买','历史收益与回撤','压力测试与成本','调仓信号日志','仓位差额','研究局限'])
    with tab[0]:
        px=p.tech;rd=p.red
        data=[
          ('创业板ETF收盘',round(px.iloc[-1],3)),
          ('4周/10周/30周均线',f'{px.rolling(4).mean().iloc[-1]:.3f} / {px.rolling(10).mean().iloc[-1]:.3f} / {px.rolling(30).mean().iloc[-1]:.3f}'),
          ('6周科技相对红利动量',f'{last.v9_tech_vs_red_6w:+.1%}'),
          ('12周科技已实现年化波动率',f'{last.v9_tech_annual_vol:.1%}'),
          ('V8买回状态',last.v8_reentry_state),
          ('V9风险限制',last.v9_reason),
        ]
        st.dataframe(pd.DataFrame(data,columns=['指标','当前值']),hide_index=True,use_container_width=True)
        st.markdown('''**路径A：企稳试仓。** 原V8需要较26周高点跌超12%，从12周低点反弹7.5%以上，连续两周站上4周均线，短期动量转正，且相对红利的弱势明显收敛；先试12%。

**路径B：趋势确认。** 创业板站上10和30周均线，6及12周收益为正，同时6周相对红利超额收益超过2个百分点，才考虑提高到38%。仓位盈利且科技继续增强，可加到最多52%。

**V9的限制。** 即使上述路径触发，如果科技相对红利明显弱或12周实现波动率过高，科技仓位目标会被进一步限制。相对强度和波动率恢复以后，上限自动放开；所有调仓以交易日实际可成交情况为准。''')
    with tab[1]:
        scenarios=[('红利单持',simulate_v7(p,base,model,cash,mode='red')),
                   ('50/50月度再平衡',simulate_v7(p,base,model,cash,mode='half')),
                   ('V7防守版',b7),('V8买回版',b8),('V9当前选项',v9)]
        st.dataframe(pd.DataFrame([{'策略':name,**metrics(df)} for name,df in scenarios]).set_index('策略'),use_container_width=True)
        fig=go.Figure()
        for name,df in scenarios:
            fig.add_trace(go.Scatter(x=df.index+pd.Timedelta(days=4),y=df.nav/df.nav.iloc[0],name=name,mode='lines'))
        fig.update_layout(title='ETF样本净值曲线（起点 = 1）',height=440,hovermode='x unified',yaxis_title='累计净值')
        st.plotly_chart(fig,use_container_width=True)
        icash=replace(cash,cash_mode='assumed_yield',assumed_cash_annual=.01)
        index_v8=simulate_v8(pidx,base,model,icash,v8)
        index_v9=(simulate_v9_execution(pidx,base,model,icash,cfg,V9ExecutionConfig(),v8) if experimental_exec else simulate_v9(pidx,base,model,icash,cfg,v8)) if mode!='V8原始买回' else index_v8
        st.subheader('长期价格指数交叉检验')
        st.dataframe(pd.DataFrame([{'策略':'V8原版','样本':'2016—2026指数',**metrics(index_v8)},
                                   {'策略':'当前V9选项','样本':'2016—2026指数',**metrics(index_v9)}]),use_container_width=True,hide_index=True)
    with tab[2]:
        if experimental_exec and mode!='V8原始买回':
            st.info('当前启用实验性调仓过滤：常规调仓双边换手不足20%暂缓，但科技彻底退出或任一权益信号降仓超过4个百分点则强制执行。参数来自已知历史，不是独立样本外优化。')
        table=[]
        for eqcost in (5,15,30):
            bb=replace(base,cost_per_side=eqcost/10000)
            cc=replace(cash,money_etf_side_cost=cost_money/10000)
            dd=simulate_v8(p,bb,model,cc,v8) if mode=='V8原始买回' else simulate_v9_execution(p,bb,model,cc,cfg,V9ExecutionConfig(),v8) if experimental_exec else simulate_v9(p,bb,model,cc,cfg,v8)
            table.append({'权益单侧费用bp':eqcost,**{k:metrics(dd)[k] for k in ('累计收益%','最大回撤%','累计双边换手')}})
        st.dataframe(pd.DataFrame(table),hide_index=True,use_container_width=True)
        st.warning('长期指数与ETF不是同一种总收益口径；2023—2026仅为已知历史的分段测试，并非真正未知样本外。原版与升级版同用收盘信号、下一周收盘成交，均未模拟开盘跳空或市场冲击。')
    with tab[3]:
        cols=['tech','red','target_tech','target_red','target_cash','v8_reentry_state','v9_reason','turnover','fee']
        st.dataframe(v9[cols].iloc[::-1].head(90),use_container_width=True)
        st.download_button('导出V9逐周交易与信号日志',v9.to_csv(index_label='week_start').encode('utf-8-sig'),file_name='V9_weekly_ledger.csv',mime='text/csv')
    with tab[4]:
        if own_tech+own_red>100:
            st.error('输入的科技与红利仓位之和超过100%，请修正。')
        else:
            target=np.array([last.target_tech,last.target_red,last.target_cash])*funds
            actual=np.array([own_tech,own_red,100-own_tech-own_red])*funds/100
            st.dataframe(pd.DataFrame({'资产':['创业板ETF','红利ETF','货币ETF/待配置现金'],
                         '当前持有（万元）':actual.round(2),'模型目标（万元）':target.round(2),'目标差额（万元）':(target-actual).round(2)}),hide_index=True,use_container_width=True)
            st.caption('差额仅表示策略子账户的计划配比，不是一次性卖出或买入指令。长期底仓、税费、流动性和实际盈亏须单独考虑。')
    with tab[5]:
        st.markdown('''- 回测使用历史数据反复开发模型，**已知2026年下跌，因此不能声称2023—2026是完全独立的样本外验证**。
- ETF样本自2020年10月开始，不覆盖2020年疫情最低点；指数样本不含股息再投资，二者不可直接比较绝对收益。
- 场内货币ETF为低风险基金而不是保本存款；511880的前复权历史价仅为收益代理，尚未经逐次分红对账。
- V9只提供周频规则。真实交易必须考虑次周开盘、收盘差异、跳空、报价与ETF溢价等；需要保留现金流动性缓冲。
- 短周期单独优化或继续堆叠信号容易过拟合。正式应用前应冻结规则并记录未来真实逐周表现。''')
except Exception as ex:
    st.exception(ex)
