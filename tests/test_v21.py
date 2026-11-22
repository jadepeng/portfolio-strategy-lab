"""V21 governance and offline dashboard regression tests."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import sys
import pandas as pd
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from strategy_core.analytics import (verify_market_history,describe,equity_curve,yearly,
   latest_weights,frozen_signal_record,execution_gate)
from strategy_core.us_snapshot import load_us_prices


def test_us_snapshot_313_weeks_and_cutoff():
    p=load_us_prices();m=verify_market_history(p,label='US')
    assert m['rows']==313 and m['last_week_start']=='2026-11-16'


def test_bad_duplicate_calendar_fails():
    p=load_us_prices().head(55).copy();p.index=p.index.where(p.index!=p.index[1],p.index[0])
    with pytest.raises(ValueError,match='non-unique'):
        verify_market_history(p,label='US')


def test_missing_prices_fail_closed():
    p=load_us_prices().head(55).copy();p.iloc[30,0]=float('nan')
    with pytest.raises(ValueError,match='missing'):
        verify_market_history(p,label='US')


def test_weight_sum_rejected():
    p=pd.DataFrame([{'target_qqq':.8,'target_bil':.3}],index=[pd.Timestamp('2026-11-16')])
    with pytest.raises(ValueError,match='conserve'):
        latest_weights(p,('target_qqq','target_bil'))


def test_weights_accept_frozen_pair():
    p=pd.DataFrame([{'target_qqq':.3,'target_bil':.7}],index=[pd.Timestamp('2026-11-16')])
    assert latest_weights(p,('target_qqq','target_bil'))=={'qqq':.3,'bil':.7}


def test_signal_hash_deterministic_and_no_order_claim():
    args=('US','2026-11-20','2026-11-16',{'qqq':.3,'bil':.7},{'fill':'next_week_open'})
    a=frozen_signal_record(*args,source_sha='1'*64)
    b=frozen_signal_record(*args,source_sha='1'*64)
    assert a==b and len(a['record_sha256'])==64
    assert a['record_type']=='research_snapshot_not_submitted_order'
    assert a['record_sha256']!=frozen_signal_record(*args,source_sha='2'*64)['record_sha256']


def test_signal_hash_unnormalized_rejected():
    with pytest.raises(ValueError):
        frozen_signal_record('US','2026-11-20','2026-11-16',{'qqq':.4,'bil':.7},{},source_sha='0'*64)


def test_candidate_not_promoted_on_overlapping_bootstrap():
    sample=pd.DataFrame([{'market':'US','version':'guarded','trend_weeks':40,'momentum_weeks':26,
                           'fill':f,'fee_multiple':m,'cumulative_pct':90.} for f,m in
                         [('next_week_open',2),('next_week_close',1)]])
    a=execution_gate(sample,{'max_dd_pct':-22.4},{'max_dd_pct':-19.5},market='US',bootstrap_interval=(-.7,1.7))
    assert a['checks']['drawdown_improved']
    assert not a['promotion_ready']


def test_final_snapshot_matches_current_sources():
    p=ROOT/'research_outputs'/'V21_dashboard_snapshot.json'
    if not p.exists():pytest.skip('dashboard not yet generated')
    d=json.loads(p.read_text(encoding='utf-8'))
    assert d['metadata']['asof']=='2026-11-20'
    assert not d['promotion']['US']['promotion_ready']
    assert abs(d['models']['CN']['baseline']['metrics']['cumulative_pct']-85.245)<.002
    assert abs(d['models']['US']['baseline']['metrics']['max_dd_pct']-(-22.413))<.002
    for market in ('CN','US'):
        for model in d['models'][market].values():
            assert len(model['history'])==313
            assert abs(sum(model['target'].values())-1)<1e-6
            assert abs(sum(model['held'].values())-1)<1e-6
            assert model['history'][-1]['d']=='2026-11-16'
    assert d['quality'][0]['from'] != d['quality'][1]['from']  # never positional-align the US bars


def test_web_offline_and_includes_schema():
    p=ROOT/'web'/'V21_策略研究驾驶舱.html'
    if not p.exists():pytest.skip('dashboard not yet generated')
    txt=p.read_text(encoding='utf-8')
    assert '<!doctype html>' in txt
    assert 'https://cdn.' not in txt and '<script src=' not in txt
    assert '2026-11-20' in txt
    assert 'V21_DATA_JSON' not in txt
    assert len(txt)>100000
