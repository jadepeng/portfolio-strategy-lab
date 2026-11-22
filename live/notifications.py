"""Deduplicated in-app and optional webhook notifications for PTMS."""
from __future__ import annotations

import hashlib
import json
import os
import threading
from typing import Any
from urllib.request import Request, urlopen

from live.store import TradingStore, market_today


def _dispatch_webhook(url: str, notification: dict[str, Any]) -> None:
    def _send():
        try:
            body = json.dumps(notification, ensure_ascii=False).encode('utf-8')
            req = Request(
                url,
                data=body,
                headers={'Content-Type': 'application/json; charset=utf-8'},
                method='POST',
            )
            with urlopen(req, timeout=4):
                pass
        except Exception:
            pass

    threading.Thread(target=_send, name='ptms-webhook', daemon=True).start()


def _emit(
    store: TradingStore,
    *,
    event_key: str,
    account_id: str | None,
    category: str,
    severity: str,
    title: str,
    message: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    created = store.notify_once(
        event_key,
        account_id=account_id,
        category=category,
        severity=severity,
        title=title,
        message=message,
        payload=payload,
    )
    if created is not None:
        webhook_url = (os.getenv('PTMS_WEBHOOK_URL') or '').strip()
        if webhook_url.startswith(('http://', 'https://')):
            _dispatch_webhook(webhook_url, created)
    return created


def evaluate_and_emit_notifications(
    store: TradingStore,
    ptms_state: dict[str, Any],
    *,
    live_status: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Inspect account data gates, weekly plans, and intraday stops; emit each event at most once."""
    emitted: list[dict[str, Any]] = []
    accounts = ptms_state.get('accounts') or {}
    errors = (live_status or {}).get('errors') or {}

    for aid, bundle in accounts.items():
        acc = bundle['account']
        market = acc['market']
        plan = bundle['plan']
        gate = plan.get('data_gate') or {}
        today_str = market_today(market)

        # 1. Data feed degradation / recovery
        if gate.get('status') == 'BLOCKED' and gate.get('reasons'):
            reason_txt = '；'.join(gate['reasons'])
            digest = hashlib.sha256(reason_txt.encode('utf-8')).hexdigest()[:10]
            note = _emit(
                store,
                event_key=f'feed_degraded:{aid}:{today_str}:{digest}',
                account_id=aid,
                category='FEED_STATUS',
                severity='warning',
                title=f'{market} 账户行情/策略数据未就绪',
                message=reason_txt,
                payload={'account_id': aid, 'view': 'desk', 'reasons': gate['reasons']},
            )
            if note:
                emitted.append(note)
        elif gate.get('status') == 'READY' and not any(k.startswith(('quotes', 'history', 'tape')) for k in errors):
            # Emit recovery only if an earlier degradation exists today for this account
            prior = store.list_notifications(limit=30)
            degraded_today = any(
                n.get('account_id') == aid
                and n.get('category') == 'FEED_STATUS'
                and str(n.get('event_key') or '').startswith(f'feed_degraded:{aid}:{today_str}:')
                for n in prior
            )
            if degraded_today:
                note = _emit(
                    store,
                    event_key=f'feed_recovered:{aid}:{today_str}',
                    account_id=aid,
                    category='FEED_STATUS',
                    severity='info',
                    title=f'{market} 账户数据已恢复可用',
                    message=f'最新信号截点 {plan.get("signal_cutoff")} 已就绪，可查看今日交易决策。',
                    payload={'account_id': aid, 'view': 'desk'},
                )
                if note:
                    emitted.append(note)

        # 2. Weekly signal actionable notification
        if gate.get('can_execute_plan') and plan.get('verdict') in ('ACTION_REBALANCE', 'ACTION_PRIORITY_EXIT'):
            cutoff = str(plan.get('signal_cutoff') or today_str)
            sha = str(plan.get('signal_sha256') or '')[:12]
            note = _emit(
                store,
                event_key=f'weekly:{aid}:{cutoff}:{sha}',
                account_id=aid,
                category='WEEKLY_SIGNAL',
                severity='action',
                title=f'{market} 账户周频信号生效：{plan.get("verdict_label")}',
                message=f'{plan.get("verdict_reason")}（共 {len(plan.get("orders") or [])} 笔建议）',
                payload={'account_id': aid, 'view': 'desk', 'cutoff': cutoff},
            )
            if note:
                emitted.append(note)

        # 3. Intraday stop confirmation notification
        for t in bundle.get('tickets') or []:
            if t.get('source') == 'INTRADAY_STOP' and t.get('status') in ('PENDING', 'PARTIAL'):
                note = _emit(
                    store,
                    event_key=f'intraday:{aid}:{t.get("signal_cutoff")}',
                    account_id=aid,
                    category='INTRADAY_STOP',
                    severity='urgent',
                    title=f'{market} 账户盘中破位防守确认',
                    message=str(t.get('reason') or '触发盘中 30 分钟有效跌破减仓线，请及时确认'),
                    payload={'account_id': aid, 'view': 'desk', 'decision_id': t.get('decision_id')},
                )
                if note:
                    emitted.append(note)

    return emitted
