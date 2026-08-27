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


def read_json(path, default):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(path)


def config():
    return read_json(CONFIG, {'enabled': False})


def set_enabled(enabled):
    cfg = config()
    cfg['enabled'] = bool(enabled)
    save_json(CONFIG, cfg)


def normalize(text):
    return re.sub(r'\s+', ' ', text).strip()


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


def process_source(title, events, export, send, paused):
    cfg = config()
    if not cfg.get('enabled') or title != cfg.get('source') or paused():
        return
    from core.moyi_inbound import parse_export
    state = read_json(STATE, {})  # corrupted state must fail closed
    cutoff = datetime.fromisoformat(cfg['start_at'])
    target = cfg['target']
    approval_batch = uuid.uuid4().hex[:12].upper()

    def record(event, status, detail):
        row = {'at': time.time(), 'status': status, 'event_id': event['event_id'],
               'sender': event.get('sender_name', ''), 'source': title, 'target': target,
               'keywords': [k for k in cfg['keywords'] if k in event['content']],
               'preview': event['content'][:160], 'detail': detail,
               'content_hash': hashlib.sha256(normalize(event['content']).encode()).hexdigest()}
        state[event['event_id']] = row
        save_json(STATE, state)
        with LOG.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(row, ensure_ascii=False) + '\n')

    def history():
        text = export(target)
        if target not in '\n'.join(text.splitlines()[:3]):
            raise RuntimeError('대상 방 내보내기 제목 불일치')
        parsed = parse_export(text, 'keyword-target')
        if not parsed:
            raise RuntimeError('대상 방 기존 대화 확인 불가')
        return parsed

    for event in events:
        previous = state.get(event['event_id'], {})
        approved = previous.get('status') == '승인됨'
        if (previous and not approved) or not any(k in event['content'] for k in cfg['keywords']):
            continue
        stamp = timestamp(event.get('timestamp', ''))
        if stamp is None:
            record(event, '확인 필요', '원본 메시지 시각 확인 불가')
            continue
        if stamp <= cutoff:
            continue
        if paused() or not config().get('enabled'):
            return
        body = event['content']
        digest = hashlib.sha256(normalize(body).encode()).hexdigest()
        if any(row.get('content_hash') == digest and row['status'] in ('전송 성공', '전송 확인중', '결과 불명') for row in state.values()):
            record(event, '중복 생략', '기존 전달/확인중 기록과 동일 본문')
            continue
        try:
            before = history()
            if duplicate(body, before):
                record(event, '중복 생략', '대상 방에 동일 본문 존재')
                continue
        except Exception as exc:
            record(event, '확인 필요', str(exc)[:200])
            continue
        if paused() or not config().get('enabled'):
            return
        # Every routed message requires explicit approval. Keyword matching is
        # already enforced above; never allow a non-approved direct send.
        if not approved:
            from core.keyword_approval import enqueue
            request_id = enqueue(event, batch_id=approval_batch)
            record(event, '승인대기', f'임재용대리 승인 필요 · 요청 {request_id}')
            continue
        payload = f"{event['sender_name']} - {body}"
        record(event, '전송 확인중', '전송 시작 전 기록; 자동 재전송 금지')
        try:
            send(target, payload)
            after = history()
            count = lambda rows: sum(normalize(r['content']) == normalize(payload) for r in rows)
            if count(after) <= count(before):
                raise RuntimeError('대상 방 재조회에서 전송 결과 확인 불가')
            record(event, '전송 성공', '대상 방 원문 재조회 확인')
        except Exception as exc:
            record(event, '결과 불명', str(exc)[:200])


def send_exact(title, payload):
    import ctypes
    from ctypes import wintypes
    import pyautogui
    import pyperclip
    import win32gui
    import win32con
    from core.moyi_control import is_paused
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
        if is_paused() or not config().get('enabled'):
            raise RuntimeError('일시정지로 전송 차단')
        if not _foreground_belongs_to(hwnd) or win32gui.GetWindowText(hwnd) != title or not focused(edit):
            raise RuntimeError('전송 직전 방 제목/포커스 불일치')
        pyperclip.copy(payload)
        pyautogui.hotkey('ctrl', 'v')
        time.sleep(0.2)
        if is_paused() or not config().get('enabled') or not _foreground_belongs_to(hwnd) or not focused(edit):
            raise RuntimeError('붙여넣기 후 정지/포커스 변경; 확인 필요')
        buffer = ctypes.create_unicode_buffer(len(payload) + 16)
        send_message = ctypes.windll.user32.SendMessageW
        send_message.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        send_message.restype = wintypes.LPARAM
        send_message(edit, win32con.WM_GETTEXT, len(buffer), ctypes.addressof(buffer))
        if normalize(buffer.value) != normalize(payload):
            raise RuntimeError('입력란 원문 검증 실패; Enter 전송 차단')
        pyautogui.press('enter')
        time.sleep(0.8)
    finally:
        close_room(hwnd)
