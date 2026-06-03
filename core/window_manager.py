"""
창 생명주기 관리

작업 전 화면 준비:
  prepare_workspace()  → 방해 창 최소화 + 카톡/워크 존재 확인 + 띄우기
  cleanup_popups()     → 잔여 창(서랍, 파일 다이얼로그 등) 닫기
  focus_kakaotalk()    → 카톡 메인 활성화 + 채팅탭
  focus_kakaowork()    → 카카오워크 앱 활성화
  return_to_kakaotalk() → 워크 작업 후 카톡 복귀
"""
from __future__ import annotations

import ctypes
import time

import pyautogui
import pygetwindow as gw
import win32gui

from core.window_detector import (
    activate_kakaotalk,
    switch_to_chat_tab,
    KAKAOTALK_TITLE,
)

# 정리 대상 창 키워드 (이 키워드가 포함된 창은 ESC로 닫기 시도)
POPUP_KEYWORDS = ["서랍", "열기", "저장", "다운로드", "사진", "미리보기"]

# 작업 전 최소화할 방해 창 키워드
MINIMIZE_KEYWORDS = ["Cursor", "Visual Studio Code", "Code"]

KAKAOWORK_TITLE = "카카오워크"

# 카톡 메인창 고정 좌표 (x, y, w, h)
# 사진 파이프라인 안정화 위해 900x900 이상 필요
# (0,0) 은 PyAutoGUI fail-safe 모서리 → 자동화 즉시 정지 → 50px 안쪽 (2026-05-11 사고)
KAKAOTALK_FIXED_POS = (50, 50, 900, 900)

# ── 창 위치 설정 (조절 가능) ──────────────────────────────────
# data/window_positions.json 으로 메인창/채팅 분리창/저장창 위치를 한 곳에서 조절.
# 파일이 없으면 아래 기본값 사용 (현재 동작과 동일 → 무회귀).
# 값을 바꾸면 다음 사이클부터 자동 반영 (mtime 기반 재로딩, 재시작 불필요).
import json as _json
from pathlib import Path as _Path

_WINDOW_POS_FILE = _Path(__file__).resolve().parent.parent / "data" / "window_positions.json"

_DEFAULT_WINDOW_POS = {
    "kakaotalk_main": {"x": 50,  "y": 50,  "w": 529, "h": 900},
    "kakaowork_main": {"x": 578, "y": 48,  "w": 760, "h": 900},
    "chatroom":       {"x": 50,  "y": 50,  "w": 529, "h": 900},
    "save_dialog":    {"x": 980, "y": 120, "w": 860, "h": 600},
}

_wp_cache: dict = {"mtime": -1.0, "data": None}


def get_window_positions() -> dict:
    """data/window_positions.json 로드 (mtime 캐시 재로딩). 없으면 기본값.

    각 항목은 {"x","y","w","h"}. 누락 키는 기본값으로 보충.
    """
    try:
        mt = _WINDOW_POS_FILE.stat().st_mtime
    except OSError:
        mt = 0.0
    if _wp_cache["data"] is not None and mt == _wp_cache["mtime"]:
        return _wp_cache["data"]

    data = {k: dict(v) for k, v in _DEFAULT_WINDOW_POS.items()}
    if mt:
        try:
            loaded = _json.loads(_WINDOW_POS_FILE.read_text(encoding="utf-8"))
            for key in _DEFAULT_WINDOW_POS:
                ov = loaded.get(key)
                if isinstance(ov, dict):
                    for k in ("x", "y", "w", "h"):
                        if isinstance(ov.get(k), (int, float)):
                            data[key][k] = int(ov[k])
        except Exception as e:
            print(f"  [WIN-POS] config 로드 실패, 기본값 사용: {e}", flush=True)

    _wp_cache["mtime"] = mt
    _wp_cache["data"] = data
    return data


def get_pos_tuple(key: str) -> tuple[int, int, int, int]:
    """설정에서 (x, y, w, h) 튜플 반환. 알 수 없는 key 면 chatroom 폴백."""
    p = get_window_positions().get(key) or _DEFAULT_WINDOW_POS.get(key) or _DEFAULT_WINDOW_POS["chatroom"]
    return (p["x"], p["y"], p["w"], p["h"])


