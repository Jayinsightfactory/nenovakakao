"""Fail-closed import-room order review workflow.

MOYI archival remains independent. This module only prepares a draft, asks the
responsible Kakao contact, applies numbered corrections, and calls a supplied
bulk registrar after an explicit ``<request-id> 등록`` reply.
"""
from __future__ import annotations
import hashlib, json, math, os, re, time
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
    from core.atomic_json import save
    save(path, value)


def config():
    return _read(CONFIG, {'enabled': False, 'source': '수입방', 'start_at': None,
                          'staff_rooms': {}, 'allowed_senders': [], 'write_enabled': False})


def direct_contacts():
    return {str(v).strip() for v in config().get('staff_rooms', {}).values() if str(v).strip()}


def configure_staff(staff, room):
    """Explicitly enable one staff sender for review; never enable ERP writes."""
    from core.credential_store import _target
    staff, room = str(staff).strip(), str(room).strip()
    _target(staff); _target(room)
    cfg = config()
    mapping = cfg.setdefault('staff_rooms', {})
    if staff in mapping and mapping[staff] != room:
        raise ValueError('기존 담당자 방 변경은 대기 주문 확인 후 별도로 진행해주세요.')
    allowed = cfg.get('allowed_senders', [])
    if not allowed:
        raise ValueError('전체 허용 설정입니다. 기존 담당자 목록부터 명시적으로 설정해주세요.')
    mapping[staff] = room
    cfg['allowed_senders'] = list(dict.fromkeys([*allowed, staff]))
    _save(CONFIG, cfg)
    from core.moyi_control import audit
    audit('staff_review_enabled', staff)


def normalize(value):
    return re.sub(r'[^0-9a-z가-힣]+', '', str(value).lower())


def _aliases(row):
    values = [row.get('name'), row.get('name_en'), row.get('code')]
    raw = row.get('name_alias') or row.get('aliases') or []
    if isinstance(raw, str):
        try: raw = json.loads(raw)
        except ValueError: raw = re.split(r'[,/|]', raw)
    values.extend(raw if isinstance(raw, list) else [])
    return [str(v).strip() for v in values if v is not None and str(v).strip()]


def match_one(term, rows, frequencies=None):
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
    candidates = [r for score, r in scored if score >= max(0.55, best[0] - 0.08)]
    if frequencies:
        candidates.sort(key=lambda r: frequencies.get(str(r.get('nenova_key') or r.get('code')), 0), reverse=True)
    candidates = candidates[:3]
    # Automatic acceptance requires a unique, strong match.
    accepted = best[1] if best[0] >= 0.92 and (len(scored) == 1 or best[0] - scored[1][0] >= 0.04) else None
    return accepted, candidates


def _rematch_item(item, master):
    history = master.get('customer_histories', {}).get(str(item.get('customer_key')))
    loader = master.get('customer_history_loader')
    if history is None and loader and item.get('customer_key'):
        try:
            history = loader(item['customer_key'])
        except Exception:
            history = {'status': 'unavailable', 'products': []}
    history = history or {'status': 'unavailable', 'products': []}
    records = {str(r['ProdKey']): r for r in history.get('products', [])}
    frequencies = {key: float(r.get('orderFrequency') or 0) for key, r in records.items()}
    products = master.get('products', {})
    products = products.get('data', []) if isinstance(products, dict) else products
    matched, candidates = match_one(item['raw_product'], products, frequencies)
    def option(product):
        key = product.get('nenova_key') or product.get('code')
        return {'label': _display_product(product, item), 'product_key': key,
                'order_history': records.get(str(key))}
    item.update(matched=bool(matched), product=_display_product(matched or {}, item),
                product_key=(matched or {}).get('nenova_key') or (matched or {}).get('code'),
                candidates=[_display_product(c, item) for c in candidates],
                candidate_options=[option(c) for c in candidates],
                history_status=history['status'], history_scope=history.get('scope', '상위 10개'),
                order_history=option(matched)['order_history'] if matched else None)


def _history_label(record):
    if not record:
        return '조회 이력 내 기록 없음'
    return (f"과거 {record.get('orderFrequency', 0)}회 / 저장 수량 합계 "
            f"{record.get('totalBox', 0)}박스·{record.get('totalBunch', 0)}단")


