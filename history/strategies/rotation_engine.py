"""Red-dividend vs ChiNext-tech momentum rotation with weekly risk override.

All arrays use completed Friday weeks. Indicators from week t set the weight for
week t+1. The research model prices trades at the signal close (a simplification);
no future weekly returns are accessed by the signal. No leverage or shorting.
"""
from dataclasses import dataclass, replace
from typing import Dict, Optional
import numpy as np
import pandas as pd


@dataclass(frozen=True)
class RotationParams:
    fast_weeks: int = 12
    slow_weeks: int = 26
    fast_share: float = 0.60
    trend_weeks: int = 30
    switch_buffer: float = 0.04  # score percentage-point difference
    rebalance_weeks: int = 4
    trade_cost: float = 0.0015
    cash_rate: float = 0.00
    use_risk_gate: bool = True
    use_ratio_tilt: bool = False
    ratio_low: float = 0.40
    ratio_high: float = 0.60
    tech_leader_weight: float = .75
    red_leader_weight: float = .75
    tech_max_weight: float = .80


def _assert_params(p: RotationParams):
    if p.fast_weeks < 2 or p.slow_weeks <= p.fast_weeks:
        raise ValueError('动量回看周期应为至少2周，长周期必须大于短周期')
    if p.trend_weeks < 2 or p.rebalance_weeks < 1:
        raise ValueError('均线/调仓周期不合法')
    for key in ['fast_share','tech_leader_weight','red_leader_weight', 'tech_max_weight']:
        val = getattr(p,key)
        if not 0 <= val <= 1: raise ValueError(f'{key}必须在0~1之间')
    if not 0<=p.switch_buffer<=.5: raise ValueError('切换缓冲范围不合法')
    if p.trade_cost < 0 or p.trade_cost > .1: raise ValueError('交易成本不合法')


def align_close(tech: pd.Series, dividend: pd.Series) -> pd.DataFrame:
    """Align completed weekly prices; NEVER forward-fill missing source weeks."""
    df=pd.concat([tech.rename('tech'), dividend.rename('red')],axis=1,join='inner').sort_index().dropna()
    df=df.astype(float)
    df=df.loc[(df['tech']>0)&(df['red']>0)]
    if df.index.has_duplicates: df=df.loc[~df.index.duplicated(keep='last')]
    if len(df)<60: raise ValueError('至少需要60个双方共有的已完成交易周')
    return df


def make_rotation_signals(prices: pd.DataFrame,p: RotationParams) -> pd.DataFrame:
    """Creates next-week target allocations from only past/current prices."""
    _assert_params(p)
    x=prices[['tech','red']].copy()
    for s in ['tech','red']:
        x[s+'_rfast']=x[s].pct_change(p.fast_weeks,fill_method=None)
        x[s+'_rslow']=x[s].pct_change(p.slow_weeks,fill_method=None)
        x[s+'_momentum']=p.fast_share*x[s+'_rfast']+(1-p.fast_share)*x[s+'_rslow']
        x[s+'_ma']=x[s].rolling(p.trend_weeks,min_periods=p.trend_weeks).mean()
        x[s+'_valid']=(x[s+'_momentum']>0)&(x[s]>x[s+'_ma'])
    x['ratio']=x['tech']/x['red']
    x['tech_dd26']=x['tech']/x['tech'].rolling(26,min_periods=1).max()-1
    x['relative_momentum']=x['tech_momentum']-x['red_momentum']
    targets=[]; leaders=[]; why=[]
    leader='red'; t=.5;r=.5
    warm=max(p.trend_weeks,p.slow_weeks)
    for i,(date,row) in enumerate(x.iterrows()):
        reasons=[]
        if i>=warm and i%p.rebalance_weeks==0:
            delta=row['relative_momentum']
            if delta>p.switch_buffer: leader='tech'
            elif delta<-p.switch_buffer: leader='red'
            tv=bool(row['tech_valid']);rv=bool(row['red_valid'])
            t=r=0.
            if tv and rv:
                t=p.tech_leader_weight if leader=='tech' else 1-p.red_leader_weight
                r=1-t
                if p.use_ratio_tilt:
                    if row['ratio']<p.ratio_low:
                        t=max(t,.65);r=1-t;reasons.append('低比值且科技趋势为正')
                    if row['ratio']>p.ratio_high:
                        r=max(r,.70);t=1-r;reasons.append('高比值且红利趋势为正')
            elif tv: t=min(p.tech_max_weight,.8)
            elif rv: r=1.
            reasons.append(f'{p.fast_weeks}/{p.slow_weeks}周动量重新配置')
        if i>=warm and p.use_risk_gate:
            dd=float(row['tech_dd26']);cap=p.tech_max_weight
            if dd<-.08 or row['tech']<x['tech'].iloc[max(0,i-9):i+1].mean(): cap=min(cap,.75)
            if dd<-.12 or row['tech']<x['tech_ma'].iloc[i]: cap=min(cap,.5)
            if dd<-.20 or (dd<-.16 and row['tech']<x['tech_ma'].iloc[i]): cap=min(cap,.25)
            if t>cap:
                t=cap;reasons.append('科技破位限仓')
            if not bool(row['red_valid']) and r>0:
                r=0.;reasons.append('红利绝对动量转弱')
        t=min(t,p.tech_max_weight)
        if t+r>1:
            r=1-t  # explicit no-leverage guard
        cash=max(0.,1-t-r)
        if min(t,r,cash)<-1e-10: raise ValueError('目标分配存在负仓位')
        targets.append((t,r,cash));leaders.append(leader)
        why.append('；'.join(reasons) if reasons else '持有或无新风险')
    x[['next_tech','next_red','next_cash']]=pd.DataFrame(targets,index=x.index)
    x['leader']=leaders;x['reason']=why
    return x


