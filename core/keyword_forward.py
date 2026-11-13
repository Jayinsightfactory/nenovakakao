"""Durable, fail-closed keyword forwarding between two exact Kakao rooms."""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / 'data' / 'keyword_forward_config.json'
STATE = ROOT / 'data' / 'keyword_forward_state.json'
LOG = ROOT / 'data' / 'keyword_forward_events.jsonl'
KST = timezone(timedelta(hours=9))
APPROVAL_WORDS = ('추가', '취소', '변경')


def requires_approval(content):
    """Route additions/cancellations and actual shipment changes, not generic status text."""
    return classify_transfer(content)['decision'] == 'forward'


def manual_handled_claim(content):
    """A claim of direct warehouse instruction needs review, not retransmission."""
    return bool(re.search(r'현장\s*(?:직접\s*)?(?:지시|전달|요청)\s*완료|추가취소방\s*전달\s*완료', content))


def classify_transfer(content):
    """Explain routing without treating an uncertain request as a confirmed instruction."""
    text = normalize(content)
    action = bool(re.search('추가|취소', text) or ('출고' in text and '변경' in text))
    if not action:
        return {'decision': 'exclude', 'reason': '추가·취소·출고 변경 내용 없음'}
    if manual_handled_claim(content):
        return {'decision':'review','reason':'현장 지시·전달 완료 표시; 신규 전달 전 직접 처리 내역 대조 필요'}
    # Tie uncertainty to the action itself. A separate packing prohibition such
    # as 검역표시하지말아주세요 must not cancel an explicit quantity cancellation.
    uncertain = re.search(
        r'(?:추가|취소|변경)\s*(?:가능|예정|하겠습니다|할\s*예정|할까요|될까요|해도|하지\s*마|하지말|말아|말고|안\s*해|불가|시\s*가능)', text)
    conditional = re.search(r'(?:확인되면|가능하면|있으면|취소시).{0,25}(?:추가|취소|변경|가능)', text)
    if uncertain or conditional:
        return {'decision': 'review', 'reason': '가능 여부·예정·조건·금지 표현; 확정 처리 지시 대조 필요'}
    # Completed actions and transport incidents are not fresh warehouse orders.
    if re.search(r'(?:추가|취소|변경)(?:\s*견적)?\s*(?:완료|처리\s*완료|됐|되었|했습니다|했어요)', text):
        return {'decision': 'review', 'reason': '기처리 완료 보고; 신규 전달 여부 확인 필요'}
    if re.search(r'(?:운항|항공편|비행편|항공|일정)[이가은는 ]*취소', text):
        return {'decision': 'review', 'reason': '운송 일정 취소 안내; 업체 주문 취소와 구분 필요'}
    explicit = bool(re.search(r'(?:추가|취소|변경)\s*(?:부탁|해주세요|해 주세요|해주세|해 주세|처리|필요|요청)', text))
    if not explicit and re.search(r'누가 취소|추가/취소 내용 적용|추가 확인|추가 설명|연락처 변경', text):
        return {'decision': 'review', 'reason': '경위·재고·정보 확인; 처리 지시 불명확'}
    if not explicit and re.search(r'추가\s*(?:입고|사용|물량\s*안내)|(?:입고|재고)\s*추가', text):
        return {'decision': 'review', 'reason': '입고·재고 안내; 실제 추가 처리 요청 확인 필요'}
    return {'decision': 'forward', 'reason': '추가·취소 또는 출고 변경 지시 후보; 승인 후 전달'}



def read_json(path, default):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default


def save_json(path, value):
    from core.atomic_json import save
    save(path, value)


def config():
    return read_json(CONFIG, {'enabled': False})


def set_enabled(enabled):
    cfg = config()
    cfg['enabled'] = bool(enabled)
    save_json(CONFIG, cfg)


def normalize(text):
    return re.sub(r'\s+', ' ', text).strip()


def paste_buffer_capacity(payload):
    # RichEdit can expand each LF into CRLF. Windows counts UTF-16 units,
    # not Python characters; include both newline expansion and terminator.
    return len(payload.encode('utf-16-le')) // 2 + payload.count('\n') + 1