def _display_product(row, item):
    category = row.get('category') or item.get('category') or ''
    name = row.get('name_en') or row.get('name') or item.get('product') or ''
    return ' '.join(part for part in (str(category).upper(), str(name).upper()) if part).strip()


def _display_quantity(value):
    return str(int(value)) if isinstance(value, float) and value.is_integer() else str(value if value is not None else '?')


def build_draft(event, parsed, master):
    products = master.get('products', {}).get('data', master.get('products', []))
    customers = master.get('customers', {}).get('data', master.get('customers', []))
    default_customer = parsed.get('customer', '')
    customer, customer_candidates = match_one(default_customer, customers)
    items = []
    for index, raw in enumerate(parsed.get('items') or [], 1):
        raw_customer = raw.get('customer') or default_customer
        item_customer, item_customer_candidates = match_one(raw_customer, customers)
        product, candidates = match_one(raw.get('product', ''), products)
        items.append({
            'index': index, 'raw_product': raw.get('product', ''),
            'raw_customer': raw_customer,
            'customer': (item_customer or {}).get('name') or raw_customer,
            'customer_key': (item_customer or {}).get('nenova_key'),
            'customer_candidates': [c.get('name') for c in item_customer_candidates],
            'quantity': raw.get('quantity'), 'unit': raw.get('unit') or '',
            'category': raw.get('category') or '', 'matched': bool(product),
            'product': _display_product(product or {}, raw),
            'product_key': (product or {}).get('nenova_key') or (product or {}).get('code'),
            'candidates': [_display_product(c, raw) for c in candidates],
            'candidate_options': [
                {'label': _display_product(c, raw),
                 'product_key': c.get('nenova_key') or c.get('code')}
                for c in candidates],
        })
    for item in items:
        _rematch_item(item, master)
    rid = 'ORD-' + hashlib.sha256(event['event_id'].encode()).hexdigest()[:8].upper()
    staff = str(parsed.get('staff') or (customer or {}).get('staff') or '').strip()
    staff_room = config().get('staff_rooms', {}).get(staff, staff)
    return {
        'id': rid, 'event': event, 'status': 'draft', 'created_at': time.time(),
        'staff': staff, 'staff_room': staff_room,
        'customer': (customer or {}).get('name') or default_customer or '',
        'customer_key': (customer or {}).get('nenova_key'),
        'customer_candidates': [c.get('name') for c in customer_candidates],
        'week': str(parsed.get('week') or ''), 'items': items,
        'questions': list(parsed.get('questions') or []), 'raw': event['content'],
    }


def validate(draft):
    issues = []
    if not draft.get('staff_room'): issues.append('담당자 카카오톡 방 매핑 필요')
    if not draft.get('week'): issues.append('차수 확인 필요')
    if not draft.get('items'): issues.append('품목 없음')
    for item in draft.get('items', []):
        if not item.get('customer_key'): issues.append(f"{item['index']}번 거래처 확인 필요")
        if not item.get('matched'): issues.append(f"{item['index']}번 품목 확인 필요")
        if (not isinstance(item.get('quantity'), (int, float)) or isinstance(item.get('quantity'), bool)
                or not math.isfinite(item['quantity']) or item['quantity'] <= 0):
            issues.append(f"{item['index']}번 수량 확인 필요")
        if item.get('unit') not in {'박스', '단', '송이', '스팀'}: issues.append(f"{item['index']}번 단위 확인 필요")
        if not item.get('product_key'): issues.append(f"{item['index']}번 품목 코드 확인 필요")
    return issues


