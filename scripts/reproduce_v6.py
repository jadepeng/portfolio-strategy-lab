"""Reproduce V6 vs V5, adjusted-ETF cross-check, stress windows and fee sensitivity."""
from pathlib import Path
import sys,json,hashlib
from dataclasses import asdict,replace
import pandas as pd
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from history.strategies.fusion_v5 import FusionConfig,weekly_config,create_signals,simulate,metrics
from history.strategies.fusion_v6 import v6_base,V6Config,signal_v6,simulate_v6

sources={
 'weekly_index':('market_data/longbridge_2016_2026_tech_dividend_prices.csv','week_start','week'),
 'weekly_etf_forward_adjusted':('market_data/longbridge_etf_fwd_2020_2026_v6.csv','week_start','week'),
 'daily_2026_index':('market_data/longbridge_daily_2026.csv','date','day')}
periods={'weekly_index':{'all':(None,None),'2016_2022':('2016-01-11','2022-12-31'),
          '2023_2026':('2023-01-01','2026-11-20'),'2018':('2018-01-01','2018-12-31'),
          '2020_V':('2020-02-01','2020-08-31'),'2022':('2022-01-01','2022-12-31'),
          '2026':('2026-09-28','2026-11-20')},
         'weekly_etf_forward_adjusted':{'all':(None,None),'2023_2026':('2023-01-01','2026-11-20'),
          '2022':('2022-01-01','2022-12-31'),'2026':('2026-09-28','2026-11-20')},
         'daily_2026_index':{'all':(None,None),'2026_may':('2026-05-01','2026-11-20'),
          '2026_jun30':('2026-06-30','2026-11-20')}}

manifest={'version':'V6','asof':'2026-11-20 close','verification_date':'2026-11-21',
    'method':'signal computed at close t, fill close t+1, no leverage, no shorting, cash 0%',
    'sources':'Longbridge historical index closes and forward-adjusted ETF closes',
    'limits':['20% permanent dividend core is a portfolio allocation choice, not evidence of timing alpha',
             'all results retrospectively designed and examined through 2026: neither blind out-of-sample nor forward returns',
             'ETF series only 2020-2026; price indices exclude dividend reinvestment',
             'no ETF slippage/fund tracking/premium, FX, taxes, actual liquidity or risk-free interest'],
    'methods':{},'data':{}}
for name,(file,ix,freq) in sources.items():
    path=ROOT/file;df=pd.read_csv(path,parse_dates=[ix]).set_index(ix)
    old_c=weekly_config() if freq=='week' else FusionConfig()
    c=v6_base(freq)
    model=V6Config()
    r_old=simulate(df,create_signals(df,old_c),old_c)
    r_new=simulate_v6(df,c,model)
    r_no_core=simulate_v6(df,c,replace(model,red_core=0.0))
    r_red=simulate(df,create_signals(df,c),c,'red')
    r_tech=simulate(df,create_signals(df,c),c,'tech')
    r_half=simulate(df,create_signals(df,c),c,'half')
    allruns={'V5_original':r_old,'V6_default':r_new,'V6_without_core':r_no_core,
             'dividend_hold':r_red,'tech_hold':r_tech,'50_50_rebalance':r_half}
    outpath=ROOT/f'market_data/v6_{name}_ledger.csv'
    r_new.to_csv(outpath,index_label=ix,encoding='utf-8-sig')
    manifest['data'][name]={'bars':len(df),'first':str(df.index[0].date()),'last':str(df.index[-1].date()),
       'input_csv':file,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'ledger':outpath.relative_to(ROOT).as_posix(),
       'windows':{period:{n:metrics(r,*lim) for n,r in allruns.items()}
                  for period,lim in periods[name].items()},
       'core_sensitivity':{str(v):metrics(simulate_v6(df,c,replace(model,red_core=v))) for v in [0,.1,.2,.3,.4]},
       'cost_sensitivity':{str(bp):metrics(simulate_v6(df,replace(c,cost_per_side=bp/10000),model))
                           for bp in [5,15,30,50]},
       'latest_target':{k:float(r_new.iloc[-1][k]) for k in ['target_tech','target_red','target_cash']}}
    manifest['methods'][name]={'v6_base':asdict(c),'v6_overlay':asdict(model)}
    print(name,'V5',metrics(r_old)['累计收益%'],metrics(r_old)['最大回撤%'],
          'V6',metrics(r_new)['累计收益%'],metrics(r_new)['最大回撤%'])
file=ROOT/'history/verification/verified_fusion_v6_results.json';file.write_text(json.dumps(manifest,ensure_ascii=False,indent=2,
                     default=lambda x:x.item() if hasattr(x,'item') else x),encoding='utf-8')
print('saved',file)
