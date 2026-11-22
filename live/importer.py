"""Broker CSV statement parser, deduplicating preview, and atomic ledger import."""
from __future__ import annotations

import csv
import hashlib
import io
from typing import Any

from live.store import (
    INSTRUMENT_META,
    TradingStore,
    clean_float,
    market_symbols,
    market_today,
    normalize_trade_date,
    project_portfolio,
)

COLUMN_ALIASES = {
    'trade_date': ('成交日期', '交易日期', '委托日期', '发生日期', 'date', 'trade_date', '日期'),
    'event_time': ('成交时间', '交易时间', 'time', 'event_time', '时间'),
    'symbol': ('证券代码', '基金代码', '标的代码', '代码', 'symbol', 'ticker', 'code'),
    'side': ('买卖标志', '业务名称', '交易方向', '方向', '操作', '买卖', 'side', 'action', 'type'),
    'quantity': ('成交数量', '数量', '股数', '成交股数', 'quantity', 'qty', 'shares'),
    'price': ('成交均价', '成交价格', '成交价', '价格', 'price', 'fill_price'),
    'fee': ('手续费', '佣金', '总费用', '费用', '净佣金', 'fee', 'commission'),
    'extra_fee': ('过户费', '印花税', '经手费', '证管费', '规费'),
    'amount': ('发生金额', '成交金额', '清算金额', '金额', 'amount', 'cash_delta'),
    'external_trade_id': ('成交编号', '合同编号', '流水号', '委托编号', 'trade_id', 'order_id', 'id'),
    'note': ('备注', '摘要', '说明', 'note', 'memo'),
}


