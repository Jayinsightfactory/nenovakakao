"""Fail-closed import-room order review workflow.

MOYI archival remains independent. This module only prepares a draft, asks the
responsible Kakao contact, applies numbered corrections, and calls a supplied
bulk registrar after an explicit ``<request-id> 등록`` reply.
"""
from __future__ import annotations
import copy, hashlib, json, math, os, re, time
from datetime import datetime
from pathlib import Path
from difflib import SequenceMatcher

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / 'data' / 'import_order_config.json'
STATE = ROOT / 'data' / 'import_order_state.json'
LOG = ROOT / 'data' / 'import_order_events.jsonl'

# Order-room aliases absent from the ERP catalog. Keep origin-sensitive names
# local to order matching so they cannot alter defect registration matching.
ORDER_PRODUCT_NAMES = {'바리스타': 'ROSE / Barista'}
ORDER_ORIGINS = (
    ('콜롬비아', '콜롬비아'), ('에콰도르', '에콰도르'),
    ('네덜란드', '네덜란드'), ('베트남', '베트남'),
    ('태국', '태국'), ('호주', '호주'), ('중국', '중국'),
    ('콜', '콜롬비아'),
)


def _read(path, default):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def _save(path, value):
    from core.atomic_json import save
    if path == STATE:
        value = {**_read(path, {}), **value}
    save(path, value)


# 사장님 지시(2026-11-13): 이 사람들에게는 발주 확인 메시지를 아직 보내지 않는다. 원문은 기록만 남긴다.
# 운영 PC 의 import_order_config.json 에 "excluded_senders" 를 적으면 그 목록이 이 기본값을 대신한다(빈 목록 = 해제).
DEFAULT_EXCLUDED_SENDERS = ['김인석']


def config():
    value = _read(CONFIG, {'enabled': False, 'source': '수입방', 'start_at': None,
                          'staff_rooms': {}, 'allowed_senders': [], 'write_enabled': False})
    value.setdefault('excluded_senders', list(DEFAULT_EXCLUDED_SENDERS))
    from core.operator_settings import CONFIG as OPERATOR_CONFIG, operator_name
    if value.get('review_gate_room') and OPERATOR_CONFIG.exists():
        value['review_gate_room'] = operator_name()
    return value


def registration_enabled():
    cfg = config()
    return cfg.get('transport') == 'direct-api' or bool(cfg.get('browser_order_registration'))


def sender_excluded(event):
    names = {normalize(n) for n in config().get('excluded_senders') or [] if str(n).strip()}
    return bool(names) and normalize((event or {}).get('sender_name', '')) in names


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


def in_current_scope(event, cfg=None):
    """Apply the source cutoff before analysis AND before old draft delivery."""
    cfg = config() if cfg is None else cfg
    if not cfg.get('start_at'):
        return True
    from core.keyword_forward import timestamp
    stamp = timestamp(event.get('timestamp', ''))
    return stamp is not None and stamp > datetime.fromisoformat(cfg['start_at'])


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
    if not key:
        return None, []
    scored = []
    for row in rows:
        aliases = _aliases(row)
        # Numeric catalog codes are valid only as an entire explicit key,
        # never as a substring of a grade such as 8F or a length such as 50cm.
        keys = [normalize(a) for a in aliases if key.isdecimal() or not normalize(a).isdecimal()]
        exact = key and key in keys
        contains = any(len(k) >= 2 and len(key) >= 2 and (key in k or k in key) for k in keys)
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


def match_customer(term, rows):
    # Customer identity requires an exact name/code/alias, never substring match.
    key = normalize(term)
    exact = [r for r in rows if key and key in [normalize(a) for a in _aliases(r)]]
    if not exact:
        from core.defect_matching import CUSTOMER_NAMES
        canonical = CUSTOMER_NAMES.get(key)
        if canonical:
            exact = [r for r in rows if normalize(canonical) in [normalize(a) for a in _aliases(r)]]
    _, candidates = match_one(term, rows)
    return (exact[0] if len(exact) == 1 else None), (exact or candidates)


def _explicit_origin(text):
    """Read only a standalone origin marker from the source; never infer a supplier."""
    text = str(text or '')
    for marker, origin in ORDER_ORIGINS:
        pattern = r'(?<![가-힣])' + re.escape(marker) + r'(?=장미|수국|카네이션|꽃|[\s/:,()]|$)'
        if re.search(pattern, text):
            return origin
    return ''


