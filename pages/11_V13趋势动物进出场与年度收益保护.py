"""V13 research UI: no live orders, not Nick's private signal engine."""
from pathlib import Path
import sys
import pandas as pd
import streamlit as st

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from fusion_v6 import v6_base,V6Config
from fusion_v7 import V7Config
from fusion_v10 import V10Config,simulate_v10,results,TARGETS
from fusion_v11 import V11Config,signals_v11
from v13.regime_v13 import RegimeConfig,signals_v13
from v13.daily_state_v13 import DailyExitConfig,simulate

st.set_page_config(page_title='V13 趋势进出场与收益保护',layout='wide')
st.title('V13｜趋势动物式进出场 × 组合年度收益保护')
st.caption('研究模型，不是趋势动物Pro原始算法；2026结果属于事后调参，没有独立前瞻验证。')

@st.cache_data(show_spinner=False)
def inputs():
    p=pd.read_csv(ROOT/'market_data/verified_5asset_weekly_v13.csv',parse_dates=['week_start']).set_index('week_start')
    c=V10Config(portfolio_vol_brake=True)
    s=signals_v11(p,v6_base('week'),V6Config(),V7Config(red_core=.3,cash_mode='money_price',money_etf_side_cost=.0001),c,V11Config(risk_guard=False))
    return p,c,s

p,c,baseline=inputs()
a,b,d=st.columns(3)
with a:
    hold=st.selectbox('科技主升信号过后的激进轮动保留期（周）',[8,12,16],index=1)
with b:
    lock_trigger=st.select_slider('年内峰值收益激活阈值',[.08,.09,.10,.11,.12],value=.10,format_func=lambda v:f'{v:.0%}')
with d:
    giveback=st.select_slider('从年内净值高点允许回吐幅度',[.015,.025,.035],value=.025,format_func=lambda v:f'{v:.1%}')

sig=signals_v13(p,baseline,RegimeConfig(hold_weeks=hold))
old=simulate_v10(p,baseline,c)
w=simulate_v10(p,sig,c)
retold=100*(old.loc['2026'].nav.iloc[-1]/old.loc[:'2025'].nav.iloc[-1]-1)
retw=100*(w.loc['2026'].nav.iloc[-1]/w.loc[:'2025'].nav.iloc[-1]-1)
daily_base,openret=simulate(p,sig,w,DailyExitConfig(use_stop=False))
daily_guard,guardret=simulate(p,sig,w,DailyExitConfig(shock=-.07,dd_stop=.08,profit_lock=True,lock_activation=lock_trigger,lock_max_drop=giveback))
metrics=st.columns(4)
metrics[0].metric('V11-A 2026收益',f'{retold:+.2f}%')
metrics[1].metric('V13 周线回测',f'{retw:+.2f}%')
metrics[2].metric('V13 下周开盘成交',f'{openret:+.2f}%')
metrics[3].metric('V13 日频收益保护',f'{guardret:+.2f}%')

st.subheader('周线历史净值')
chart=pd.DataFrame({'V11-A':old.nav/old.nav.iloc[0],'V13':w.nav/w.nav.iloc[0]})
st.line_chart(chart)
st.subheader('2026年逐日净值')
nav0=float(w.loc[:'2025'].nav.iloc[-1])
df=pd.DataFrame({'下一周开盘成交':daily_base.nav/nav0,'增加年度收益保护':daily_guard.nav/nav0})
st.line_chart(df)

st.subheader('截至11月20日的研究目标配置')
base_target=sig.iloc[-1][list(TARGETS)]
show={'创业板':base_target['target_tech'],'红利':base_target['target_red'],'国债':base_target['target_bond'],'黄金':base_target['target_gold'],'货币ETF':base_target['target_money']}
st.table(pd.DataFrame.from_dict(show,orient='index',columns=['当前V13原始目标权重']).style.format('{:.1%}'))
if bool(daily_guard.iloc[-1].profit_locked):
    st.warning('2026收益保护状态已触发：研究模拟当前持仓约为红利30%、国债30%、货币ETF40%，暂停科技与黄金战术仓。此为历史状态，不是你的真实账户指令。')
else:
    st.info('截至样本末日，年度收益保护状态未触发。')

st.subheader('历史回测结果')
st.dataframe(pd.DataFrame([
 {'研究版本':'V11-A','2016?':'否，2020-2026','累计收益':results(old.nav)['total'],'最大回撤':results(old.nav)['mdd']},
 {'研究版本':'V13','2016?':'否，2020-2026','累计收益':results(w.nav)['total'],'最大回撤':results(w.nav)['mdd']},
]).set_index('研究版本').round(2),use_container_width=True)

st.download_button('下载V13逐周目标与成交记录',w.to_csv(index_label='week_start').encode('utf-8-sig'),file_name='v13_weekly_ledger.csv',mime='text/csv')
st.download_button('下载2026年日频模拟记录',daily_guard.to_csv(index_label='date').encode('utf-8-sig'),file_name='v13_daily_profit_guard.csv',mime='text/csv')
st.caption('周线日期为当周起始日，信号只在该周最后一个交易日收盘后获知；实际价格、滑点、申赎分配以及未来行情可能不同。2026的10%阈值具有事后选择偏差。')
