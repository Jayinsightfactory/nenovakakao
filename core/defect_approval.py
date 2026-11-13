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
from core.defect_matching import match as match_one, customer as match_customer, PRODUCT_NAMES, CATEGORY_NAMES, ORIGIN_NAMES

ROOT = Path(__file__).resolve().parents[1] / 'data' / 'defect_requests'


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


RECIPIENTS = {'박성수':'박성수', '정재훈':'정재훈', '조현욱':'조현욱', '김원영':'김원영차장', '김원영차장':'김원영차장'}

def recipient_for(event):
    return RECIPIENTS.get(event.get('sender_name', ''))

def source_key(event):
    key = (event.get('sender_name'), event.get('timestamp'), clean_text(event.get('content', '')).split())
    # Older requests have no occurrence_index and continue to identify the
    # first matching message. Later identical messages in the same minute are
    # distinct source events and must not be deduplicated against that first.
    occurrence = event.get('occurrence_index')
    try:
        occurrence = int(occurrence)
    except (TypeError, ValueError):
        occurrence = 1
    return (*key, occurrence) if occurrence > 1 else key

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


LEARNED_ALIASES = ROOT.parent / 'defect_learned_aliases.json'
_learned_cache = {'mtime': None, 'value': {}}


def learned_aliases():
    """Aliases learned from Kakao text joined to posted ERP deductions."""
    try:
        mtime = LEARNED_ALIASES.stat().st_mtime_ns
    except OSError:
        return {}
    if _learned_cache['mtime'] != mtime:
        try:
            value = json.loads(LEARNED_ALIASES.read_text(encoding='utf-8')).get('aliases', {})
        except (OSError, ValueError):
            value = {}
        _learned_cache.update(mtime=mtime, value=value)
    return _learned_cache['value']


def origin_priors():
    """category -> origin, learned where posted deductions use one origin only."""
    learned_aliases()  # refresh the cache when the file changed
    try:
        return json.loads(LEARNED_ALIASES.read_text(encoding='utf-8')).get('origin_priors', {})
    except (OSError, ValueError):
        return {}