def _rematch_item(item, master):
    history = master.get('customer_histories', {}).get(str(item.get('customer_key')))
    loader = master.get('customer_history_loader')
    if history is None and loader and item.get('customer_key'):
        try:
            history = loader(item['customer_key'])
        except Exception as exc:
            history = {'status': 'unavailable', 'products': [],
                       'reason': 'authentication_required' if getattr(getattr(exc, 'response', None), 'status_code', None) == 401 else 'lookup_failed'}
    history = history or {'status': 'unavailable', 'products': []}
    if history.get('customer_key') is not None and str(history['customer_key']) != str(item.get('customer_key')):
        history = {'status': 'unavailable', 'products': [], 'reason': 'customer_mismatch'}
    records = {str(r['ProdKey']): r for r in history.get('products', [])} if history['status'] == 'available' else {}
    frequencies = {key: float(r.get('orderFrequency') or 0) for key, r in records.items()}
    products = master.get('products', {})
    products = products.get('data', []) if isinstance(products, dict) else products
    families = {'카네이션': ('carnation', '카네이션'), '장미': ('rose', '장미'),
                '수국': ('hydrangea', '수국'), '호접': ('phalaenopsis', '호접'),
                '심비디움': ('cymbidium', '심비디움')}
    family_names = [name for name in families if name in str(item.get('category') or '')]
    family = families[family_names[0]] if len(family_names) == 1 else None
    if family and not str(item['raw_product']).strip().isdecimal():
        products = [p for p in products if any(token in normalize(
            str(p.get('category', '')) + str(p.get('name', '')) + str(p.get('name_en', '')))
            for token in family)]
    origin = normalize(item.get('origin')) if item.get('origin') else ''
    if origin:
        # If the source names an origin, candidates from another origin are
        # never eligible. An empty filtered set stays unmatched.
        products = [p for p in products if origin in normalize(p.get('origin', ''))]
    from core.defect_matching import PRODUCT_NAMES, norm
    query = item['raw_product']
    # Explicit lengths override vocabulary defaults such as a curated 50cm alias.
    lengths = re.findall(r'\d+(?:\.\d+)?\s*cm', query, re.I)
    if lengths:
        products = [p for p in products if all(normalize(length) in normalize(
            ' '.join(_aliases(p))) for length in lengths)]
    if family:
        # A bare variety is an exact alias of 'Cymbidium / Gerry', but not
        # of 'Cymbidium / Gerry 80cm' or of another flower family.
        products = [{**p, 'name_alias': _aliases(p) + [
            re.sub(r'^\s*(?:' + '|'.join(map(re.escape, family)) + r')\s*[/：:]?\s*', '', alias, flags=re.I)
            for alias in _aliases(p)]} for p in products]
    matched, candidates = match_one(query, products, frequencies)
    known = PRODUCT_NAMES.get(norm(query))
    if not known and lengths:
        known = PRODUCT_NAMES.get(norm(re.sub(r'\d+(?:\.\d+)?\s*cm', '', query, flags=re.I)))
    if known and lengths:
        known = re.sub(r'\d+(?:\.\d+)?\s*cm', '', known, flags=re.I).strip()
        known += ' ' + ' '.join(lengths)
    if not known and item.get('origin') == '콜롬비아':
        known = ORDER_PRODUCT_NAMES.get(norm(query))
    if known:
        curated, curated_candidates = match_one(known, products, frequencies)
        if curated:
            matched, candidates = curated, curated_candidates
    if not matched and known and not candidates:
        query = known
        matched, candidates = match_one(query, products, frequencies)
    if not matched and records and family:
        # Exact catalog color annotation plus actual customer history; a
        # substring or popularity alone must never select a variety.
        historical = [p for p in products if str(p.get('nenova_key') or p.get('code')) in records
                      and any(normalize(query) == normalize(annotation)
                              for alias in _aliases(p)
                              for annotation in re.findall(r'[（(]([^()（）]+)[)）]', alias))]
        if len(historical) == 1:
            matched = historical[0]
    item['matching_query'] = query
    def option(product):
        key = product.get('nenova_key') or product.get('code')
        return {'label': _display_product(product, item), 'product_key': key,
                'order_history': records.get(str(key))}
    item.update(matched=bool(matched), product=_display_product(matched or {}, {**item, 'product': item['raw_product']}),
                product_key=(matched or {}).get('nenova_key') or (matched or {}).get('code'),
                candidates=[_display_product(c, item) for c in candidates],
                candidate_options=[option(c) for c in candidates],
                history_status=history['status'], history_scope=history.get('scope', '상위 10개'),
                history_reason=history.get('reason'),
                order_history=option(matched)['order_history'] if matched else None)


