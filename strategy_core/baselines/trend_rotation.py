"""TrendRotation 纪律融合：公开趋势思想的透明代理 + 红利科技相对动量 + 日频风控。

Not the proprietary 趋势动物 Pro model and not Nick's actual trades.
Causal rules: signal at bar t computed using observations <= t; fill at close t+1;
price P&L for t->t+1 uses previous filled holdings; expenses deducted on fills.
Prices are price-only indices, cash 0%, no leverage / shorting.
"""
from __future__ import annotations
from dataclasses import dataclass, replace
from typing import Dict
import numpy as np
import pandas as pd

STAGES = ['冻', '寒', '凉', '平', '温', '热', '沸']

@dataclass(frozen=True)
class TrendRotationConfig:
    frequency: str = 'day'  # day/week
    fast: int = 20
    slow: int = 60
    short_ma: int = 10
    long_ma: int = 60
    vol_window: int = 20
    confirm_days: int = 2
    rotate_every: int = 5
    relative_buffer: float = .025
    max_tech: float = .80
    max_red: float = .85
    trial: float = .25
    tech_exit_shock: float = -.045
    red_exit_shock: float = -.045
    tech_trail: float = .12
    red_trail: float = .09
    cooldown: int = 8
    target_tech_vol: float = .30
    target_red_vol: float = .17
    cost_per_side: float = .0015
    cash_annual_rate: float = 0.0
    use_relative: bool = True
    use_trend: bool = True
    use_shock: bool = True
    use_trailing: bool = True
    use_leader_proxy: bool = True
    use_crowding_proxy: bool = True
    use_vol_control: bool = True
    use_gradual_entry: bool = True
    use_red_early: bool = False
    use_portfolio_brake: bool = False
    min_rebalance: float = .04


def weekly_config():
    return TrendRotationConfig(frequency='week', fast=6, slow=26, short_ma=6, long_ma=30,
        vol_window=12, confirm_days=2, rotate_every=4, relative_buffer=.06,
        tech_exit_shock=-.085, red_exit_shock=-.075, cooldown=2,
        tech_trail=.15, red_trail=.13, target_tech_vol=.29, target_red_vol=.18,
        use_leader_proxy=False, use_crowding_proxy=False)


def validate_input(prices: pd.DataFrame, c: TrendRotationConfig):
    req={'tech','red'}
    if not req.issubset(prices.columns): raise ValueError('必须包含 tech、red 收盘价')
    if c.frequency not in ('day','week'): raise ValueError('frequency 只能是 day/week')
    if not (3<=c.short_ma<c.long_ma and 3<=c.fast<c.slow): raise ValueError('均线/动量周期必须递增')
    if len(prices)<max(c.slow,c.long_ma,c.vol_window)+25: raise ValueError('数据不足完成预热')
    if not prices.index.is_monotonic_increasing or prices.index.has_duplicates:
        raise ValueError('日期必须严格递增且无重复')
    check=['tech','red']+[x for x in ['market','growth_leader_proxy'] if x in prices]
    if prices[check].isna().any().any() or (prices[check]<=0).any().any():
        raise ValueError('价格必须为正数且不得缺失')
    if 'tech_volume_million' in prices and (prices['tech_volume_million']<0).any():
        raise ValueError('成交量不能为负')
    for k in ['max_tech','max_red','trial','relative_buffer','min_rebalance']:
        v=getattr(c,k)
        if not 0<=v<=1: raise ValueError(f'{k} 参数必须在0~1')
    if not (0<=c.cost_per_side<.02 and c.rotate_every>=1 and c.cooldown>=0):
        raise ValueError('成本或周期参数无效')


