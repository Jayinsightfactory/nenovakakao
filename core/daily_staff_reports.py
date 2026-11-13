"""17:00 KST staff reports, serialized by the macro and journaled before send."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import time
from core.atomic_json import read, save

ROOT = Path(__file__).resolve().parents[1] / 'data'
STATE = ROOT / 'daily_staff_reports.json'
CONFIG = ROOT / 'daily_staff_reports_config.json'
KST = timezone(timedelta(hours=9))


def collect(start, end):
    from core.defect_notifications import completion_recipient
    routing = read(ROOT/'defect_config.json') if (ROOT/'defect_config.json').exists() else {}
    groups = {}
    seen = set()
    for path in (ROOT / 'defect_requests').glob('*.json'):
        row = read(path)
        if row.get('status') != 'completed' or not start <= row.get('completed_at', 0) <= end:
            continue
        room = completion_recipient(row, routing.get('completion_recipient_policy', 'source_author'))
        if not room:
            continue
        for receipt in row.get('receipt') or []:
            key = 'defect:' + str(receipt.get('deductionKey') or '')
            if key == 'defect:' or key in seen:
                continue
            seen.add(key)
            groups.setdefault(room, []).append({'key': key, 'kind': '불량',
                'customer': receipt.get('customerName', ''),
                'product': receipt.get('matchedProductName') or receipt.get('productName', ''),
                'quantity': receipt.get('quantity'), 'unit': receipt.get('sourceUnit', ''),
                'period': f"{receipt.get('orderYear', '')}년 {receipt.get('orderWeek', '')}차"})
    orders_path = ROOT / 'import_order_state.json'
    orders = read(orders_path) if orders_path.exists() else {}
    from core.import_order import final_message
    for row in orders.values():
        if row.get('status') != 'completed' or not start <= row.get('completed_at', 0) <= end:
            continue
        result = row.get('registration_result') or {}
        if result.get('outcome') != 'registered' or not row.get('staff_room'):
            continue
        try:
            final_message(row, result)
        except RuntimeError:
            continue
        # final_orders includes pre-existing orders; report only this request's
        # registered items, never the customer's entire order ledger.
        for index, item in enumerate(row['items']):
            groups.setdefault(row['staff_room'], []).append({
                'key': f"order:{row['id']}:{index}", 'kind': '주문',
                'customer': item['customer'], 'product': item['product'],
                'quantity': item['quantity'], 'unit': item['unit'], 'period': row['week']})
    return groups


def message(day, entries):
    lines = [f'[등록 일일보고 · {day}]']
    for kind in ('불량', '주문'):
        items = [e for e in entries if e['kind'] == kind]
        if not items:
            continue
        lines += ['', f'{kind} {len(items)}건']
        for item in items:
            lines.append(f"{item['customer']} · {item['product']} · {item['quantity']}{item['unit']} · 등록 {item['period']}")
    lines += ['', '실제 등록 완료 건 기준 · 이전 보고 이후 17시까지']
    return '\n'.join(lines)


def poll(export, send, paused, now=None):
    if paused() or not CONFIG.exists():
        return
    cfg = read(CONFIG)
    if not cfg.get('enabled'):
        return
    now = datetime.fromtimestamp(time.time(), KST) if now is None else now.astimezone(KST)
    if now.hour < 17:
        return
    cutoff = now.replace(hour=17, minute=0, second=0, microsecond=0).timestamp()
    state = read(STATE) if STATE.exists() else {}
    already = {entry['key'] for batch in state.values() for entry in batch['entries']}
    for room, entries in collect(cfg['enabled_at'], cutoff).items():
        key = now.date().isoformat() + ':' + room
        pending = [e for e in entries if e['key'] not in already]
        if key not in state and pending:
            state[key] = {'room': room, 'entries': pending, 'status': 'queued',
                          'payload': message(now.date().isoformat(), pending)}
    save(STATE, state)
    from core.defect_approval import verified_message
    for key, batch in state.items():
        if batch['status'] == 'sent':
            continue
        history = export(batch['room'])
        if batch['status'] == 'unknown':
            found = verified_message(batch['payload'], batch['before_ids'], history)
            if found:
                batch.update(status='sent', event_id=found['event_id'], sent_at=time.time())
                save(STATE, state)
            continue  # Never retransmit an uncertain report.
        if paused():
            return
        batch.update(status='unknown', before_ids=[e['event_id'] for e in history])
        save(STATE, state)
        send(batch['room'], batch['payload'])
        found = verified_message(batch['payload'], batch['before_ids'], export(batch['room']))
        if found:
            batch.update(status='sent', event_id=found['event_id'], sent_at=time.time())
            save(STATE, state)
        return  # One staff report per turn, yield to approvals.
