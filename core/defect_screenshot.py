"""Verified completion screenshots, with write-ahead attachment delivery."""
import base64
import time
from collections import Counter
from pathlib import Path

from core.atomic_json import save


def verified_attachment(before, after, filename, sender='네노바'):
    """Kakao exports PNG attachments as '사진', without their filenames."""
    def eligible(event):
        content = event.get('content', '').strip()
        return (event.get('sender_name') == sender
                and (filename in content or content in ('사진', '[사진]', 'Photo', '[Photo]')))
    old = Counter(e['event_id'] for e in before if eligible(e))
    new = Counter(e['event_id'] for e in after if eligible(e))
    added = new - old
    if sum(added.values()) != 1:
        return None
    key = next(iter(added))
    return next(e for e in reversed(after) if e['event_id'] == key and eligible(e))


def deliver(path, row, export, before_events=None):
    from core.browser_session_bridge import request, screenshot_scope
    from core.moyi_control import is_paused, OperationPaused
    from core.moyi_inbound import KAKAO_UI_LOCK, _open_or_reuse_exact_room
    from core.safe_worker_room import _foreground_belongs_to
    import pyautogui
    import win32gui
    import win32con

    if row.get('screenshot_state') in ('sent', 'unknown'):
        return
    if is_paused():
        raise OperationPaused('스크린샷 전송 일시정지')
    folder = Path(path).parent.parent / 'defect_screenshots'
    folder.mkdir(exist_ok=True)
    target = (folder / (row['id'] + '-registered.png')).resolve()
    from core import defect_approval as d
    cfg_path = Path(path).parent.parent / 'defect_config.json'
    cfg = d.load(cfg_path) if cfg_path.exists() else {}
    if cfg.get('completion_image_mode') == 'verified_receipt':
        from core.defect_receipt_image import create
        from core.browser_session_bridge import BrowserDefectAdapter
        create(row, target, BrowserDefectAdapter())
        row['completion_image_kind'] = 'verified_ledger_receipt'
    else:
        params = screenshot_scope(row)
        response = request(row['id'], 'SCREENSHOT', params=params)
        prefix = 'data:image/png;base64,'
        if not response.get('png', '').startswith(prefix):
            raise ValueError('invalid_screenshot')
        image = base64.b64decode(response['png'][len(prefix):], validate=True)
        if not image.startswith(b'\x89PNG\r\n\x1a\n'):
            raise ValueError('invalid_png')
        target.write_bytes(image)
    row['screenshot_path'] = str(target)
    save(path, row)
    with KAKAO_UI_LOCK:
        # The just-verified completion notice already exported this same room.
        before = before_events if before_events is not None else export(row['recipient'])
        hwnd = _open_or_reuse_exact_room(row['recipient'])
        if is_paused() or not _foreground_belongs_to(hwnd):
            raise OperationPaused('첨부 전 대상방 확인 필요')
        pyautogui.hotkey('ctrl', 't')
        deadline = time.monotonic() + 5
        dialog = 0
        while time.monotonic() < deadline:
            candidate = win32gui.GetForegroundWindow()
            if (win32gui.GetClassName(candidate) == '#32770'
                    and win32gui.GetWindow(candidate, win32con.GW_OWNER) == hwnd
                    and win32gui.GetWindowText(candidate) in ('열기', 'Open')):
                dialog = candidate
                break
            time.sleep(.1)
        if not dialog:
            raise RuntimeError('첨부 파일 선택창 확인 실패; 전송하지 않음')
        filename_fields = []
        def find_filename(child, _):
            if (win32gui.GetClassName(child) == 'Edit'
                    and win32gui.GetDlgCtrlID(child) == 1148):
                filename_fields.append(child)
        win32gui.EnumChildWindows(dialog, find_filename, None)
        if len(filename_fields) != 1:
            raise RuntimeError('첨부 파일 이름 입력란 확인 실패; 전송하지 않음')
        # Target the verified File name field; Alt+N/clipboard can land in
        # a different control on localized Windows file dialogs.
        filename = ''
        for _ in range(5):
            if is_paused() or win32gui.GetForegroundWindow() != dialog:
                raise OperationPaused('첨부 파일 이름 입력 중 중단')
            win32gui.SendMessage(filename_fields[0], win32con.WM_SETTEXT, 0, str(target))
            time.sleep(.2)
            length = win32gui.SendMessage(filename_fields[0], win32con.WM_GETTEXTLENGTH, 0, 0)
            buffer = win32gui.PyMakeBuffer((length + 1) * 2)
            win32gui.SendMessage(filename_fields[0], win32con.WM_GETTEXT, length + 1, buffer)
            filename = bytes(buffer).decode('utf-16-le').rstrip('\0')
            if filename == str(target):
                break
        if is_paused() or win32gui.GetForegroundWindow() != dialog:
            raise OperationPaused('첨부 파일 선택 중 중단')
        if filename != str(target):
            raise RuntimeError('첨부 파일 경로 검증 실패; 전송하지 않음')
        open_button = win32gui.GetDlgItem(dialog, win32con.IDOK)
        if not open_button:
            raise RuntimeError('첨부 열기 버튼 확인 실패; 전송하지 않음')
        # Opening may send immediately. Persist before clicking.
        row.update(screenshot_state='unknown', screenshot_attempted_at=time.time())
        save(path, row)
        win32gui.SendMessage(open_button, win32con.BM_CLICK, 0, 0)
        time.sleep(1)
        found = verified_attachment(before, export(row['recipient']), target.name,
                                    cfg.get('kakao_sender_name', '네노바'))
        if found:
            row.update(screenshot_state='sent', screenshot_event_id=found['event_id'])
            for key in ('last_error','retry_at','screenshot_error','screenshot_retry_at','completion_image_failures'):
                row.pop(key, None)
            save(path, row)
            from core.error_notifications import resolve
            resolve('defect_error', row['id'].removeprefix('불량-'))
