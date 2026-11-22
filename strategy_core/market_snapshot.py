"""Longbridge unadjusted CSI Broad Market and component-index turnover snapshots.
2023-12-28..2026-11-20, common 699 daily sessions, aggregated by week.
Public index proxy, not constituent-level realized congestion or whole A-share tape.
Averages are daily turnover in 1e8 CNY; the original retrieval rounded to 1e8.
"""
from __future__ import annotations
from pathlib import Path
import base64,struct
import pandas as pd

W_MARKET_PRICE='EUYQxRCGEBYQLw62D5EQXBC/EL4RFBETENcRAhCLEIIQ0RECEUkRSRDOEMAQYRBdEBcPyA+VD8UPyw9YD3QPMQ8vDt8PDw6uDmEOkBDFEmMRohInEp8ScxO5EvASihLuE10TUxMcEwARqxF8EhwSQhIZErwTCxNtEu8TZROtE0QTBRLKEfsSCRI8EjsSpRLFEqQSnRLjEs0SkxMyE2kTthP2FGUUKRSLFSQV9BZcFgsWfxZ1FoEW8xbgFhQW0xbrFxAW8BXHFmUWkRacFpIXMhcfGEoYZxjVGHMYEhhUGP4YbBhNF04XIhafF8oYbhh/GMYZhBlRGU8Y4RiUGDQZYxkBGQQYRxYoFjcWMRdaF2cXDhcvFsYWiRbmFpwWEBXCFKkUhhNZE/sTwhKJ'
W_MARKET_TURN='IHkb8Bl8GkgeUhxeJL0iBClfJVwnTSjqI6skpR/KI3ggXSroJEogOyAYG7cc0RwOGvcZXxdFGoUZFhgWG4wYmRQmFKEXEhZOE8kVdSibY1lgwD8xR7hOoVpZUbk/6DkUQHxItTpvNNMzCSuSLckvMCsdPeBC2ElETCtAlD7bOsIvzCsaPQoplCsbKaczJS9YK8EpAC1fMz0uAjh/Nq04yjq9RmVFEkCMUApikHHTY2JYuWA7WF5TZGN2U6dERFhATGBNvEabQelAEUmpQxFLCVFbbTqE5mtRdYBcPlDXXY5lW1+xVHdQtUhrUedZ2mOrZAR3/X8xco15Xm9taYx4FIdEgr5uzGSTX1lT8ls9WIFUzUq9SidHG0OZR1A5B0NrOu46nTuWRgM9zzTE'
W_TECH_TURN='B9kGaQXqBcYGoQYSCA4IigrbCW8KKAsJCWYJJgdRCA8IGAmzCP4HoggMB18HzQdKB2oG5QY5BsYGYwYoBz8GLwWXBggGVwYMBSAFJwqTGlsddRXdGNcXihk0F1kR6xBDErsUlw+rDksNTAs7DLQNewyoEqoTwRY0Fp8TbhIWEU0NHgudEAAKkQu9CsUOiw1wDAMLbAzjDxMOLxHGEG4QZBC3EbgTRBODF7UcLyA4HAUZLhpUGBYWohj8FHgRvhfUE5QT6hMvE4YSRRUjEhYUXxWaHi8naRwLHj8YxBc2GUYY/xiTFnUUtRJPFksZRht1GhUgMyKJHycgxh2YG+QghCTPIwAcZxkfF6wWSBkjF9IWqBOnEu0RwhFqEtEPHxH5D8sQPBC9E3MRug2T'
W_STAR_TURN='AkUB2wGsAagB3gHQApMCOAL8AnsCfQK+AjICFwHPAhAB6gKrAj8BxwHhAcwB/wKDAl0B8gGPAcAB7gGyAbsBggEuAS4BRgFBARoBHgHOBloIBgR/BesE9gXYBrIEfwPfBFIFBgRHBIgDwgNwA94D6QOuBc0FqAZjB2YF7QUGBEcDegNyBRUDNgDcANQA+wDVAK8ApQDJAQEAygEfAPwA5QEHAU0BewFNAfQC2gSRA0cDYAQbBFMD1QUwA48C5AO2AtkChwIUAiEB/QJvAdMB8gItA28ERwQrA/YDEAKCAvAC7wKdApUCggIlAtEDdAOZBIsGTQbGBvUHjgWfBTAGtwfZCF8HhAdyBqMFngUzBG4EXwNnAsoCzANgA2YC0wNoAsMC8QLWA8ADXwO1'
W_RED_TURN='ATYBWwEWAUIBkAGzAlkB1gGaAYUBsAFUAW4BpAGEAfQBpQJiAdMBtAHLAWYBdgGSATgBPAFPAVsBRgFgAT8BHAD3ASMBZAFLAR4BIgI3BgMEsAKeAdkCLgJvAiYCFAGNAfECPgH+AdoCOwGrAXcBgAGXAY4BgwG3Ad8BsAHCAZQBewGKAnABdAFAAZkBjAGpAUwBRgF8AagBiQH7AaMB9QGrAr0CIwGsAfUCKwK6AqwCHAJbAfsCIQKVAr0CUgJjAiYCIAIJAa4BoQHxAewBtAIWAs4C/gLOBCEC+gIJAoYERQNpAtcCzQJXAhcB8gIjAtsCxAKCAh0CTQKfAqcCpwKuAlQCJAKTAvcCsgKTAg0CNgJaApkCQwHHAasBvwJPAnICSQJrAvgB/AJS'

def _u16(encoded: str) -> list[int]:
    raw=base64.b64decode(encoded)
    assert len(raw)%2==0
    return list(struct.unpack('>'+str(len(raw)//2)+'H',raw))

def load_weekly() -> pd.DataFrame:
    base=pd.read_csv(Path(__file__).resolve().parents[1]/'market_data'/'verified_5asset_weekly_v13.csv',parse_dates=['week_start']).set_index('week_start')
    idx=base.loc['2023-12-25':].index
    columns={
        'market_close':_u16(W_MARKET_PRICE),
        'market_turn_100m':_u16(W_MARKET_TURN),
        'tech_turn_100m':_u16(W_TECH_TURN),
        'star_turn_100m':_u16(W_STAR_TURN),
        'red_turn_100m':_u16(W_RED_TURN),
    }
    assert all(len(v)==len(idx) for v in columns.values()),(len(idx),{k:len(v) for k,v in columns.items()})
    out=pd.DataFrame(columns,index=pd.DatetimeIndex(idx,name='week_start'))
    assert len(out)==150 and out.index.min()==pd.Timestamp('2023-12-25') and out.index.max()==pd.Timestamp('2026-11-16')
    assert out.iloc[-1].market_turn_100m==13508
    assert out.iloc[-1].market_close==4745
    return out

if __name__=='__main__':
    target=Path(__file__).resolve().parents[1]/'market_data'/'market_turnover_proxy_2024_2026_weekly.csv'
    df=load_weekly();df.to_csv(target);print('saved',target,df.shape,df.tail(3).to_string())