def learn_from_approval(row):
    """Record 'first wording -> approved product' for staff-corrected items."""
    from core.defect_matching import norm
    pairs = [(norm(item['original_product_raw']), item['product']['name'].strip())
             for item in row.get('items', [])
             if item.get('original_product_raw') and item.get('product')]
    pairs = [(alias, target) for alias, target in pairs if len(alias) >= 2 and not alias.isdecimal()]
    if not pairs:
        return 0
    try:
        document = json.loads(LEARNED_ALIASES.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        document = {}
    aliases = document.setdefault('aliases', {})
    bucket = aliases.setdefault(row['extracted'].get('category') or '*', {})
    for alias, target in pairs:
        previous = bucket.get(alias, {})
        support = previous.get('support', 0) + 1 if previous.get('target') == target else 1
        bucket[alias] = {'target': target, 'support': support, 'source': 'staff_correction'}
    save(LEARNED_ALIASES, document)
    return len(pairs)


def _fallback_match(query, size, products, category, learned=None, catalog=None):
    """Learned alias first, then Hangul-to-English phonetic similarity."""
    from core.defect_matching import norm
    from core import defect_phonetic
    sized = lambda rows, cm: [p for p in rows if re.search(r'(?<!\d)' + cm + r'\s*cm\b', p.get('name', ''), re.I)]
    has_size = lambda rows: any(re.search(r'\d+\s*cm\b', p.get('name', ''), re.I) for p in rows)
    table = learned_aliases() if learned is None else learned
    entry = (table.get(category or '', {}) or {}).get(norm(query)) or table.get('*', {}).get(norm(query))
    if entry:
        target = entry['target']
        if size:
            target = re.sub(r'\d+\s*cm\b', size[1] + 'cm', target, flags=re.I)
        found = [p for p in products if str(p.get('name', '')).strip() == target]
        if len(found) == 1:
            default = '50cm' if not size and re.search(r'(?<!\d)50\s*cm\b', target, re.I) else None
            return found[0], found, 'learned', default
    pool, default = products, None
    spray = re.match(r'\s*(?:스프레이|sp)\s*(\S.*)', query, re.I)
    if spray and category in ('카네이션', '미니카네이션') and catalog is not None:
        # "스프레이 화이트" under 카네이션 means the spray/mini carnation rows,
        # which the master files under several categories.
        pool = [p for p in catalog if re.match(
            r'\s*(?:\[[^\]]*\]\s*)?(?:mini\s*carnation|spa?r[ay]y?\s*carnation)', p.get('name', ''), re.I)]
        chosen, candidates = defect_phonetic.resolve(spray[1], pool)
        if chosen:
            return chosen, candidates, 'phonetic', None
        pool = products
    if size:
        pool = sized(products, size[1])
    elif has_size(products):
        pool = sized(products, '50') + [p for p in products if not re.search(r'\d+\s*cm\b', p.get('name', ''), re.I)]
    chosen, candidates = defect_phonetic.resolve(query, pool)
    if not chosen and len(candidates) > 1 and len({c.get('name') for c in candidates}) == 1:
        # One catalog name under several origins: posted ERP deductions for
        # this category may show that only one origin is ever used.
        prior = origin_priors().get(category or '')
        preferred = [c for c in candidates if prior and c.get('origin') == prior]
        if len(preferred) == 1:
            chosen = preferred[0]
    if chosen and not size and re.search(r'(?<!\d)50\s*cm\b', chosen.get('name', ''), re.I):
        default = '50cm'
    return chosen, candidates, ('phonetic' if chosen else None), default


def rematch(row, master, learned=None):
    result = deepcopy(row)
    source = result['extracted']
    customer, customers = match_customer(source, _data(master, 'customers'))
    result['customer'] = customer
    result['customer_candidates'] = customers
    products = _data(master, 'products')
    origin_products = [p for p in products if not source.get('origin') or p.get('origin') == source['origin']]
    category = source.get('category')
    # Never silently accept the same variety name from another flower category.
    if category:
        # Staff write "알스트로메리아" while the master category is "알스트로".
        known = {p.get('category') for p in products if p.get('category')}
        if category not in known:
            near = sorted(c for c in known if c.startswith(category) or category.startswith(c))
            if len(near) == 1:
                category = near[0]
        products = [p for p in products if p.get('category') == category or (
            category == '델피늄' and source.get('origin') == '중국'
            and p.get('category') == '기타'
            and re.match(r'^Delphinium\s+CHINA\s*/', p.get('name', ''), re.I))]
    if source.get('origin'):
        products = [p for p in products if p.get('origin') == source['origin']]
    products = deepcopy(products)
    for p in products:
        if re.search(r'\bTinted Blue\b', p.get('name', ''), re.I):
            p['name_alias'] = list(p.get('name_alias') or []) + ['틴티드블루']
    items = []
    vocabulary = {**PRODUCT_NAMES, **CATEGORY_NAMES.get(category, {}),
                  **ORIGIN_NAMES.get(source.get('origin'), {})}
    if category == '델피늄' and source.get('origin') == '중국':
        vocabulary.update({'화이트': 'Delphinium CHINA / 화이트 White',
                           '화이트델피늄': 'Delphinium CHINA / 화이트 White'})
    distribution = master.get('distribution', {})
    periods = []
    if customer and distribution.get('status') == 'available' and str(
            distribution.get('customer_key')) == str(customer['nenova_key']):
        from core.defect_distribution import scopes
        expected = scopes(row)
        periods = [p for y, w in expected for p in distribution.get('periods', [])
                   if p.get('year') == y and p.get('week') == w]
    result['distribution_lookup'] = distribution
    for item in source['items']:
        term = item['product_raw']
        # A staff-specified catalog key is more precise than a broad category
        # inferred from the original wording (e.g. 중국 안개 is filed as 기타).
        if str(term).isdecimal():
            chosen = [p for p in origin_products if str(p.get('nenova_key')) == str(term)]
            if len(chosen) == 1:
                items.append({**item, 'product': chosen[0], 'candidates': chosen,
                              'default_size': None, 'matched_by': 'explicit_key',
                              'distribution_evidence': None})
                continue
        size = re.search(r'(\d+)\s*cm\b', term, re.I)
        explicit_key = any(str(p.get('nenova_key')) == term for p in products)
        query = re.sub(r'\d+\s*cm\b', '', term, flags=re.I).strip() if size else term
        pool = products
        if size:
            pool = [p for p in products if re.search(r'(?<!\d)' + size[1] + r'\s*cm\b', p.get('name', ''), re.I)]
        product, candidates = match_one(term, pool, vocabulary)
        default_size = None
        if not size and not explicit_key and any(re.search(r'\d+\s*cm\b', p.get('name', ''), re.I) for p in candidates):
            pool = [p for p in candidates if re.search(r'(?<!\d)50\s*cm\b', p.get('name', ''), re.I)]
            product, candidates = match_one(term, pool, vocabulary)
            default_size = '50cm'
        matched_by = 'catalog' if product else None
        if not product and not explicit_key:
            product, extra, matched_by, fallback_size = _fallback_match(
                query, size, products, category, learned, _data(master, 'products'))
            candidates = candidates or extra
            default_size = default_size or fallback_size
        evidence = None
        if not explicit_key:
            for period in periods:
                keys = set(map(str, period['product_keys']))
                allocated = [p for p in products if str(p.get('nenova_key')) in keys]
                cm = size[1] if size else '50'
                allocated = [p for p in allocated if (
                    re.search(r'(?<!\d)' + cm + r'\s*cm\b', p.get('name', ''), re.I)
                    or (not size and not re.search(r'\d+\s*cm\b', p.get('name', ''), re.I)))]
                chosen, options = match_one(term, allocated, vocabulary)
                if not chosen:
                    chosen, options, _, _ = _fallback_match(
                        query, size, allocated, category, learned, allocated)
                if chosen:
                    product, candidates = chosen, options
                    matched_by = 'customer_distribution'
                    default_size = '50cm' if not size and re.search(
                        r'(?<!\d)50\s*cm\b', chosen.get('name', ''), re.I) else None
                    evidence = {'customer_key': customer['nenova_key'],
                                'year': period['year'], 'week': period['week']}
                    break
        # Prefer source-period customer shipment evidence in the explanation,
        # without turning an ambiguous name into a confirmed catalog key.
        history_keys = {str(key) for period in periods for key in period['product_keys']}
        candidates = sorted(candidates, key=lambda c: str(c.get('nenova_key')) not in history_keys)
        candidate_history = {
            str(c['nenova_key']): [f"{p['year']}년 {p['week']}차" for p in periods
                                   if str(c['nenova_key']) in set(map(str, p['product_keys']))]
            for c in candidates if str(c.get('nenova_key')) in history_keys}
        items.append({**item, 'product': product, 'candidates': candidates,
                      'candidate_history': candidate_history,
                      'default_size': default_size, 'matched_by': matched_by,
                      'distribution_evidence': evidence})
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
                    and i['unit_raw'] in ('단', '박스', 'BOX', 'box', '대', '스팀', '스팀(대)', '송이')
                    for i in row['items']))


