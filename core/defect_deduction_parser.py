"""Conservative text extraction for defect matching, never ERP authorization."""
import re

SENDERS = {'박성수': '박성수', '정재훈': '정재훈', '조현욱': '조현욱',
           '김원영': '김원영', '김원영차장': '김원영'}
SEQUENCE = re.compile(r'(?<!\d)(\d{1,2})\s*[-/]\s*(\d{1,2})\s*차?')
QUANTITY = re.compile(r'(?<![\d.])(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>파렛트|박스|송이|스팀|묶음|BOX|box|단|대|속|개)')
CATEGORIES = ('알스트로메리아', '알스트로 메리아', '카네이션', '루스커스', '델피늄',
              '알스트로', '레몬잎', '리시안', '거베라', '모카라', '수국', '장미', '안개', '백합', '국화')
ORIGINS = ('콜롬비아', '에콰도르', '이스라엘', '네덜란드', '말레이시아', '중국', '베트남', '케냐', '호주', '태국', '콜', '에콰')


def extract(event):
    text = str(event.get('content', '')).strip()
    sender = str(event.get('sender_name', '')).strip()
    result = {'event_id': event.get('event_id'), 'sender': sender, 'staff': SENDERS.get(sender),
              'source_time': event.get('timestamp', ''), 'raw_text': text, 'sequence': '',
              'category': '', 'customer': None, 'customer_candidates': [], 'items': [],
              'issues': [], 'notes': [], 'status': 'review', 'approved': False}
    if sender not in SENDERS:
        result.update(status='excluded_sender')
        return result
    if re.fullmatch(r'(?:사진(?:\s*\d+장)?|동영상(?:\s*\d+개)?)', text) or text.startswith('파일:'):
        result.update(status='attachment_only')
        return result
    if '메시지가 삭제되었습니다' in text:
        result.update(status='deleted', issues=['삭제 표시 포함; 원문 확인 필요'])
        return result
    lines = [re.sub(r'\s+', ' ', line).strip() for line in text.splitlines() if line.strip()]
    header = lines[0] if lines else ''
    sequences = list(dict.fromkeys(f'{m[1]}-{m[2]}' for m in SEQUENCE.finditer(text)))
    if len(sequences) == 1:
        result['sequence'] = sequences[0]
    else:
        result['issues'].append('차수 없음' if not sequences else '여러 차수; 항목별 확인 필요')
    if re.search(r'\d\s*(?:차)?\s*[~∼～]', header):
        result['issues'].append('차수 범위 표기; 단일 차수로 확정 금지')
    result['category'] = next((c for c in CATEGORIES if c in header), '')
    for number, line in enumerate(lines):
        if re.match(r'^(?:[-=]*>|※|\*)', line):
            result['notes'].append(line)
            continue
        amounts = list(QUANTITY.finditer(line))
        if amounts:
            if len(amounts) > 1:
                result['issues'].append('한 줄 복수 수량/포장 환산 병기; 확인 필요')
            first = amounts[0]
            prefix = line[:first.start()].strip()
            prefix = SEQUENCE.sub('', prefix).strip()
            if number == 0:
                prefix = re.sub(r'^\d{1,2}\s*차\s*', '', prefix)
            for word in ORIGINS + CATEGORIES:
                prefix = re.sub(r'^' + re.escape(word) + r'(?:\s+|$)', '', prefix).strip()
            prefix = re.sub(r'^(?:불량|클레임)\s*', '', prefix).strip()
            suffix = line[first.end():].strip()
            if re.search(r'더 추가|수정|정정|취소|파악중|파악 중|확인 중', line):
                result['issues'].append('추가·정정·확인 문맥; 독립 입력 금지')
            if first.start() and line[first.start()-1] in '-−':
                result['issues'].append('음수 수량 표기 확인 필요')
            if float(first['value']) <= 0:
                result['issues'].append('0 이하 수량')
            if not prefix:
                prefix = result['category']
                result['issues'].append('품목 상세 확인 필요')
            result['items'].append({'product_raw': prefix, 'quantity_raw': first['value'],
                'unit_raw': first['unit'], 'line_number': number + 1, 'raw_line': line,
                'additional_quantities': [m.group(0) for m in amounts[1:]]})
            if suffix.startswith('/') and len(amounts) == 1:
                candidate = suffix[1:].strip()
                if candidate and len(candidate) <= 25:
                    result['customer_candidates'].append(candidate)
            elif suffix:
                result['notes'].append(suffix)
            continue
        if number == 0 or SEQUENCE.search(line):
            continue
        if len(line) <= 25 and not re.search(r'불량|클레임|사진|동영상|부탁|확인|수량|파악|입니다|습니다|주세요|농장|취소|수정', line):
            result['customer_candidates'].append(line)
        else:
            result['notes'].append(line)
    candidates = list(dict.fromkeys(result['customer_candidates']))
    result['customer_candidates'] = candidates
    if len(candidates) == 1:
        result['customer'] = candidates[0]
    else:
        result['issues'].append('거래처 없음' if not candidates else '거래처/농장/품종 후보 복수; 확인 필요')
    if not result['items']:
        result['issues'].append('품목·수량 없음')
    result['issues'] = list(dict.fromkeys(result['issues']))
    result['status'] = 'matching_candidate' if not result['issues'] else 'review'
    return result
