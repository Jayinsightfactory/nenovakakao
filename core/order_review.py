"""Non-UI order review export; human review columns are never overwritten."""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
TITLE = '수입방 주문검토'
HEADERS = ['요청번호', '항목', '원문 시각', '원문 작성자', '분석 담당자', '담당자 카톡',
           '차수', '원문 거래처', '매칭 거래처', '원문 품목', '매칭 품목', '수량', '단위',
           '매칭 판정', '품목 후보', '처리 상태', '확인 필요사항', '원문',
           '검토 결과', '거래처 수정', '품목 수정', '담당자·처리방식 수정 의견']
_process = None


def review_rows(state):
    result = []
    for order in sorted(state.values(), key=lambda row: (row.get('created_at', 0), row['id'])):
        event = order.get('event', {})
        for item in order.get('items') or [{}]:
            issues = list(order.get('questions') or [])
            if order.get('error'): issues.append(order['error'])
            result.append([order['id'], str(item.get('index', 0)), event.get('timestamp', ''),
                event.get('sender_name', ''), order.get('staff', ''), order.get('staff_room', ''),
                order.get('week', ''), item.get('raw_customer', ''), item.get('customer', ''),
                item.get('raw_product', ''), item.get('product', ''),
                item.get('quantity') if item.get('quantity') is not None else '', item.get('unit', ''),
                '매칭' if item.get('matched') and item.get('customer_key') else '확인 필요',
                ' / '.join(item.get('candidates') or []), order.get('status', ''),
                ' / '.join(issues), str(event.get('content') or order.get('raw', ''))[:45000]])
    return result


def plan_updates(existing, desired):
    positions = {}
    for number, row in enumerate(existing[1:], 2):
        if len(row) >= 2 and row[0]:
            key = (str(row[0]), str(row[1]))
            if key in positions: raise ValueError('검토 시트 요청·항목 중복; 자동 덮어쓰기 중지')
            positions[key] = number
    next_row = len(existing) + 1
    updates = []
    for row in desired:
        key = (str(row[0]), str(row[1]))
        number = positions.get(key)
        if number is None:
            number = next_row; next_row += 1
            positions[key] = number
        old = existing[number - 1][:18] if number <= len(existing) else []
        old += [''] * (18 - len(old))
        if old != row:
            updates.append({'range': f'A{number}:R{number}', 'values': [row]})
    return updates, next_row - 1


def start_sync():
    global _process
    if _process is not None and _process.poll() is None:
        return
    with (ROOT / 'data' / 'order_review_sync.log').open('a', encoding='utf-8') as log:
        _process = subprocess.Popen([sys.executable, '-X', 'utf8', str(ROOT / 'scripts' / 'sync_order_review.py')],
            cwd=ROOT, stdout=log, stderr=log,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