def has_draft(text):
    # Kakao stores its empty-input hint as the RICHEDIT control text.
    # Never clear the control; verify the full pasted payload before Enter.
    return text not in ('', '메시지 입력')


def timestamp(value):
    match = re.fullmatch(r'(\d{4})년 (\d{1,2})월 (\d{1,2})일 (오전|오후) (\d{1,2}):(\d{2})', value)
    if not match:
        return None
    y, mo, d, ampm, h, mi = match.groups()
    return datetime(int(y), int(mo), int(d), int(h) % 12 + (12 if ampm == '오후' else 0), int(mi), tzinfo=KST)


def duplicate(body, target_events):
    key = normalize(body)
    for event in target_events:
        text = event['content']
        if normalize(text) == key or (' - ' in text and normalize(text.split(' - ', 1)[1]) == key):
            return True
    return False


def exact_delivery_candidates(source, target_events):
    """Text alone is insufficient: require same-day, non-earlier delivery."""
    stamp = timestamp(source.get('timestamp', ''))
    if stamp is None:
        return []
    return [e for e in target_events
            if (target_stamp := timestamp(e.get('timestamp', ''))) is not None
            and target_stamp.date() == stamp.date() and target_stamp >= stamp
            and duplicate(source['content'], [e])]


def possible_manual_delivery(source, target_events):
    """Evidence for review only, never proof authorizing a send or completion.

    Require the same customer/item/quantity/action text and same-day chronology.
    Only the leading shipment week and non-quantity shipping prose may differ.
    """
    def signature(text):
        # Strip only the known forwarding sender wrapper, not arbitrary prose.
        if ' - ' in text.split('\n', 1)[0]:
            text = text.split(' - ', 1)[1]
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            return ''
        lines[0] = re.sub(r'^\d{1,2}(?:\s*[-/]\s*\d{1,2})?\s*차?\s*', '', lines[0])
        kept=[]
        for line in lines:
            if (re.match(r'^(?:[-=]*>\s*)?(?:오늘|늦게)',line)
                    and not re.search(r'\d\s*(?:박스|단|대|송이|스팀)',line)):
                continue
            kept.append(re.sub(r'\s+','',line))
        body='\n'.join(kept)
        return body if re.search(r'\d+(?:\.\d+)?(?:박스|단|대|송이|스팀)',body) else ''
    stamp=timestamp(source.get('timestamp',''))
    key=signature(source['content'])
    if stamp is None or not key:
        return []
    matches=[]
    for event in target_events:
        target_stamp=timestamp(event.get('timestamp',''))
        if (target_stamp is not None and target_stamp.date()==stamp.date() and target_stamp>=stamp
                and signature(event['content'])==key):
            matches.append(event)
    return matches


def partial_manual_delivery(source, target_events):
    """Review-only evidence when a multi-item original was split in the target."""
    quantity=re.compile(r'\d+(?:\.\d+)?\s*(?:박스|단|대|송이|스팀)')
    compact=lambda value:re.sub(r'\s+','',value)
    lines=[line.strip() for line in source['content'].splitlines() if line.strip()]
    items=[compact(line) for line in lines if quantity.search(line)]
    if len(items)<2: return []
    customers=[compact(line) for line in lines if not quantity.search(line)
               and not re.match(r'^\d{1,2}(?:\s*[-/]\s*\d{1,2})?\s*차?',line)
               and not re.search(r'추가|취소|변경|출고|배송|농장|부탁|감사|확인|^[-=>]',line)
               and 2<=len(compact(line))<=30]
    actions=set(re.findall(r'추가|취소|변경',source['content']))
    stamp=timestamp(source.get('timestamp',''))
    if stamp is None or not customers or not actions: return []
    matches=[]
    for event in target_events:
        target_stamp=timestamp(event.get('timestamp',''))
        if target_stamp is None or target_stamp.date()!=stamp.date() or target_stamp<stamp: continue
        text=compact(event['content'])
        if (any(customer in text for customer in customers)
                and actions.intersection(re.findall(r'추가|취소|변경',event['content']))
                and any(item in text for item in items)):
            matches.append(event)
    return matches