def reanalyze_draft(row, master):
    """Return a source-grounded proposal; never mutate state, approve or register."""
    from core.order_input import body, quantity_evidence
    proposed = copy.deepcopy(row)
    source = body(row.get('event', {}).get('content', row.get('raw', '')))
    lines = source.splitlines()
    customers = master.get('customers', [])
    customers = customers.get('data', []) if isinstance(customers, dict) else customers
    families = [f for f in ('장미', '카네이션', '수국', '심비디움', '호접') if f in source]
    origins = {origin for marker, origin in ORDER_ORIGINS if _explicit_origin(marker) == origin
               and re.search(r'(?<![가-힣])' + re.escape(marker) + r'(?=장미|수국|카네이션|꽃|[\s/:,()]|$)', source)}
    headers = []
    for n, line in enumerate(lines):
        customer, _ = match_customer(re.sub(r'^업체\s*[:：]\s*', '', line.strip()), customers)
        if customer:
            headers.append((n, customer))
    for item in proposed.get('items', []):
        evidence = quantity_evidence(source, str(item.get('raw_product') or ''))
        if len(evidence) == 1 and len(evidence[0][1]) == 1:
            qty, unit = evidence[0][1][0]
            item.update(quantity=float(qty), unit='박스' if unit.lower() == 'box' else '스팀' if unit == '대' else unit)
            positions = [n for n, line in enumerate(lines) if line.strip() == evidence[0][0].strip()]
            preceding = [c for n, c in headers if len(positions) == 1 and n < positions[0]]
            if (not item.get('customer_key') and not str(item.get('raw_customer') or '').strip()
                    and preceding and len({str(c['nenova_key']) for _, c in headers}) == 1):
                c = preceding[-1]
                item.update(customer_key=c['nenova_key'], customer=c['name'], raw_customer=c['name'])
        if not item.get('customer_key'):
            customer, _ = match_customer(item.get('raw_customer', ''), customers)
            if customer:
                item.update(customer_key=customer['nenova_key'], customer=customer['name'])
        if len(families) == 1:
            item['category'] = families[0]
        if len(origins) == 1:
            item['origin'] = next(iter(origins))
        _rematch_item(item, master)
    # Keep the original week, approvals and status. This proposal is not an approval.
    return proposed


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
    customer, customer_candidates = match_customer(default_customer, customers)
    items = []
    for index, raw in enumerate(parsed.get('items') or [], 1):
        raw_customer = raw.get('customer') or default_customer
        item_customer, item_customer_candidates = match_customer(raw_customer, customers)
        product, candidates = match_one(raw.get('product', ''), products)
        items.append({
            'index': index, 'raw_product': raw.get('product', ''),
            'raw_customer': raw_customer,
            'customer': (item_customer or {}).get('name') or raw_customer,
            'customer_key': (item_customer or {}).get('nenova_key'),
            'customer_candidates': [c.get('name') for c in item_customer_candidates],
            'quantity': raw.get('quantity'), 'unit': raw.get('unit') or '',
            'category': raw.get('category') or '',
            'origin': (_explicit_origin(raw.get('origin'))
                       or str(raw.get('origin') or '').strip()),
            'matched': bool(product),
            'product': _display_product(product or {}, raw),
            'product_key': (product or {}).get('nenova_key') or (product or {}).get('code'),
            'candidates': [_display_product(c, raw) for c in candidates],
            'candidate_options': [
                {'label': _display_product(c, raw),
                 'product_key': c.get('nenova_key') or c.get('code')}
                for c in candidates],
        })
    items = reanalyze_draft({'event': event, 'items': items}, master)['items']
    if config().get('llm_history_review'):
        from core.order_llm import review_candidates
        for item in items:
            if not item.get('matched'):
                review_candidates(item)
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
    from core.order_input import source_issues
    issues = source_issues(draft.get('event', {}).get('content', draft.get('raw', '')))
    if not draft.get('staff_room'): issues.append('담당자 카카오톡 방 매핑 필요')
    if not draft.get('week'): issues.append('차수 확인 필요')
    elif registration_enabled() and not draft.get('simulation'):
        from core.order_registration import scope
        try: scope(draft)
        except (ValueError, KeyError): issues.append('차수를 41-1처럼 확인해주세요')
    if not draft.get('items'): issues.append('품목 없음')
    for item in draft.get('items', []):
        if item.get('catalog_revalidation_issue'):
            issues.append(f"{item['index']}번 기존 품목 재확인 필요: " + item['catalog_revalidation_issue'])
        if str(item.get('raw_customer') or '').strip() in {'콜', '콜롬비아', '중국', '베트남', '네덜란드', '태국', '호주'}:
            issues.append(f"{item['index']}번 원산지를 업체로 사용하지 않음: 업체 확인 필요")
        if not item.get('customer_key'): issues.append(f"{item['index']}번 거래처 확인 필요")
        if not item.get('matched'): issues.append(f"{item['index']}번 품목 확인 필요")
        if (not isinstance(item.get('quantity'), (int, float)) or isinstance(item.get('quantity'), bool)
                or not math.isfinite(item['quantity']) or item['quantity'] <= 0):
            issues.append(f"{item['index']}번 수량 확인 필요")
        if item.get('unit') not in {'박스', '단', '송이', '스팀'}: issues.append(f"{item['index']}번 단위 확인 필요")
        if not item.get('product_key'): issues.append(f"{item['index']}번 품목 코드 확인 필요")
    return issues