def label(row):
    if row.get('reply_number'):
        return str(row['reply_number'])
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


def clarification_field(row):
    source = row['extracted']
    if not source.get('sequence'):
        return 'sequence', None
    if not row.get('customer'):
        return ('full', None) if len(source.get('items', [])) > 1 else ('customer', None)
    for index, item in enumerate(row.get('items', [])):
        if not item.get('product'):
            return 'product', index
    return 'full', None


def question(row):
    from core.staff_request_text import original_first
    return original_first(row, _question_details(row))


def _question_details(row):
    if row.get('information_only'):
        n = label(row)
        source = row['extracted']
        customer = (row.get('customer') or {}).get('name') or source.get('customer') or ''
        items = row.get('items') or source['items']
        summary = ' / '.join(f"{i['product_raw']} {i['quantity_raw']}{i['unit_raw']}" for i in items)
        field, index = clarification_field(row)
        if field == 'sequence':
            prompt, example = '원래 몇 차수 물건인가요?\n등록은 카톡 작성 시점과 마감 여부를 확인해 등록 가능한 차수로 처리합니다.', f'{n} 39차'
        elif field == 'customer':
            candidates = row.get('customer_candidates') or []
            if source.get('customer') and candidates:
                names = list(dict.fromkeys(c['name'] for c in candidates))
                prompt = '전산 업체를 골라주세요: ' + ' / '.join(names)
                example = f'{n} {names[0]}'
            else:
                prompt, example = '어느 업체 불량인가요?', f'{n} 업체명'
        elif field == 'product':
            item = items[index]
            if '주밀리아' in item['product_raw']:
                prompt, example = '주밀리아 일반인가요, DY인가요?', f'{n} 일반'
            else:
                prompt, example = f"{item['product_raw']}의 정확한 품종·색상을 알려주세요.", f'{n} 품종명 색상'
            candidates = item.get('candidates') or []
            if candidates:
                comparison = []
                for candidate in candidates:
                    key = str(candidate['nenova_key'])
                    history = (item.get('candidate_history') or {}).get(key, [])
                    basis = ' · 해당 업체 출고이력: ' + ', '.join(history) if history else ' · 해당 업체 거래이력 일치 미확인'
                    origin = f" · {candidate['origin']}" if candidate.get('origin') else ''
                    comparison.append(f"- [{key}] {candidate['name']}{origin}{basis}")
                prompt = '구분이 필요한 전산 후보:\n' + '\n'.join(comparison) + '\n' + prompt
                prompt += '\n후보가 맞지 않으면 실제 품종명·색상을 적어주세요.'
                example = f"{n} 품목 {index+1}={candidates[0]['nenova_key']}"
            else:
                prompt = '원문에 맞는 전산 후보를 찾지 못했습니다.\n' + prompt
        else:
            prompt = '업체별 차수·품목·수량을 다시 적어주세요.'
            example = f'{n} 원문 뒤에 수정 내용'
        return f"{n} · {customer} {summary}\n{prompt}\n답장 예: {example}"
    customer = row.get('customer') or {}
    lines = [f"불량 확인 {label(row)} · {row['extracted']['sequence']} {customer.get('name') or row['extracted'].get('customer') or '거래처 확인 필요'}"]
    if row.get('replaces_label'):
        lines.append(f"이전 {row['replaces_label']} 대신 이 번호로 답해주세요.")
    # A clarification message can be sent before destination-period resolution:
    # that resolution depends on the support registration ledger and is only
    # required for submission. Requiring it here made unmatched requests fail
    # before staff could answer, even though the question itself is valid.
    lines.append('등록 차수: 카톡 작성 시점과 영업지원 전산등록 마감 여부 확인 후 결정')
    for index, item in enumerate(row.get('items', []), 1):
        product = item.get('product') or {}
        lines.append(f"{'원문' if len(row['items']) == 1 else str(index)+'. 원문'}: {item['product_raw']} {item['quantity_raw']}{item['unit_raw']}")
        if product:
            lines.append(f"매칭: {product['name']} {item['quantity_raw']}{item['unit_raw']}")
        else:
            shown = [c['name'] for c in item.get('candidates', [])]
            for n, candidate in enumerate(item.get('candidates', []), 1):
                # Same catalog name under two origins is unreadable without it.
                origin = f" ({candidate.get('origin')})" if shown.count(candidate['name']) > 1 and candidate.get('origin') else ''
                lines.append(f"{n}) {candidate['name']}{origin}")
            if item.get('candidates'):
                lines.append(f"선택: {label(row)} 선택 {index}=번호")
    if ready(row): lines.append(f"맞으면 {label(row)} 맞아\n다르면 {label(row)} 틀려\n처리 안 하면 {label(row)} 안함")
    else:
        lines.append('매칭 확인이 필요합니다.')
        lines.append(f"답장: {label(row)} 품목 품목명" if len(row.get('items',[]))==1 else f"답장: {label(row)} 품목 1=품목명")
        lines.append(f"처리 안 하면 {label(row)} 안함")
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
    content = event.get('content', '').strip()
    if result.get('information_only'):
        short = re.fullmatch(re.escape(label(result)) + r'\s+(.+)', content, re.S)
        if short and not re.match(r'^(?:맞아|승인|틀려|틀림|안함|안보내|품목|수량|거래처|차수|선택|원문)(?:\s|=|$)', short[1]):
            answer = short[1].strip()
            field, index = clarification_field(result)
            if field == 'sequence':
                week = re.fullmatch(r'(\d{1,2}(?:-\d+)?)\s*차?', answer)
                if week:
                    content = label(result) + ' 차수=' + week[1]
            elif field == 'customer':
                content = label(result) + ' 거래처=' + answer
            elif field == 'product':
                item = result['items'][index]
                if '주밀리아' in item['product_raw'] and answer.upper() in ('일반', 'DY'):
                    options = [c for c in item.get('candidates', []) if
                        bool(re.search(r'\bDY\b', c['name'], re.I)) == (answer.upper() == 'DY')]
                    if len(options) != 1:
                        return result
                    answer = str(options[0]['nenova_key'])
                content = f"{label(result)} 품목 {index+1}={answer}"
    prefix = re.match(r'^'+re.escape(label(result))+r'\s*(?=맞아|승인|틀려|틀림|안함|안보내|품목|수량|거래처|차수|선택|원문)', content)
    if not prefix:
        return result
    command = content[prefix.end():].strip()
    if command.startswith('원문 ') or command.startswith('원문\n'):
        original = {**result['event'], 'content': command[2:].strip()}
        parsed = extract(original)
        if not parsed['items']:
            return row
        result['history'].append({'revision': result['revision'], 'extracted': deepcopy(result['extracted']),
                                  'correction_event_id': eid})
        result.update(extracted=parsed, revision=result['revision']+1, status='needs_match')
        for key in ('question_event_id', 'approved_revision', 'approval_event_id', 'receipt_event_id'):
            result.pop(key, None)
        result['processed_replies'].append(eid)
        return result
    simple_product = re.fullmatch(r'품목\s+(\S.*)', command)
    if simple_product and len(result.get('items',[]))==1 and not re.match(r'\d+\s*=',simple_product[1]):
        command = '품목 1=' + simple_product[1]
    selection = re.fullmatch(r'선택\s+(\d+)=(\d+)', command)
    if selection and 1 <= int(selection[1]) <= len(result.get('items', [])):
        candidates = result['items'][int(selection[1])-1].get('candidates', [])
        if 1 <= int(selection[2]) <= len(candidates):
            command = f"품목 {selection[1]}={candidates[int(selection[2])-1]['nenova_key']}"
    correction = re.fullmatch(r'품목\s+(\d+)\s*=\s*(\S.*)', command)
    field_edit = re.fullmatch(r'(거래처|차수)\s*=\s*(\S.*)', command)
    quantity_edit = re.fullmatch(r'수량\s+(\d+)\s*=\s*(\d+(?:\.\d{1,4})?)\s*(단|박스|대|스팀|송이)', command)
    if field_edit or quantity_edit:
        from decimal import Decimal
        source = result['extracted']
        if field_edit and field_edit[1] == '차수' and not re.fullmatch(r'(?:[1-9]|[1-4]\d|5[0-3])(?:-\d+)?', field_edit[2]):
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
        try:
            learn_from_approval(result)
        except Exception:
            pass  # Learning is advisory; it must never block an approval.
    elif command in ('틀려', '틀림'):
        result['status'] = 'awaiting_product'
        product_guide = f"{label(result)} 품목 품목명" if len(result.get('items',[]))==1 else f"{label(result)} 품목 1=품목명"
        result['correction_message'] = (f"수정할 내용만 답해주세요.\n{product_guide}\n"
            f"{label(result)} 수량 1=3단\n{label(result)} 거래처=거래처명\n{label(result)} 차수=37-1\n"
            "수정 후 다시 승인받겠습니다.")
    elif command in ('안함', '안보내'):
        result['status'] = 'declined'
    elif correction and 1 <= int(correction[1]) <= len(result['extracted']['items']):
        result['history'].append({'revision': result['revision'], 'items': deepcopy(result.get('items')),
                                  'question_event_id': result['question_event_id']})
        corrected = result['extracted']['items'][int(correction[1])-1]
        # Keep the staff's first wording so an approved correction can teach
        # the matcher what that wording meant.
        corrected.setdefault('original_product_raw', corrected['product_raw'])
        corrected['product_raw'] = correction[2].strip()
        result['extracted']['issues'] = [v for v in result['extracted']['issues'] if v != '품목 상세 확인 필요']
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
    # Scope is validated at submission. Clarification questions may themselves
    # be needed to learn a missing sequence/customer/product; requiring a valid
    # destination scope first prevented those questions and orphaned requests.
    before = export(row['recipient'])
    # Never reuse a numeric approval token, including after a correction. This
    # prevents a late reply to the previous question from approving a new match.
    numbers = [int(value) for p in Path(path).parent.glob('*.json')
               for value in [str(load(p).get('reply_number') or '')]
               if re.fullmatch(r'[0-9]+', value)]
    row['reply_number'] = max(numbers, default=0) + 1
    payload = question(row)
    if paused():
        return
    row.update(status='question_unknown', question_payload=payload,
               question_before_ids=[e['event_id'] for e in before])
    save(path, row)
    send(row['recipient'], payload)
    reconcile_question(path, export(row['recipient']))


