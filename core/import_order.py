"""Fail-closed import-room order review workflow.

MOYI archival remains independent. This module only prepares a draft, asks the
responsible Kakao contact, applies numbered corrections, and calls a supplied
bulk registrar after an explicit ``<request-id> 등록`` reply.
"""
from __future__ import annotations
import hashlib, json, os, re, time
from datetime import datetime
from pathlib import Path
from difflib import SequenceMatcher

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / 'data' / 'import_order_config.json'
STATE = ROOT / 'data' / 'import_order_state.json'
LOG = ROOT / 'data' / 'import_order_events.jsonl'


def _read(path, default):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def _save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(path)


def config():
    return _read(CONFIG, {'enabled': False, 'source': '수입방', 'start_at': None,
                          'staff_rooms': {}, 'write_enabled': False})


def direct_contacts():
    return {str(v).strip() for v in config().get('staff_rooms', {}).values() if str(v).strip()}


def normalize(value):
    return re.sub(r'[^0-9a-z가-힣]+', '', str(value).lower())


def _aliases(row):
    values = [row.get('name'), row.get('name_en'), row.get('code')]
    raw = row.get('name_alias') or row.get('aliases') or []
    if isinstance(raw, str):
        try: raw = json.loads(raw)
        except ValueError: raw = re.split(r'[,/|]', raw)
    values.extend(raw if isinstance(raw, list) else [])
    return [str(v).strip() for v in values if str(v).strip()]


def match_one(term, rows):
    key = normalize(term)
    scored = []
    for row in rows:
        aliases = _aliases(row)
        keys = [normalize(a) for a in aliases]
        exact = key and key in keys
        contains = key and any(key in k or k in key for k in keys)
        score = 1.0 if exact else 0.92 if contains else max(
            [SequenceMatcher(None, key, k).ratio() for k in keys] or [0]
        )
        scored.append((score, row))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    if not scored:
        return None, []
    best = scored[0]
    candidates = [r for score, r in scored[:3] if score >= max(0.55, best[0] - 0.08)]
    # Automatic acceptance requires a unique, strong match.
    accepted = best[1] if best[0] >= 0.92 and (len(scored) == 1 or best[0] - scored[1][0] >= 0.04) else None
    return accepted, candidates


def _display_product(row, item):
    category = row.get('category') or item.get('category') or ''
    name = row.get('name_en') or row.get('name') or item.get('product') or ''
    return ' '.join(part for part in (str(category).upper(), str(name).upper()) if part).strip()


def build_draft(event, parsed, master):
    products = master.get('products', {}).get('data', master.get('products', []))
    customers = master.get('customers', {}).get('data', master.get('customers', []))
    customer, customer_candidates = match_one(parsed.get('customer', ''), customers)
    items = []
    for index, raw in enumerate(parsed.get('items') or [], 1):
        product, candidates = match_one(raw.get('product', ''), products)
        items.append({
            'index': index, 'raw_product': raw.get('product', ''),
            'quantity': raw.get('quantity'), 'unit': raw.get('unit') or '',
            'category': raw.get('category') or '', 'matched': bool(product),
            'product': _display_product(product or {}, raw),
            'product_key': (product or {}).get('nenova_key') or (product or {}).get('code'),
            'candidates': [_display_product(c, raw) for c in candidates],
        })
    rid = 'ORD-' + hashlib.sha256(event['event_id'].encode()).hexdigest()[:8].upper()
    staff = str(parsed.get('staff') or (customer or {}).get('staff') or '').strip()
    staff_room = config().get('staff_rooms', {}).get(staff, staff)
    return {
        'id': rid, 'event': event, 'status': 'draft', 'created_at': time.time(),
        'staff': staff, 'staff_room': staff_room,
        'customer': (customer or {}).get('name') or parsed.get('customer') or '',
        'customer_key': (customer or {}).get('nenova_key'),
        'customer_candidates': [c.get('name') for c in customer_candidates],
        'week': str(parsed.get('week') or ''), 'items': items,
        'questions': list(parsed.get('questions') or []), 'raw': event['content'],
    }