def review_message(draft):
    from core.staff_request_text import original_first
    details = _review_message_details(draft)
    if draft.get('require_request_id'):
        rid = draft['id']
        details += ('\n\n여러 요청이 있으므로 모든 답장 앞에 요청번호를 적어주세요.'
                    + f'\n확인: {rid} 확인\n후보 선택: {rid} 1A'
                    + f'\n품목 수정: {rid} 1 품목명\n취소: {rid} 취소')
    return original_first(draft, details)


def _review_message_details(draft):
    if registration_enabled() and not draft.get('simulation'):
        lines = [f"[주문 {draft['id']}] {draft.get('week') or '차수 확인 필요'}"]
        for item in draft['items']:
            customer = item.get('customer') or item.get('raw_customer') or '업체 미기재'
            lines.append(f"{item['index']}. {customer} · {item.get('product') or item.get('raw_product')} {_display_quantity(item.get('quantity'))}{item.get('unit')}")
            if not item.get('matched'):
                lines.extend(f"  {item['index']}{chr(65+n)} {o['label']}" for n, o in enumerate(item.get('candidate_options', [])[:3]))
        issues = validate(draft)
        if issues:
            lines += ['미등록: 정보 보완이 필요합니다. 확인만 답해도 등록되지 않습니다.',
                      '확인 필요: ' + ' · '.join(issues)]
        else:
            lines.append('맞으면: 확인 → 주문등록')
        lines += ['후보 선택: 1A / 품목 수정: 1 품목명',
                  '업체 수정: 1 거래처 업체명',
                  '차수 수정: 차수 41-1 / 취소: 안함']
        return '\n'.join(lines)
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


def analysis_retry_due(row):
    return (row.get('status') == 'analysis_error' and not row.get('items')
            and int(row.get('analysis_attempts', 1)) < 3
            and time.time() >= row.get('analysis_retry_at', 0))