def review_message(draft):
    customers = list(dict.fromkeys(
        (item.get('customer') or item.get('raw_customer') or '확인 필요')
        for item in draft.get('items', [])))
    common_customer = customers[0] if len(customers) == 1 else None
    lines = [f"[주문 확인 {draft['id']}]", '',
             f"차수 {draft['week'] or '확인 필요'}", '',
             f"업체 {common_customer or '품목별 확인'}", '', '품목', '']
    if draft.get('simulation'):
        lines.insert(0, '[테스트: 실제 주문등록 없음]')
    for item in draft['items']:
        name = item['product'] or item['raw_product'] or '매칭 필요'
        options = item.get('candidate_options') or []
        customer = item.get('customer') or item.get('raw_customer') or '확인 필요'
        prefix = '' if common_customer else f"{customer} / "
        lines.append(f"{item['index']}. {prefix}{name} {_display_quantity(item.get('quantity'))}{item['unit'] or ''}")
        if not item['matched'] and options:
            lines.append('   후보 ' + ' / '.join(
                f"{item['index']}{chr(65 + n)} {option['label']}" for n, option in enumerate(options[:3])))
    issues = validate(draft)
    if issues:
        lines += ['', '확인 필요: ' + ' · '.join(issues)]
    lines += ['', '주문하시겠습니까?', '', '맞으면: 확인']
    unresolved = [i for i in draft['items'] if not i['matched'] and i.get('candidate_options')]
    if unresolved:
        choices = [f"{i['index']}{'B' if n % 2 and len(i['candidate_options']) > 1 else 'A'}"
                   for n, i in enumerate(unresolved)]
        lines.append('후보 선택: ' + ' '.join(choices) + ' (한 품목씩 따로 답변 가능)')
    lines += ['품목·수량 수정: 1, pink mondial 50, 10단',
              '수량만 수정: 2. 2박스',
              '취소: 취소',
              '확인은 내용 확인만 하며, 안내 후 요청번호와 등록을 답해야 실제 등록됩니다.']
    return '\n'.join(lines)


def gate_message(row):
    """Ask the administrator using the untouched Kakao text and match result."""
    room = row['staff_room']
    lines = [f"[발주 전송 확인 {row['id']}]", "기존 카톡 내용 그대로", "",
             str(row.get('raw') or row.get('event', {}).get('content') or '')]
    for item in row.get('items', []):
        customer = (item.get('customer') if item.get('customer_key') else None)
        customer = customer or f"확인 필요 ({item.get('raw_customer') or '미기재'})"
        product = (item.get('product') if item.get('matched') else None)
        if not product:
            candidates = item.get('candidates') or []
            product = ('확인 필요 (후보: ' + ' / '.join(candidates) + ')' if candidates
                       else f"확인 필요 ({item.get('raw_product') or '미기재'})")
        lines += ["", f"{item.get('index', 1)}번 매칭 결과",
                  f"업체 = {customer}", f"품목 = {product}",
                  f"단위 = {item.get('unit') or '확인 필요'}"]
    payload = '\n'.join(lines)
    return (f"{payload}\n\n"
            f"위 내용을 {room} 담당자에게 보낼까요?\n"
            f"보내기: 보내 {row['id']} / 취소: 보내지마 {row['id']}")


def capture(event, parse, master):
    rows = _read(STATE, {})
    if event['event_id'] in rows: return rows[event['event_id']]['id']
    cfg = config()
    if not cfg.get('enabled'): return None
    sender = str(event.get('sender_name') or '').strip()
    allowed = {normalize(value) for value in cfg.get('allowed_senders', []) if str(value).strip()}
    if allowed and normalize(sender) not in allowed:
        return None
    if cfg.get('start_at'):
        from core.keyword_forward import timestamp
        stamp = timestamp(event.get('timestamp', ''))
        if stamp is None or stamp <= datetime.fromisoformat(cfg['start_at']):
            return None
    parsed = parse(event['content'])
    # The Kakao export is authoritative for who requested the order. LLM text
    # extraction frequently cannot infer a staff name from the message body.
    parsed['staff'] = sender
    draft = build_draft(event, parsed, master())
    draft['simulation'] = not cfg.get('write_enabled', False)
    if not draft['items']:
        draft['status'] = 'ignored'
    rows[event['event_id']] = draft
    _save(STATE, rows)
    if draft['items']:
        from core.error_notifications import report
        report('order_received', draft['id'])
    return draft['id']