def auto_authorized(row):
    config_path = ROOT.parent / 'defect_config.json'
    cfg = load(config_path) if config_path.exists() else {}
    return (cfg.get('approval_mode') == 'automatic'
            and row.get('authorization') == {'mode': 'automatic',
                'policy_id': cfg.get('automatic_policy_id'), 'revision': row['revision']}
            and bool(cfg.get('automatic_policy_id')))


def prepare_automatic(path):
    row = load(path)
    cfg = load(ROOT.parent / 'defect_config.json')
    if cfg.get('approval_mode') != 'automatic':
        return
    row['authorization'] = {'mode': 'automatic', 'policy_id': cfg['automatic_policy_id'],
                            'revision': row['revision']}
    # Source completeness and destination-period resolution are separate.
    # submit() calls adapter.validate() to resolve the authoritative period
    # before lookup/write. A missing server-derived scope is not something
    # the sender can fix by repeating an already complete source message.
    eligible = ready(row)
    row['status'] = 'approved' if eligible else 'clarification_pending'
    row['information_only'] = not eligible
    row.pop('receipt_event_id', None)
    row['approved_revision'] = row['revision']
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
    from core.defect_services import DuplicateDefect
    try:
        existing = adapter.lookup(row)
    except DuplicateDefect as exc:
        if not auto_authorized(row):
            raise
        row.update(status='duplicate_source_hold', duplicate_receipt=exc.entries,
                   duplicate_partial=exc.partial)
        save(path, row)
        return
    if existing is not None:
        if not adapter.matches(row, existing):
            raise ValueError('기존 등록 내용 불일치')
        row.update(status='completed', receipt=existing, completed_at=row.get('completed_at') or time.time())
        save(path, row)
        return
    if paused():
        return
    from core.defect_scope import upload_scope
    row['registration_scope'] = upload_scope(row)
    row['status'] = 'write_unknown'
    save(path, row)
    try:
        adapter.insert(row)
    except Exception as exc:
        # 저장 직전 재확인(차수·조회·담당자)에서 멈췄으면 전산에 보낸 것이 없다. 승인 상태로 되돌려 다음 주기에 다시 시도한다.
        if getattr(adapter, 'post_dispatched', None) is False:
            row.update(status='approved', last_error='저장 전 확인 실패: ' + str(exc)[:200])
            save(path, row)
        raise
    receipt = adapter.lookup(row)
    if receipt is not None and adapter.matches(row, receipt):
        row.update(status='completed', receipt=receipt, completed_at=time.time())
        save(path, row)