def capture(event, parse, master):
    rows = _read(STATE, {})
    previous = rows.get(event['event_id'])
    if previous and not analysis_retry_due(previous): return previous['id']
    cfg = config()
    if not cfg.get('enabled'): return None
    sender = str(event.get('sender_name') or '').strip()
    allowed = {normalize(value) for value in cfg.get('allowed_senders', []) if str(value).strip()}
    from core.workflow_settings import config as workflow_config
    review_only = workflow_config()['order_review_only']
    if not review_only and allowed and normalize(sender) not in allowed:
        return None
    if cfg.get('start_at'):
        from core.keyword_forward import timestamp
        stamp = timestamp(event.get('timestamp', ''))
        if stamp is None or stamp <= datetime.fromisoformat(cfg['start_at']):
            return None
    try:
        parsed = parse(event['content'])
        # The Kakao export is authoritative for who requested the order. LLM text
        # extraction frequently cannot infer a staff name from the message body.
        parsed['staff'] = sender
        draft = build_draft(event, parsed, master())
    except Exception as exc:
        from core.moyi_control import OperationPaused
        if isinstance(exc, OperationPaused): raise
        if not review_only: raise
        draft = {'id': 'ORD-' + hashlib.sha256(event['event_id'].encode()).hexdigest()[:8].upper(),
                 'event': event, 'status': 'analysis_error', 'created_at': time.time(),
                 'staff': sender, 'staff_room': cfg.get('staff_rooms', {}).get(sender, ''),
                 'items': [], 'error': str(exc)[:300], 'questions': ['분석 실패; 원문 직접 검토 필요']}
    draft['simulation'] = not cfg.get('write_enabled', False)
    draft['analysis_attempts'] = int((previous or {}).get('analysis_attempts', 1 if previous else 0)) + 1
    if previous:
        draft['analysis_history'] = [*previous.get('analysis_history', []),
            {'error': previous.get('error'), 'at': previous.get('created_at')}]
    if draft.get('status') == 'analysis_error':
        draft['analysis_retry_at'] = time.time() + 300
    if not draft['items'] and draft.get('status') != 'analysis_error':
        draft['status'] = 'ignored'
    elif draft.get('status') == 'draft' and sender_excluded(event):
        draft.update(status='sender_excluded', previous_status='draft')
    rows = _read(STATE, {})
    if event['event_id'] in rows and rows[event['event_id']] != previous:
        return rows[event['event_id']]['id']
    rows[event['event_id']] = draft
    _save(STATE, {event['event_id']: draft})
    if draft['items'] and draft.get('status') != 'sender_excluded':
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
    if text.startswith(rid + ' '):
        return parse_command(text[len(rid):].strip(), rid, allow_short=True)
    if not allow_short:
        return None
    compact = re.sub(r'\s+', ' ', text).strip()
    if compact in {'확인', '확정', '네', '맞아', '맞아요', '맞습니다', 'ㅇㅋ', 'ok', 'OK'}:
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


