"""Reconstruct unchanged Longbridge forward-adjusted weekly ETF bars; provenance in V6 manifest."""
import base64
from pathlib import Path
import pandas as pd

data = {
'tech':'tCmLAl6UASNVV9IBR64BeuoB1AJb9gOnA26iA/UBxwVT4wGdAY4B1AGDAQOWA8QB0wLsAZYB2gEobHPUAR+YATJOTXSRAqkCdKcC+gFLGE4TTBKAAQGYARVmDABThwIsrQMYkwHzAb0CngEwyQGdAWiLAR6xAdMBvwImhQHYAXy/AY4CvAHQAbQCTUBlOXEYDlCrAdEBHOUCcSWgAleLAogDaR2RAZQBQGOXAXKOAYYBtAENRa0BKQlnjwGKATxUG6sBG2khPF8QsQH0AWcQW2Z1alKVAX+TAXRlVxQVD70BPkpKKVsNS1ErfOMBG1k/gQLaAjiEASWaAR9hLJkBD4oBVDAfVREvEEGNATk8ToEBI1UJV0ZXAgHABegF1wLAAVDfAYwDmwGHAVZaPS8L8QJPsAF0b84BVoIB3QFANJMBLXuNAhtUAn4wGytaBj/iAUBohgGEAR0SigOyApYDngFmhgGGAbAB8wHnAtADJia3AfkChgJsqgGFAe4BT4ACQBED0wFISJcBlgFSX68C0ATSAwge6gHIAi/WAZcBhQLOBmvjAt0CnQZalwK0A3apAekBkwJIZJ0BuwKzAaUErQGVBFpLpwM=',
'red':'+gsdK2YoSAAFSwktCQoFElkBQl4hNA4FDxoWFBAKJAoNVBQLLVglBywHYQ50I3BchAFbI1MEMQphGSIqAigTAAoWKhkYbYoBBD82Yy80ah4baTUpNCAKADQfBToLWRoaQVgaFgVcbw8lIB1vKkQIOCgOM1kUIgguAAMLUDRVIBMDECYaMj4pKTsEKic7GgokA2YCOwETGgswDhkVRSAfBQQcAyMIIQpGHy1mO25mGzpLBQouBHJnAkAgBRQLKSULCB8LRRQhIh0pJVkgwgKgAZkBHg0bRD8VJkgHISQ5TSgAIhkGLx4INAMEFl0qBhMqFgQBBAQAGjYgDUJBRiEYHw8KGQcMLBggBzgOZQcIORQSACgxPCwJBEwOMFsPJxAEKi4xIz0WNAuXAWU8AzpGWiMLMjoRIzMAEDQJUV4bBwY='
}
def unpack(s,scale):
    by=base64.b64decode(s);out=[];z=0;j=0
    while j<len(by):
        n=0;bits=0
        while True:
            x=by[j];j+=1;n+=(x&127)<<bits;bits+=7
            if not (x&128):break
        z+=n//2 if n%2==0 else -(n//2+1)
        out.append(z/scale)
    return out

def main():
    raw={k:unpack(v,1000) for k,v in data.items() if k!='dates'}
    assert {len(x) for x in raw.values()}=={313}
    reference=Path(__file__).resolve().parents[1]/'market_data/longbridge_2016_2026_tech_dividend_prices.csv'
    ref=pd.read_csv(reference,parse_dates=['week_start'])
    idx=ref.loc[(ref.week_start>='2020-10-12')&(ref.week_start<='2026-11-16'),'week_start'].reset_index(drop=True)
    assert len(idx)==313, 'ETF date alignment requires 313 matching weekly reference rows'
    df=pd.DataFrame({'week_start':idx,'tech':raw['tech'],'red':raw['red']})
    assert df.iloc[0].week_start.strftime('%Y-%m-%d')=='2020-10-12'
    assert abs(df.iloc[0].tech-2.650)<1e-9 and abs(df.iloc[0].red-.765)<1e-9
    assert df.iloc[-1].week_start.strftime('%Y-%m-%d')=='2026-11-16'
    assert abs(df.iloc[-1].tech-2.230)<1e-9 and abs(df.iloc[-1].red-1.368)<1e-9
    dest=Path(__file__).resolve().parents[1]/'market_data/longbridge_etf_fwd_2020_2026_v6.csv'
    df.to_csv(dest,index=False)
    print(dest,len(df))
if __name__=='__main__':main()