def _decode_csv_text(raw: bytes | str) -> str:
    if isinstance(raw, str):
        return raw.lstrip('\ufeff')
    for enc in ('utf-8-sig', 'utf-8', 'gb18030', 'gbk'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ValueError('无法识别 CSV 文件编码，请导出为 UTF-8 或 GB18030 编码')


def _find_col(headers: list[str], aliases: tuple[str, ...]) -> str | None:
    norm = {h.strip().lower(): h for h in headers if h}
    for alias in aliases:
        if alias.lower() in norm:
            return norm[alias.lower()]
    return None


def normalize_broker_symbol(raw_sym: str, market: str) -> str:
    s = str(raw_sym or '').strip().upper()
    if not s:
        return ''
    if s in INSTRUMENT_META:
        return s
    if '.' in s:
        code, suffix = s.split('.', 1)
        if suffix == 'SS':
            s = f'{code}.SH'
        if s in INSTRUMENT_META:
            return s
        return s
    if market == 'CN':
        digits = ''.join(ch for ch in s if ch.isdigit())
        if len(digits) == 6:
            if f'{digits}.SZ' in INSTRUMENT_META:
                return f'{digits}.SZ'
            if f'{digits}.SH' in INSTRUMENT_META:
                return f'{digits}.SH'
            prefix = 'SH' if digits.startswith(('5', '6')) else 'SZ'
            return f'{digits}.{prefix}'
    else:
        if f'{s}.US' in INSTRUMENT_META:
            return f'{s}.US'
        return f'{s}.US'
    return s


def _map_side(raw_side: str) -> str | None:
    s = str(raw_side or '').strip().upper()
    if any(k in s for k in ('买入', '证券买入', '申购', 'BUY', 'BOT')):
        return 'BUY'
    if any(k in s for k in ('卖出', '证券卖出', '赎回', 'SELL', 'SLD')):
        return 'SELL'
    if any(k in s for k in ('红利', '分红', '派息', '股息', 'DIVIDEND', 'DIV')):
        return 'DIVIDEND'
    if any(k in s for k in ('银证转入', '入金', '转入', 'DEPOSIT', 'CASH_IN')):
        return 'CASH_IN'
    if any(k in s for k in ('银证转出', '出金', '转出', 'WITHDRAW', 'CASH_OUT')):
        return 'CASH_OUT'
    return None


def _clean_number(val: Any) -> float | None:
    if val is None:
        return None
    txt = str(val).strip().replace(',', '').replace('￥', '').replace('$', '')
    if not txt or txt in ('--', '—', '-'):
        return None
    return clean_float(txt)


def parse_broker_csv(
    csv_input: bytes | str,
    account: dict[str, Any],
) -> list[dict[str, Any]]:
    text = _decode_csv_text(csv_input).strip()
    if not text:
        raise ValueError('对账单 CSV 内容为空')

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError('未识别到 CSV 表头行')
    headers = list(reader.fieldnames)

    col_date = _find_col(headers, COLUMN_ALIASES['trade_date'])
    col_side = _find_col(headers, COLUMN_ALIASES['side'])
    if not col_date or not col_side:
        raise ValueError('CSV 至少需要包含「成交日期/交易日期」和「买卖标志/业务名称」列')

    col_time = _find_col(headers, COLUMN_ALIASES['event_time'])
    col_sym = _find_col(headers, COLUMN_ALIASES['symbol'])
    col_qty = _find_col(headers, COLUMN_ALIASES['quantity'])
    col_px = _find_col(headers, COLUMN_ALIASES['price'])
    col_fee = _find_col(headers, COLUMN_ALIASES['fee'])
    col_amt = _find_col(headers, COLUMN_ALIASES['amount'])
    col_id = _find_col(headers, COLUMN_ALIASES['external_trade_id'])
    col_note = _find_col(headers, COLUMN_ALIASES['note'])
    extra_fee_cols = [h for h in headers if h and h.strip() in COLUMN_ALIASES['extra_fee']]

    market = account['market']
    allowed = set(market_symbols(market))
    rows: list[dict[str, Any]] = []

    for idx, raw in enumerate(reader, start=1):
        if not any(str(v or '').strip() for v in raw.values()):
            continue
        raw_date = str(raw.get(col_date) or '').strip()
        raw_side = str(raw.get(col_side) or '').strip()
        raw_sym = str(raw.get(col_sym) or '').strip() if col_sym else ''
        raw_time = str(raw.get(col_time) or '').strip() if col_time else ''
        raw_ext_id = str(raw.get(col_id) or '').strip() if col_id else ''
        raw_note = str(raw.get(col_note) or '').strip() if col_note else ''

        try:
            td = normalize_trade_date(raw_date)
        except ValueError as exc:
            rows.append({'row_no': idx, 'status': 'ERROR', 'error': str(exc), 'raw_symbol': raw_sym, 'raw_side': raw_side})
            continue

        ev_type = _map_side(raw_side)
        if ev_type is None:
            rows.append({'row_no': idx, 'status': 'IGNORED', 'reason': f'非交易流水类型：{raw_side}', 'trade_date': td, 'raw_symbol': raw_sym, 'raw_side': raw_side})
            continue

        norm_sym = normalize_broker_symbol(raw_sym, market) if raw_sym else None
        if ev_type in ('BUY', 'SELL'):
            if not norm_sym or norm_sym not in allowed:
                rows.append({
                    'row_no': idx,
                    'status': 'IGNORED',
                    'reason': f'标的 {raw_sym or "空"} 不在当前账户策略池内（已跳过）',
                    'trade_date': td,
                    'raw_symbol': raw_sym,
                    'raw_side': raw_side,
                })
                continue
        elif ev_type == 'DIVIDEND' and norm_sym and norm_sym not in allowed:
            norm_sym = None
        elif ev_type in ('CASH_IN', 'CASH_OUT'):
            norm_sym = None

        qty = abs(_clean_number(raw.get(col_qty)) or 0.0) if col_qty else 0.0
        px = abs(_clean_number(raw.get(col_px)) or 0.0) if col_px else 0.0
        fee_val = abs(_clean_number(raw.get(col_fee)) or 0.0) if col_fee else 0.0
        for ec in extra_fee_cols:
            fee_val += abs(_clean_number(raw.get(ec)) or 0.0)
        amt_val = _clean_number(raw.get(col_amt)) if col_amt else None

        if ev_type in ('BUY', 'SELL') and (qty <= 0 or px <= 0):
            rows.append({
                'row_no': idx,
                'status': 'ERROR',
                'error': f'买卖记录缺少有效数量或成交价 (qty={qty}, price={px})',
                'trade_date': td,
                'raw_symbol': raw_sym,
                'raw_side': raw_side,
            })
            continue
        if ev_type in ('CASH_IN', 'CASH_OUT', 'DIVIDEND'):
            cash_amt = abs(amt_val or 0.0)
            if cash_amt <= 0:
                rows.append({
                    'row_no': idx,
                    'status': 'ERROR',
                    'error': '出入金/分红记录金额必须大于 0',
                    'trade_date': td,
                    'raw_symbol': raw_sym,
                    'raw_side': raw_side,
                })
                continue
        else:
            cash_amt = 0.0

        event_time = f'{td} {raw_time}' if raw_time else None
        if not raw_ext_id:
            fp_src = f'{account["account_id"]}|{td}|{raw_time}|{ev_type}|{norm_sym or ""}|{qty:.6f}|{px:.6f}|{fee_val:.4f}|{cash_amt:.4f}'
            raw_ext_id = 'csv_' + hashlib.sha256(fp_src.encode('utf-8')).hexdigest()[:20]

        rows.append({
            'row_no': idx,
            'status': 'CANDIDATE',
            'trade_date': td,
            'event_time': event_time,
            'event_type': ev_type,
            'symbol': norm_sym,
            'quantity': round(qty, 6),
            'price': round(px, 6),
            'fee': round(fee_val, 4),
            'amount': round(cash_amt, 4),
            'external_trade_id': raw_ext_id,
            'note': raw_note or f'对账单导入 ({raw_side})',
        })
    return rows


def preview_broker_csv(
    store: TradingStore,
    account_id: str,
    csv_input: bytes | str,
    *,
    prices: dict[str, dict[str, Any]] | None = None,
    today_local_date: str | None = None,
) -> dict[str, Any]:
    acc = store.get_account(account_id)
    parsed = parse_broker_csv(csv_input, acc)
    existing_events = store.list_events(account_id, limit=None)
    existing_ext = {str(e['external_trade_id']) for e in existing_events if e.get('external_trade_id')}
    today_str = today_local_date or market_today(acc['market'])

    before_pf = project_portfolio(acc, existing_events, prices, today_str)
    simulated_events = list(reversed(existing_events))
    seen_in_batch: set[str] = set()
    next_id = max((int(e['event_id']) for e in existing_events), default=0) + 1

    new_rows: list[dict[str, Any]] = []
    dup_rows: list[dict[str, Any]] = []
    ignored_rows: list[dict[str, Any]] = []
    error_rows: list[dict[str, Any]] = []
    all_rows: list[dict[str, Any]] = []

    for item in parsed:
        st = item['status']
        if st == 'ERROR':
            error_rows.append(item)
            all_rows.append(item)
            continue
        if st == 'IGNORED':
            ignored_rows.append(item)
            all_rows.append(item)
            continue
        ext_id = item['external_trade_id']
        if ext_id in existing_ext or ext_id in seen_in_batch:
            dup = {**item, 'status': 'DUPLICATE', 'reason': f'成交编号 {ext_id} 已入账（自动去重）'}
            dup_rows.append(dup)
            all_rows.append(dup)
            continue
        seen_in_batch.add(ext_id)
        new_item = {**item, 'status': 'NEW'}
        new_rows.append(new_item)
        all_rows.append(new_item)

        et = item['event_type']
        cd = (
            -(item['quantity'] * item['price'] + item['fee'])
            if et == 'BUY'
            else (item['quantity'] * item['price'] - item['fee'])
            if et == 'SELL'
            else item['amount']
            if et in ('CASH_IN', 'DIVIDEND')
            else -item['amount']
        )
        simulated_events.append({
            'event_id': next_id,
            'account_id': account_id,
            'event_time': item['event_time'] or f'{item["trade_date"]}T15:00:00',
            'trade_date': item['trade_date'],
            'event_type': et,
            'symbol': item['symbol'],
            'quantity': item['quantity'],
            'price': item['price'],
            'fee': item['fee'],
            'cash_delta': cd,
            'reverses_event_id': None,
        })
        next_id += 1

    after_pf = project_portfolio(acc, simulated_events, prices, today_str)
    before_sh = {p['symbol']: p['shares'] for p in before_pf['positions']}
    after_sh = {p['symbol']: p['shares'] for p in after_pf['positions']}
    position_diffs = [
        {
            'symbol': p['symbol'],
            'name': p['name'],
            'before_shares': before_sh[p['symbol']],
            'after_shares': after_sh[p['symbol']],
            'delta_shares': round(after_sh[p['symbol']] - before_sh[p['symbol']], 6),
        }
        for p in after_pf['positions']
        if abs(after_sh[p['symbol']] - before_sh[p['symbol']]) > 1e-6
    ]

    return {
        'account_id': account_id,
        'currency': acc['currency'],
        'counts': {
            'new': len(new_rows),
            'duplicate': len(dup_rows),
            'ignored': len(ignored_rows),
            'error': len(error_rows),
        },
        'cash_before': before_pf['cash'],
        'cash_after': after_pf['cash'],
        'cash_delta': round(after_pf['cash'] - before_pf['cash'], 2),
        'position_diffs': position_diffs,
        'rows': all_rows[:200],
    }


def commit_broker_csv(
    store: TradingStore,
    account_id: str,
    csv_input: bytes | str,
    *,
    ignore_t_plus: bool = True,
    today_local_date: str | None = None,
) -> dict[str, Any]:
    acc = store.get_account(account_id)
    parsed = parse_broker_csv(csv_input, acc)
    errors = [r for r in parsed if r['status'] == 'ERROR']
    if errors:
        raise ValueError(f'对账单存在 {len(errors)} 条格式错误行（第 {errors[0]["row_no"]} 行：{errors[0]["error"]}）')

    today_str = today_local_date or market_today(acc['market'])
    candidates = [r for r in parsed if r['status'] == 'CANDIDATE']
    # Order chronologically and CASH_IN/SELL before BUY on the same day so cash is available
    priority = {'CASH_IN': 0, 'DIVIDEND': 1, 'SELL': 2, 'BUY': 3, 'CASH_OUT': 4}
    candidates.sort(key=lambda r: (r['trade_date'], priority.get(r['event_type'], 9), r['row_no']))

    imported_ids: list[int] = []
    skipped_duplicates = 0
    for item in candidates:
        ev = store.record_event(
            account_id,
            event_type=item['event_type'],
            trade_date=item['trade_date'],
            event_time=item['event_time'],
            symbol=item['symbol'],
            quantity=item['quantity'],
            price=item['price'],
            fee=item['fee'],
            amount=item['amount'] if item['event_type'] in ('CASH_IN', 'CASH_OUT', 'DIVIDEND') else None,
            discipline_tag='IMPORT',
            note=item['note'],
            ignore_t_plus=ignore_t_plus,
            today_local_date=today_str,
            external_trade_id=item['external_trade_id'],
        )
        if ev.get('duplicate'):
            skipped_duplicates += 1
        else:
            imported_ids.append(int(ev['event_id']))

    return {
        'imported_count': len(imported_ids),
        'skipped_duplicates': skipped_duplicates,
        'ignored_count': sum(1 for r in parsed if r['status'] == 'IGNORED'),
        'created_event_ids': imported_ids,
    }
