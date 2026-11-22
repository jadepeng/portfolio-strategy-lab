"""V19: conservative model blending; V18 remains frozen default."""
from pathlib import Path
import sys
import pandas as pd
import streamlit as st
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from v14.market_snapshot import load_weekly
from v15.us_snapshot import load_us_prices,load_us_open
from v19.ensemble import V19Config,build_cn_v19,build_us_v19

st.set_page_config(page_title='V19 双模型稳健性',layout='wide')
st.title('V19｜中美双模型稳健性、分段与执行成本')
st.caption('2026-11-20离线数据快照；所有年份都曾用于先前策略开发，此处为历史稳健性检验，而非真实独立样本外成绩。')
st.warning('正式基准仍是V18。V19的50/50混合仅为研究候选，未经未来行情验证；不能从五档回测中选取事后最优值作为实盘参数。')
@st.cache_data(show_spinner=False)
def load():
    cn=pd.read_csv(ROOT/'market_data/verified_5asset_weekly_v13.csv',parse_dates=['week_start']).set_index('week_start')
    return cn,load_weekly(),load_us_prices(),load_us_open()
cn,market,us,uo=load()
with st.sidebar:
    st.header('预注册式敏感性实验')
    cn_mix=st.select_slider('A股：V18占比',options=[0.,.25,.5,.75,1.],value=1.,format_func=lambda x:f'{x:.0%}')
    us_mix=st.select_slider('美股：Gemini多周期占比',options=[0.,.25,.5,.75,1.],value=1.,format_func=lambda x:f'{x:.0%}')
    core=st.select_slider('美股QQQ长期核心',options=[0.,.1,.2,.3,.4,.5],value=.3,format_func=lambda x:f'{x:.0%}')
    cfees=st.select_slider('A股费用倍率',options=[1.,2.,3.],value=1.)
    ufees=st.select_slider('美股费用倍率',options=[1.,2.,3.],value=1.)
    fill=st.radio('美股下一周成交',options=['next_week_open','next_week_close'],index=0,
        format_func=lambda x:'周初开盘（主要口径）' if x=='next_week_open' else '周末收盘（压力测试）')

cfg=V19Config(cn_v18_share=cn_mix,us_v18_share=us_mix,us_core=core,
    cn_extra_fee_multiple=cfees,us_extra_fee_multiple=ufees,us_fill_mode=fill)
_,_,cs,ch=build_cn_v19(cn,market,us,cfg)
_,_,usig,uh=build_us_v19(us,uo,cfg)

def year_ret(nav,year):
    val=nav.loc[str(year)]
    if val.empty:return float('nan')
    j=nav.index.get_loc(val.index[0])
    return (val.iloc[-1]/nav.iloc[j-1]-1)*100 if j>0 else float('nan')

def summarize(nav):
    p=nav.cummax();return {'全样本收益':f'{(nav.iloc[-1]/nav.iloc[0]-1)*100:+.2f}%',
    '最大回撤':f'{(nav/p-1).min()*100:.2f}%',
    '2026收益':f'{year_ret(nav,2026):+.2f}%'}

cn_tab,us_tab,compare_tab=st.tabs(['A股五资产','美股QQQ/BIL','稳健性与执行审计'])
with cn_tab:
    a=summarize(ch.nav);cols=st.columns(3)
    for col,(k,v) in zip(cols,a.items()):col.metric(k,v)
    st.line_chart((ch.nav/ch.nav.iloc[0]).rename('A股组合净值'))
    target=pd.DataFrame({'资产':['创业板159915','红利515180','国债511260','黄金518880','货币511880'],
                         '权重':[float(cs.iloc[-1]['target_'+s]) for s in ('tech','red','bond','gold','money')]})
    st.dataframe(target.style.format({'权重':'{:.1%}'}),hide_index=True,use_container_width=True)
    st.caption('配比是A股策略子账户目标，不是跨币种资产配置；价格信号出自当周收盘，下一周收盘模型成交。')
    st.download_button('下载A股逐周账本',ch.to_csv(index_label='week_start').encode('utf-8-sig'),'V19_CN_ledger.csv')
with us_tab:
    a=summarize(uh.nav);cols=st.columns(3)
    for col,(k,v) in zip(cols,a.items()):col.metric(k,v)
    st.line_chart((uh.nav/uh.nav.iloc[0]).rename('美股组合净值'))
    st.write({'QQQ目标':f'{usig.target_qqq.iloc[-1]:.1%}','BIL目标':f'{usig.target_bil.iloc[-1]:.1%}'})
    st.caption('QQQ长期核心不被趋势完全卖出；在一周收盘生成信号，下一周开盘或收盘成交。')
    st.download_button('下载美股逐周账本',uh.to_csv(index_label='week_start').encode('utf-8-sig'),'V19_US_ledger.csv')
with compare_tab:
    st.subheader('V15-V18固定比例敏感性')
    f=ROOT/'research_outputs'/'V19_CN_ensemble_robustness.csv'
    if f.exists():st.dataframe(pd.read_csv(f),hide_index=True,use_container_width=True)
    st.subheader('美股基准与多周期模型的执行对比')
    f=ROOT/'research_outputs'/'V19_US_ensemble_robustness.csv'
    if f.exists():st.dataframe(pd.read_csv(f),hide_index=True,use_container_width=True)
    st.info('重要限制：不同窗口复用同一历史轨迹，不能作为独立验证。手续费是成交额比例的假设值，未包含汇率、税务、拆分派息的单独核算。')
    st.code('python scripts/research_v19.py\npython -m pytest -q')
