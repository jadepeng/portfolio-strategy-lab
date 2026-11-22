"""Shared week-close NAV metrics; preserve frozen research reporting convention."""
import numpy as np

def metrics(ledger):
    n=ledger.nav.astype(float);p=n.cummax();ret=n.pct_change(fill_method=None).dropna()
    years=(n.index[-1]-n.index[0]).days/365.25
    ans={'cumulative_pct':100*(n.iloc[-1]/n.iloc[0]-1),
         'cagr_pct':100*((n.iloc[-1]/n.iloc[0])**(1/years)-1),
         'max_dd_pct':100*min(0,float((n/p-1).min())),
         'vol_pct':100*ret.std(ddof=1)*np.sqrt(52),
         'trade_weeks':int((ledger.turnover>0).sum()),
         'fee_initial_pct':100*ledger[('fee' if 'fee' in ledger else 'cost')].sum()}
    for y in [2021,2022,2023,2024,2025,2026]:
        x=n.loc[str(y)]
        if len(x):
            ind=n.index.get_loc(x.index[0]);p0=n.iloc[ind-1] if ind>0 else n.iloc[0]
            ans[f'r{y}_pct']=100*(x.iloc[-1]/p0-1)
    return {k:round(float(v),3) if isinstance(v,(float,np.floating)) else v for k,v in ans.items()}
