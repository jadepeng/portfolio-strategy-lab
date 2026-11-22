"""V3 defensive tech/dividend tactical allocation, research engine.

Causality: weekly closing observations at time t set targets held during t+1.
No forward fill across missing input weekly prices. No leverage, no shorting.
Market/bond auxiliary prices, if supplied, are *optional risk proxies* and
must not be interpreted as breadth or actual Treasury yield.

Conventions: 52 observations/year; turnover = sum absolute weight changes,
fee=cost per unit traded notional (the sum includes both sells and buys).
"""
from dataclasses import dataclass, replace
import numpy as np
import pandas as pd


@dataclass(frozen=True)
class V3Config:
    fast_weeks: int = 12
    slow_weeks: int = 26
    fast_share: float = .60
    trend_weeks: int = 30
    switch_buffer: float = .05
    rebalance_weeks: int = 4
    cost_rate: float = .0015
    cash_rate: float = 0.
    tech_leader_weight: float = .75
    dividend_leader_weight: float = .80
    max_tech_weight: float = .85
    min_trade_weight: float = .04
    use_relative_momentum: bool = True
    use_absolute_trend: bool = True
    use_risk_gate: bool = True
    use_volatility: bool = True
    use_shock: bool = True
    use_broad_market: bool = False
    use_us_stress: bool = False
    use_bond_proxy: bool = False
    use_breakout_reentry: bool = True
    use_trend_slope: bool = False
    use_relative_percentile: bool = False
    use_portfolio_brake: bool = False
    tech_vol_target: float = .24
    dividend_vol_target: float = .18
    vol_window: int = 12
    price_drawdown_window: int = 26
    shock_4w: float = -.10
    shock_1w: float = -.07
    ratio_tilt: bool = False
    ratio_low: float = .40
    ratio_high: float = .60


def _validate(config: V3Config):
    c=config
    if not (4<=c.fast_weeks<c.slow_weeks<=104): raise ValueError('动量窗口必须满足4≤短期<长期≤104周')
    if not (10<=c.trend_weeks<=104 and 1<=c.rebalance_weeks<=26): raise ValueError('趋势/调仓周数无效')
    if not (4<=c.vol_window<=104 and c.price_drawdown_window>=10): raise ValueError('波动/回撤窗口无效')
    for k in ('fast_share','tech_leader_weight','dividend_leader_weight','max_tech_weight','min_trade_weight'):
        if not 0<=getattr(c,k)<=1: raise ValueError(f'{k}必须在0~1')
    if not 0<=c.switch_buffer<=.5 or not 0<=c.cost_rate<=.1: raise ValueError('缓冲或交易成本无效')
    if not 0<c.tech_vol_target<=1 or not 0<c.dividend_vol_target<=1: raise ValueError('年化目标波动必须>0')
    if c.cash_rate < -.2 or c.cash_rate > .2: raise ValueError('现金年利率范围无效')


def align_weekly(tech: pd.Series, dividend: pd.Series, market:pd.Series=None,
                 us_tech:pd.Series=None, bond:pd.Series=None)->pd.DataFrame:
    def norm(s, name):
        z=pd.Series(s,dtype=float).rename(name)
        z.index=pd.to_datetime(z.index).tz_localize(None) if getattr(z.index,'tz',None) else pd.to_datetime(z.index)
        if z.index.has_duplicates: raise ValueError(f'{name}: 重复周时间戳')
        if z.isna().any() or (z<=0).any(): raise ValueError(f'{name}: 缺失或非正价格')
        return z.sort_index()
    df=pd.concat([norm(tech,'tech'),norm(dividend,'dividend')],axis=1,join='inner')
    if len(df)<100: raise ValueError('必须有至少100个科技与红利共同周收盘观测')
    if df.isna().any().any(): raise ValueError('价格序列不完整')
    for s,key in ((market,'market'),(us_tech,'us_tech'),(bond,'bond')):
        if s is not None:
            extra=norm(s,key)
            # Only dates common to all activated series are eligible: no silent forward fill.
            df=df.join(extra,how='inner')
    if len(df)<100: raise ValueError('可用共同交易周不足100个，请核对数据日期')
    return df.astype(float)


