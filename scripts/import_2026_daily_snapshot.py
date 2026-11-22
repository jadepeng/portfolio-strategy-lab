"""Decode compact snapshots downloaded from Longbridge market-data connector 2026-11-21.

Snapshot bars are the common trading days in the latest 210 mainland close records.
Encoding: zigzag varint delta, base64; units 0.01 index point and 1 million index volume.
Preserves verifiable close-series history without remote network or API credentials.
"""
from pathlib import Path
import base64, datetime, csv

data = {
'DATES': '2r8CAgYCAgICBgICAgIGAgICAgYCAgICBgICAgIWAgICBgICAgIGAgICAgYCAgICBgICAgIGAgICAggCAgIGAgICAgYCAgICBgICAgwCAgYCAgICBgICAgIGAgICAgYCAgICBgICAgIGAgICCAICAgIGAgICAgYCAgICBgICAgIGAgICAgYCAgICBgICAgIGAgICAgYCAgICBgICAgIGAgICAgYCAgICBgICAgIGAgICCAICEAIGAgICAgYCAgICBgICAgIGAgICAgYCAgICBgICAgI=',
'399006.SZ': '7qco7CfKXulnyiqsHeMKySSVXbQb4jPKILUv0iTfHeEdskHBgAH8XvMUhVCbJb6WAY0T9zfYQ7NRvDLgSK8PlzabGaeEAb1GklKoE68guJgB5kOBMqcLhEmfeM5nnTqYQ8+2AcIZnGa/RcYklyORigHiYf10gSTEEe6jAv0lisQBmiuigAGJRPatAaRRjQHgEdxj5TL7Udcdr1GsjQHTD+qdAeRV2TnIzwGeCdKhAZ2IAb8ikxaHCu4U4Y8B8KkBlIEBniKcBPR7j4gB+YcB+qMB8GilNcvMAa3kAajqAY+nAYNE7B28vQK6bJZkrIUBmKcBn4UCxlyyvAHjlQKSI/LEAY2AAbH8ApQEhW/dOf9nhI4Cj5ICoboBtMcBx0ivrwGdnAPOFr77Aqe6Ad4N8ZMB4KsBj5wD3lC/0QGcmwHZQKSjApJIyx6oSr0o1hLYUocZ9D70sQGHNs3qAsQijk7RsQHZNZAbrlu9TKgW2UbtfjzNKJqvAe88tQftGcsZ+zjrOqZjxxSadIYqRN8f040BgekBpASvC4uaAbYKnXzHCIuPAc2CAe0Q4SiJC8FFibgBwKcB1UffjAGqhwGJtwG3ogG2RuFomO8BviLZngG1CoA4rnnvYZ2CAdulAbsrrB7HHPF+',
'000922.SH': 'qIZEoBzyHP8d+z3JEL1LjEf+ccs1uj+1Cpo5oUvIiAHwbYVaq5YCwkHO5wHvJ4UYpDveINg+8RyrggHWhgHYMIEaplrcephJ85UBrQqWEaAG40SqhAG+b70i4UHJROc2uTW7KfnkAbaHAe4VgAS+FPRS22moBfYU4ZQBziyuR9VDhhP3Bv4S5h/gFZ09ngvSTlucLeMHvRPKZcZW/x2aGu+TAcEe1hjTDc8pgVTjFfEo4AyrHfWKAfEOqiruB6NO3xSkZajmAbUbnwP9JtIDu07bBYVA3yKogQHvW82XAf9cy7cByFjvL+V8vWSbsAHUNsGZAf6FAYJJiFL0pwGFdtwnxWTDGfQV9KoBvG+fWqs9yocC61yMYPxSuZ4BuC6QBvB+wF71D9Efq4wB3xHSZucKkFXfLsUb+0zsFewhqjb0HNIx/wjcc781mj7YGpYYhCp+/YMBYNQUrWq2ceBi5TyhlwH/EfU13S/RLvESxGZ200TtFsUuixGGZfZh6kayG6gZlDvN1gHCRtRC/WnzWZGQAekKtmeSM74CkEqyR78HogupBolypBbSLvcyywalM5IdunqnIOgH9yfFHA==',
'000300.SH': 'yuo5niG4MJEt6x3sDscehgSrGMYGgAGRIfwG/QGcE4g4oUrnnAHKVKI8kyzzKfR1mAivEIAJuVzSSaYsiw6rGZQc2XGdU7RG9hOjRtRcvC67GuMc5AOnNdIgp3WtGevoAdBYhmLTXZAnyxC7QeR210nHOyGM8gHrLfZt/g6QVoUZoFCzDPoswBC2MeMUvxq8AvMTvFHfBIRtzCTXLPJ89QXKTuuCAflV3SigHrMDz2nwYMR3gin/PbgJwSLjSuxt8iWbNZ2JAa+hAeKJAZNT8yjmVd6yAdcL2En6D7y4AZXbAfYkuHin7QHcW4ZS+R+X5QHWLiHbTbE53LwBn5UBuYUBgJ4Blw+FigHJiAKUbJbcAa0i6BCRe5hT1c8BhDD7TpA8q0aeWrZZ2QqyQ+4L3zuyKpEqggPEdfEXndYBqgbuKJFXqxHIPNo9+yDuGKkVl2OcB4kHlCq5GdQV6SXdO/8u9y6eL7Uf5kmkMuwH1SqLetuZAfoGshP7SfQK3y2bE89a3VCYB5IGhSPjPpV27mvlAq1U0DXVQYNYjDHHDLyQAe4SsVzuAtgE+BnTMZcrgTmbBbYL1gGlLw==',
'000688.SH': '2uERwCCGOIVC/DDvCrgfoQulJfBR4gnuEtUgnCTrAddJ4AKzW8IfvRvfIOUP7jfsFM0ZuijVEN8HsAzUE64DqSSnd9kKliWQDq0l/C6zHp0b0Q/eEZcw5hy1NK8g7VjuLbwmxynWEpMRoTS2QaM4qQnqG/Z60w2OIO4QxC76Ae4Y4AG6Kp8lnCb9HPwgvFXpHsoHmHmYhgGkIoU8jneKC7pIs0aFLbQV7GXwWIFqyCm+pAGhLNNRgC2ZkQH1iAH+KdQ3yBL5bI9w/GeBEfwPnAH+hAHiD+CAAbpuvDqPM7RylHiZNbqSAfR/1VX9ggKhEoQgygjwFoCJAum8AbNuihjDhQH1eLfNAZIFnqACpUP/bfEDtiDtsgGDF4+NAaRK5YEBlmPmePQL4kLhCZUs+iqHHg2ab5QD3cAB3RZ2g1DCA+4qhGD5MOAiyTnjLoUK6TTSO6kmixH3EN0YnyeCJa5kuw+0SMwH5gvFBvM8+WbwFLk9kXO+Ad0vXKdJsyCtAYMu1hSsAbk6+j3zLuNi8i/XVYlptDirR7ayAZIhpzTwBcwMoDfkDpMvi2feC/ApziOBPg==',
'VOL': 'xARsmgEaLo8CEVsMQQp4LGMPSkUvERA7BjIKJQopHhYmF0YajwEyJ0IjARMCJQAdDDgeRwIrHwYSAg0XH6IBPRQzLAQSAgQnGDQLFwQFGWAEETwlAQYJOQ4DVmsmBwchNCkPICEyACcJCT4RDgkIZDsXHg0CJ1ofLRk/DCRQOzECBEAOLG0vGQwENAZAYTAiDRQrJQ9OOQAGFC8bAgAHJggMBDsJQjcEER0yNQMKBB4cFDUlFCsKHlY9IjckAR8HBhSAAV8XIhUwGCtQBFMfDhIcMx8IEREy'
}