def get_room_list_click_y_offset() -> int:
    """방 리스트에서 방을 클릭할 때 Y 좌표 보정(px, 아래로 +).

    카톡 방 목록 맨 위에 공지/배너가 떠 있으면 첫 줄 클릭이 거기에 닿아
    방이 안 열리고 저장이 실패한다. 이 값만큼 클릭을 아래로 내려 배너를 피한다.
    window_positions.json 의 'room_list_click_y_offset' (없으면 0).
    파일이 작아 매번 직접 읽음(편집 즉시 반영).
    """
    try:
        loaded = _json.loads(_WINDOW_POS_FILE.read_text(encoding="utf-8"))
        raw = loaded.get("room_list_click_y_offset")
        if isinstance(raw, (int, float)):
            return int(raw)
    except Exception:
        pass
    return 0


def force_foreground(hwnd: int) -> bool:
    """AttachThreadInput 해킹으로 강제 포그라운드 탈취.

    Windows의 SetForegroundWindow 제한 우회 — 다른 앱이 포커스 잡고 있어도
    확실히 가져옴.
    """
    import ctypes
    import win32con
    import win32process

    try:
        fg = win32gui.GetForegroundWindow()
        if fg == hwnd:
            return True

        fg_thread, _ = win32process.GetWindowThreadProcessId(fg) if fg else (0, 0)
        target_thread, _ = win32process.GetWindowThreadProcessId(hwnd)
        current_thread = ctypes.windll.kernel32.GetCurrentThreadId()

        # 두 스레드 모두 현재 스레드와 입력 연결
        attached_a = attached_b = False
        if fg_thread and fg_thread != current_thread:
            attached_a = bool(ctypes.windll.user32.AttachThreadInput(current_thread, fg_thread, True))
        if target_thread and target_thread != current_thread and target_thread != fg_thread:
            attached_b = bool(ctypes.windll.user32.AttachThreadInput(current_thread, target_thread, True))

        try:
            # 최소화 복구
            placement = win32gui.GetWindowPlacement(hwnd)
            if placement[1] == win32con.SW_SHOWMINIMIZED:
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.BringWindowToTop(hwnd)
            win32gui.SetForegroundWindow(hwnd)
            win32gui.SetActiveWindow(hwnd)
            win32gui.SetFocus(hwnd)
        finally:
            if attached_a:
                ctypes.windll.user32.AttachThreadInput(current_thread, fg_thread, False)
            if attached_b:
                ctypes.windll.user32.AttachThreadInput(current_thread, target_thread, False)

        time.sleep(0.1)
        return win32gui.GetForegroundWindow() == hwnd
    except Exception as e:
        print(f"  [FORCE-FG] 실패: {e}", flush=True)
        return False


def _find_main_window_any() -> int | None:
    """카톡 메인창 hwnd (숨김 포함). X로 트레이에 닫히면 IsWindowVisible=False 가
    되므로 visible 필터 없이 정확 제목으로 찾는다. visible 우선, 없으면 숨김."""
    vis: list[int] = []
    hidden: list[int] = []
    def _f(h, _):
        try:
            if win32gui.GetWindowText(h) == KAKAOTALK_TITLE:
                (vis if win32gui.IsWindowVisible(h) else hidden).append(h)
        except Exception:
            pass
    win32gui.EnumWindows(_f, None)
    if vis:
        return vis[0]
    if hidden:
        return hidden[0]
    return None