def _factor_table(df:pd.DataFrame,c:V3Config):
    out=df.copy()
    for name in ('tech','red'):
        s=out[name]
        out[f'{name}_mom']=c.fast_share*s.pct_change(c.fast_weeks,fill_method=None)+(1-c.fast_share)*s.pct_change(c.slow_weeks,fill_method=None)
        out[f'{name}_ma']=s.rolling(c.trend_weeks,min_periods=c.trend_weeks).mean()
        out[f'{name}_ma10']=s.rolling(10,min_periods=10).mean()
        out[f'{name}_vol']=s.pct_change(fill_method=None).rolling(c.vol_window,min_periods=c.vol_window).std(ddof=1)*np.sqrt(52)
        out[f'{name}_dd']=s/s.rolling(c.price_drawdown_window,min_periods=c.price_drawdown_window).max()-1
        out[f'{name}_r4']=s.pct_change(4,fill_method=None)
        out[f'{name}_r1']=s.pct_change(fill_method=None)
        out[f'{name}_breakout']=s>s.shift(1).rolling(12,min_periods=12).max()
        out[f'{name}_slope']=out[f'{name}_ma'].pct_change(4,fill_method=None)
    out['relative_strength']=out['tech_mom']-out['red_mom']
    out['ratio']=out['tech']/out['red']
    # Lagged 104-week rank: today's ratio is compared only against PRIOR ratios.
    out['ratio_pct104']=out['ratio'].rolling(104,min_periods=104).apply(lambda x: np.mean(x[:-1]<=x[-1]) if len(x)>1 else np.nan, raw=True)
    if 'market' in out:
        out['market_ma']=out['market'].rolling(c.trend_weeks,min_periods=c.trend_weeks).mean()
    if 'us_tech' in out:
        out['us_tech_ma']=out['us_tech'].rolling(c.trend_weeks,min_periods=c.trend_weeks).mean()
        out['us_tech_r4']=out['us_tech'].pct_change(4,fill_method=None)
    if 'bond' in out:
        out['bond_r8']=out['bond'].pct_change(8,fill_method=None)
    return out


