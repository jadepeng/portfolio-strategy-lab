"""Point-in-time 2026 weekly opens of five ETFs, Longbridge forward-adjusted,
retrieved 2026-11-21. Zip-like varint deltas of prices rounded to .001.
"""
import base64, numpy as np, pandas as pd
PACKED = {
 'tech': 'njLGARoyG2M8hQGHAfoBD3OxAaoExgMupgGOAZwBjAGCAbEDpAH2BbUBqQLhA9MEE+UCjgSAAbkBzwJlS/IBkQL1AfMBlQTpAZcDdsEB',
 'red':'vhQmLz4eCApGHCBrBSUQBiIwMR81EiQCnQFnQgNAPEwZBS5CFx8zAhI0DVdeGQs=',
 'bond_511260_adj':'iPoPW6IHsAT+AqYC6ATXA+oD6wQTxgMcnALgCOEByAE74gPcAeAHWt0GxgTSA+8BpAIH/APiA9oB8AMXtwG8A2v8AooCjwFI6gWmAgviC7EE',
 'gold_518880_adj':'mpMBzAXuArgOow/YDJADgAf7B8cD6xEQugWSAfQB6QLBAoIChQa2AYkCmQapAekCwwT2A8MDvQKAA80BuAisApwGjwWVAuMBkAHnBPECvgLlAeUFoQPcBr0H',
 'cash_511880_adj':'wpsMDHAFFjJaIhw6KBpqCSQmLCgwNhBEASYqTC4UKjQSNiQwMioQOnIUCx4sLkg='
}
def unvarint(s):
 b=base64.b64decode(s);v=[];p=0;shift=0;last=0
 for val in b:
  p|=(val&127)<<shift
  if val &128:shift+=7
  else:
   d=(-(p+1)//2 if p&1 else p//2)
   last+=d;v.append(last/1000);shift=0;p=0
 if shift:raise ValueError('partial')
 return np.array(v)
def opens_2026(dates):
 out=pd.DataFrame({k:unvarint(v) for k,v in PACKED.items()}, index=dates)
 if len(out)!=45:raise ValueError(f'expected 45 rows, got {len(out)}')
 if round(out.tech.iloc[0],3)!=3.215 or round(out.tech.iloc[-1],3)!=2.413:raise ValueError('open decode failed')
 return out