def process_source(title, events, export, send, paused, max_new_events=5):
    cfg = config()
    if not cfg.get('enabled') or title != cfg.get('source') or paused():
        return
    from core.moyi_inbound import parse_export
    state = read_json(STATE, {})  # corrupted state must fail closed
    cutoff = datetime.fromisoformat(cfg['start_at'])
    target = cfg['target']
    approval_batch = uuid.uuid4().hex[:12].upper()
    # Approval prompts are operational, not archival. Within a newly detected
    # group show the most recent Kakao message first.
    events = sorted(events, key=lambda event: timestamp(event.get('timestamp', '')) or cutoff,
                    reverse=True)

    def record(event, status, detail, audit=None):
        from core.error_notifications import report
        from core.error_notifications import resolve
        notice_request_id = event['event_id'].removeprefix('kakao_')[:12]
        if status == '전송 성공': report('forward_sent')
        elif status == '중복 생략': report('forward_skipped')
        if status in ('전송 성공', '중복 생략'):
            resolve('forward_error', notice_request_id)
        if status in ('확인 필요', '검증 재시도', '결과 불명'):
            from core.error_notifications import notify
            notify('forward_error', notice_request_id, {
                'source_room': title, 'target_room': target,
                'sender': event.get('sender_name', ''), 'preview': event.get('content', ''),
                'stage': status, 'cause': detail,
                'automatic_action': '완료 처리와 자동 재전송을 차단하고 원문 상태를 보류',
            })
        row = {'at': time.time(), 'status': status, 'event_id': event['event_id'],
               'sender': event.get('sender_name', ''), 'source': title, 'target': target,
               'keywords': [k for k in cfg['keywords'] if k in event['content']],
               'preview': event['content'][:160], 'detail': detail,
               'content_hash': hashlib.sha256(normalize(event['content']).encode()).hexdigest()}
        # Keep Kakao's own message timestamps for an auditable source-to-target
        # delivery measurement.  ``at`` remains the program processing time.
        source_stamp = timestamp(event.get('timestamp', ''))
        if source_stamp is not None:
            row['source_kakao_at'] = source_stamp.isoformat()
        if audit:
            row.update(audit)
        state[event['event_id']] = row
        save_json(STATE, state)
        with LOG.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(row, ensure_ascii=False) + '\n')

    cached_target_history = None
    def history(refresh=False):
        nonlocal cached_target_history
        if cached_target_history is not None and not refresh:
            return cached_target_history
        text = export(target)
        if target not in '\n'.join(text.splitlines()[:3]):
            raise RuntimeError('대상 방 내보내기 제목 불일치')
        parsed = parse_export(text, 'keyword-target')
        if not parsed:
            raise RuntimeError('대상 방 기존 대화 확인 불가')
        cached_target_history = parsed
        return parsed

    considered = 0
    for event in events:
        previous = state.get(event['event_id'], {})
        classification = classify_transfer(event['content'])
        if not previous and classification['decision'] == 'review':
            stamp = timestamp(event.get('timestamp', ''))
            if stamp is not None and stamp > cutoff:
                record(event, '분류 검토 필요', classification['reason'])
            continue
        approved = previous.get('status') == '승인됨'
        retryable = previous.get('status') == '검증 재시도'
        if (previous and not approved and not retryable) or not requires_approval(event['content']):
            continue
        stamp = timestamp(event.get('timestamp', ''))
        if stamp is None:
            record(event, '확인 필요', '원본 메시지 시각 확인 불가')
            continue
        if stamp <= cutoff:
            continue
        if '메시지가 삭제되었습니다' in event['content']:
            record(event, '삭제 메시지 생략', '카카오에서 삭제된 원문은 승인/전달하지 않음')
            continue
        if considered >= max_new_events:
            continue
        considered += 1
        if paused() or not config().get('enabled'):
            return
        body = event['content']
        digest = hashlib.sha256(normalize(body).encode()).hexdigest()
        if any(row.get('content_hash') == digest and row['status'] in ('전송 성공', '전송 확인중', '결과 불명') for row in state.values()):
            record(event, '중복 생략', '기존 전달/확인중 기록과 동일 본문')
            continue
        if not approved and cfg.get('approval_dedup_at_dispatch_only'):
            # Queue locally only. Approval.poll MUST export/deduplicate the
            # target before it sends any operator prompt. Avoid exporting the
            # same room twice in succession; approved forwarding still rechecks.
            from core.keyword_approval import enqueue
            request_id = enqueue(event, batch_id=approval_batch)
            record(event, '승인요청 전송대기', f'대상 방 대조 대기 · 요청 {request_id}')
            continue
        try:
            before = history()
            if duplicate(body, before):
                record(event, '중복 생략', '대상 방에 동일 본문 존재')
                continue
        except Exception as exc:
            from core.moyi_control import OperationPaused
            import pyautogui
            if isinstance(exc, (OperationPaused, pyautogui.FailSafeException)):
                raise
            record(event, '검증 재시도', str(exc)[:200])
            return
        if paused() or not config().get('enabled'):
            return
        # Every routed message requires explicit approval. Keyword matching is
        # already enforced above; never allow a non-approved direct send.
        if not approved:
            from core.keyword_approval import enqueue
            request_id = enqueue(event, batch_id=approval_batch)
            record(event, '승인요청 전송대기', f'승인 담당자에게 아직 미전송 · 요청 {request_id}')
            continue
        payload = f"{event['sender_name']} - {body}"
        record(event, '전송 확인중', '전송 시작 전 기록; 자동 재전송 금지')
        try:
            send(target, payload)
            after = history(refresh=True)
            count = lambda rows: sum(normalize(r['content']) == normalize(payload) for r in rows)
            if count(after) <= count(before):
                raise RuntimeError('대상 방 재조회에서 전송 결과 확인 불가')
            delivered = [r for r in after if normalize(r.get('content', '')) == normalize(payload)]
            target_stamp = max((timestamp(r.get('timestamp', '')) for r in delivered
                                if timestamp(r.get('timestamp', '')) is not None), default=None)
            audit = {}
            if target_stamp is not None:
                audit['target_kakao_at'] = target_stamp.isoformat()
                source_stamp = timestamp(event.get('timestamp', ''))
                if source_stamp is not None:
                    audit['delivery_elapsed_sec'] = round((target_stamp - source_stamp).total_seconds(), 3)
            record(event, '전송 성공', '대상 방 원문 재조회 확인', audit)
        except Exception as exc:
            from core.moyi_control import OperationPaused
            import pyautogui
            if isinstance(exc, (OperationPaused, pyautogui.FailSafeException)):
                raise
            record(event, '결과 불명', str(exc)[:200])