def _target_signals(factors:pd.DataFrame,c:V3Config):
    f=factors.copy()
    warm=max(c.fast_weeks,c.slow_weeks,c.trend_weeks,c.vol_window,c.price_drawdown_window,30)
    leader='red'; tgt=np.array([.5,.5,0.])
    rows=[]
    for i,(idx,r) in enumerate(f.iterrows()):
        why=[];caps=[];confidence='准备期'
        if i>=warm:
            tm=float(r.tech_mom);rm=float(r.red_mom);relative=tm-rm
            good_t=(tm>0 and r.tech>r.tech_ma) if c.use_absolute_trend else True
            good_r=(rm>0 and r.red>r.red_ma) if c.use_absolute_trend else True
            if c.use_trend_slope:
                good_t=bool(good_t and r.tech_slope>0 or (good_t and c.use_breakout_reentry and r.tech_breakout))
                good_r=bool(good_r and r.red_slope>0 or (good_r and r.red_breakout))
            # Rotation adjustments at regular intervals; emergency *reductions* can occur weekly.
            if i%c.rebalance_weeks==0:
                if c.use_relative_momentum:
                    if relative>c.switch_buffer:leader='tech'
                    elif relative<-c.switch_buffer:leader='red'
                else:
                    leader='tech' if tm>=rm else 'red'
                if good_t and good_r:
                    t=(c.tech_leader_weight if leader=='tech' else 1-c.dividend_leader_weight)
                    red=1-t
                    if c.use_relative_percentile and np.isfinite(r.ratio_pct104):
                        if r.ratio_pct104<.2:t=min(1.,t+.10);red=1-t
                        elif r.ratio_pct104>.8:red=min(1.,red+.10);t=1-red
                    if c.ratio_tilt:
                        if r.ratio<c.ratio_low:t=max(t,.65);red=1-t
                        if r.ratio>c.ratio_high:red=max(red,.70);t=1-red
                elif good_t: t=min(.8,c.max_tech_weight);red=0.
                elif good_r: t=0.;red=1.
                else:t=red=0.
                tgt=np.array([t,red,max(0,1-t-red)])
                why.append('月度相对动量与趋势轮动')
            # Shock-based early reentry: only after breakout and both returns positive.
            if c.use_breakout_reentry and i%c.rebalance_weeks!=0 and good_t and r.tech_breakout and r.tech_r4>0 and tgt[0]<.4:
                # No full-risk immediate buyback. Excess comes from cash, not automatically from dividend.
                buy=min(.4-tgt[0],tgt[2])
                if buy>.04:
                    tgt[0]+=buy;tgt[2]-=buy;why.append('科技突破：提前试探性回补')
            # Calculate risk caps; red only receives unspent cash if its *own* trend is good and next rebalance.
            tcap=c.max_tech_weight;rcap=1.
            if c.use_risk_gate:
                if r.tech_dd<-.08 or r.tech<r.tech_ma10:tcap=min(tcap,.75);caps.append('科技回撤/10周线预警')
                if r.tech_dd<-.12 or r.tech<r.tech_ma:tcap=min(tcap,.5);caps.append('科技30周线防御')
                if r.tech_dd<-.20 or (r.tech_dd<-.16 and r.tech<r.tech_ma):tcap=min(tcap,.25);caps.append('科技极端回撤')
                if r.red_dd<-.14 or r.red<r.red_ma:rcap=min(rcap,.65);caps.append('红利趋势失守')
                if r.red_dd<-.22:rcap=min(rcap,.35);caps.append('红利深跌')
            if c.use_shock:
                if r.tech_r4<=c.shock_4w or r.tech_r1<=c.shock_1w:
                    tcap=min(tcap,.20);caps.append('科技4周急跌或单周冲击')
                if r.red_r4<=-.09 or r.red_r1<=-.06:
                    rcap=min(rcap,.5);caps.append('红利急跌')
            if c.use_broad_market and 'market_ma' in r and r.market<r.market_ma:
                tcap=min(tcap,.55);caps.append('沪深300弱势（非市场广度）')
            if c.use_us_stress and 'us_tech_ma' in r and r.us_tech<r.us_tech_ma and r.us_tech_r4<0:
                tcap=min(tcap,.40);caps.append('美股科技同步走弱')
            if c.use_bond_proxy and 'bond_r8' in r and 'us_tech_ma' in r:
                if r.bond_r8<-.05 and r.us_tech<r.us_tech_ma:
                    tcap=min(tcap,.30);caps.append('TLT与美股科技同步走弱（非收益率）')
            if c.use_volatility:
                # Volatility-scaled allocation limits; never lever up for low volatility.
                tcap=min(tcap,c.max_tech_weight*min(1.,c.tech_vol_target/max(1e-6,float(r.tech_vol))))
                rcap=min(rcap,min(1.,c.dividend_vol_target/max(1e-6,float(r.red_vol))))
                if r.tech_vol>c.tech_vol_target: caps.append('科技波动高于预算')
                if r.red_vol>c.dividend_vol_target: caps.append('红利波动高于预算')
            if not good_t: tcap=0.
            if not good_r: rcap=0.
            tnext=min(float(tgt[0]),tcap);rnext=min(float(tgt[1]),rcap)
            # Trigger reductions immediately, but delay small risk-cap increases to routine rebalance.
            # Allow normal rebalance/re-entry to replenish as trend recovers.
            if tnext<tgt[0]-.001 or rnext<tgt[1]-.001:
                tgt=np.array([tnext,rnext,max(0.,1.-tnext-rnext)])
                why+=caps
            elif caps and i%c.rebalance_weeks==0:
                why+=caps
            confidence='强势' if good_t and good_r else ('科技' if good_t else '红利' if good_r else '双弱')
        if not np.isclose(tgt.sum(),1.,atol=1e-8):raise ValueError('仓位和不为1')
        if min(tgt)<-1e-10:raise ValueError('负仓位')
        rows.append((float(tgt[0]),float(tgt[1]),float(tgt[2]),leader,confidence,'；'.join(why) or '等待确认/维持配置'))
    f[['target_tech','target_red','target_cash','leader','regime','reason']]=pd.DataFrame(rows,index=f.index)
    return f


