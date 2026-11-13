"""Serial desktop recovery using Chrome's saved autofill, without storing secrets."""
import time
from urllib.parse import urlsplit
from core.atomic_json import save
from core import browser_session_auth as auth


def _url(window):
    fields = [c for c in window.descendants(control_type='Edit')
              if c.element_info.name in ('주소창 및 검색창', 'Address and search bar')]
    if len(fields) != 1:
        raise RuntimeError('Chrome 주소창을 하나로 확인할 수 없습니다')
    value = fields[0].get_value().strip()
    parsed = urlsplit(value if '://' in value else 'https://' + value)
    if parsed.scheme != 'https' or parsed.hostname != 'nenovaweb.com':
        raise RuntimeError('자동 로그인 대상이 네노바 HTTPS 페이지가 아닙니다')
    return parsed.path or '/'


def _window():
    import psutil
    from pywinauto import Desktop
    matches = []
    for window in Desktop(backend='uia').windows(class_name='Chrome_WidgetWin_1', visible_only=True):
        if psutil.Process(window.process_id()).name().lower() != 'chrome.exe':
            continue
        try:
            _url(window)
            matches.append(window)
        except RuntimeError:
            continue
    if len(matches) != 1:
        raise RuntimeError('네노바 Chrome 창을 하나로 확인할 수 없습니다')
    return matches[0]


def _click(window, control):
    import pyautogui
    import win32gui
    from core.moyi_control import is_paused, OperationPaused
    if is_paused():
        raise OperationPaused('자동 로그인 일시정지')
    _url(window)
    window.set_focus()
    win32gui.ShowWindow(window.handle, 9)
    win32gui.SetForegroundWindow(window.handle)
    time.sleep(.25)
    if win32gui.GetForegroundWindow() != window.handle or not control.is_enabled():
        raise RuntimeError('자동 로그인 창 초점 확인 실패')
    point = control.rectangle().mid_point()
    bounds = window.rectangle()
    if not (bounds.left <= point.x < bounds.right and bounds.top <= point.y < bounds.bottom):
        raise RuntimeError('자동 로그인 버튼 좌표가 현재 창 밖입니다')
    pyautogui.click(point.x, point.y)


def _visible_controls(window, kind):
    bounds = window.rectangle()
    return [c for c in window.descendants(control_type=kind)
            if c.is_visible() and bounds.left <= c.rectangle().left
            and c.rectangle().right <= bounds.right
            and bounds.top <= c.rectangle().top
            and c.rectangle().bottom <= bounds.bottom
            and c.rectangle().width() > 0 and c.rectangle().height() > 0
            and _active_document(c, bounds)]


def _active_document(control, bounds):
    parent = control.parent()
    while parent is not None:
        if parent.element_info.control_type == 'Document':
            rect = parent.rectangle()
            return (bounds.left <= rect.left and rect.right <= bounds.right
                    and bounds.top <= rect.top and rect.bottom <= bounds.bottom)
        if parent.element_info.control_type == 'Window':
            return False
        parent = parent.parent()
    return False


def _navigate(window, url):
    import pyautogui
    import win32gui
    from core.moyi_control import is_paused, OperationPaused
    if is_paused():
        raise OperationPaused('자동 로그인 일시정지')
    _url(window)
    window.set_focus()
    win32gui.ShowWindow(window.handle, 9)
    win32gui.SetForegroundWindow(window.handle)
    time.sleep(.3)
    if win32gui.GetForegroundWindow() != window.handle:
        raise RuntimeError('자동 로그인 창 초점 확인 실패')
    pyautogui.hotkey('ctrl', 'l')
    pyautogui.write(url, interval=.01)
    pyautogui.press('enter')
    _wait(window, lambda path: path == urlsplit(url).path or (urlsplit(url).path == '/dashboard' and path == '/login'))
    time.sleep(1)


def _button(window, name):
    from core.moyi_control import is_paused, OperationPaused
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if is_paused():
            raise OperationPaused('자동 로그인 일시정지')
        controls = [c for c in _visible_controls(window, 'Button')
                    if c.window_text() == name]
        if len(controls) == 1:
            return controls[0]
        time.sleep(.25)
    raise RuntimeError('자동 로그인 버튼 확인 실패: ' + name)


def _wait(window, predicate, timeout=15):
    from core.moyi_control import is_paused, OperationPaused
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if is_paused():
            raise OperationPaused('자동 로그인 일시정지')
        if predicate(_url(window)):
            return
        time.sleep(.25)
    raise RuntimeError('자동 로그인 화면 전환 시간 초과')


def login():
    """Click validated element centers, so window position changes are harmless."""
    window = _window()
    _navigate(window, 'https://nenovaweb.com/dashboard')
    current = _url(window)
    if current != '/login':
        _click(window, _button(window, '로그아웃'))
        _wait(window, lambda path: path == '/login')
    # No password values are read, copied, logged, or persisted here.
    fields = [c for c in _visible_controls(window, 'Edit')
              if c.is_visible() and c.element_info.name not in
              ('주소창 및 검색창', 'Address and search bar')]
    if len(fields) != 2:
        raise RuntimeError('로그인 입력란 확인 실패; 자동입력하지 않음')
    _click(window, min(fields, key=lambda c: c.rectangle().top))
    time.sleep(.7)  # Chrome populates the saved login after this trusted click.
    _click(window, _button(window, '확인'))
    _wait(window, lambda path: path != '/login')
    _button(window, '로그아웃')  # Authenticated UI must actually be present.
    _navigate(window, 'https://nenovaweb.com/sales/defect-deductions?popup=1')


def maybe_recover():
    from core import defect_approval as d
    cfg = d.load(d.ROOT.parent / 'defect_config.json')
    state = auth.state()
    if not cfg.get('automatic_relogin') or not state.get('blocked'):
        return
    now = time.time()
    if now < state.get('relogin_retry_at', 0):
        return
    state.update(relogin_retry_at=now+600, relogin_attempted_at=now)
    save(auth.path(), state)
    try:
        login()
        state = auth.state()
        state.update(next_probe_at=0, relogin_ui_ok=True)
        # Only a subsequent successful API read clears blocked.
        save(auth.path(), state)
    except Exception as exc:
        from core.moyi_control import OperationPaused
        from pyautogui import FailSafeException
        if isinstance(exc, (OperationPaused, FailSafeException)):
            raise
        state.update(relogin_error=str(exc)[:180])
        save(auth.path(), state)
        from core.error_notifications import notify
        if not state.get('relogin_alerted'):
            notify('browser_login_needed', context={'cause':'자동 재로그인 실패: '+str(exc)[:180]})
            state['relogin_alerted']=True
            save(auth.path(),state)
