"""Durable text-only defect approvals; storage adapter must verify real writes.

Only the single UI worker may mutate these records. Parsing/matching never
constitutes approval. Each revision needs a new verified question and reply.
"""
import hashlib
import json
import re
import time
from copy import deepcopy
from datetime import datetime
from pathlib import Path

from core.atomic_json import save
from core.defect_deduction_parser import extract
from core.keyword_forward import timestamp
from core.import_order import match_one

ROOT = Path(__file__).resolve().parents[1] / 'data' / 'defect_requests'


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def capture(event, cutoff, recipient, directory=None):
    stamp = timestamp(event.get('timestamp', ''))
    if stamp is None or stamp <= datetime.fromisoformat(cutoff):
        return None
    parsed = extract(event)
    if parsed['status'] in ('excluded_sender', 'attachment_only', 'deleted') or not parsed['items']:
        return None
    rid = '불량-' + hashlib.sha256(event['event_id'].encode()).hexdigest()[:12].upper()
    path = Path(directory or ROOT) / (rid + '.json')
    if not path.exists():
        save(path, {'id': rid, 'event': event, 'extracted': parsed,
                    'recipient': recipient, 'revision': 1, 'status': 'needs_match',
                    'created_at': time.time(), 'history': [], 'processed_replies': []})
    return path


def _data(master, name):
    rows = master.get(name, [])
    return rows.get('data', []) if isinstance(rows, dict) else rows


def rematch(row, master):
    result = deepcopy(row)
    source = result['extracted']
    customer, customers = match_one(source.get('customer') or '', _data(master, 'customers'))
    result['customer'] = customer
    result['customer_candidates'] = customers
    products = _data(master, 'products')
    category = source.get('category')
    # Never silently accept the same variety name from another flower category.
    if category:
        products = [p for p in products if p.get('category') == category]
    items = []
    for item in source['items']:
        product, candidates = match_one(item['product_raw'], products)
        items.append({**item, 'product': product, 'candidates': candidates})
    result['items'] = items
    result['status'] = 'ready_question'
    result.pop('approved_revision', None)
    result.pop('question_event_id', None)
    result.pop('correction_notice_id', None)
    return result


def ready(row):
    return (not row['extracted']['issues'] and bool(row.get('customer', {}).get('nenova_key')
            if row.get('customer') else False) and bool(row.get('items'))
            and all(i.get('product') and i['product'].get('nenova_key')
                    and i['unit_raw'] in ('단', '박스', 'BOX', 'box', '대', '스팀', '스팀(대)')
                    for i in row['items']))


def label(row):
    return f"{row['id']} v{row['revision']}"


def question(row):
    customer = row.get('customer') or {}
    lines = [f"[불량 매칭 승인 · {label(row)}]", '입력 위치: 영업수입불량차감 > 영업입력',
             f"원문: {row['event']['sender_name']} · {row['event']['timestamp']}",
             f"차수: {row['extracted']['sequence']}",
             f"거래처: {row['extracted'].get('customer') or '확인 필요'} → {customer.get('name', '매칭 확인 필요')}"]
    for index, item in enumerate(row.get('items', []), 1):
        product = item.get('product') or {}
        candidates = ' / '.join(str(p.get('name', '')) for p in item.get('candidates', []))
        lines.append(f"{index}. {item['product_raw']} → {product.get('name') or '확인 필요: ' + candidates} / {item['quantity_raw']}{item['unit_raw']}")
    lines += ['원문 내용:', row['event']['content'], '',
              f"일치·입력 승인: {label(row)} 맞아" if ready(row) else '미확정 정보가 있어 현재 입력 승인 불가',
              f"품목 불일치: {label(row)} 틀려", f"품목 수정: {label(row)} 품목 1=정확한 품목명",
              f"제외: {label(row)} 안함"]
    return '\n'.join(lines)


def apply_reply(row, event, later_event_ids):
    """Caller supplies IDs strictly after the verified question in this export."""
    result = deepcopy(row)
    eid = event.get('event_id')
    if (result['status'] not in ('waiting', 'awaiting_product')
            or not result.get('question_event_id')
            or eid not in later_event_ids or eid in result['processed_replies']
            or event.get('sender_name') != result['recipient']):
        return result
    prefix = label(result) + ' '
    content = event.get('content', '').strip()
    if not content.startswith(prefix):
        return result
    command = content[len(prefix):].strip()
    correction = re.fullmatch(r'품목\s+(\d+)\s*=\s*(\S.*)', command)
    if command in ('맞아', '승인') and result['status'] == 'waiting' and ready(result):
        result.update(status='approved', approved_revision=result['revision'], approval_event_id=eid)
    elif command in ('틀려', '틀림'):
        result['status'] = 'awaiting_product'
        result['correction_message'] = f"{label(result)} 품목 1=정확한 품목명 으로 답장해주세요. 재매칭 후 다시 승인받겠습니다."
    elif command in ('안함', '안보내'):
        result['status'] = 'declined'
    elif correction and 1 <= int(correction[1]) <= len(result['extracted']['items']):
        result['history'].append({'revision': result['revision'], 'items': deepcopy(result.get('items')),
                                  'question_event_id': result['question_event_id']})
        result['extracted']['items'][int(correction[1])-1]['product_raw'] = correction[2].strip()
        result['revision'] += 1
        result['status'] = 'needs_match'
        result.pop('question_event_id', None)
        result.pop('approved_revision', None)
    else:
        return result
    result['processed_replies'].append(eid)
    return result


def send_question(path, export, send, paused):
    """Persist uncertainty before sending; never automatically repeat a send."""
    row = load(path)
    if paused() or row['status'] != 'ready_question':
        return
    before = export(row['recipient'])
    payload = question(row)
    if paused():
        return
    row.update(status='question_unknown', question_payload=payload,
               question_before_ids=[e['event_id'] for e in before])
    save(path, row)
    send(row['recipient'], payload)
    after = export(row['recipient'])
    matches = [e for e in after if e['content'].strip() == payload.strip()
               and e['event_id'] not in row['question_before_ids']]
    if len(matches) == 1:
        row.update(status='waiting', question_event_id=matches[0]['event_id'])
        save(path, row)


def submit(path, adapter, paused):
    """Adapter provides exact destination validation and per-request readback.

    No generic ERP endpoint fallback is allowed. An uncertain write remains
    held, even if a process crashes or the remote call raises after committing.
    """
    row = load(path)
    if paused() or row['status'] != 'approved' or row.get('approved_revision') != row['revision'] or not ready(row):
        return
    adapter.validate(row)  # must verify destination, fields, units and identity
    existing = adapter.lookup(row)
    if existing is not None:
        if not adapter.matches(row, existing):
            raise ValueError('기존 등록 내용 불일치')
        row.update(status='completed', receipt=existing)
        save(path, row)
        return
    if paused():
        return
    row['status'] = 'write_unknown'
    save(path, row)
    adapter.insert(row)
    receipt = adapter.lookup(row)
    if receipt is not None and adapter.matches(row, receipt):
        row.update(status='completed', receipt=receipt)
        save(path, row)