def _portfolio_backtest(f:pd.DataFrame,c:V3Config,method='model'):
    if method=='model':targets=f[['target_tech','target_red','target_cash']].to_numpy(float)
    elif method=='hold_tech':targets=np.tile([1.,0.,0.],(len(f),1))
    elif method=='hold_red':targets=np.tile([0.,1.,0.],(len(f),1))
    elif method=='mix':targets=np.tile([.5,.5,0.],(len(f),1))
    else:raise ValueError('未知基准')
    prev=f.iloc[0];holdings=targets[0].astype(float).copy();out=[];portfolio_peak=1.;level=0
    lowest_triggered=0.;healthy_weeks=0
    for i,(date,r) in enumerate(f.iterrows()):
        if i:
            holdings[0]*=r.tech/prev.tech
            holdings[1]*=r.red/prev.red
            holdings[2]*=(1+c.cash_rate)**(1/52)
        gross=float(holdings.sum());portfolio_peak=max(portfolio_peak,gross)
        proposed=targets[i].copy()
        if c.use_portfolio_brake and method=='model' and i:
            drawdown=gross/portfolio_peak-1
            alert_level=3 if drawdown<=-.15 else 2 if drawdown<=-.10 else 1 if drawdown<=-.06 else 0
            # Escalate on NEW material portfolio deterioration, not every week that the old NAV peak remains far away.
            if alert_level>level and drawdown<lowest_triggered-.015:
                level=alert_level; lowest_triggered=drawdown;healthy_weeks=0
            # Recover one risk tier after 3 consecutive weekly positive underlying trend confirmations.
            healthy=bool((pd.notna(r.tech_ma) and r.tech>r.tech_ma and r.tech_r4>0) or
                         (pd.notna(r.red_ma) and r.red>r.red_ma and r.red_r4>0))
            healthy_weeks=healthy_weeks+1 if healthy else 0
            if healthy_weeks>=3 and level>0:
                level-=1;healthy_weeks=0
                # Keep same crash from re-triggering each week without a new 1.5pp worsening.
            totalcap={0:1.,1:.75,2:.5,3:.25}[level]
            total=float(proposed[:2].sum())
            if total>totalcap:proposed[:2]*=totalcap/total;proposed[2]=1.-proposed[:2].sum()
        drift=holdings/gross
        change=float(np.abs(proposed-drift).sum())
        if method=='model' and i>0 and change/2 < c.min_trade_weight:
            # Weight bands: avoid noise rebalancing; no ability to delay larger protective sells.
            proposed=drift.copy();change=0.
        fee=gross*c.cost_rate*change if i else 0.
        nav=gross-fee
        holdings=proposed*nav
        out.append((nav,change,fee,proposed[0],proposed[1],proposed[2],level))
        prev=r
    ret=f.copy()
    ret[['nav','turnover','fee','hold_tech','hold_red','hold_cash','portfolio_brake']]=out
    return ret


