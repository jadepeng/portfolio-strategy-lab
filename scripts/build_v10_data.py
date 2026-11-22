"""Decode lossless-to-0.001 ETF adjusted closes pulled from Longbridge on 2026-11-21.
Data: 511260.SH / 518880.SH, period=week, count=1000, forward_adjust=true.
Both 313 weekly opening-date labels align with existing V7 ETF snapshot.
The string captures rounded-to-.001 provider values, not a re-fetched source.
"""
from pathlib import Path
import base64
import pandas as pd
ROOT=Path(__file__).resolve().parents[1]
BOND='iNwMwgWoArYC7QjnA6QB5AXlAaMDogaCAqoFlAmsAYcNhgHyAQsltALEAekDpAO+Ai2ODPgDyQJX8gL2CimSAakEVaYDzgbIC5wFjgTSBYIHnwiABY0DlAaBBM8BlAGyA8cDuQSDA9gElAnfA6YEogetA7gB5AHCBOQEEJYFiAyVBKsEMWGjB5oGpAN06gK6Bu0BrQT0AZ8BvgUW9AnZBcAB8QEf4wFgygaUAe4JtAXtA54RsQXECL0DvQfQAssL4AlmqAa9Am2/DNAF4QWHA+AC8gKmA1O7AscCoAUuYkGFAZ4HvAOlAkrQAtwCqgOsBcAGkAN8lQHGA5wG7QKwA/gDXkrSBs8C4AHyAYALTqUEjwf2AZ8FbBwDmAL4BqYBwAH/AtQCGY4GjAPIB4YIvwS+BMgCxAncAcAF9ALsCtsI7AO8AdIC7gTIBskGqQLqAh2MBXzkBYoCoAPaCa8E7gLgAoQNvAjPBpEBiAWbBOwJ/gi0BasW3weWDo4F7wbMBdoFqgWSAsYIrgriHfYG9ALOEO8GcukC+ge2Aa0FjQzXAukIkQrwA7wC5g/sCMgCrwKEB1y/CLYCnQGuA+gEsAOdAZYC9QR0kw2MCPoCyQeBCZ4C7gGbA/wBzwOGA3rGCacG+gvJAVnAAe8FmwHKAxqmAf0GQOgFsgWwA9QDzgOBBLwE+wO5AZIC0AGGArIIVikv3AN6qgh+0QekBqABgQGcAzqKA64DjgLeAxrZAbwDM8QC/AF5MvYFqAI5zAztA5IC'
GOLD='4j0GddAB3QFd9QFEHcYBHgU4uQEqN8EBlgHzASmFAlSCARNCJhgyVbgBLJoBH4cBftMCARBgPjVIcY8BiAEuCjWHAQtVWFQbCx7EARDLAXsBgAEIFGV0LJsBZuYBXkTAAasBcGsBuAEOESdDH1pPFxJdVeUBXTjGAVIKQ0qPAXZxVBo8U2IfqAIADihtEUJMG26UATubAQE6LiKSApoBDF5MmwEU6gFjRSeCAQAfBQ0UOoIBYQUcDDZSXhga+QE9gAMEFm8kkQG0AQQBSiogIwUjbCE/SqwDJF7oAtIBogRBmwJVpgFewQEwcP8B0gGdAagBggEplQG2AtkBcmYkCbwBaFoBQJwDamqxAq0DjgWtASfEAdkBaqgBmAFEngEftAPyAkn3AaQBpgLKAcgCuALmAuwEaXV4wwWqBKkB4AHWAa8C2wGiAT02DHG4AtUBLdgBwgTmAm+MBOYCjgSMD9MJtwIB3gSXBN4DigG2AawBogW5BcYEkgTwC5AGlwq0A8wFd40Bkw6jBpwFoAJylQOnApQDiwW5AX+hAqEJ8gOLCLQEhQLLA5QBYJYHbPQGrgHtBLkDhAG5A7UCH33FBacBlALVBL8K'
def decode(encoded):
    v=base64.b64decode(encoded); nums=[];n=0;shift=0;prior=0
    for c in v:
        n|=(c&127)<<shift
        if c&128:shift+=7;continue
        d=-(n//2)-1 if n&1 else n//2
        prior+=d
        nums.append(prior/1000.)
        n=shift=0
    if shift or n:raise ValueError('bad varint')
    return nums
if __name__=='__main__':
    path=ROOT/'market_data/longbridge_etf_fwd_2020_2026_v7_money.csv'
    data=pd.read_csv(path)
    bond=decode(BOND); gold=decode(GOLD)
    assert len(bond)==len(gold)==len(data)==313,(len(bond),len(gold),len(data))
    assert (round(bond[0],3),round(bond[-1],3))==(104.196,136.023)
    assert (round(gold[0],3),round(gold[-1],3))==(3.953,7.282)
    data['bond_511260_adj']=bond; data['gold_518880_adj']=gold
    out=ROOT/'market_data/longbridge_5asset_fwd_2020_2026_v10.csv'
    data.to_csv(out,index=False,float_format='%.3f')
    print(out, len(data))
