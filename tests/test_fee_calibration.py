from strategy_core.fee_calibration import estimate_fee_rates, apply_fee_estimates, MIN_FILLS, FLOOR
from strategy_core.baselines.multi_asset import MultiAssetConfig

BUCKET = {'159915.SZ': 'tech', '515180.SH': 'red'}
DEF = {'tech': .0015, 'red': .0015}


def _fill(sym, side, qty, px, fee, tid=None, **kw):
    return dict(symbol=sym, event_type=side, quantity=qty, price=px, fee=fee, decision_id=tid, **kw)


def test_insufficient_fills_keep_default():
    ev = [_fill('159915.SZ', 'BUY', 1000, 2.0, .1)] * (MIN_FILLS - 1)
    est = estimate_fee_rates(ev, BUCKET, DEF)
    assert est['tech'].applied_rate == .0015 and est['tech'].source.startswith('default')


def test_calibrated_includes_slippage_and_clamps():
    tickets = {1: {'snapshot': {'orders': [{'symbol': '159915.SZ', 'side': 'BUY', 'ref_price': 2.0}]}}}
    # 0.5bp commission + 2bp adverse slippage
    ev = [_fill('159915.SZ', 'BUY', 10000, 2.0004, 2.0004 * 10000 * .00005, 1) for _ in range(MIN_FILLS)]
    est = estimate_fee_rates(ev, BUCKET, DEF, tickets=tickets)['tech']
    assert est.source == 'calibrated'
    assert abs(est.p75_rate - .00025) < 1e-6
    assert est.applied_rate == FLOOR['tech']          # never below the safety floor


def test_never_above_default_and_ignores_reversed():
    ev = [_fill('515180.SH', 'SELL', 100, 1.0, 1.0) for _ in range(MIN_FILLS)]   # 100bp fee
    ev += [_fill('515180.SH', 'SELL', 100, 1.0, 0.0, is_reversed=True)] * 50
    est = estimate_fee_rates(ev, BUCKET, DEF)['red']
    assert est.fills == MIN_FILLS and est.applied_rate == .0015


def test_apply_to_config():
    est = estimate_fee_rates([_fill('159915.SZ', 'BUY', 10000, 1.0, 5.0)] * MIN_FILLS, BUCKET, DEF)
    cfg = apply_fee_estimates(MultiAssetConfig(), est)
    assert cfg.tech_fee == 0.0005 and cfg.red_fee == MultiAssetConfig().red_fee


def test_calibrate_from_store_report_is_json_safe():
    import json
    from strategy_core.fee_calibration import calibrate_from_store
    from live.store import INSTRUMENT_META

    class _S:
        def list_events(self, aid, limit=None):
            return [_fill('159915.SZ', 'BUY', 10000, 1.0, 1.0)] * MIN_FILLS
        def list_tickets(self, aid, limit=100):
            return []
    cfg, rep = calibrate_from_store(_S(), 'CN_MAIN', MultiAssetConfig(), INSTRUMENT_META, 'CN')
    json.dumps(rep, allow_nan=False)
    assert {r['bucket'] for r in rep} == {'tech', 'red', 'bond', 'gold', 'money'}
    assert cfg.tech_fee == FLOOR['tech']