def parse_command(text, rid, allow_short=True):
    text = text.strip()
    if text == f'{rid} 계정사용 동의': return ('account_consent', True)
    if text == f'{rid} 계정사용 거절': return ('account_consent', False)
    if text == f'{rid} 등록': return ('register', None)
    if text == f'{rid} 확인': return ('confirm', None)
    if text == f'{rid} 취소': return ('cancel', None)
    m = re.fullmatch(re.escape(rid) + r'\s+(\d+)수량=(\d+(?:\.\d+)?)\s*(\S+)', text)
    if m: return ('quantity', (int(m[1]), float(m[2]), m[3]))
    m = re.fullmatch(re.escape(rid) + r'\s+(\d+)=(.+)', text)
    if m: return ('product', (int(m[1]), m[2].strip()))
    m = re.fullmatch(re.escape(rid) + r'\s+(\d+)거래처=(.+)', text)
    if m: return ('item_customer', (int(m[1]), m[2].strip()))
    m = re.fullmatch(re.escape(rid) + r'\s+(거래처|차수)=(.+)', text)
    if m: return (m[1], m[2].strip())
    if not allow_short:
        return None
    compact = re.sub(r'\s+', ' ', text).strip()
    if compact in {'확인', '확정', '네', '맞아요', '맞습니다', 'ㅇㅋ', 'ok', 'OK'}:
        return ('confirm', None)
    if compact in {'취소', '안함', '안 해', '하지마', '하지 마'}:
        return ('cancel', None)
    choices = re.findall(r'(\d+)\s*([A-C])', compact.upper())
    if choices and re.sub(r'[\dA-C\s,]+', '', compact.upper()) == '':
        return ('candidates', [(int(index), ord(letter) - 65) for index, letter in choices])
    m = re.fullmatch(r'(\d+)\s*(?:번|[.,])?\s+(?:수량\s*)?(\d+(?:\.\d+)?)\s*(박스|단|송이|스팀|개)(?:\s*으로\s*입력.*)?', compact)
    if m: return ('quantity', (int(m[1]), float(m[2]), m[3]))
    m = re.fullmatch(r'(\d+)\s*(?:번|[.,])?\s+(.+?)\s*,\s*(\d+(?:\.\d+)?)\s*(박스|단|송이|스팀|개)(?:\s*으로\s*입력.*)?', compact)
    if m: return ('product_quantity', (int(m[1]), m[2].strip(' ,'), float(m[3]), m[4]))
    m = re.fullmatch(r'(\d+)번?\s+거래처\s+(.+)', compact)
    if m: return ('item_customer', (int(m[1]), m[2].strip()))
    m = re.fullmatch(r'(\d+)번?\s+(?:품목\s+)?(.+)', compact)
    if m: return ('product', (int(m[1]), m[2].strip()))
    m = re.fullmatch(r'차수\s+(.+)', compact)
    if m: return ('차수', m[1].strip())
    return None


def _waiting_in_room(rows, room):
    return [(event_id, row) for event_id, row in rows.items()
            if row.get('staff_room') == room and row.get('status') in
            {'waiting', 'request_unknown', 'registration_unknown', 'registering'}]


def revision(row):
    """Bind approval to the exact reviewed destination and item values."""
    value = {k: row.get(k) for k in ('id', 'week', 'staff', 'staff_room')}
    value['items'] = [{k: i.get(k) for k in ('customer_key', 'product_key', 'quantity', 'unit')}
                      for i in row['items']]
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def final_message(row, result):
    """Only a verified registrar receipt may be rendered as completion."""
    if (not isinstance(result, dict) or result.get('verified') is not True
            or result.get('request_id') != row['id'] or result.get('revision') != revision(row)
            or result.get('outcome') not in {'registered', 'already_registered'}):
        raise RuntimeError('최종 주문 검증 결과 누락 또는 요청 불일치')
    groups = result.get('final_orders') or []
    if {str(g.get('customer_key')) for g in groups} != {str(i['customer_key']) for i in row['items']}:
        raise RuntimeError('최종 주문 거래처 범위 불일치')
    already = result['outcome'] == 'already_registered'
    title = '기등록 확인·추가 등록 없음' if already else '주문등록 완료'
    lines = [f"[{title} {row['id']}]", '', row['week']]
    for group in groups:
        if group.get('complete') is not True or group.get('week') != row['week'] or not group.get('items'):
            raise RuntimeError('해당 차수 최종 주문 전체 조회 미확인')
        lines += ['', str(group.get('customer') or group['customer_key']), '']
        for item in group['items']:
            if not item.get('product') or not item.get('unit') or item.get('quantity') is None:
                raise RuntimeError('최종 주문 품목·수량·단위 누락')
            lines.append(f"{item['product']} {_display_quantity(item['quantity'])}{item['unit']}")
    lines += ['', '가 이미 등록되어 있습니다.' if already else '가 주문등록되었습니다.']
    return '\n'.join(lines)


