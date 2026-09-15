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


def classify_transfer(content):
    """Explain routing without treating an uncertain request as a confirmed instruction."""
    text = normalize(content)
    action = bool(re.search('추가|취소', text) or ('출고' in text and '변경' in text))
    if not action:
        return {'decision': 'exclude', 'reason': '추가·취소·출고 변경 내용 없음'}
    # A polite confirmation request does not cancel an explicit operational instruction.
    explicit = bool(re.search(r'(?:추가|취소|변경)\s*(?:부탁|해주세요|해 주세요|해주세|해 주세|처리)', text))
    uncertain = re.search(r'(?:추가|취소|변경).{0,30}(?:가능할|가능한가|가능 여부|할까요|될까요|하겠습니다|하지 마|하지마|말아|말고)', text)
    if uncertain:
        return {'decision': 'review', 'reason': '가능 여부·예정·금지 표현이 있어 확정 지시로 판단 불가'}
    if not explicit and re.search(r'누가 취소|추가/취소 내용 적용|추가 확인|추가 설명|연락처 변경', text):
        return {'decision': 'review', 'reason': '처리 지시보다 경위·재고·정보 확인에 해당'}
    return {'decision': 'forward', 'reason': '추가·취소 또는 출고 변경 지시 후보'}


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

    def record(event, status, detail):
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
            record(event, '전송 성공', '대상 방 원문 재조회 확인')
        except Exception as exc:
            from core.moyi_control import OperationPaused
            import pyautogui
            if isinstance(exc, (OperationPaused, pyautogui.FailSafeException)):
                raise
            record(event, '결과 불명', str(exc)[:200])


def send_exact(title, payload, require_forward_enabled=True):
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