def _send_exact(title, payload, require_forward_enabled=True):
    import ctypes
    from ctypes import wintypes
    import pyautogui
    import pyperclip
    import win32gui
    import win32con
    from core.moyi_control import is_paused, OperationPaused
    from core.moyi_inbound import _open_or_reuse_exact_room
    from core.safe_worker_room import close_room, _foreground_belongs_to
    class GuiInfo(ctypes.Structure):
        _fields_ = [('cbSize', wintypes.DWORD), ('flags', wintypes.DWORD)] + [(name, wintypes.HWND) for name in ('hwndActive', 'hwndFocus', 'hwndCapture', 'hwndMenuOwner', 'hwndMoveSize', 'hwndCaret')] + [('rcCaret', wintypes.RECT)]
    def focused(control):
        info = GuiInfo()
        info.cbSize = ctypes.sizeof(info)
        return ctypes.windll.user32.GetGUIThreadInfo(0, ctypes.byref(info)) and info.hwndFocus == control
    hwnd = _open_or_reuse_exact_room(title)
    try:
        controls = []
        def collect(child, _):
            if win32gui.IsWindowVisible(child) and 'richedit' in win32gui.GetClassName(child).lower():
                controls.append(child)
        win32gui.EnumChildWindows(hwnd, collect, None)
        if len(controls) != 1:
            raise RuntimeError('메시지 입력란을 유일하게 확인하지 못함')
        edit = controls[0]
        initial = ctypes.create_unicode_buffer(65536)
        send_message = ctypes.windll.user32.SendMessageW
        send_message.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        send_message.restype = wintypes.LPARAM
        send_message(edit, win32con.WM_GETTEXT, len(initial), ctypes.addressof(initial))
        if has_draft(initial.value):
            raise RuntimeError('입력란에 기존 초안이 있어 전송 차단')
        left, top, right, bottom = win32gui.GetWindowRect(edit)
        pyautogui.click((left + right)//2, (top + bottom)//2)
        if is_paused() or (require_forward_enabled and not config().get('enabled')):
            raise OperationPaused('일시정지로 전송 차단')
        if not _foreground_belongs_to(hwnd) or win32gui.GetWindowText(hwnd) != title or not focused(edit):
            raise RuntimeError('전송 직전 방 제목/포커스 불일치')
        pyperclip.copy(payload)
        pyautogui.hotkey('ctrl', 'v')
        # Kakao RichEdit applies multiline clipboard text asynchronously on
        # some PCs. 0.2s intermittently read the previous/partial value.
        time.sleep(0.5)
        if is_paused() or (require_forward_enabled and not config().get('enabled')):
            raise OperationPaused('일시정지로 전송 차단')
        if not _foreground_belongs_to(hwnd) or not focused(edit):
            raise RuntimeError('붙여넣기 후 정지/포커스 변경; 확인 필요')
        capacity = max(paste_buffer_capacity(payload),
                       send_message(edit, win32con.WM_GETTEXTLENGTH, 0, 0) + 1)
        buffer = ctypes.create_unicode_buffer(capacity)
        send_message = ctypes.windll.user32.SendMessageW
        send_message.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        send_message.restype = wintypes.LPARAM
        send_message(edit, win32con.WM_GETTEXT, len(buffer), ctypes.addressof(buffer))
        # Clipboard application may still be in progress. Re-read only;
        # never paste again or press Enter while contents differ.
        deadline = time.monotonic() + 2
        while normalize(buffer.value) != normalize(payload) and time.monotonic() < deadline:
            if is_paused():
                raise OperationPaused('일시정지로 전송 차단')
            if not _foreground_belongs_to(hwnd) or not focused(edit):
                raise RuntimeError('붙여넣기 확인 중 정지/포커스 변경; 전송 차단')
            time.sleep(0.1)
            actual_capacity = send_message(edit, win32con.WM_GETTEXTLENGTH, 0, 0) + 1
            if actual_capacity > len(buffer):
                buffer = ctypes.create_unicode_buffer(actual_capacity)
            send_message(edit, win32con.WM_GETTEXT, len(buffer), ctypes.addressof(buffer))
        if normalize(buffer.value) != normalize(payload):
            from core.moyi_control import audit
            audit('paste_mismatch', f'expected_chars={len(payload)} actual_chars={len(buffer.value)}; Enter 차단')
            # The input was empty before this worker pasted. If Kakao transforms
            # or truncates that paste, remove only this worker-owned draft so a
            # later retry cannot append to stale text.
            if _foreground_belongs_to(hwnd) and focused(edit):
                pyautogui.hotkey('ctrl', 'a')
                pyautogui.press('backspace')
            raise RuntimeError('입력란 원문 검증 실패; Enter 전송 차단')
        if is_paused() or (require_forward_enabled and not config().get('enabled')):
            raise OperationPaused('일시정지로 전송 차단')
        if not _foreground_belongs_to(hwnd) or not focused(edit):
            raise RuntimeError('Enter 직전 정지/포커스 변경; 전송 차단')
        pyautogui.press('enter')
        time.sleep(0.8)
    finally:
        close_room(hwnd)

# Serialize all Kakao UI sends with room exports.
def send_exact(title, payload, require_forward_enabled=True):
    from core.moyi_inbound import KAKAO_UI_LOCK
    with KAKAO_UI_LOCK:
        return _send_exact(title, payload, require_forward_enabled)