def factor_table(prices: pd.DataFrame, c: TrendRotationConfig):
    validate_input(prices,c)
    f=prices.copy().astype(float)
    periods=252 if c.frequency=='day' else 52
    for name in ['tech','red']:
        s=f[name]; ret=s.pct_change(fill_method=None)
        f[f'{name}_r1']=ret
        f[f'{name}_mom_fast']=s.pct_change(c.fast,fill_method=None)
        f[f'{name}_mom_slow']=s.pct_change(c.slow,fill_method=None)
        f[f'{name}_momentum']=.6*f[f'{name}_mom_fast']+.4*f[f'{name}_mom_slow']
        mfast=s.rolling(c.short_ma,min_periods=c.short_ma).mean()
        mslow=s.rolling(c.long_ma,min_periods=c.long_ma).mean()
        f[f'{name}_ma_fast']=mfast;f[f'{name}_ma_slow']=mslow
        f[f'{name}_ma_slope']=mslow/mslow.shift(max(2,c.fast//2))-1
        f[f'{name}_vol']=ret.rolling(c.vol_window,min_periods=c.vol_window).std(ddof=1)*np.sqrt(periods)
        f[f'{name}_dd']=s/s.rolling(max(26,c.slow),min_periods=1).max()-1
        breakout=s>s.shift(1).rolling(max(10,c.fast),min_periods=max(10,c.fast)).max()
        # Transparent qualitative stand-in: six binary conditions; not original Nick temperature
        stage=(s>mfast).astype(int)+(s>mslow).astype(int)+(f[f'{name}_ma_slope']>0).astype(int) \
              +(f[f'{name}_mom_fast']>0).astype(int)+(f[f'{name}_mom_slow']>0).astype(int)+breakout.astype(int)
        f[f'{name}_stage']=stage
        f[f'{name}_temperature']=pd.Series(stage,index=f.index).map(lambda v:STAGES[int(v)])
        f[f'{name}_breakout']=breakout
        f[f'{name}_below_ma']=s<mslow
        # rolling percentile of tech volume and positive price stretch: proxy != actual sector crowding
    f['relative']=f['tech_momentum']-f['red_momentum']
    f['red_short_rel']=f['red'].pct_change(max(4,c.fast//2),fill_method=None)-f['tech'].pct_change(max(4,c.fast//2),fill_method=None)
    f['red_short_return']=f['red'].pct_change(max(4,c.fast//2),fill_method=None)
    f['red_short_ma']=f['red'].rolling(max(4,c.short_ma),min_periods=max(4,c.short_ma)).mean()
    if 'growth_leader_proxy' in f:
        leader=f['growth_leader_proxy'];window=max(5,c.short_ma)
        f['growth_leader_weak']=(leader<leader.rolling(window,min_periods=window).mean()) & (leader.pct_change(c.fast,fill_method=None)<0)
    else:f['growth_leader_weak']=False
    if 'tech_volume_million' in f:
        v=f['tech_volume_million']; hist=v.shift(1).rolling(max(c.slow,60),min_periods=max(c.slow,60)).quantile(.85)
        stretch=f['tech']/f['tech_ma_fast']-1
        f['crowding_proxy']=(v>hist)&(stretch>.07)&(f.tech_mom_fast>0)
    else:f['crowding_proxy']=False
    if 'market' in f:
        f['market_weak']=(f.market < f.market.rolling(c.long_ma,min_periods=c.long_ma).mean())
    else:f['market_weak']=False
    return f


def create_signals(prices: pd.DataFrame,c: TrendRotationConfig)->pd.DataFrame:
    f=factor_table(prices,c)
    warm=max(c.slow,c.long_ma,c.vol_window,60 if c.frequency=='day' else 30)
    state={name:{'live':False,'entry':None,'peak':None,'cool':0,'size':0.,'last_exit':None} for name in ['tech','red']}
    leader='red'; records=[]
    brake_peak=1.;brake_draw=0.
    for i,(date,r) in enumerate(f.iterrows()):
        if i<warm:
            records.append((.5,.5,0.,'准备期','样本预热，无交易判断',0));continue
        explanations=[]
        if c.use_relative and i%c.rotate_every==0 and pd.notna(r.relative):
            if r.relative>c.relative_buffer: leader='tech'
            elif r.relative<-c.relative_buffer:leader='red'
        if not c.use_relative: leader='无'
        desired={}
        for name in ['tech','red']:
            st=state[name]
            p=float(r[name]);stage=int(r[f'{name}_stage'])
            fast=float(r[f'{name}_ma_fast']);slow=float(r[f'{name}_ma_slow'])
            mom=float(r[f'{name}_mom_fast']); sh=float(r[f'{name}_r1'])
            if st['live']:st['peak']=max(p,st['peak'])
            if st['cool']>0:st['cool']-=1
            # Hard exits: market shock / confirmed trend break / trailing risk.
            series=f[name]
            below_fast=(series.iloc[i-c.confirm_days+1:i+1]<f[f'{name}_ma_fast'].iloc[i-c.confirm_days+1:i+1]).all()
            stalling=below_fast and mom<0
            rotation_red=(name=='red' and c.use_red_early and bool(r.red_short_rel>.06)
                          and bool(r.red_short_return>0) and p>float(r.red_short_ma)
                          and r.tech_stage<=3)
            soft_break=((stage<=3) and not rotation_red) or (c.use_trend and p<slow and stalling and not rotation_red)
            shock=(c.use_shock and sh <= (c.tech_exit_shock if name=='tech' else c.red_exit_shock))
            trail=(c.use_trailing and st['live'] and p/st['peak']-1<= (-(c.tech_trail if name=='tech' else c.red_trail)))
            hard=bool(shock or trail or (st['live'] and soft_break))
            if hard and st['live']:
                explanations.append(f'{name}风控退出：'+('急跌' if shock else '移动止损' if trail else '趋势失效'))
                st.update(live=False,size=0.,entry=None,peak=None,cool=c.cooldown,last_exit=i)
            # A shock can disallow new entry; re-entry requires a breakout and a short cooldown.
            if shock and not st['live']:
                st['cool']=max(st['cool'],c.cooldown)
            eligible=stage>=5 and mom>0 and ((p>slow) if c.use_trend else True)
            early_red=(name=='red' and ((stage>=4 and p>fast and mom>0 and p>=slow*.98) or rotation_red))
            reenter=(st['cool']==0 or (bool(r[f'{name}_breakout']) and stage>=6))
            if not st['live'] and (eligible or early_red) and reenter and not hard:
                st['live']=True;st['entry']=p;st['peak']=p
                st['size']= c.trial if c.use_gradual_entry else (c.max_tech if name=='tech' else c.max_red)
                explanations.append(f'{name}趋势试仓')
            elif st['live']:
                if (not c.use_trend) or stage>=5 or (name=='red' and rotation_red):
                    if p>=st['entry'] and st['cool']==0:
                        st['size']= min(c.max_tech if name=='tech' else c.max_red, st['size']+(0.20 if c.use_gradual_entry else 1.0))
                if stage<=4 and not (name=='red' and rotation_red):st['size']=min(st['size'],c.trial)
                # partial profit reservation in overheated and retreating market
                if stage==6 and r[f'{name}_mom_fast']>.25 and sh<-.02:
                    st['size']*=.75;explanations.append(f'{name}肥尾回吐，部分兑现')
            desired[name]=st['size'] if st['live'] else 0.
            if c.use_vol_control and desired[name]>0:
                vol=float(r[f'{name}_vol'])
                if np.isfinite(vol) and vol>0:
                    tgt=c.target_tech_vol if name=='tech' else c.target_red_vol
                    desired[name]*=min(1.,max(.25,tgt/vol))
        tech=desired['tech'];red=desired['red']
        # Leadership is used for weights, never to override an invalid asset trend.
        if c.use_relative:
            if leader=='tech':red=min(red,.3)
            elif leader=='red':tech=min(tech,.3)
        if c.use_leader_proxy and bool(r.growth_leader_weak) and r.tech>r.tech_ma_fast:
            tech=min(tech,.50);explanations.append('科创50领先代理转弱：科技上限50%')
        if c.use_crowding_proxy and bool(r.crowding_proxy):
            tech=min(tech,.55);explanations.append('成交量/价格乖离代理预警')
        if c.use_shock:
            if float(r.tech_r1)<=c.tech_exit_shock:tech=0.
            if float(r.red_r1)<=c.red_exit_shock:red=0.
        # Granular portfolio guardrail is intentionally off by default until robustly validated.
        if c.use_portfolio_brake and bool(r.market_weak):
            tech=min(tech,.5);red=min(red,.65)
        if tech+red>1:
            scale=1/(tech+red);tech*=scale;red*=scale
        records.append((tech,red,max(0.,1-tech-red),leader,'；'.join(explanations) or '维持趋势纪律',int(r.tech_stage)))
    f[['target_tech','target_red','target_cash','leader','reason','tech_stage_id']]=pd.DataFrame(records,index=f.index)
    if (f[['target_tech','target_red','target_cash']]<-.00000001).any().any():raise AssertionError('出现负仓位')
    if not np.allclose(f[['target_tech','target_red','target_cash']].sum(axis=1),1):raise AssertionError('仓位预算不守恒')
    return f


def simulate(prices:pd.DataFrame,signals:pd.DataFrame,c:TrendRotationConfig,mode='strategy'):
    """Trade at t+1 close using t signal, so t->t+1 P&L uses holdings filled <=t."""
    validate_input(prices,c)
    f=signals.copy()
    if mode=='strategy':
        targets=f[['target_tech','target_red','target_cash']].to_numpy(float)
    elif mode=='tech':targets=np.tile([1.,0.,0.],(len(f),1))
    elif mode=='red':targets=np.tile([0.,1.,0.],(len(f),1))
    elif mode=='half':targets=np.tile([.5,.5,0.],(len(f),1))
    else:raise ValueError('bad mode')
    holdings=targets[0].copy()
    days=252 if c.frequency=='day' else 52
    cash_growth=(1+c.cash_annual_rate)**(1/days)
    report=[]
    for i,(d,r) in enumerate(f.iterrows()):
        if i:
            old=f.iloc[i-1]
            holdings*=np.array([r.tech/old.tech,r.red/old.red,cash_growth])
        gross=holdings.sum()
        # At i, fill signal seen at close of i-1, no forward-return leakage.
        if i:
            desired=targets[i-1]
            if mode=='half' and i%max(1,(20 if c.frequency=='day' else 4))!=0:
                desired=holdings/gross
            if mode in ('tech','red'):desired=holdings/gross
            turnover=float(np.abs(holdings/gross-desired).sum())
            if mode=='strategy' and turnover<c.min_rebalance:desired=holdings/gross;turnover=0.
        else:desired=targets[0];turnover=0.
        fee=gross*c.cost_per_side*turnover
        nav=gross-fee
        holdings=nav*desired
        report.append((nav,turnover,fee,desired[0],desired[1],desired[2]))
    f[['nav','turnover','fee','held_tech','held_red','held_cash']]=pd.DataFrame(report,index=f.index)
    return f


def metrics(df:pd.DataFrame,start=None,end=None):
    q=df.loc[start:end] if start or end else df
    if len(q)<3:return None
    nv=q.nav;rv=nv.pct_change(fill_method=None).dropna();gain=nv.iloc[-1]/nv.iloc[0]
    years=(q.index[-1]-q.index[0]).days/365.25
    return {'累计收益%':round((gain-1)*100,2),
            '年化收益%':round((gain**(1/years)-1)*100,2) if years>0 else 0,
            '最大回撤%':round((nv/nv.cummax()-1).min()*100,2),
            '年化波动%':round(rv.std(ddof=1)*np.sqrt(252 if (q.index.to_series().diff().dropna().dt.days.median()<5) else 52)*100,2),
            '显著调仓次数':int((q.turnover>.08).sum()),
            '累计双边换手':round(float(q.turnover.sum()),1),
            '平均科技仓位%':round(q.held_tech.mean()*100,1),
            '平均红利仓位%':round(q.held_red.mean()*100,1),
            '平均现金仓位%':round(q.held_cash.mean()*100,1)}


def compare(prices:pd.DataFrame,c:TrendRotationConfig):
    f=create_signals(prices,c)
    baseline={
      '科技买入持有':simulate(prices,f,c,'tech'),
      '红利买入持有':simulate(prices,f,c,'red'),
      '50/50四周再平衡':simulate(prices,f,c,'half'),
      'TrendRotation完整版':simulate(prices,f,c,'strategy'),
      '去掉领先代理':simulate(prices,create_signals(prices,replace(c,use_leader_proxy=False)),c),
      '去掉拥挤代理':simulate(prices,create_signals(prices,replace(c,use_crowding_proxy=False)),c),
      '去掉急跌与移动止损':simulate(prices,create_signals(prices,replace(c,use_shock=False,use_trailing=False)),c),
      '不分步入场':simulate(prices,create_signals(prices,replace(c,use_gradual_entry=False)),c),
      '红利早试仓实验':simulate(prices,create_signals(prices,replace(c,use_red_early=True)),c),
      '去掉波动率控制':simulate(prices,create_signals(prices,replace(c,use_vol_control=False)),c),
      '去掉相对动量':simulate(prices,create_signals(prices,replace(c,use_relative=False)),c),
    }
    return baseline