def validate(draft):
    issues = []
    if not draft.get('staff_room'): issues.append('담당자 카카오톡 방 매핑 필요')
    if not draft.get('customer_key'): issues.append('거래처 매칭 확인 필요')
    if not draft.get('week'): issues.append('차수 확인 필요')
    if not draft.get('items'): issues.append('품목 없음')
    for item in draft.get('items', []):
        if not item.get('matched'): issues.append(f"{item['index']}번 품목 확인 필요")
        if not isinstance(item.get('quantity'), (int, float)) or item['quantity'] <= 0:
            issues.append(f"{item['index']}번 수량 확인 필요")
        if not item.get('unit'): issues.append(f"{item['index']}번 단위 확인 필요")
    return issues


def review_message(draft):
    lines = [f"[수입 주문 확인 {draft['id']}]", f"담당자: {draft['staff'] or '확인 필요'}",
             f"거래처: {draft['customer'] or '확인 필요'}", f"차수: {draft['week'] or '확인 필요'}", '']
    for item in draft['items']:
        name = item['product'] or item['raw_product'] or '매칭 필요'
        suffix = '' if item['matched'] else f" [후보: {', '.join(item['candidates']) or '없음'}]"
        lines.append(f"{item['index']}. {name} / {item['quantity'] or '?'}{item['unit'] or ''}{suffix}")
    issues = validate(draft)
    if issues:
        lines += ['', '확인 필요: ' + ' · '.join(issues)]
    lines += ['', f"품목 수정: {draft['id']} 3=CARNATION NOVIA",
              f"수량 수정: {draft['id']} 3수량=2박스",
              f"거래처 수정: {draft['id']} 거래처=주광",
              f"차수 수정: {draft['id']} 차수=35-1",
              f"전체 확인 후 등록: {draft['id']} 등록", f"취소: {draft['id']} 취소"]
    return '\n'.join(lines)


def capture(event, parse, master):
    rows = _read(STATE, {})
    if event['event_id'] in rows: return rows[event['event_id']]['id']
    cfg = config()
    if not cfg.get('enabled'): return None
    if cfg.get('start_at'):
        from core.keyword_forward import timestamp
        stamp = timestamp(event.get('timestamp', ''))
        if stamp is None or stamp <= datetime.fromisoformat(cfg['start_at']):
            return None
    parsed = parse(event['content'])
    draft = build_draft(event, parsed, master())
    if not draft['items']:
        draft['status'] = 'ignored'
    rows[event['event_id']] = draft
    _save(STATE, rows)
    return draft['id']


def parse_command(text, rid):
    text = text.strip()
    if text == f'{rid} 등록': return ('register', None)
    if text == f'{rid} 취소': return ('cancel', None)
    m = re.fullmatch(re.escape(rid) + r'\s+(\d+)수량=(\d+(?:\.\d+)?)\s*(\S+)', text)
    if m: return ('quantity', (int(m[1]), float(m[2]), m[3]))
    m = re.fullmatch(re.escape(rid) + r'\s+(\d+)=(.+)', text)
    if m: return ('product', (int(m[1]), m[2].strip()))
    m = re.fullmatch(re.escape(rid) + r'\s+(거래처|차수)=(.+)', text)
    if m: return (m[1], m[2].strip())
    return None


def append_log(row, action, detail):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps({'at': time.time(), 'id': row['id'], 'action': action,
                                 'detail': detail}, ensure_ascii=False) + '\n')


def _history(export, room):
    from core.moyi_inbound import parse_export
    text = export(room)
    if not text.splitlines() or room not in text.splitlines()[0]:
        raise RuntimeError('담당자 대화방 제목 불일치')
    return parse_export(text, 'import-order-review')


def _verified_send(row, message, export, send, paused):
    before = _history(export, row['staff_room'])
    baseline = [e['event_id'] for e in before]
    row.update(status='request_unknown', baseline=baseline)
    rows = _read(STATE, {}); rows[row['event']['event_id']] = row; _save(STATE, rows)
    if paused(): raise RuntimeError('일시정지')
    send(row['staff_room'], message)
    after = _history(export, row['staff_room'])
    new = [e for e in after if e['event_id'] not in baseline and normalize(e['content']) == normalize(message)]
    if len(new) != 1: raise RuntimeError('담당자 카톡 전송 결과 확인 불가')
    row.update(status='waiting', request_event_id=new[0]['event_id'], baseline=baseline)


