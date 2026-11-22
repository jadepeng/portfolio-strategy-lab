from pathlib import Path
from dataclasses import replace
import sys
import pandas as pd
import streamlit as st
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from fusion_v6 import V6Config,v6_base
from fusion_v7 import V7Config
from fusion_v10 import V10Config,signals_v10,simulate_v10,results,TARGETS
from fusion_v11 import V11Config,signals_v11,simulate_v11
st.set_page_config(page_title='V11 多周期集成与风险上限',layout='wide')
st.title('V11 · 多周期集成 / 双窗口风险预算')
st.caption('研究用途，不自动交易。历史行情截至2026-11-20；周标签为该周开盘日，周末生成信号，下一周收盘模拟成交。')
with st.sidebar:
    mode=st.selectbox('策略模式',['V11-A 收益优先','V11-B 风险优先','V11-C 稳健防守'],index=1)
    costs=st.slider('交易成本倍数',.5,3.,1.,.25)
    custom=st.file_uploader('上传五资产复权周线CSV',type=['csv'])
try:
    path=ROOT/'market_data/longbridge_5asset_fwd_2020_2026_v10.csv'
    p=pd.read_csv(custom if custom is not None else path,parse_dates=['week_start']).set_index('week_start').sort_index()
    base=v6_base('week');model=V6Config();cash=V7Config(red_core=.30,cash_mode='money_price',money_etf_side_cost=.0001)
    v10=V10Config(portfolio_vol_brake=True,tech_fee=.0015*costs,red_fee=.0015*costs,bond_fee=.0003*costs,gold_fee=.0015*costs,money_fee=.0001*costs)
    cfg=V11Config(risk_guard=not mode.startswith('V11-A'),risk_cap=.08 if mode.startswith('V11-C') else .10)
    b=simulate_v10(p,signals_v10(p,base,model,cash,v10),v10)
    s=signals_v11(p,base,model,cash,v10,cfg);f=simulate_v11(p,s,v10,cfg)
except Exception as exc:
    st.error(f'数据或计算错误：{exc}。请检查五资产CSV是否存在且日期对齐。');st.stop()
labels=['创业板','红利','国债','黄金','货币ETF']
for col,label,key in zip(st.columns(5),labels,TARGETS):col.metric(label,f'{f[key].iloc[-1]:.1%}')
st.metric('最新双窗口较高预测波动率',f'{f.worst_window_vol.iloc[-1]:.2%}')
st.caption(f'国债趋势一致性 {f.bond_signal_agreement.iloc[-1]:.0%}；黄金趋势一致性 {f.gold_signal_agreement.iloc[-1]:.0%}；红利底仓风险豁免已触发 {int(f.core_override.sum())} 周。')
st.dataframe(pd.DataFrame([results(b.nav),results(f.nav)],index=['V10',mode]))
nav=pd.DataFrame({'V10':b.nav,mode:f.nav});st.subheader('累计净值');st.line_chart(nav);st.subheader('回撤');st.line_chart(nav/nav.cummax()-1.)
st.subheader('实际模拟持仓（成交后）');st.area_chart(f[['held_tech','held_red','held_bond','held_gold','held_money']])
st.download_button('导出回测明细',f.to_csv(index_label='week_start').encode('utf-8-sig'),file_name='v11_ledger.csv')
st.warning('历史正收益窗口占比不是未来盈利概率；预测波动率约束也不是实际波动率或最大亏损保证。')