def append_log(row, action, detail):
    from core.error_notifications import report
    report_action = {'review_sent': 'order_review_sent', 'confirm': 'order_confirm',
                     'cancel': 'order_cancel', 'candidates': 'order_candidates',
                     'quantity': 'order_quantity', 'product': 'order_product',
                     'product_quantity': 'order_changed',
                     'account_consent': 'order_account_consent', 'hold': 'order_hold',
                     'item_customer': 'order_changed', '거래처': 'order_changed', '차수': 'order_changed'}
    if action == 'register':
        report('order_' + row['status'] if row['status'] in ('completed', 'completed_simulation') else 'order_register', row['id'])
    elif action in report_action:
        report(report_action[action], row['id'])
    if action == 'error':
        from core.error_notifications import notify
        notify('order_error', row['id'], {
            'source_room': config().get('source', '수입방'),
            'target_room': row.get('staff_room', ''),
            'stage': row.get('status', ''), 'cause': detail,
            'automatic_action': '현재 처리 기록 보존; 불명확한 전송·등록 자동 반복 중지',
        })
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
    from core.moyi_control import OperationPaused
    before = _history(export, row['staff_room'])
    if paused(): raise OperationPaused('일시정지')
    baseline = [e['event_id'] for e in before]
    row.update(status='request_unknown', baseline=baseline)
    rows = _read(STATE, {}); rows[row['event']['event_id']] = row; _save(STATE, rows)
    if paused(): raise OperationPaused('일시정지')
    send(row['staff_room'], message)
    after = _history(export, row['staff_room'])
    new = [e for e in after if e['event_id'] not in baseline and normalize(e['content']) == normalize(message)]
    if len(new) != 1: raise RuntimeError('담당자 카톡 전송 결과 확인 불가')
    row.update(status='waiting', request_event_id=new[0]['event_id'], baseline=baseline)


def _apply(row, command, master_value):
    action, value = command
    if action == 'account_consent':
        row['account_consent'] = bool(value)
        row['account_consent_at'] = time.time()
        if not value:
            row.pop('confirmed_revision', None)
            return '계정 사용 거절을 반영했습니다. 실제 주문등록은 진행하지 않습니다.'
        return ('이번 요청의 담당자 계정 사용 동의를 기록했습니다. 아직 주문등록하지 않았습니다.\n'
                '계정 연결이 필요하면 프로그램의 담당자별 네노바 로그인 설정에서 직접 입력해주세요. '
                '아이디·비밀번호는 카톡에 보내지 마세요.\n' + review_message(row))
    if action == 'cancel': row['status'] = 'cancelled'; return '주문 작업을 취소했습니다.'
    if action == 'confirm':
        issues = validate(row)
        if issues: return '확인이 필요합니다: ' + ' · '.join(issues) + '\n\n' + review_message(row)
        row['confirmed_revision'] = revision(row)
        return (f"[내용 확인 완료 {row['id']}]\n아직 주문등록하지 않았습니다.\n"
                f"등록을 원하시면: {row['id']} 등록\n"
                '수정 답변을 보내면 내용 확인부터 다시 진행합니다.\n'
                + ('현재 테스트 모드이므로 등록 요청도 실제 등록하지 않습니다.' if row.get('simulation')
                   or not config().get('write_enabled') else '등록 연결 준비 상태를 확인한 후 처리합니다.'))
    if action == 'register':
        if not row.get('simulation') and config().get('write_enabled') and not row.get('account_consent'):
            return (f"담당자 본인의 계정 사용 동의가 먼저 필요합니다: {row['id']} 계정사용 동의\n"
                    '계정 사용 동의는 주문 내용 확인 및 등록 요청과 별개입니다.')
        if row.get('confirmed_revision') != revision(row):
            return '현재 품목·수량·단위를 먼저 확인해주세요.\n\n' + review_message(row)
        row['status'] = 'approved'; return None
    row.pop('confirmed_revision', None)
    if action == 'quantity':
        index, quantity, unit = value
        if not 1 <= index <= len(row['items']): raise ValueError('품목 번호 범위 오류')
        row['items'][index-1].update(quantity=quantity, unit=unit)
    elif action == 'candidates':
        for index, option_index in value:
            if not 1 <= index <= len(row['items']): raise ValueError('품목 번호 범위 오류')
            item = row['items'][index-1]
            options = item.get('candidate_options') or []
            if not 0 <= option_index < len(options): raise ValueError(f'{index}번 후보 범위 오류')
            option = options[option_index]
            item.update(matched=True, product=option['label'], product_key=option['product_key'],
                        order_history=option.get('order_history'))
    elif action == 'product':
        index, term = value
        if not 1 <= index <= len(row['items']): raise ValueError('품목 번호 범위 오류')
        matched, candidates = match_one(term, master_value.get('products', {}).get('data', []))
        item = row['items'][index-1]
        item.update(raw_product=term, matched=bool(matched), product=_display_product(matched or {}, item),
                    product_key=(matched or {}).get('nenova_key') or (matched or {}).get('code'),
                    candidates=[_display_product(c, item) for c in candidates])
        _rematch_item(item, master_value)
    elif action == 'product_quantity':
        index, term, quantity, unit = value
        if not 1 <= index <= len(row['items']): raise ValueError('품목 번호 범위 오류')
        matched, candidates = match_one(term, master_value.get('products', {}).get('data', []))
        item = row['items'][index-1]
        item.update(raw_product=term, matched=bool(matched), product=_display_product(matched or {}, item),
                    product_key=(matched or {}).get('nenova_key') or (matched or {}).get('code'),
                    candidates=[_display_product(c, item) for c in candidates],
                    quantity=quantity, unit=unit)
        _rematch_item(item, master_value)
    elif action == 'item_customer':
        index, term = value
        if not 1 <= index <= len(row['items']): raise ValueError('품목 번호 범위 오류')
        matched, candidates = match_one(term, master_value.get('customers', {}).get('data', []))
        row['items'][index-1].update(
            raw_customer=term, customer=(matched or {}).get('name') or term,
            customer_key=(matched or {}).get('nenova_key'),
            customer_candidates=[c.get('name') for c in candidates])
        _rematch_item(row['items'][index-1], master_value)
    elif action == '거래처':
        matched, candidates = match_one(value, master_value.get('customers', {}).get('data', []))
        row.update(customer=(matched or {}).get('name') or value,
                   customer_key=(matched or {}).get('nenova_key'),
                   customer_candidates=[c.get('name') for c in candidates])
        for item in row['items']:
            item.update(raw_customer=value, customer=(matched or {}).get('name') or value,
                        customer_key=(matched or {}).get('nenova_key'),
                        customer_candidates=[c.get('name') for c in candidates])
            _rematch_item(item, master_value)
    elif action == '차수': row['week'] = value
    return review_message(row)


