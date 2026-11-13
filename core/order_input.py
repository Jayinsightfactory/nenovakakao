"""Source-grounded import order extraction helpers. No writes or AI calls."""
import copy
import re

QUANTITY = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s*(박스|box|단|송이|스팀|대)(?![a-z가-힣])", re.I)


def reports_existing_registration(text):
    return bool(re.search(r'전산\s*등록\s*완료|전산\s*입력\s*완료|이미\s*(?:전산\s*)?등록\s*(?:완료|했|된)', body(text)))


def source_issues(text):
    text = body(text)
    issues = []
    if reports_existing_registration(text):
        issues.append('원문에 전산 등록 완료 표시: 기존 등록 대조 전 신규 ADD 금지')
    if text and not QUANTITY.search(text) and not re.search(r'발주|주문', text):
        issues.append('발주 품목·수량 없는 대화: 신규 주문 등록 금지')
    if re.search(r'취소', text):
        issues.append('취소가 포함된 변경 요청: 신규 ADD 주문으로 등록 금지')
    if re.search(r'컨펌\s*여부|발주\s*확인\s*요청|원본이\s*다른', text):
        issues.append('기존 발주 확인 문의: 신규 발주와 구분 필요')
    if re.search(r'\d{1,2}[-.]\d\s*(?:차)?\s*(?:또는|혹은)', text):
        issues.append('대안 차수가 여러 개: 등록 차수 확정 필요')
    if re.search(r'(?:^|\n)\s*(?:좌|우|왼쪽|오른쪽)\s*\d', text):
        issues.append('사진 위치로 지정된 품목: 원본 사진 연결 필요')
    if re.search(r'\d+\s*가지.*\d+\s*대씩', text):
        issues.append('샘플 컬러별 품목·수량 구분 필요')
    return issues


def quantity_evidence(text, product):
    unit = None
    evidence = []
    key = re.sub(r'\s+', '', product).lower()
    for line in body(text).splitlines():
        header = re.fullmatch(r'\s*[*★]?\s*(?:발주\s*)?단위\s*[/：:]\s*(박스|box|단|송이|스팀|대)\s*', line, re.I)
        if header:
            unit = '박스' if header[1].lower() == 'box' else header[1]
            continue
        if not key or key not in re.sub(r'\s+', '', line).lower():
            continue
        quantities = QUANTITY.findall(line)
        if not quantities and unit:
            bare = re.fullmatch(r'\s*(.+?)\s*(\d+(?:\.\d+)?)\s*', line)
            if bare and re.sub(r'\s+', '', bare[1]).lower() == key:
                quantities = [(bare[2], unit)]
        evidence.append((line, quantities))
    return evidence

def body(text):
    # Only explicit section headings terminate the order, not inline remarks.
    return re.split(r"(?m)^\s*\[기타사항(?:[^]\n]*)\]\s*", str(text), maxsplit=1)[0].strip()

def structured(text):
    text = body(text)
    if not re.match(r"^\[발주\]\s*", text):
        return None
    result = dict(staff='', customer='', week='', items=[], questions=[])
    for line in text.splitlines()[1:]:
        line = line.strip()
        if not line:
            continue
        field = re.fullmatch(r"(발주 차수|차수|업체|출고일)\s*[:：]\s*(.+)", line)
        if field:
            name, value = field.groups()
            if name == '업체': result['customer'] = value
            elif name in ('발주 차수', '차수'): result['week'] = value
            else: result['delivery_date'] = value
            continue
        item = re.fullmatch(r"(.+?)\s*/\s*(\d+(?:\.\d+)?)\s*(박스|단|송이|스팀|대)", line)
        if not item:
            return None  # Never partially discard an unrecognised line.
        product, qty, unit = item.groups()
        result['items'].append(dict(customer=result['customer'], product=product.strip(),
                                    quantity=float(qty), unit=unit, category=''))
    return result

def grounded(text, parsed):
    """Clear unsupported quantities instead of inventing 1 box; retain for review."""
    result = copy.deepcopy(parsed)
    for item in result.get('items', []):
        evidence = quantity_evidence(text, str(item.get('product') or ''))
        if len(evidence) == 1 and len(evidence[0][1]) == 1:
            qty, unit = evidence[0][1][0]
            item.update(quantity=float(qty), unit='박스' if unit.lower() == 'box' else unit, source_line=evidence[0][0])
        else:
            item.update(quantity=None, unit='', source_issue='원문 품목별 수량·단위 대조 필요')
    return result
