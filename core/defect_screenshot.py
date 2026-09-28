"""Verified completion screenshots, with write-ahead attachment delivery."""
import base64
import time
from pathlib import Path

from core.atomic_json import save


def deliver(path, row, export):
    from core.browser_session_bridge import request
    from core.moyi_control import is_paused, OperationPaused
    from core.moyi_inbound import KAKAO_UI_LOCK, _open_or_reuse_exact_room
    from core.safe_worker_room import _foreground_belongs_to
    import pyautogui
    import pyperclip
    import win32gui
    import win32con

    if row.get('screenshot_state') in ('sent', 'unknown'):
        return
    if is_paused():
        raise OperationPaused('스크린샷 전송 일시정지')
    params = [{k: r.get(k) for k in ('customerName', 'matchedProductName', 'quantity', 'orderWeek')}
              for r in row['receipt']]
    response = request(row['id'], 'SCREENSHOT', params=params)
    prefix = 'data:image/png;base64,'
    if not response.get('png', '').startswith(prefix):
        raise ValueError('invalid_screenshot')
    image = base64.b64decode(response['png'][len(prefix):], validate=True)
    if not image.startswith(b'\x89PNG\r\n\x1a\n'):
        raise ValueError('invalid_png')
    folder = Path(path).parent.parent / 'defect_screenshots'
    folder.mkdir(exist_ok=True)
    target = (folder / (row['id'] + '-registered.png')).resolve()
    target.write_bytes(image)
    row['screenshot_path'] = str(target)
    save(path, row)
    with KAKAO_UI_LOCK:
        before = {e['event_id'] for e in export(row['recipient'])}
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
        pyautogui.hotkey('alt', 'n')
        pyperclip.copy(str(target))
        pyautogui.hotkey('ctrl', 'v')
        time.sleep(.3)
        if is_paused() or win32gui.GetForegroundWindow() != dialog:
            raise OperationPaused('첨부 파일 선택 중 중단')
        filenames = []
        def inspect(child, _):
            if win32gui.GetClassName(child) == 'Edit':
                filenames.append(win32gui.GetWindowText(child))
        win32gui.EnumChildWindows(dialog, inspect, None)
        if str(target) not in filenames:
            raise RuntimeError('첨부 파일 경로 검증 실패; 전송하지 않음')
        # Enter may send the attachment immediately. Persist before pressing.
        row.update(screenshot_state='unknown', screenshot_attempted_at=time.time())
        save(path, row)
        pyautogui.press('enter')
        time.sleep(1)
        found = [e for e in export(row['recipient']) if e['event_id'] not in before
                 and target.name in e.get('content', '')]
        if found:
            row.update(screenshot_state='sent', screenshot_event_id=found[-1]['event_id'])
            save(path, row)