def poll(export, send, master, registrar, paused):
    if paused() or not config().get('enabled'): return
    rows = _read(STATE, {})
    history_cache = {}
    def cached_history(room):
        if room not in history_cache:
            history_cache[room] = _history(export, room)
        return history_cache[room]
    pending = sorted(rows.items(), key=lambda pair: pair[1]['status'] not in ('waiting', 'gate_waiting'))
    for event_id, row in pending:
        if paused(): return
        if row['status'] not in ('draft', 'gate_request_unknown', 'gate_waiting', 'waiting'):
            continue
        allowed = {normalize(s) for s in config().get('allowed_senders', [])}
        if allowed and normalize(row.get('event', {}).get('sender_name', '')) not in allowed:
            continue
        try:
            if row['status'] == 'draft':
                if not row.get('staff_room'):
                    row['status'] = 'hold'; append_log(row, 'hold', '담당자 방 매핑 없음')
                elif _waiting_in_room(rows, row['staff_room']):
                    # A mobile reply such as "확인" must always refer to exactly
                    # one visible review. Queue later drafts for this person.
                    continue
                elif config().get('review_gate_room'):
                    gate = config()['review_gate_room']
                    message = gate_message(row)
                    before = _history(export, gate)
                    row.update(status='gate_request_unknown', gate_room=gate,
                               gate_payload=message,
                               gate_baseline=[e['event_id'] for e in before])
                    rows[event_id] = row; _save(STATE, rows)
                    if paused(): return
                    send(gate, message)
                    after = _history(export, gate)
                    new = [e for e in after if e['event_id'] not in row['gate_baseline']
                           and normalize(e['content']) == normalize(message)]
                    if len(new) != 1:
                        raise RuntimeError('임재용대리 검증 질문 전송 결과 확인 불가')
                    row.update(status='gate_waiting', gate_request_event_id=new[0]['event_id'])
                    append_log(row, 'gate_sent', f"{row['staff_room']} 담당자 전송 전 확인")
                    rows[event_id] = row; _save(STATE, rows)
                    return
                else:
                    _verified_send(row, review_message(row), export, send, paused)
                    append_log(row, 'review_sent', row['staff_room'])
            elif row['status'] == 'gate_request_unknown':
                replies = cached_history(row['gate_room'])
                baseline = set(row.get('gate_baseline', []))
                matches = [e for e in replies if e['event_id'] not in baseline
                           and normalize(e['content']) == normalize(row.get('gate_payload', ''))]
                if len(matches) == 1:
                    row.update(status='gate_waiting', gate_request_event_id=matches[0]['event_id'])
                    append_log(row, 'gate_recovered', '검증 질문 전송 확인 복구')
                return
            elif row['status'] == 'gate_waiting':
                replies = cached_history(row['gate_room'])
                boundary = next((i for i, e in enumerate(replies)
                                 if e['event_id'] == row['gate_request_event_id']), None)
                if boundary is None: continue
                choice = None
                for reply in replies[boundary+1:]:
                    if reply.get('sender_name') != row['gate_room']:
                        continue
                    match = re.fullmatch(r'(보내|보내지마)\s+' + re.escape(row['id']),
                                         reply.get('content', '').strip())
                    if match: choice = match[1]; break
                if not choice: continue
                if choice == '보내지마':
                    row['status'] = 'gate_rejected'
                    append_log(row, 'gate_rejected', '임재용대리 보내지마')
                else:
                    _verified_send(row, review_message(row), export, send, paused)
                    append_log(row, 'review_sent', row['staff_room'])
            elif row['status'] == 'waiting':
                allow_short = len(_waiting_in_room(rows, row['staff_room'])) == 1
                replies = cached_history(row['staff_room'])
                boundary = next((i for i, e in enumerate(replies) if e['event_id'] == row['request_event_id']), None)
                if boundary is None: continue
                command_event = None; command = None
                for reply in replies[boundary+1:]:
                    if reply['sender_name'] != row['staff_room']: continue
                    command = parse_command(reply['content'], row['id'], allow_short=allow_short)
                    if command: command_event = reply; break
                if not command: continue
                row['decision_event_id'] = command_event['event_id']
                needs_master = command[0] in {'product', 'product_quantity', 'item_customer', '거래처'}
                response = _apply(row, command, master() if needs_master else {})
                if row['status'] == 'approved':
                    issues = validate(row)
                    if issues:
                        row['status'] = 'waiting'
                        response = '확인이 필요합니다: ' + ' · '.join(issues) + '\n\n' + review_message(row)
                    elif row.get('simulation') or not config().get('write_enabled', False):
                        row.update(status='completed_simulation', completed_at=time.time())
                        response = (f"[시뮬레이션 완료 {row['id']}]\n"
                                    "검토 답변을 반영했습니다. 실제 주문등록은 0건입니다.")
                    else:
                        issues = validate(row)
                        if issues:
                            row['status'] = 'waiting'
                            response = '등록할 수 없습니다: ' + ' · '.join(issues) + '\n\n' + review_message(row)
                        else:
                            if paused(): return
                            row['status'] = 'registering'; rows[event_id] = row; _save(STATE, rows)
                            result = registrar(row)  # registrar must verify the persisted order
                            response = final_message(row, result)
                            row.update(status='completed', registration_result=result, completed_at=time.time())
                if response:
                    # A reply is itself the new boundary for the next command.
                    row['status_before_reply'] = row['status']
                    row['pending_response'] = response
                    _verified_send(row, response, export, send, paused)
                    row['status'] = row.pop('status_before_reply')
                    row.pop('pending_response', None)
                    # For confirmation, registration must arrive after the new
                    # prompt, not as a previously queued reply before it.
                    if command[0] != 'confirm':
                        row['request_event_id'] = command_event['event_id']
                    append_log(row, command[0], '담당자 답변 적용')
        except Exception as exc:
            from core.moyi_control import OperationPaused
            if isinstance(exc, OperationPaused):
                if row.get('status') == 'registering':
                    row['status'] = 'registration_unknown'
                append_log(row, 'paused', str(exc))
                rows[event_id] = row
                _save(STATE, rows)
                return
            # No automatic retry after a possible external write.
            if row.get('status') == 'registering': row['status'] = 'registration_unknown'
            elif row.get('status') == 'request_unknown': pass
            else: row['status'] = 'hold'
            row['error'] = str(exc)[:300]
            append_log(row, 'error', row['error'])
        rows[event_id] = row
        _save(STATE, rows)