def _backtest_targets(sig:pd.DataFrame,p:RotationParams,mode:str='rotation')->pd.DataFrame:
    """Price return accrues before rebalancing at a completed weekly close.
    Current period returns always use holdings decided the previous week.
    """
    d=sig.copy()
    if mode=='rotation': target=d[['next_tech','next_red','next_cash']].to_numpy()
    elif mode=='科技买入持有': target=np.tile([1.,0.,0.],(len(d),1))
    elif mode=='红利买入持有': target=np.tile([0.,1.,0.],(len(d),1))
    elif mode=='50/50定期再平衡': target=np.tile([.5,.5,0.],(len(d),1))
    else: raise ValueError('未知策略')
    holdings=target[0].astype(float).copy()  # normalized starting NAV at close of first week
    prev=d.iloc[0];out=[]
    weekly_cash=(1+p.cash_rate)**(1/52)
    for i,(date,row) in enumerate(d.iterrows()):
        if i:
            holdings[0]*=row['tech']/prev['tech']
            holdings[1]*=row['red']/prev['red']
            holdings[2]*=weekly_cash
        gross=holdings.sum()
        desired=target[i]
        if not np.isclose(desired.sum(),1,atol=1e-9): raise ValueError('非法目标仓位总和')
        turnover=np.abs(holdings/gross-desired).sum()
        fee=gross*p.trade_cost*turnover if i else 0.
        nav=gross-fee
        holdings=desired*nav
        out.append([nav,turnover,fee/gross if gross else 0.,*desired])
        prev=row
    d[['nav','turnover','fee_pct','held_next_tech','held_next_red','held_next_cash']]=out
    return d


def run_rotation_comparison(tech:pd.Series,red:pd.Series,p:RotationParams) -> Dict[str,pd.DataFrame]:
    prices=align_close(tech,red)
    sig=make_rotation_signals(prices,p)
    results = {
        '科技买入持有': _backtest_targets(sig,p,'科技买入持有'),
        '红利买入持有': _backtest_targets(sig,p,'红利买入持有'),
        '50/50定期再平衡': _backtest_targets(sig,p,'50/50定期再平衡'),
        '纯动量轮动': _backtest_targets(
            make_rotation_signals(prices,replace(p,use_risk_gate=False,use_ratio_tilt=False)),p),
        '动量+破位风控': _backtest_targets(
            make_rotation_signals(prices,replace(p,use_risk_gate=True,use_ratio_tilt=False)),p),
        '动量+比值倾斜+风控': _backtest_targets(
            make_rotation_signals(prices,replace(p,use_risk_gate=True,use_ratio_tilt=True)),p),
    }
    # Selected model respects the two user controls, with an independently named result.
    results['当前参数方案'] = _backtest_targets(sig,p,'rotation')
    return results


def rotation_metrics(res:pd.DataFrame, start:str=None,end:str=None)->dict:
    q=res.loc[start:end] if start or end else res
    if len(q)<8: return {'区间收益率':np.nan,'年化收益率':np.nan,'最大回撤':np.nan,'年化波动':np.nan,'累计换手':np.nan,'调仓周数':0}
    nv=q['nav'];rv=nv.pct_change(fill_method=None).dropna();total=nv.iloc[-1]/nv.iloc[0]-1
    return {'区间收益率':total*100,'年化收益率':((1+total)**(52/(len(q)-1))-1)*100,
            '最大回撤':(nv/nv.cummax()-1).min()*100,
            '年化波动':rv.std(ddof=1)*np.sqrt(52)*100,
            '累计换手':q['turnover'].sum(),
            '调仓周数':int((q['turnover']>.08).sum()),
            '平均科技仓位':q['held_next_tech'].mean()*100,
            '平均红利仓位':q['held_next_red'].mean()*100}


def rotation_windows():
    return {
        '2018年下跌':('2018-01-01','2018-12-31'),
        '2020年V形反转':('2020-02-03','2020-08-31'),
        '2022年熊市':('2022-01-01','2022-12-31'),
        '2026年本轮':('2026-09-28','2026-11-20'),
    }