def run_v3(prices:pd.DataFrame,c:V3Config,compare:bool=True):
    _validate(c)
    needed=['tech','red']
    if not set(needed).issubset(prices.columns):raise ValueError('必须含tech/red')
    if prices[needed].isna().any().any() or (prices[needed]<=0).any().any():raise ValueError('无效行情')
    if c.use_broad_market and 'market' not in prices:raise ValueError('开启沪深300因子但无market数据')
    if c.use_us_stress and 'us_tech' not in prices:raise ValueError('开启美股风险因子但无us_tech数据')
    if c.use_bond_proxy and not {'bond','us_tech'}.issubset(prices.columns):raise ValueError('TLT因子需要bond和us_tech数据')
    factors=_factor_table(prices,c)
    model=_portfolio_backtest(_target_signals(factors,c),c)
    if not compare:return {'V3多因子防御':model}
    modes={'科技持有':_portfolio_backtest(factors,c,'hold_tech'),
           '红利持有':_portfolio_backtest(factors,c,'hold_red'),
           '50/50再平衡':_portfolio_backtest(factors,c,'mix')}
    modes.update({'V3多因子防御':model})
    # Pre-specified ablations: show incremental contribution, not cherry-picked optimization.
    ablations={
       '仅动量+绝对趋势':replace(c,use_risk_gate=False,use_volatility=False,use_shock=False,use_broad_market=False,use_us_stress=False,use_bond_proxy=False,use_breakout_reentry=False,use_portfolio_brake=False),
       '动量+破位':replace(c,use_volatility=False,use_shock=False,use_broad_market=False,use_us_stress=False,use_bond_proxy=False,use_breakout_reentry=False,use_portfolio_brake=False),
       '动量+破位+波动':replace(c,use_shock=False,use_broad_market=False,use_us_stress=False,use_bond_proxy=False,use_breakout_reentry=False,use_portfolio_brake=False)
    }
    for name,params in ablations.items():modes[name]=_portfolio_backtest(_target_signals(_factor_table(prices,params),params),params)
    return modes


def metrics(x:pd.DataFrame,start=None,end=None):
    z=x.loc[start:end] if start is not None or end is not None else x
    if len(z)<8:return None
    nav=z.nav;ret=nav.pct_change(fill_method=None).dropna();gross=float(nav.iloc[-1]/nav.iloc[0]);years=(len(z)-1)/52
    h=nav.cummax();dd=nav/h-1
    return {'累计收益%':round((gross-1)*100,2),'年化收益%':round((gross**(1/years)-1)*100,2),
            '最大回撤%':round(dd.min()*100,2),'波动率%':round(ret.std(ddof=1)*np.sqrt(52)*100,2),
            '卡玛比':round((gross**(1/years)-1)/abs(dd.min()),2) if dd.min()<0 else np.nan,
            '平均科技%':round(z.hold_tech.mean()*100,2), '平均红利%':round(z.hold_red.mean()*100,2),
            '平均现金%':round(z.hold_cash.mean()*100,2),
            '总换手':round(z.turnover.sum(),2),'调仓周数':int((z.turnover>.08).sum()),
            '费用/初始净值%':round(float(z.fee.sum()/nav.iloc[0])*100,2)}


def sensitivity_grid(prices:pd.DataFrame,base:V3Config,train_end='2022-12-26',test_start='2023-01-02'):
    # Small, predeclared neighbourhood. We do NOT select based on test performance.
    scenarios=[('基准',base),('快动量8周',replace(base,fast_weeks=8)),
      ('慢动量16周',replace(base,fast_weeks=16)),('趋势20周',replace(base,trend_weeks=20)),
      ('趋势40周',replace(base,trend_weeks=40)),('科技低波动预算18%',replace(base,tech_vol_target=.18)),
      ('科技高波动预算30%',replace(base,tech_vol_target=.30)),
      ('较少换手：6周调仓',replace(base,rebalance_weeks=6))]
    records=[]
    for label,cfg in scenarios:
        res=run_v3(prices,cfg,compare=False)['V3多因子防御']
        tr=metrics(res,None,train_end);ts=metrics(res,test_start,None)
        records.append({'版本':label,'训练年化%':tr['年化收益%'] if tr else np.nan,
                        '训练回撤%':tr['最大回撤%'] if tr else np.nan,
                        '样本外年化%':ts['年化收益%'] if ts else np.nan,
                        '样本外回撤%':ts['最大回撤%'] if ts else np.nan,
                        '样本外累计收益%':ts['累计收益%'] if ts else np.nan})
    return pd.DataFrame(records)
