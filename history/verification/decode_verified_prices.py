"""Decode source-verified historical weekly close data packed as ZigZag varints.
Provenance: Longbridge candlesticks, 2026-11-21; week stamp is Monday week start.
Pre-fetched only price index history (NOT dividend-adjusted total return).
"""
import base64
from datetime import date, timedelta
from pathlib import Path
import pandas as pd

DATES='''1oYCDg4OHA4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4OHA4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4OHA4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4OHA4ODg4ODg4ODg4ODg4cDg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODhwODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4OHA4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODhwODg4ODg4ODg4ODg4ODhwODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4OHA4ODg4ODg4ODg4ODg4ODg4OHA4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4cDg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4ODg4='''
TECH='''tOUZqjn/8gHooAGUsgGxsgLHqAG+K9j7Atw73xCwJs58940C7AL1DrmiAdA+lgeI0wHlHLlj/gb8ggG8K9wmwxWtxwHtFIwXkH6RF8EdljKhW9YSqQqiRdUUqRjHH/gC+hD6Ds8lh0SDnwG5NO8D0gTZZf0dyAjlDtIriyLsVu8P+CFFmBinWr48xVvZS6gS6zKHRKop4UCzFbpO3hWLBNAQkhu1jAHLVtBEyRaYI7x89w3qUaQfhQ+xDm6qXNNHghnpYtxpoWiHUKgiuxPlDJ0H2SmaTMUY+VnEigHZsgHfqwHkVL4imqEB+oMB8zbzlAHMkAK9Y4sTo0HOI7oOkh+KA6cyt5QBigOTbd+PAZJZl2bGePUNsxe/sAGEL/d41BihF8kP01vORSyp3wH3HOQU+oQB4SfafduTAaIglhLfL98+8R6xCNAZtAyhBoQJoocB7JkBqq4BrIcB2gzqMD/mggGxf64fy1qXNb2MAY9W1zauPs9pnDuSbZsTzjitLq4liB//B81MmF6qSqMHjH/SHM8I71mbH7A9txzeKZwS7CX3OOwHoxfcYNosvBm5BvZqxGqgLLkHzIkBvFP89QGl8gHSvQHX/QHBtAG5Ea4EwkPibssa0GaaV7kBtXnOPsZ8jD+KsAG8YpJ9zO0Dq7UB/zXohQKBR/d+0zj2wwGRKMGxAvRc+VbSNbKZAcpQm8EB/FXSeIMphz6TS+KuAaNDnpEB7l2ExAGooAKHX5yjA7PmAqpl9NcCt8gBq8QE60H3swGhhQG4c/amAdNrCPDJAraXAeWaAujAAZJ8/rkBkBCqV/lY1KMB0Ra2dfopkDnbNLpShecB0ecBxlvV8QHy0QHzNuwRnjetCII20hO2as4CjnPvD7pUiAHsBMc+594BjjPV5wKaI4OPAae3AfOCApiAAeQs8a8Bj3XoVLt2iCKjngH/rQHR/wGOKd1n4qgBqFmrlwGw2gGqlwGgnwHo8wGRPZo/w0eFKb1yvhKED5xG650Bo54BsBrDnAKnZJcRtN0Bzz6B4gHOuQLbR6cYz33Ic6Y6pUnhiAHUXuJ19m6GkQGRCc022ZUB8R+TCq9R6Xfud8Atkkq7HeOHAd0ZmVnpF/ooh02gBoONAcLGAfta+ATFR+JVo1+UWL5EoXe3atd6xF3xTr9JyhDdDrUL2ZsBwDPsO+45nR31S5sSozWfRLMjsmb1tAHDFrdHvzO1zgHikwL6MPBmkxqAeKcX0U/aIpF53wrsaZI29B7VFLtIgRXHJYoQtzeZcb8r6Cu0QYdnlSHTQsMGz0W4NJ1CwwSeApKhBMDEA/tznpMBuET3swGStQLpealq1kukQ9cx/ye3B5mnAsU/2o8BolXTWtqsAf4/umfPrQHINrohp3TNJZFindkBrxP0M6gBzGOgK4UcnSyiSOwGgTXqsgHqMb5Pum2mYp0b2hH0uALi5wGsxAKqaqBhpG7KXq6HAZPDAfmVAojxAvgYqCCLlwGNqwKGzwHOWPSEAddwiL4BzT/gwgHyM/8R8wTbqwHcPdQ1x37EfqxB61fH5AG+0wPM5gKzENAO9LkB2s8B4A6ymwGBfa3HAbiTBfNap5AC75QCg4cF0FH11QG41gLcYo9+q70BsdcBujeQT+mCAY3wAdWPAa2nA/eJAc2mA9BQlT2lzgI='''
RED='''rqMs4BSvxwKCR5i4AYOtAZTkAetuwpkCyBqaQJMj8pgB17UBmQe/TO1d7zHYCujGAf8z+TbnU5qEAZYo7poBt0maVyjkpAGgd6teuQKOJuGjAaZk8x3EZZZA6wPQCPZ4/A7U0AGhC60bk5ECpSnIEqBgk0LEY7Q14yi4dJYT5nmlOtUW0iXShQG1L/aCAdMPqyz1Z5tzkyrKXJ5tngvgrwHRiQGirwHKUbUPqiqOQMkGjTOHWJyAAfZwvBr5FagblCTnG55sngHAZNWAAfy2AbEJyTnNfYlCkxGoUfo0yPwBwGG+gwGWzQGZyQHBsQXitQG+tgG/bISdAblvxcECuwjVcIIvgcsBlBGUQfSlAcg+/3j5JdM+zyjpiAKfowH7jALkuAHWIbpIz8EColT3jgK0fdYWrV6xC9KYAogpm88DwV2kb66QAYeGAZ7HAcfSAbolwlSvA41c4wSjCYqQAcxv2h3eUPyTAZbHAazPAssyjKMByvoBsA3gswPBLEL3wQP0Lq3PAvF770KSbvWCAsBT7pABn0OSngGJ0AHJHMIZobsBofwBlF68eLcyxpQCwh6NKKvrAY0kurkBxW+qS4sYkAHXyAHuNJgF2DeyiAH+Q6wH6uUBmAvfDaecAqeqAsarAd71Afm/Ap7bAr/6Aq+lAtxR8034PPAbyyG8btpC3WWPoAHgOb6HAY9T/HuPM8DSAuSmAtuqAclIiNYBll6eNvYc+lK5PrG5AfTDAbG2Ap8MkJcBwrABv7YB3VXoyAKkeO7wAdcG/SC53wH2CfWcAf0ahh2rEJIjhbACthi00QHsrQLFa5qXAdI/5RCNO4pCzliyNsQrOpJ/lhWRQZDrAfQexzKT1wHEkgKzjQGpU6Bng1TdsQLaJbTgArGGAb7VAvKqAoryAt+NArWOAbf5AbUBv50B3AmtoQKfgQGKiwGeigHOAv6KAZkxvBGQKZBU/IYBm17WP5WhA4jtA4QficsBlrIBtcYCkZQBrpoBpOwC5kHXUPvWAsmqAZeYAbCmAZZC8gXHH+x833jhI9yPAYdWt5EDqF6MStPCAar6AcpNjEWpEcKdAoWDA5sUi5AB6GTHV4eGA5a+AcjpAZ8NrrUB4mySdMlqia4CgjiCdPQdzIgBoSyGGKMmpvsB9pgB1YsCnmSXPfcIjC7If9YxmpYBkMAB6XSdoAGRxwGtDIhc+4YBtfUB4jEIkj2tZc6wAo4Lvd8BzArdP6JD0yPigwHEN7dPkz+T2AGsXL9jjQOWC+haoRvFXb47s1uWKbzKAbVok40B0MgCr8cB7NYCqOIC01jmqwH97wHbI8YznowB8hTk4QKZxQLvA4DJAfxe2TuMHeE+ibMBtZcBsWKMELWtAYlWy9UByi+/W8xb41vDiQG5breXAuRitKMHyJgE5e4DlEr3FfNWrMkBoc8Bm0XufIL2Ack7kz2kcqW9AcmHAq6EAdkPtGnhZ6Irx58BslWQJ9qmAZEerBOQUNuuAoKKAcQP9U6OfYw2/gzRHfQD/TGnI5Ij1KMBzjPlScrGAZfpAdzMAZVh7EfvX98rjh/TX7cVwAmImQGeOuhb6SrewwHsFu/MAscN+hKJzQG+WMAvoQ/MigGRmwHCuAGqigGRLcME0vcB6DTmkgGJnAKXL6uRAaxDrgTMftKKAZeYAbOIAaXTAbg0pqQB1TWJiATn6AKavgG/JNiYAay/AYKCAvNhpSzcnQGkrwHHQ59ql7kB+Au0JeCoAZ0gmZwCyK4CrV6FIbwd'''

