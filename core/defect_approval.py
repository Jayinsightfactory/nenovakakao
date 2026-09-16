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
from core.defect_deduction_parser import extract, clean_text
from core.keyword_forward import timestamp
from core.import_order import match_one

ROOT = Path(__file__).resolve().parents[1] / 'data' / 'defect_requests'


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


RECIPIENTS = {'박성수':'박성수', '정재훈':'정재훈', '조현욱':'조현욱', '김원영':'김원영차장', '김원영차장':'김원영차장'}

def recipient_for(event):
    return RECIPIENTS.get(event.get('sender_name', ''))

def source_key(event):
    return (event.get('sender_name'), event.get('timestamp'), clean_text(event.get('content', '')).split())

def capture(event, cutoff, recipient, directory=None):
    stamp = timestamp(event.get('timestamp', ''))
    if stamp is None or stamp <= datetime.fromisoformat(cutoff):
        return None
    parsed = extract(event)
    if parsed['status'] in ('excluded_sender', 'attachment_only', 'deleted') or not parsed['items']:
        return None
    directory = Path(directory or ROOT)
    for existing in directory.glob('*.json'):
        if source_key(load(existing)['event']) == source_key(event): return existing
    event = {**event, 'content': clean_text(event['content'])}
    rid = '불량-' + hashlib.sha256(event['event_id'].encode()).hexdigest()[:12].upper()
    path = Path(directory or ROOT) / (rid + '.json')
    if not path.exists():
        save(path, {'id': rid, 'event': event, 'extracted': parsed,
                    'short_label': '불량' + str(1 + len(list(directory.glob('*.json')))),
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
    if source.get('origin'):
        products = [p for p in products if p.get('origin') == source['origin']]
    products = deepcopy(products)
    for p in products:
        if re.search(r'\bTinted Blue\b', p.get('name', ''), re.I):
            p['name_alias'] = list(p.get('name_alias') or []) + ['틴티드블루']
    items = []
    for item in source['items']:
        term = item['product_raw']
        size = re.search(r'(\d+)\s*cm\b', term, re.I)
        explicit_key = any(str(p.get('nenova_key')) == term for p in products)
        query = re.sub(r'\d+\s*cm\b', '', term, flags=re.I).strip() if size else term
        pool = products
        if size:
            pool = [p for p in products if re.search(r'(?<!\d)' + size[1] + r'\s*cm\b', p.get('name', ''), re.I)]
        product, candidates = match_one(query, pool)
        default_size = None
        if not size and not explicit_key and any(re.search(r'\d+\s*cm\b', p.get('name', ''), re.I) for p in candidates):
            pool = [p for p in candidates if re.search(r'(?<!\d)50\s*cm\b', p.get('name', ''), re.I)]
            product, candidates = match_one(query, pool)
            default_size = '50cm'
        items.append({**item, 'product': product, 'candidates': candidates, 'default_size': default_size})
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
    return (row['short_label'] + (f"-{row['revision']}" if row['revision'] > 1 else '')) if row.get('short_label') else f"{row['id']} v{row['revision']}"


def verified_message(payload, before_ids, history):
    """Kakao's export parser omits blank lines; preserve all other content.

    Match only new event IDs with the full message, never an ID substring.
    """
    def canonical(text):
        return '\n'.join(line.rstrip() for line in str(text).replace('\r\n', '\n').split('\n')
                         if line.strip()).strip()
    expected = canonical(payload)
    if not expected:
        return None
    matches = [e for e in history if e['event_id'] not in set(before_ids)
               and canonical(e.get('content', '')) == expected]
    return matches[0] if len(matches) == 1 else None


def reconcile_question(path, history):
    row = load(path)
    if row['status'] != 'question_unknown' or 'question_before_ids' not in row:
        return False
    found = verified_message(row.get('question_payload', ''), row['question_before_ids'], history)
    if found is None:
        return False
    row.update(status='waiting', question_event_id=found['event_id'], reconciled_at=time.time())
    row.pop('last_error', None)
    save(path, row)
    return True


def question(row):
    customer = row.get('customer') or {}
    lines = [f"{label(row)} · {row['extracted']['sequence']} {customer.get('name') or row['extracted'].get('customer') or '거래처 확인 필요'}"]
    for index, item in enumerate(row.get('items', []), 1):
        product = item.get('product') or {}
        lines.append(f"{index}. {item['product_raw']} {item['quantity_raw']}{item['unit_raw']}")
        if product:
            lines.append(f"매칭: {product['name']}")
        else:
            for n, candidate in enumerate(item.get('candidates', []), 1):
                lines.append(f"{n}) {candidate['name']}")
            if item.get('candidates'):
                lines.append(f"선택: {label(row)} 선택 {index}=번호")
    if ready(row): lines.append(f"{label(row)} 맞아 / 틀려 / 안함")
    else: lines.append(f"품목 수정: {label(row)} 품목 1=품목명")
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
    selection = re.fullmatch(r'선택\s+(\d+)=(\d+)', command)
    if selection and 1 <= int(selection[1]) <= len(result.get('items', [])):
        candidates = result['items'][int(selection[1])-1].get('candidates', [])
        if 1 <= int(selection[2]) <= len(candidates):
            command = f"품목 {selection[1]}={candidates[int(selection[2])-1]['nenova_key']}"
    correction = re.fullmatch(r'품목\s+(\d+)\s*=\s*(\S.*)', command)
    field_edit = re.fullmatch(r'(거래처|차수)\s*=\s*(\S.*)', command)
    quantity_edit = re.fullmatch(r'수량\s+(\d+)\s*=\s*(\d+(?:\.\d{1,4})?)\s*(단|박스|대|스팀)', command)
    if field_edit or quantity_edit:
        from decimal import Decimal
        source = result['extracted']
        if field_edit and field_edit[1] == '차수' and not re.fullmatch(r'(?:[1-9]|[1-4]\d|5[0-3])-\d+', field_edit[2]):
            return row
        if quantity_edit and (not 1 <= int(quantity_edit[1]) <= len(source['items']) or Decimal(quantity_edit[2]) <= 0):
            return row
        result['history'].append({'revision': result['revision'], 'extracted': deepcopy(source),
                                  'items': deepcopy(result.get('items')), 'question_event_id': result['question_event_id']})
        if field_edit:
            field = 'customer' if field_edit[1] == '거래처' else 'sequence'
            source[field] = field_edit[2].strip()
            addressed = {'거래처 없음', '거래처/농장/품종 후보 복수; 확인 필요'} if field == 'customer' else {
                '차수 없음', '여러 차수; 항목별 확인 필요', '차수 범위 표기; 단일 차수로 확정 금지'}
            source['issues'] = [issue for issue in source['issues'] if issue not in addressed]
        else:
            source['items'][int(quantity_edit[1])-1].update(quantity_raw=quantity_edit[2], unit_raw=quantity_edit[3])
        result.update(revision=result['revision']+1, status='needs_match')
        result.pop('question_event_id', None)
        result.pop('approved_revision', None)
        result.pop('approval_event_id', None)
        result['processed_replies'].append(eid)
        return result
    if command in ('맞아', '승인') and result['status'] == 'waiting' and ready(result):
        result.update(status='approved', approved_revision=result['revision'], approval_event_id=eid)
    elif command in ('틀려', '틀림'):
        result['status'] = 'awaiting_product'
        result['correction_message'] = (f"수정할 내용만 답해주세요.\n{label(result)} 품목 1=품목명\n"
            f"{label(result)} 수량 1=3단\n{label(result)} 거래처=거래처명\n{label(result)} 차수=37-1\n"
            "수정 후 다시 승인받겠습니다.")
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
    reconcile_question(path, export(row['recipient']))


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