def _apply(row, command, master_value):
    action, value = command
    if action == 'cancel': row['status'] = 'cancelled'; return '주문 작업을 취소했습니다.'
    if action == 'register': row['status'] = 'approved'; return None
    if action == 'quantity':
        index, quantity, unit = value
        if not 1 <= index <= len(row['items']): raise ValueError('품목 번호 범위 오류')
        row['items'][index-1].update(quantity=quantity, unit=unit)
    elif action == 'product':
        index, term = value
        if not 1 <= index <= len(row['items']): raise ValueError('품목 번호 범위 오류')
        matched, candidates = match_one(term, master_value.get('products', {}).get('data', []))
        item = row['items'][index-1]
        item.update(raw_product=term, matched=bool(matched), product=_display_product(matched or {}, item),
                    product_key=(matched or {}).get('nenova_key') or (matched or {}).get('code'),
                    candidates=[_display_product(c, item) for c in candidates])
    elif action == '거래처':
        matched, candidates = match_one(value, master_value.get('customers', {}).get('data', []))
        row.update(customer=(matched or {}).get('name') or value,
                   customer_key=(matched or {}).get('nenova_key'),
                   customer_candidates=[c.get('name') for c in candidates])
    elif action == '차수': row['week'] = value
    return review_message(row)


def poll(export, send, master, registrar, paused):
    if paused() or not config().get('enabled'): return
    rows = _read(STATE, {})
    for event_id, row in list(rows.items()):
        if paused(): return
        try:
            if row['status'] == 'draft':
                if not row.get('staff_room'):
                    row['status'] = 'hold'; append_log(row, 'hold', '담당자 방 매핑 없음')
                else:
                    _verified_send(row, review_message(row), export, send, paused)
                    append_log(row, 'review_sent', row['staff_room'])
            elif row['status'] == 'waiting':
                replies = _history(export, row['staff_room'])
                boundary = next((i for i, e in enumerate(replies) if e['event_id'] == row['request_event_id']), None)
                if boundary is None: continue
                command_event = None; command = None
                for reply in replies[boundary+1:]:
                    if reply['sender_name'] != row['staff_room']: continue
                    command = parse_command(reply['content'], row['id'])
                    if command: command_event = reply; break
                if not command: continue
                row['decision_event_id'] = command_event['event_id']
                response = _apply(row, command, master())
                if row['status'] == 'approved':
                    issues = validate(row)
                    if issues:
                        row['status'] = 'waiting'
                        response = '등록할 수 없습니다: ' + ' · '.join(issues) + '\n\n' + review_message(row)
                    else:
                        row['status'] = 'registering'; rows[event_id] = row; _save(STATE, rows)
                        result = registrar(row)  # registrar must verify the persisted order
                        row.update(status='completed', registration_result=result, completed_at=time.time())
                        response = (f"[주문등록 완료 {row['id']}]\n거래처: {row['customer']}\n차수: {row['week']}\n"
                                    f"품목: {len(row['items'])}개\n네노바 주문 재조회까지 확인했습니다.")
                if response:
                    # A reply is itself the new boundary for the next command.
                    row['status_before_reply'] = row['status']
                    _verified_send(row, response, export, send, paused)
                    row['status'] = row.pop('status_before_reply')
                    row['request_event_id'] = command_event['event_id']
                    append_log(row, command[0], '담당자 답변 적용')
        except Exception as exc:
            # No automatic retry after a possible external write.
            if row.get('status') == 'registering': row['status'] = 'registration_unknown'
            elif row.get('status') == 'request_unknown': pass
            else: row['status'] = 'hold'
            row['error'] = str(exc)[:300]
            append_log(row, 'error', row['error'])
        rows[event_id] = row
        _save(STATE, rows)