def decode(s):
    b=base64.b64decode(s.strip());v=[];j=0;last=0
    while j<len(b):
        bits=0;n=0
        while True:
            q=b[j];j+=1;n+=(q&127)<<bits;bits+=7
            if not q&128:break
        delta=n//2 if n%2==0 else -(n+1)//2
        last+=delta;v.append(last)
    return v

def save():
    ds=decode(DATES);tech=decode(TECH);red=decode(RED)
    # The final six regular Monday bars (2026-10-12 through 2026-11-16).
    ds.extend([ds[-1]+7*i for i in range(1,7)])
    assert len(ds)==len(tech)==len(red)==555,(len(ds),len(tech),len(red))
    assert date(1970,1,1)+timedelta(days=ds[0])==date(2016,1,11)
    assert date(1970,1,1)+timedelta(days=ds[-1])==date(2026,11,16)
    assert abs(tech[-1]/100-2212.13)<.01,tech[-1]
    assert abs(red[-1]/100-5529.78)<.01,red[-1]
    rows={'week_start':[date(1970,1,1)+timedelta(days=d) for d in ds], 'tech':[t/100 for t in tech],'red':[t/100 for t in red]}
    p=Path(__file__).resolve().parents[2]/'market_data'/'longbridge_2016_2026_tech_dividend_prices.csv';p.parent.mkdir(exist_ok=True)
    pd.DataFrame(rows).to_csv(p,index=False)
    print(p, len(ds), rows['tech'][0],rows['red'][0],rows['tech'][-1],rows['red'][-1]);return p
if __name__=='__main__':save()