def decode(text: str, scale=1.0):
    buf=base64.b64decode(text)
    vals=[]; cursor=0; last=0
    while cursor<len(buf):
        v=0;shift=0
        while True:
            byte=buf[cursor];cursor+=1
            v|=(byte&127)<<shift;shift+=7
            if byte<128:break
        delta=v//2 if v%2==0 else -(v+1)//2
        last+=delta
        vals.append(last/scale)
    return vals

def main():
    cols={}
    for k,v in data.items():
        try: cols[k]=decode(v,1 if k=='DATES' or k=='VOL' else 100)
        except Exception as exc: print('Decode error',k,repr(exc))
    if len(cols)!=len(data):
        raise ValueError('Data provider transfer incomplete')
    sizes={k:len(v) for k,v in cols.items()}
    if set(sizes.values())!={210}:
        raise ValueError(f'Encoded series length inconsistency: {sizes}')
    dates=[(datetime.datetime(1970,1,1)+datetime.timedelta(days=n)).strftime('%Y-%m-%d') for n in cols['DATES']]
    path=Path(__file__).resolve().parents[1]/'market_data'/'longbridge_daily_2026.csv'
    with path.open('w',newline='',encoding='utf-8') as f:
        writer=csv.writer(f)
        writer.writerow(['date','tech','red','market','growth_leader_proxy','tech_volume_million'])
        for i,d in enumerate(dates):writer.writerow([d,cols['399006.SZ'][i],cols['000922.SH'][i],cols['000300.SH'][i],cols['000688.SH'][i],cols['VOL'][i]])
    print(path,len(dates),dates[0],dates[-1],{k:v[-1] for k,v in cols.items()})

if __name__=='__main__':main()