def _review_slot_available(rows, room, request=None):
    from core.keyword_forward import timestamp
    waiting = _waiting_in_room(rows, room)
    # A past uncertain question must not be replayed or treated as approved.
    # A distinct current request can ask its own explicitly numbered question;
    # uncertain writes and current-scope uncertain sends still block the room.
    independent = bool(request and request.get('require_request_id')
                       and config().get('start_at') and in_current_scope(request.get('event', {})))
    return all(r['status'] == 'waiting' or (
        independent and r['status'] == 'request_unknown'
        and r.get('id') and r['id'] != request.get('id')
        and timestamp(r.get('event', {}).get('timestamp', '')) is not None
        and not in_current_scope(r['event'])) for _, r in waiting)


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
    row.update(status='request_unknown', baseline=baseline, request_payload=message)
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
        if registration_enabled() and not row.get('simulation'):
            row['status'] = 'approved'
            return None
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
            item.pop('catalog_revalidation_issue', None)
    elif action == 'product':
        index, term = value
        if not 1 <= index <= len(row['items']): raise ValueError('품목 번호 범위 오류')
        matched, candidates = match_one(term, master_value.get('products', {}).get('data', []))
        item = row['items'][index-1]
        item.update(raw_product=term, matched=bool(matched), product=_display_product(matched or {}, item),
                    product_key=(matched or {}).get('nenova_key') or (matched or {}).get('code'),
                    candidates=[_display_product(c, item) for c in candidates])
        _rematch_item(item, master_value)
        item.pop('catalog_revalidation_issue', None)
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
        item.pop('catalog_revalidation_issue', None)
    elif action == 'item_customer':
        index, term = value
        if not 1 <= index <= len(row['items']): raise ValueError('품목 번호 범위 오류')
        matched, candidates = match_customer(term, master_value.get('customers', {}).get('data', []))
        row['items'][index-1].update(
            raw_customer=term, customer=(matched or {}).get('name') or term,
            customer_key=(matched or {}).get('nenova_key'),
            customer_candidates=[c.get('name') for c in candidates])
        _rematch_item(row['items'][index-1], master_value)
    elif action == '거래처':
        matched, candidates = match_customer(value, master_value.get('customers', {}).get('data', []))
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
    review_sent_rooms = set()
    def cached_history(room):
        if room not in history_cache:
            history_cache[room] = _history(export, room)
        return history_cache[room]
    pending = sorted(rows.items(), key=lambda pair: pair[1]['status'] not in ('waiting', 'gate_waiting'))
    for event_id, row in pending:
        if paused(): return
        if row['status'] in ('draft', 'waiting', 'gate_waiting', 'gate_request_unknown') and not in_current_scope(row.get('event', {})):
            row.update(previous_scope_status=row['status'], status='archived_previous_scope')
            rows[event_id] = row; _save(STATE, rows)
            continue
        if row['status'] in ('draft', 'waiting', 'gate_waiting', 'gate_request_unknown') and sender_excluded(row.get('event')):
            row.update(previous_status=row['status'], status='sender_excluded')
            rows[event_id] = row; _save(STATE, rows)
            append_log(row, 'sender_excluded', '확인 메시지 발송 제외 대상')
            continue
        # Recovery is read-only after a possible write; the registrar's journal
        # never issues a second POST for an uncertain customer group.
        if registration_enabled() and row['status'] in ('registering', 'registration_unknown') and time.time() >= row.get('next_reconcile_at', 0):
            try:
                row['status'] = 'registering'
                rows[event_id] = row; _save(STATE, rows)
                result = registrar(row)
                response = final_message(row, result)
                row.update(status='completed', registration_result=result, completed_at=time.time(),
                           pending_response=response, status_before_reply='completed')
                rows[event_id] = row; _save(STATE, rows)
            except Exception as exc:
                from core.order_registration import OrderRejected
                if isinstance(exc, OrderRejected):
                    row.update(status='hold', error='전산 거절(저장된 내용 없음): ' + str(exc)[:260])
                    append_log(row, 'rejected', row['error'])
                    rows[event_id] = row; _save(STATE, rows)
                    continue
                row.update(status='registration_unknown', error=str(exc)[:300], next_reconcile_at=time.time()+300)
                rows[event_id] = row; _save(STATE, rows)
                continue
        if row.get('pending_response') and row['status'] == 'completed':
            try:
                _verified_send(row, row['pending_response'], export, send, paused)
                row.update(status='completed', completion_sent_at=time.time())
                row.pop('pending_response', None); row.pop('status_before_reply', None)
            finally:
                rows[event_id] = row; _save(STATE, rows)
            return
        if row['status'] == 'request_unknown':
            replies = cached_history(row['staff_room'])
            payload = row.get('pending_response') or row.get('request_payload')
            matches = [e for e in replies if e['event_id'] not in row.get('baseline', [])
                       and payload and normalize(e['content']) == normalize(payload)]
            if len(matches) == 1:
                row.update(status=row.pop('status_before_reply', 'waiting'), request_event_id=matches[0]['event_id'])
                row.pop('pending_response', None)
                rows[event_id] = row; _save(STATE, rows)
            continue
        if row['status'] not in ('draft', 'gate_request_unknown', 'gate_waiting', 'waiting'):
            continue
        from core.operator_settings import CONFIG as OPERATOR_CONFIG
        if OPERATOR_CONFIG.exists() and row.get('gate_room') and row['gate_room'] != config().get('review_gate_room') and row['status'] in ('gate_waiting', 'gate_request_unknown'):
            row.update(previous_operator_status=row['status'], status='operator_changed_review')
            _save(STATE, rows)
            continue
        allowed = {normalize(s) for s in config().get('allowed_senders', [])}
        if allowed and normalize(row.get('event', {}).get('sender_name', '')) not in allowed:
            continue
        try:
            if row['status'] == 'draft':
                from core.order_input import reports_existing_registration
                if reports_existing_registration(row.get('event', {}).get('content', '')):
                    row.update(status='existing_registration_review',
                               error='원문 전산등록완료 표시: 기존 등록 대조 전 신규 ADD 금지')
                    rows[event_id] = row; _save(STATE, rows)
                    append_log(row, 'existing_registration_review', row['error'])
                    continue
                if not row.get('staff_room'):
                    row['status'] = 'hold'; append_log(row, 'hold', '담당자 방 매핑 없음')
                elif row['staff_room'] in review_sent_rooms or not _review_slot_available(
                        rows, row['staff_room'], {**row, 'require_request_id': not config().get('review_gate_room')}):
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
                        raise RuntimeError('승인 담당자 검증 질문 전송 결과 확인 불가')
                    row.update(status='gate_waiting', gate_request_event_id=new[0]['event_id'])
                    append_log(row, 'gate_sent', f"{row['staff_room']} 담당자 전송 전 확인")
                    rows[event_id] = row; _save(STATE, rows)
                    return
                else:
                    row['require_request_id'] = True
                    _verified_send(row, review_message(row), export, send, paused)
                    review_sent_rooms.add(row['staff_room'])
                    append_log(row, 'review_sent', row['staff_room'])
            elif row['status'] == 'gate_request_unknown':
                replies = cached_history(row['gate_room'])
                baseline = set(row.get('gate_baseline', []))
                matches = [e for e in replies if e['event_id'] not in baseline
                           and normalize(e['content']) == normalize(row.get('gate_payload', ''))]
                if len(matches) == 1:
                    row.update(status='gate_waiting', gate_request_event_id=matches[0]['event_id'])
                    append_log(row, 'gate_recovered', '검증 질문 전송 확인 복구')
                    rows[event_id] = row; _save(STATE, rows)
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
                    append_log(row, 'gate_rejected', '승인 담당자 보내지마')
                else:
                    _verified_send(row, review_message(row), export, send, paused)
                    append_log(row, 'review_sent', row['staff_room'])
            elif row['status'] == 'waiting':
                allow_short = (not row.get('require_request_id')
                               and len(_waiting_in_room(rows, row['staff_room'])) == 1)
                replies = cached_history(row['staff_room'])
                boundary = next((i for i, e in enumerate(replies) if e['event_id'] == row['request_event_id']), None)
                if boundary is None: continue
                command_event = None; command = None
                for reply in replies[boundary+1:]:
                    if reply['sender_name'] != row['staff_room']: continue
                    command = parse_command(reply['content'], row['id'], allow_short=allow_short)
                    if command and command[0] in ('confirm', 'register') and row.get('approval_boundary_event_id'):
                        visible = next((i for i, e in enumerate(replies) if e['event_id'] == row['approval_boundary_event_id']), None)
                        if visible is None or replies.index(reply) <= visible:
                            command = None
                            continue
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
                            from core.order_registration import OrderRejected
                            try:
                                result = registrar(row)  # registrar must verify the persisted order
                            except OrderRejected as exc:
                                row.update(status='waiting', error=str(exc)[:300])
                                append_log(row, 'rejected', row['error'])
                                response = ('전산에 등록되지 않았습니다(저장된 내용 없음): ' + str(exc)[:200]
                                            + '\n내용을 고치거나 잠시 후 다시 확인해주세요.\n\n' + review_message(row))
                            else:
                                response = final_message(row, result)
                                row.update(status='completed', registration_result=result, completed_at=time.time(),
                                           pending_response=response, status_before_reply='completed')
                                # 등록 사실을 알림 전송보다 먼저 남긴다. 전송이 실패해도 '보류'로 덮이지 않는다.
                                rows[event_id] = row; _save(STATE, rows)
                if response:
                    # A reply is itself the new boundary for the next command.
                    row['status_before_reply'] = row['status']
                    row['pending_response'] = response
                    _verified_send(row, response, export, send, paused)
                    row['status'] = row.pop('status_before_reply')
                    row.pop('pending_response', None)
                    # Any later approval must follow the newly displayed
                    # revision, including after a product/quantity correction.
                    row['approval_boundary_event_id'] = row['request_event_id']
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
            elif row.get('status') == 'completed' and row.get('pending_response'): pass  # 등록 완료, 알림만 다음 주기에 재확인
            else: row['status'] = 'hold'
            row['error'] = str(exc)[:300]
            append_log(row, 'error', row['error'])
        rows[event_id] = row
        _save(STATE, rows)
