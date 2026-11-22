"""V20: public-rule GTAA/dual momentum research versus frozen V18."""
from pathlib import Path
import sys
import pandas as pd
import streamlit as st
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from history.v14.market_snapshot import load_weekly
from history.v15.us_snapshot import load_us_prices,load_us_open
from history.v20.public_rules import V20Config
from history.v20.integrated import run_cn_v20,run_us_v20,run_advisory_cn,run_advisory_us

st.set_page_config(page_title='V20 公开量化策略融合审计',layout='wide')
st.title('V20｜Faber趋势 × Antonacci双动量 × 动量质量 · 中美独立验证')
st.caption('离线行情：截至2026-11-20。2013—2026海外研究只是规则来源；本项目回测样本2020—2026，已被此前V1—V19反复使用。')
st.warning('没有“稳定盈利”的保证。完整公开策略直接替换V18的历史结果更差；默认冻结V18，只把外部规则作为可切换研究。')

@st.cache_data(show_spinner=False)
def inputs():
    p=pd.read_csv(ROOT/'market_data'/'verified_5asset_weekly_v13.csv',parse_dates=['week_start']).set_index('week_start')
    return p,load_weekly(),load_us_prices(),load_us_open()
p,m,u,o=inputs()
with st.sidebar:
    st.header('研究参数（不改变冻结基准）')
    cnmode=st.radio('A股策略',('V18冻结基准','外部规则仅阻止风险加仓','V18＋公开双动量集成','纯公开双动量独立基准'),index=0)
    usmode=st.radio('美股策略',('V18冻结基准','双重否决：只冻结新增QQQ','V18＋公开双动量集成','纯公开趋势＋动量独立基准'),index=0)
    blend=st.select_slider('外部规则混合权重',options=[0.,.25,.50,1.],value=.25,format_func=lambda x:f'{x:.0%}')
    trend=st.select_slider('长期趋势周数',options=[35,40,45],value=40)
    momentum=st.select_slider('动量观察周数',options=[22,26,30],value=26)
    cnfee=st.select_slider('A股手续费倍率',options=[1.,2.,3.],value=1.)
    usfee=st.select_slider('美股手续费倍率',options=[1.,2.,3.],value=1.)
    usfill=st.radio('美股下一周成交时点',('next_week_open','next_week_close'),index=0,
                    format_func=lambda v:'首个交易日开盘' if v=='next_week_open' else '最后交易日收盘（压力测试）')

cfg=V20Config(cn_blend=1. if cnmode.startswith('纯') else blend if '集成' in cnmode else 0.,
              us_blend=1. if usmode.startswith('纯') else blend if '集成' in usmode else 0.,
              trend_weeks=trend,momentum_weeks=momentum,
              cn_fee_multiplier=cnfee,us_fee_multiplier=usfee,us_fill_mode=usfill)

def summary(ledger):
    nav=ledger.nav
    peak=nav.cummax();dates=nav.loc['2026'].index
    i=nav.index.get_loc(dates[0])
    prior=nav.iloc[i-1] if i>0 else nav.iloc[0]
    return {'累计收益':f'{(nav.iloc[-1]/nav.iloc[0]-1)*100:+.2f}%',
            '最大回撤':f'{(nav/peak-1).min()*100:.2f}%',
            '2026至今':f'{(nav.iloc[-1]/prior-1)*100:+.2f}%'}

if '阻止风险加仓' in cnmode:
    a_sig,csig,ah,ch=run_advisory_cn(p,m,u,cfg)
    ch_sig_count=int(csig.public_warning_restricted.sum())
else:
    c=run_cn_v20(p,m,u,cfg)
    csig,ch=c['signal'],c['ledger'];ah=c['baseline_ledger'];ch_sig_count=0
if '双重否决' in usmode:
    usig_base,usig,uh_base,uh=run_advisory_us(u,o,cfg)
    uh_sig_count=int(usig.public_warning_restricted.sum())
else:
    b=run_us_v20(u,o,cfg)
    usig,uh=b['signal'],b['ledger'];uh_base=b['baseline_ledger'];uh_sig_count=0

cn_tab,us_tab,audit=st.tabs(['A股五资产','美股QQQ/BIL','公开研究与历史局限'])
with cn_tab:
    c1,c2,c3=st.columns(3)
    for col,(k,v) in zip((c1,c2,c3),summary(ch).items()):col.metric(k,v)
    st.line_chart(pd.DataFrame({'选中研究模式':ch.nav/ch.nav.iloc[0],
                                 'V18冻结基准':ah.nav/ah.nav.iloc[0]}))
    st.dataframe(pd.DataFrame({'资产':['创业板159915','红利515180','10年国债511260','黄金518880','货币511880'],
        '目标仓位':[csig.iloc[-1][f'target_{k}'] for k in ('tech','red','bond','gold','money')]}).style.format({'目标仓位':'{:.1%}'}),hide_index=True,use_container_width=True)
    if ch_sig_count:st.caption(f'长趋势与绝对动量同时否决的新增仓位周数：{ch_sig_count}')
    st.download_button('导出A股逐周成交与净值',ch.to_csv(index_label='week_start').encode('utf-8-sig'),'V20_CN_ledger.csv')
with us_tab:
    c1,c2,c3=st.columns(3)
    for col,(k,v) in zip((c1,c2,c3),summary(uh).items()):col.metric(k,v)
    st.line_chart(pd.DataFrame({'选中研究模式':uh.nav/uh.nav.iloc[0],
                                 'V18冻结基准':uh_base.nav/uh_base.nav.iloc[0]}))
    st.write({'QQQ目标':f'{usig.target_qqq.iloc[-1]:.1%}','短期美债BIL':f'{usig.target_bil.iloc[-1]:.1%}'})
    if uh_sig_count:st.caption(f'双重否决而禁止新增QQQ仓位的历史周数：{uh_sig_count}')
    st.download_button('导出美股逐周成交与净值',uh.to_csv(index_label='week_start').encode('utf-8-sig'),'V20_US_ledger.csv')
with audit:
    st.subheader('纯公开规则与V18对照：不要只挑最高收益')
    for name in ('V20_public_rules_stress.csv','V20_advisory_robustness.csv'):
        f=ROOT/'research_outputs'/name
        if f.exists():
            st.markdown(f'**{name}**')
            st.dataframe(pd.read_csv(f),hide_index=True,use_container_width=True)
    st.markdown('**公开方法出处**：[Meb Faber](https://mebfaber.com/timing-model/) · [AQR时间序列动量](https://www.aqr.com/insights/research/journal-article/time-series-momentum) · [Alpha Architect](https://alphaarchitect.com/quantitative-momentum-investing-philosophy/) · [Quantpedia 2026](https://quantpedia.com/active-dual-momentum-gtaa-strategy/)')
    st.info('40周≈10个月趋势；26周动量剔除最近4周只是12-2动量理念的较短周期代理，不是Alpha Architect原股票选股系统。国内E大的估值数据缺失，因此没有虚构估值信号。')
    st.code('python scripts/research_v20.py\npython scripts/research_v20_advisory.py\npython -m pytest -q')
