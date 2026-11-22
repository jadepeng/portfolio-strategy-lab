"""Research-only, transparent approximation of '趋势动物' trend philosophy.

NOT Nick/趋势动物 Pro's proprietary model. Weekly closing prices only.
Rules: seven temperature levels, right-side temperature transitions,
relative strength, add to winners, hard exit, champagne profit reduction,
and timed re-entry. Uses V3's shared close-to-close rebalance engine for
like-for-like costs and performance comparisons.
"""
from dataclasses import dataclass, replace
import numpy as np
import pandas as pd
from v3_engine import V3Config, _portfolio_backtest, _factor_table

STAGES=['冻','寒','凉','平','温','热','沸']

@dataclass(frozen=True)
class TrendAnimalV4Config:
    mom_short: int=12
    mom_long: int=26
    short_ma: int=10
    long_ma: int=30
    strength_buffer: float=.10 # risk-adjusted score distance
    rotate_weeks: int=4
    enter_fraction: float=.35
    max_tech: float=.80
    max_dividend: float=.80
    tech_trail_stop: float=.14
    dividend_trail_stop: float=.10
    cooldown_weeks: int=4
    vol_window: int=12
    cost_rate: float=.0015
    cash_rate: float=0.
    risk_overlay: bool=True
    champagne_overlay: bool=True
    use_dividend_cooldown: bool=True
    breakouts_reenter: bool=True


def _assert_config(c):
    if not (4<=c.mom_short<c.mom_long<=80 and 6<=c.short_ma<c.long_ma<=80):raise ValueError('观察周期无效')
    if not (1<=c.rotate_weeks<=16 and 1<=c.cooldown_weeks<=16):raise ValueError('时间窗口无效')
    if not (0<c.enter_fraction<=.6 and 0<c.max_tech<=1 and 0<c.max_dividend<=1):raise ValueError('仓位上限无效')
    if not (.04<=c.tech_trail_stop<=.4 and .04<=c.dividend_trail_stop<=.4):raise ValueError('移动止盈百分比无效')
    if not (0<=c.cost_rate<=.04 and -0.1<=c.cash_rate<=.2):raise ValueError('成本无效')