def _relaunch_kakaotalk() -> bool:
    """카톡 메인창 hwnd 자체가 없을 때(트레이에서 완전 숨김) exe 재실행으로 복귀.
    단일 인스턴스라 재실행 시 기존 인스턴스의 메인창이 다시 뜬다."""
    import os
    import subprocess
    cands = [
        r"C:\Program Files (x86)\Kakao\KakaoTalk\KakaoTalk.exe",
        r"C:\Program Files\Kakao\KakaoTalk\KakaoTalk.exe",
    ]
    for c in cands:
        if os.path.exists(c):
            try:
                subprocess.Popen([c])
                # 콜드 스타트(로그인 스플래시/트레이 초기화)는 2초 초과 흔함 →
                # 고정 sleep 대신 창이 뜰 때까지 폴링(최대 ~8s).
                for _ in range(16):
                    time.sleep(0.5)
                    if _find_main_window_any() is not None:
                        return True
                return _find_main_window_any() is not None
            except Exception as e:
                print(f"  [ENSURE-MAIN] 재실행 실패: {e}", flush=True)
                return False
    return False


def ensure_main_window_foreground() -> bool:
    """카톡 메인창이 '보이고 + foreground' 상태가 되도록 보장.

    - 트레이로 닫힘(숨김)/최소화 → 복원(ShowWindow) + 잠긴 좌표로 재배치
    - hwnd 자체가 없으면 exe 재실행으로 복귀 시도
    반환: True = 메인창이 foreground (클릭 안전). False = 실패 → 호출자는 클릭 보류.

    목적: 메인창이 닫힌 채 좌표만 맹목 클릭해 엉뚱한 창(바탕화면/터널창)을
          누르는 사고 방지 (2026-05-25).
    """
    import win32con
    hwnd = _find_main_window_any()
    relaunched = False
    if hwnd is None:
        relaunched = _relaunch_kakaotalk()
        hwnd = _find_main_window_any()
        if hwnd is None:
            print("  [ENSURE-MAIN] 메인창 없음(복귀 실패)", flush=True)
            return False
    try:
        restored = False
        if not win32gui.IsWindowVisible(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
            restored = True
            time.sleep(0.2)
        placement = win32gui.GetWindowPlacement(hwnd)
        if placement[1] == win32con.SW_SHOWMINIMIZED:
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            restored = True
            time.sleep(0.2)
        if restored or relaunched:
            try:
                x, y, w, h = get_pos_tuple("kakaotalk_main")
                win32gui.MoveWindow(hwnd, x, y, w, h, True)
                time.sleep(0.2)
                print("  [ENSURE-MAIN] 닫힌 메인창 복원 + 재배치", flush=True)
            except Exception as _me:
                print(f"  [ENSURE-MAIN] 재배치 실패(좌표 어긋남 가능): {_me}", flush=True)
        force_foreground(hwnd)
        time.sleep(0.05)
        return win32gui.GetForegroundWindow() == hwnd
    except Exception as e:
        print(f"  [ENSURE-MAIN] 실패: {e}", flush=True)
        return False


def lock_kakaotalk_window(pos: tuple[int, int, int, int] | None = None) -> bool:
    """카톡 메인창을 고정 좌표/크기로 강제 이동·리사이즈.

    스크롤/클릭 좌표가 창 위치에 의존하므로, 사용자가 창을 옮겨도
    매번 같은 자리로 끌어온다. pos=None 이면 window_positions.json 의
    'kakaotalk_main' 설정을 사용 (없으면 기본 50,50,900,900).
    """
    import win32con
    if pos is None:
        pos = get_pos_tuple("kakaotalk_main")
    x, y, w, h = pos
    hwnds: list[int] = []

    def _f(hwnd, lst):
        if (
            win32gui.IsWindowVisible(hwnd)
            and win32gui.GetWindowText(hwnd) == KAKAOTALK_TITLE
        ):
            lst.append(hwnd)

    win32gui.EnumWindows(_f, hwnds)
    if not hwnds:
        return False

    try:
        hwnd = hwnds[0]
        # 최소화 복구
        placement = win32gui.GetWindowPlacement(hwnd)
        if placement[1] == win32con.SW_SHOWMINIMIZED:
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            time.sleep(0.2)
        win32gui.MoveWindow(hwnd, x, y, w, h, True)
        time.sleep(0.2)
        return True
    except Exception as e:
        print(f"  [LOCK] 카톡 창 고정 실패: {e}", flush=True)
        return False


def lock_kakaowork_window(pos: tuple[int, int, int, int] | None = None) -> bool:
    """카카오워크 메인창을 고정 좌표/크기로 강제 이동·리사이즈.

    워크 창은 제목이 워크스페이스명(예 '네노바')이라 제목매칭이 안 됨 →
    work_vision_reader.find_kakaowork_window(클래스 기반 hwnd)를 재사용.
    pos=None 이면 window_positions.json 의 'kakaowork_main'.
    """
    import win32con
    if pos is None:
        pos = get_pos_tuple("kakaowork_main")
    x, y, w, h = pos
    try:
        from core.work_vision_reader import find_kakaowork_window
        hwnd = find_kakaowork_window()
        if not hwnd:
            return False
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            time.sleep(0.2)
        win32gui.MoveWindow(hwnd, x, y, w, h, True)
        time.sleep(0.2)
        return True
    except Exception as e:
        print(f"  [LOCK] 워크 창 고정 실패: {e}", flush=True)
        return False


def get_capture_region(key: str) -> dict | None:
    """capture_regions[key] 반환(앱 상대좌표 dict). 없으면 None.
    파일을 매번 읽어 편집 즉시 반영."""
    try:
        loaded = _json.loads(_WINDOW_POS_FILE.read_text(encoding="utf-8"))
        return (loaded.get("capture_regions") or {}).get(key)
    except Exception:
        return None


def minimize_distractions():
    """
    작업 영역을 가리는 방해 창(Cursor, VS Code 등)을 최소화한다.
    카톡/워크 메인은 건드리지 않음.
    """
    all_wins = gw.getAllWindows()
    minimized = []

    for w in all_wins:
        if not w.title or not w.visible or w.isMinimized:
            continue
        if w.title == KAKAOTALK_TITLE or w.title == KAKAOWORK_TITLE:
            continue
        for kw in MINIMIZE_KEYWORDS:
            if kw in w.title:
                try:
                    w.minimize()
                    minimized.append(w.title[:30])
                except Exception:
                    pass
                break

    if minimized:
        print(f"  [MINIMIZE] 최소화: {minimized}")


def prepare_workspace():
    """
    작업 전 화면 준비:
    1. 방해 창 최소화 (Cursor, VS Code 등)
    2. 카톡 창 존재 확인 → 없으면 에러
    3. 카톡 활성화
    4. 워크 창 존재 확인 (경고만)
    """
    # 1. 방해 창 내리기
    minimize_distractions()
    time.sleep(0.3)

    # 2. 카톡 확인 + 활성화
    katalk_wins = gw.getWindowsWithTitle(KAKAOTALK_TITLE)
    if not katalk_wins:
        raise RuntimeError("카카오톡이 실행 중이지 않습니다. 먼저 카카오톡을 실행해주세요.")

    window = activate_kakaotalk()
    switch_to_chat_tab(window)
    time.sleep(0.3)

    # 3. 워크 확인 (없으면 경고만)
    work_wins = gw.getWindowsWithTitle(KAKAOWORK_TITLE)
    if not work_wins:
        print("  [WARN] 카카오워크 앱이 실행 중이지 않습니다. 이미지 업로드 불가.")

    print(f"  [READY] 카톡: ({window.left},{window.top}) {window.width}x{window.height}")
    return window


def fix_chat_window_position(
    hwnd: int,
    x: int | None = None,
    y: int | None = None,
    w: int | None = None,
    h: int | None = None,
) -> bool:
    """카톡 채팅 분리창을 고정 위치로 이동/리사이즈 + TOPMOST.

    인자 미지정 시 window_positions.json 의 'chatroom' 설정 사용
    (없으면 기본 100,50,600,800). 사용자가 창을 옮겨도 매 사이클 같은 자리로 끌어옴.
    카톡 메인과 겹쳐도 OK — 분리창은 TOPMOST 이므로 위에 올라옴.
    """
    try:
        import win32gui
        import win32con
        import ctypes

        cx, cy, cw, ch = get_pos_tuple("chatroom")
        if x is None: x = cx
        if y is None: y = cy
        if w is None: w = cw
        if h is None: h = ch

        # 화면 해상도 — 경계 보정 (화면 밖 나가면 끌어당김)
        user32 = ctypes.windll.user32
        screen_w = user32.GetSystemMetrics(0)
        screen_h = user32.GetSystemMetrics(1)
        if x + w > screen_w - 20:
            x = max(0, screen_w - w - 20)
        if y + h > screen_h - 80:  # 작업표시줄 고려
            y = max(0, screen_h - h - 80)

        win32gui.SetWindowPos(
            hwnd, 0, x, y, w, h,
            win32con.SWP_NOZORDER | win32con.SWP_SHOWWINDOW,
        )
        # TOPMOST 로 올려 Claude/기타 앱이 덮지 못하게
        try:
            SWP = win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW
            win32gui.SetWindowPos(hwnd, -1, 0, 0, 0, 0, SWP)  # HWND_TOPMOST = -1
        except Exception:
            pass
        return True
    except Exception as e:
        print(f"  [WIN] 분리창 고정 실패: {e}", flush=True)
        return False


# 저장/폴더 선택 다이얼로그 제목 키워드
SAVE_DIALOG_KEYWORDS = (
    "다른 이름으로 저장", "Save As", "폴더 선택",
    "Select Folder", "Browse For Folder", "파일 저장",
)


def find_save_dialog_hwnd() -> int | None:
    """현재 떠 있는 '다른 이름으로 저장' / '폴더 선택' 다이얼로그 hwnd 탐색.

    visible + 제목에 저장/폴더 키워드 포함 + 폭 300+ 인 실제 다이얼로그만.
    """
    found: list[int] = []

    def _f(h, _):
        if not win32gui.IsWindow(h) or not win32gui.IsWindowVisible(h):
            return
        t = win32gui.GetWindowText(h) or ""
        if not any(k in t for k in SAVE_DIALOG_KEYWORDS):
            return
        r = win32gui.GetWindowRect(h)
        if (r[2] - r[0]) > 300 and (r[3] - r[1]) > 200:
            found.append(h)

    win32gui.EnumWindows(_f, None)
    return found[0] if found else None


def fix_save_dialog_position(
    hwnd: int | None = None,
    pos: tuple[int, int, int, int] | None = None,
) -> bool:
    """저장 다이얼로그('다른 이름으로 저장' 등)를 고정 위치로 이동.

    hwnd=None 이면 자동 탐색. pos=None 이면 window_positions.json 의
    'save_dialog' 설정 사용 (없으면 기본 980,120,860,600).
    포커스를 유지하므로 파일명 입력/Enter 흐름에 영향 없음.
    """
    try:
        import win32con
        import ctypes

        if hwnd is None:
            hwnd = find_save_dialog_hwnd()
        if not hwnd or not win32gui.IsWindow(hwnd):
            return False

        x, y, w, h = pos if pos is not None else get_pos_tuple("save_dialog")

        # 경계 보정
        user32 = ctypes.windll.user32
        screen_w = user32.GetSystemMetrics(0)
        screen_h = user32.GetSystemMetrics(1)
        if x + w > screen_w - 20:
            x = max(0, screen_w - w - 20)
        if y + h > screen_h - 80:
            y = max(0, screen_h - h - 80)

        placement = win32gui.GetWindowPlacement(hwnd)
        if placement[1] == win32con.SW_SHOWMINIMIZED:
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            time.sleep(0.1)
        win32gui.MoveWindow(hwnd, x, y, w, h, True)
        # 다이얼로그를 위로 올리되 포커스 유지
        try:
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            pass
        return True
    except Exception as e:
        print(f"  [WIN] 저장창 고정 실패: {e}", flush=True)
        return False


def cleanup_popups():
    """
    잔여 팝업/서랍/다이얼로그 창을 모두 닫는다.
    보호 대상: 카톡 메인, 카카오워크 메인, selected_rooms.json의 모든 방 분리창,
               외부 앱(Chrome, Edge 등 브라우저/타 앱).
    학습된 키워드(`auto_popup_keywords.json`)도 함께 사용.
    """
    # 기본 + 자동 학습 키워드 합집합
    learned: list[str] = []
    try:
        from core.popup_auto_learner import load_learned_keywords
        learned = load_learned_keywords()
    except Exception:
        pass
    all_kws = list({*POPUP_KEYWORDS, *learned})

    # 보호 대상: selected_rooms의 모든 방 이름 (분리창 보호)
    protected_titles: set[str] = set()
    try:
        import json
        from pathlib import Path
        sel_path = Path(__file__).parent.parent / "data" / "selected_rooms.json"
        if sel_path.exists():
            for item in json.loads(sel_path.read_text(encoding="utf-8")):
                if isinstance(item, dict) and item.get("name"):
                    protected_titles.add(item["name"])
                elif isinstance(item, str):
                    protected_titles.add(item)
    except Exception:
        pass

    # 외부 앱 키워드 (브라우저/타 프로세스 → 절대 ESC 금지)
    EXTERNAL_KEYWORDS = (" - Chrome", " - Edge", " - Firefox", " - Brave",
                         "Visual Studio", "VS Code", "Notepad", "Explorer",
                         "Cmd ", "PowerShell", "Terminal")

    all_wins = gw.getAllWindows()
    closed = []
    skipped_protected = []

    for w in all_wins:
        if not w.title or not w.visible:
            continue
        # 메인 창 보호
        if w.title == KAKAOTALK_TITLE or w.title == KAKAOWORK_TITLE:
            continue
        # 카톡 채팅 분리창 (방 이름과 정확히 일치) 보호
        if w.title in protected_titles:
            continue
        # 외부 앱 보호
        if any(ek in w.title for ek in EXTERNAL_KEYWORDS):
            continue
        for kw in all_kws:
            if kw and kw in w.title:
                # 보호 대상 부분 매칭 한 번 더 체크 (예: "현장 추가취소방"에 "추가" 매칭 방지)
                if any(pt in w.title or w.title in pt for pt in protected_titles):
                    skipped_protected.append(w.title)
                    break
                try:
                    win32gui.SetForegroundWindow(w._hWnd)
                    time.sleep(0.1)
                    pyautogui.press("escape")
                    time.sleep(0.3)
                    closed.append(w.title)
                except Exception:
                    pass
                break

    if closed:
        print(f"  [CLEANUP] 닫힌 창: {closed}")
    if skipped_protected:
        print(f"  [CLEANUP] 보호로 스킵: {skipped_protected}")


def focus_kakaotalk(*, ensure_min_size: tuple[int, int] = (900, 900), retries: int = 3):
    """
    카톡 메인 창 활성화 + 채팅탭 전환 + 최소 크기 보장.

    카톡 PC 앱은 폭이 좁으면 채팅 패널이 안 보이고 분리창도 잘 안 떠서
    사진 다운로드/업로드가 실패함. ensure_min_size로 강제 리사이즈.

    Args:
        ensure_min_size: (width, height) — 이 크기 미만이면 리사이즈
        retries: 카톡 창 못 찾을 때 재시도 횟수 (분리창 닫는 도중 일시 사라짐 대응)
    """
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            window = activate_kakaotalk()
            break
        except RuntimeError as e:
            last_err = e
            if attempt < retries:
                print(f"  [FOCUS] 카톡 미발견, {attempt+1}회 재시도 중...", flush=True)
                time.sleep(1.0)
            else:
                raise
    else:
        raise last_err  # type: ignore[misc]

    # 항상 고정 좌표로 락 (좌표 자동화 기준선 유지) — 설정 파일 우선
    x, y, w, h = get_pos_tuple("kakaotalk_main")
    if (window.left, window.top, window.width, window.height) != (x, y, w, h):
        if lock_kakaotalk_window():
            time.sleep(0.3)
            window = activate_kakaotalk()
            print(f"  [LOCK] 카톡창 → ({x},{y}) {w}x{h}")

    switch_to_chat_tab(window)
    time.sleep(0.3)
    return window


def focus_kakaowork():
    """
    카카오워크 앱 활성화.

    개선: KakaoWork 메인창이 visible=False 상태여도 찾아서 ShowWindow(SW_SHOW)로 강제.
    hwnd=XXXX title='카카오워크' 상태인데 IsWindowVisible=0일 수 있음.
    """
    import win32con
    # 0차 (신규): 타이틀 '카카오워크' + KakaoWork class + 크기 > 400, visible 무관
    # 이걸 제일 먼저 찾아서 SW_SHOW로 강제 표시
    candidates: list[int] = []
    def _find_kakaowork_main(h, lst):
        cls = win32gui.GetClassName(h) or ""
        if "KakaoWork" not in cls:
            return
        t = win32gui.GetWindowText(h) or ""
        if t != KAKAOWORK_TITLE:
            return
        r = win32gui.GetWindowRect(h)
        if (r[2] - r[0]) < 400 or (r[3] - r[1]) < 400:
            return
        lst.append(h)
    win32gui.EnumWindows(_find_kakaowork_main, candidates)
    if candidates:
        # 타이틀=카카오워크 + class=KakaoWork + 400+ 사이즈 창 발견
        h = candidates[0]
        try:
            win32gui.ShowWindow(h, win32con.SW_SHOW)
            win32gui.ShowWindow(h, win32con.SW_RESTORE)
            time.sleep(0.3)
            ctypes.windll.user32.AllowSetForegroundWindow(-1)
            win32gui.BringWindowToTop(h)
            win32gui.SetForegroundWindow(h)
            time.sleep(0.3)
            # 래퍼 반환 (pygetwindow 호환)
            import pygetwindow as _gw
            wraps = _gw.getWindowsWithTitle(KAKAOWORK_TITLE)
            if wraps:
                return max(wraps, key=lambda w: w.width * w.height)
            # 폴백: 경량 wrapper
            r = win32gui.GetWindowRect(h)
            class _FW: pass
            fw = _FW()
            fw._hWnd = h
            fw.left, fw.top = r[0], r[1]
            fw.width, fw.height = r[2]-r[0], r[3]-r[1]
            fw.isMinimized = win32gui.IsIconic(h)
            fw.restore = lambda: win32gui.ShowWindow(h, win32con.SW_RESTORE)
            fw.activate = lambda: win32gui.SetForegroundWindow(h)
            fw.minimize = lambda: win32gui.ShowWindow(h, win32con.SW_MINIMIZE)
            return fw
        except Exception as e:
            print(f"  [WORK-FOCUS] 메인창 강제 표시 실패: {e}", flush=True)

    # 1차: 타이틀 매칭 (기존 경로, visible only)
    windows = gw.getWindowsWithTitle(KAKAOWORK_TITLE)

    # 2차: 클래스명 기반 (KakaoWork.exe) — ToastWindow 등 제외
    if not windows:
        import win32con
        kw_hwnds: list[int] = []

        def _f(h, lst):
            if not win32gui.IsWindowVisible(h):
                return
            cls = win32gui.GetClassName(h) or ""
            title = win32gui.GetWindowText(h) or ""
            if "KakaoWork" not in cls:
                return
            # ToastWindow는 팝업이므로 메인 아님
            if "Toast" in title:
                return
            # 빈 제목 + 0x0 size는 숨김 창
            r = win32gui.GetWindowRect(h)
            w, hh = r[2] - r[0], r[3] - r[1]
            if w == 0 or hh == 0:
                return
            kw_hwnds.append(h)

        win32gui.EnumWindows(_f, kw_hwnds)

        if not kw_hwnds:
            # 3차: 최소화된(tray) 창 찾기 — IsWindowVisible=False이지만 존재
            all_kw: list[int] = []
            def _all(h, lst):
                cls = win32gui.GetClassName(h) or ""
                if "KakaoWork" in cls:
                    lst.append(h)
            win32gui.EnumWindows(_all, all_kw)
            # 크기가 있는 첫 창 선택 → ShowWindow로 복원
            for h in all_kw:
                r = win32gui.GetWindowRect(h)
                if (r[2] - r[0]) > 100 and (r[3] - r[1]) > 100:
                    try:
                        win32gui.ShowWindow(h, win32con.SW_RESTORE)
                        win32gui.ShowWindow(h, win32con.SW_SHOW)
                        time.sleep(0.3)
                        win32gui.SetForegroundWindow(h)
                        time.sleep(0.5)
                        # pygetwindow 래퍼로 반환
                        import pygetwindow as _gw
                        wraps = _gw.getWindowsWithTitle(win32gui.GetWindowText(h))
                        if wraps:
                            return wraps[0]
                    except Exception:
                        pass
            raise RuntimeError("카카오워크 앱이 실행 중이지 않습니다.")

        # kw_hwnds 중 가장 큰 것 선택 → 메인 창
        best = max(kw_hwnds, key=lambda h: (
            (win32gui.GetWindowRect(h)[2] - win32gui.GetWindowRect(h)[0]) *
            (win32gui.GetWindowRect(h)[3] - win32gui.GetWindowRect(h)[1])
        ))
        try:
            ctypes.windll.user32.AllowSetForegroundWindow(-1)
            win32gui.SetForegroundWindow(best)
            time.sleep(0.5)
            import pygetwindow as _gw
            t = win32gui.GetWindowText(best) or ""
            wraps = _gw.getWindowsWithTitle(t) if t else []
            if wraps:
                return wraps[0]
            # 타이틀 빈 창 → 경량 래퍼 반환 (pygetwindow wrapper 호환)
            r = win32gui.GetWindowRect(best)
            class _FakeWin:
                pass
            fw = _FakeWin()
            fw._hWnd = best
            fw.left = r[0]
            fw.top = r[1]
            fw.width = r[2] - r[0]
            fw.height = r[3] - r[1]
            fw.isMinimized = win32gui.IsIconic(best)
            fw.restore = lambda: win32gui.ShowWindow(best, win32con.SW_RESTORE)
            fw.activate = lambda: win32gui.SetForegroundWindow(best)
            fw.minimize = lambda: win32gui.ShowWindow(best, win32con.SW_MINIMIZE)
            return fw
        except Exception as e:
            print(f"  [WORK-FOCUS] 클래스 기반 활성화 실패: {e}", flush=True)

        raise RuntimeError("카카오워크 메인창 활성화 실패")

    # 타이틀 매칭 성공한 경우 (원래 경로)
    main = max(windows, key=lambda w: w.width * w.height)
    if main.isMinimized:
        main.restore()
        time.sleep(0.3)

    ctypes.windll.user32.AllowSetForegroundWindow(-1)
    try:
        win32gui.SetForegroundWindow(main._hWnd)
    except Exception:
        main.minimize()
        time.sleep(0.2)
        main.restore()
        time.sleep(0.3)
        win32gui.SetForegroundWindow(main._hWnd)

    time.sleep(0.5)
    return main


def return_to_kakaotalk():
    """카카오워크 작업 완료 후 카톡 메인으로 복귀."""
    return focus_kakaotalk()


def find_chat_room_hwnd() -> int | None:
    """
    현재 열려있는 카카오톡 채팅방 창의 hwnd를 찾는다.
    채팅방 창 = 카카오톡 메인이 아닌, 작은 카톡 창.
    """
    all_wins = gw.getAllWindows()
    candidates = []

    for w in all_wins:
        if not w.title or not w.visible:
            continue
        # 메인 창 제외 (메인은 가장 큰 창)
        if w.title == KAKAOTALK_TITLE:
            continue
        # 채팅방 창은 카카오톡 프로세스의 작은 창
        # 제목에 특정 키워드가 없고, 적당한 크기
        if w.width > 300 and w.height > 400 and w.width < 800:
            candidates.append(w)

    if not candidates:
        return None

    # 가장 최근 활성화된(= 포그라운드에 가까운) 창
    return candidates[0]._hWnd
