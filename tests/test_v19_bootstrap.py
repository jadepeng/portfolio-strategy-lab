from __future__ import annotations
import numpy as np
import pandas as pd
import pytest
from scripts.bootstrap_v19 import block_bootstrap_diff

def test_equal_nav_ci_exactly_zero():
    index=pd.date_range('2024-01-01',periods=60,freq='W-MON')
    nav=pd.Series(1.003**np.arange(60),index=index)
    z=block_bootstrap_diff(nav,nav,replications=100,block_size=8)
    assert z['difference_annual_log_pct']==0
    assert z['interval_90_lo']==0 and z['interval_90_hi']==0
    assert z['n_weeks']==59 and not z['is_forward_validation']

def test_mismatched_calendar_rejected():
    a=pd.Series([1,1.1],index=pd.DatetimeIndex(['2025-01-01','2025-01-02']))
    b=pd.Series([1,1.1],index=pd.DatetimeIndex(['2025-01-01','2025-01-03']))
    with pytest.raises(ValueError):block_bootstrap_diff(a,b)

def test_bootstrap_reproducible_and_paired():
    idx=pd.date_range('2022-01-01',periods=72,freq='W-MON')
    x=pd.Series((1+.005)**np.arange(72),idx)
    y=pd.Series((1+.004)**np.arange(72),idx)
    a=block_bootstrap_diff(x,y,replications=120,seed=1)
    b=block_bootstrap_diff(x,y,replications=120,seed=1)
    assert a==b and a['difference_annual_log_pct']>0