def build_temperature_signals(prices:pd.DataFrame,c:TrendAnimalV4Config):
    """Signal at weekly close t sets targets held t->t+1; no future prices."""
    _assert_config(c)
    if not {'tech','red'}.issubset(prices.columns):raise ValueError('需要 tech/red 两列')
    p=prices[['tech','red']].copy().astype(float)
    if len(p)<110 or p.isna().any().any() or (p<=0).any().any() or p.index.has_duplicates:
        raise ValueError('至少110个共同、无缺失、无重复、正数周线价格')
    warm=max(35,c.mom_long,c.long_ma,c.vol_window+1)
    derived={}
    for name in ['tech','red']:
        s=p[name]
        rs=s.pct_change(fill_method=None)
        ma_short=s.rolling(c.short_ma,min_periods=c.short_ma).mean()
        ma_long=s.rolling(c.long_ma,min_periods=c.long_ma).mean()
        m_short=s.pct_change(c.mom_short,fill_method=None)
        m_long=s.pct_change(c.mom_long,fill_method=None)
        v=rs.rolling(c.vol_window,min_periods=c.vol_window).std(ddof=1)*np.sqrt(52)
        # Seven coarse bins, proxy not the creator's private 'temperature' formula.
        bits=(s>ma_short).astype(int)+(s>ma_long).astype(int)+(ma_long>ma_long.shift(4)).astype(int)+(m_short>0).astype(int)+(m_long>0).astype(int)+(s>s.shift(1).rolling(12,min_periods=12).max()).astype(int)
        score=np.select([bits<=0,bits==1,bits==2,bits==3,bits==4,bits==5,bits>=6],[0,1,2,3,4,5,6]).astype(int)
        # percentile of recent vol and 12wk return using *past* observations only
        volhist=v.shift(1).rolling(52,min_periods=35).quantile(.75)
        momhist=m_short.shift(1).rolling(52,min_periods=35).quantile(.85)
        champagne=(score>=6)&(m_short>momhist)&(v>volhist)
        danger=((s<ma_long)&(s.pct_change(4,fill_method=None)<-.08))|(rs<-.075)|((s<ma_short)&(s.pct_change(4,fill_method=None)<-.11))
        strength=(.6*m_short+.4*m_long)/v.clip(lower=.08)
        derived[name]={
          'stage':score,'strength':strength,'vol':v,'ma_short':ma_short,'ma_long':ma_long,
          'champagne':champagne.fillna(False),'danger':danger.fillna(False),
          'r4':s.pct_change(4,fill_method=None),'breakout':s>s.shift(1).rolling(12,min_periods=12).max(),
        }
        p[f'{name}_temp_id']=score
        p[f'{name}_temperature']=[STAGES[x] for x in score]
        p[f'{name}_strength']=strength
        prev_temp=pd.Series(score,index=p.index).shift(1).fillna(0)
        p[f'{name}_season']=np.select([score<=3,(score==4)&(prev_temp>=5),score==4,
             (score==5)&(prev_temp<5),score==5,(score>=6)&champagne,score>=6],
             ['冬季','立秋','谷雨','立夏','夏至','大暑','小暑'],default='未知')
        p[f'{name}_champagne']=champagne.fillna(False)
        p[f'{name}_danger']=danger.fillna(False)
    # Keep independent track of each entry, high watermark and stop-out cooldown.
    tracked={k:{'live':False,'entry':None,'high':None,'cooldown':0,'size':0.,'last_exit':None} for k in ['tech','red']}
    leader='red'
    out=[]
    previous={'tech':0,'red':0}
    # Every new signal gets new target, while actual P&L from V3 engine is lagged.
    for i,(_,row) in enumerate(p.iterrows()):
        if i<warm:
            out.append((.5,.5,0.,'准备期','样本预热：固定50/50'));continue
        reasons=[]
        if i%c.rotate_weeks==0:
            delta=row.tech_strength-row.red_strength
            if np.isfinite(delta):
                if delta>c.strength_buffer:leader='tech'
                elif delta<-c.strength_buffer:leader='red'
        wants={}
        for name,limit,trail in [('tech',c.max_tech,c.tech_trail_stop),('red',c.max_dividend,c.dividend_trail_stop)]:
            st=tracked[name];price=row[name];cur=int(row[f'{name}_temp_id']);old=previous[name]
            # `peak` is updated only after entry; stops are based on observed historical prices.
            if st['live']:st['high']=max(st['high'],price)
            falling=bool(row[f'{name}_danger'])
            broken=st['live'] and st['high']>0 and price/st['high']-1<=-trail
            exit_due=st['live'] and (cur<=3 or (c.risk_overlay and (falling or broken)))
            if exit_due:
                st['live']=False;st['entry']=None;st['high']=None;st['size']=0
                st['last_exit']=i;st['cooldown']=c.cooldown_weeks
                reasons.append(f'{name}危险/温转平/移动止损：退出')
            if st['cooldown']>0:st['cooldown']-=1
            eligible=cur>=5 and row[name]>derived[name]['ma_long'].iloc[i] and derived[name]['r4'].iloc[i]>0
            restart=bool(derived[name]['breakout'].iloc[i])
            allowed=st['cooldown']==0 or (c.breakouts_reenter and restart and cur>=5 and row[name]>derived[name]['ma_long'].iloc[i])
            if (not st['live']) and eligible and allowed and not exit_due:
                st['live']=True;st['entry']=price;st['high']=price
                st['size']=min(limit,c.enter_fraction)
                reasons.append(f'{name}温转热/强趋势：观察仓')
            elif st['live']:
                if cur==4:st['size']=min(st['size'],.25)
                elif cur>=5 and price>st['entry'] and old>=5:
                    # Add only to a winning position, never to a losing one.
                    st['size']=limit
                elif cur>=5 and st['size']>c.enter_fraction and price<st['entry']:
                    st['size']=min(st['size'],c.enter_fraction)
                if c.champagne_overlay and bool(row[f'{name}_champagne']):
                    st['size']=min(st['size'],limit*.65)
                    reasons.append(f'{name}沸+波动扩张：兑现部分肥尾')
            wants[name]=st['size'] if st['live'] else 0.
            previous[name]=cur
        wt=wants['tech'];wr=wants['red']
        # Winner gets higher budget, but loser can retain a smaller live position.
        if wt>0 and wr>0:
            if leader=='tech':wt=min(wt,.75);wr=min(wr,.25)
            else:wr=min(wr,.75);wt=min(wt,.25)
        if wt+wr>1:
            factor=1/(wt+wr);wt*=factor;wr*=factor
        out.append((wt,wr,max(0.,1-wt-wr),leader,'；'.join(reasons) or '趋势延续/等待确认'))
    p[['target_tech','target_red','target_cash','leader','reason']]=pd.DataFrame(out,index=p.index)
    if not np.allclose(p[['target_tech','target_red','target_cash']].sum(axis=1),1):raise AssertionError('仓位预算错误')
    return p


def backtest_v4(prices:pd.DataFrame,c:TrendAnimalV4Config):
    sig=build_temperature_signals(prices,c)
    # Same weekly close-price execution and cost assumptions as V3.
    v3=V3Config(cost_rate=c.cost_rate,cash_rate=c.cash_rate,use_portfolio_brake=False,min_trade_weight=0)
    # Shared backtester uses MA/4w return only for optional portfolio brake; disabled here.
    return _portfolio_backtest(sig,v3,'model')


def get_stage_snapshot(bt:pd.DataFrame)->dict:
    r=bt.iloc[-1]
    return {'date':r.name.date().isoformat(),'tech':r.tech_temperature,'dividend':r.red_temperature,
            'tech_season':r.tech_season,'dividend_season':r.red_season,
            'leader':r.leader,'tech_weight':float(r.target_tech),
            'red_weight':float(r.target_red),'cash_weight':float(r.target_cash),'reason':r.reason}
