"""
pywinauto UIAutomation 기반 카톡 서랍 오프너.

기존 `drawer_handler.open_drawer`의 픽셀 클릭 방식은 다음 이유로 불안정:
  - ≡ 버튼 위치가 창 크기에 의존 (rect[2]-20, rect[1]+55)
  - 팝업 메뉴 탐지를 윈도우 enum + 크기 필터로 추측
  - "채팅방 서랍" 위치를 popup[1]+82 오프셋으로 추측 (메뉴 아이템 순서/구분선 변화에 깨짐)
  - 서브메뉴 hover/click 타이밍 민감
  - 이전 "100% 완료" 등 블로킹 다이얼로그에 포커스 뺏김

이 모듈은 Windows UI Automation API (pywinauto backend="uia")로:
  1. 채팅창에 접근 가능한 버튼/메뉴 아이템을 **이름/역할**로 찾음 (픽셀 무관)
  2. `.invoke()` 네이티브 메서드로 실행 (SetForegroundWindow 경쟁 없음)
  3. 메뉴 아이템 텍스트 ("채팅방 서랍", "사진/동영상")로 정확히 타겟팅

카톡이 DirectUI 커스텀 렌더링을 쓰면 UIA가 ≡ 버튼을 못 볼 수 있어서,
실패 시 기존 픽셀 경로로 graceful fallback 한다.

환경변수:
  NENOVA_DRAWER_FORCE_PIXEL=1 → UIA 경로 완전 스킵 (비상 스위치)
  NENOVA_DRAWER_DEBUG=1       → UIA 트리 덤프를 captures/uia_*.txt 에 남김
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import win32gui

try:
    from pywinauto import Application, Desktop, timings
    PYWINAUTO_AVAILABLE = True
except Exception as _e:
    PYWINAUTO_AVAILABLE = False
    _IMPORT_ERR = str(_e)

from core.traced_actions import mark

CAPTURES_DIR = Path(__file__).parent.parent / "captures"

# 메뉴 아이템 후보 이름 (카톡 한글 UI 우선, 로케일 변경 대비 영어도)
MENU_NAMES_HAMBURGER = ["더보기", "메뉴", "More", "Menu"]
MENU_NAMES_DRAWER = ["채팅방 서랍", "Chat Drawer", "Drawer"]
MENU_NAMES_PHOTO_TAB = ["사진/동영상", "사진", "Photos", "Photo"]


def _debug_enabled() -> bool:
    return os.getenv("NENOVA_DRAWER_DEBUG") == "1"


def _dump_tree(element, path: Path, max_depth: int = 6):
    """UIA 요소 트리를 텍스트로 덤프 (디버그용)."""
    try:
        lines: list[str] = []

        def walk(el, depth: int):
            if depth > max_depth:
                return
            try:
                info = el.element_info
                name = (info.name or "")[:50]
                ctrl = info.control_type or ""
                cls = info.class_name or ""
                rect = info.rectangle
                lines.append(
                    f"{'  '*depth}[{ctrl}] name={name!r} cls={cls} "
                    f"rect=({rect.left},{rect.top},{rect.right},{rect.bottom})"
                )
                for child in el.children():
                    walk(child, depth + 1)
            except Exception as e:
                lines.append(f"{'  '*depth}<err: {e}>")

        walk(element, 0)
        CAPTURES_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines), encoding="utf-8")
    except Exception as e:
        print(f"    [UIA] dump 실패: {e}", flush=True)


# ═══════════════════════════════════════════════════════
# UIA 연결
# ═══════════════════════════════════════════════════════

def _connect_window(chat_hwnd: int):
    """chat_hwnd 에 UIA 로 연결. None 반환 = UIA 사용 불가."""
    if not PYWINAUTO_AVAILABLE:
        return None
    try:
        app = Application(backend="uia").connect(handle=chat_hwnd, timeout=3)
        return app.window(handle=chat_hwnd)
    except Exception as e:
        print(f"    [UIA] connect 실패: {e}", flush=True)
        return None


def _find_by_name_substr(root, name_candidates: list[str], control_types: list[str] | None = None):
    """root 하위에서 name에 후보 문자열 포함 + control_type 일치하는 첫 요소."""
    if control_types is None:
        control_types = ["Button", "MenuItem", "ListItem", "Hyperlink", "TabItem"]
    try:
        for ct in control_types:
            try:
                elements = root.descendants(control_type=ct)
            except Exception:
                elements = []
            for el in elements:
                try:
                    nm = el.element_info.name or ""
                    for cand in name_candidates:
                        if cand in nm:
                            return el
                except Exception:
                    continue
    except Exception as e:
        print(f"    [UIA] descendants 탐색 실패: {e}", flush=True)
    return None


def _invoke_safely(element, step_tag: str) -> bool:
    """UIA invoke → 실패 시 click_input 폴백."""
    try:
        element.invoke()
        mark(step_tag, "after", {"method": "uia.invoke"})
        return True
    except Exception as e:
        # Invoke 패턴 미지원 또는 일시적 에러 → 좌표 클릭
        try:
            rect = element.element_info.rectangle
            cx = (rect.left + rect.right) // 2
            cy = (rect.top + rect.bottom) // 2
            import pyautogui
            pyautogui.click(cx, cy)
            mark(step_tag, "after", {"method": "uia.coord_click", "xy": [cx, cy], "invoke_err": str(e)})
            return True
        except Exception as e2:
            mark(step_tag, "fail", {"invoke_err": str(e), "coord_err": str(e2)})
            return False


# ═══════════════════════════════════════════════════════
# 블로킹 팝업 선제 정리
# ═══════════════════════════════════════════════════════

# 차단 팝업 식별 — 너무 광범위한 단어("알림")는 카톡 메인창까지 잡아서 위험.
# "완료되었습니다"/"저장되었습니다"/"다운로드가 완료" 등 구체 문구만 사용.
BLOCKING_KEYWORDS = [
    "완료되었습니다",          # "100% 완료되었습니다"
    "다운로드가 완료",
    "저장되었습니다",
    "전송이 완료",
    "Download complete",
    "Saved successfully",
]

# 참고용 — 과거 사용, 현재는 False-positive 방지차원에서 미사용
BLOCKING_DIALOG_TITLES: list[str] = []


def dismiss_blocking_dialogs(timeout: float = 2.0) -> int:
    """카톡 작업을 막는 모달 팝업("100% 완료되었습니다" 등)만 선택적으로 닫음.

    Returns: 닫은 팝업 개수.
    """
    closed = 0
    t0 = time.time()
    while time.time() - t0 < timeout:
        found_any = False

        def _check(hwnd, _):
            nonlocal found_any
            if not win32gui.IsWindowVisible(hwnd):
                return
            title = win32gui.GetWindowText(hwnd) or ""
            if not title:
                return
            # 보수적 매칭: 구체 완료 문구를 포함한 제목만
            if any(k in title for k in BLOCKING_KEYWORDS):
                # 작은 모달 창인지 크기로 재확인 (메인 앱 창 방어)
                try:
                    r = win32gui.GetWindowRect(hwnd)
                    w, h = r[2] - r[0], r[3] - r[1]
                    if w > 600 or h > 600:
                        return  # 메인 앱 수준 크기 → 닫지 않음
                except Exception:
                    return
                found_any = True
                try:
                    import win32con
                    win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
                    if _debug_enabled():
                        print(f"    [UIA] 차단팝업 닫기: {title!r}", flush=True)
                except Exception:
                    pass

        win32gui.EnumWindows(_check, None)
        if not found_any:
            break
        closed += 1
        time.sleep(0.3)

    if closed and _debug_enabled():
        print(f"    [UIA] 사전 차단팝업 {closed}개 정리", flush=True)
    return closed


# ═══════════════════════════════════════════════════════
# 핵심: ≡ 메뉴 열기 → 채팅방 서랍 → 사진/동영상
# ═══════════════════════════════════════════════════════

def _find_kakao_popup_menu(timeout: float = 3.0):
    """≡ 클릭 직후 뜨는 EVA_Menu 클래스 팝업을 UIA 로 래핑해서 반환."""
    if not PYWINAUTO_AVAILABLE:
        return None
    t0 = time.time()
    while time.time() - t0 < timeout:
        popup_hwnd = None

        def _cb(hwnd, _):
            nonlocal popup_hwnd
            if popup_hwnd or not win32gui.IsWindowVisible(hwnd):
                return
            if win32gui.GetWindowText(hwnd):
                return
            cls = win32gui.GetClassName(hwnd) or ""
            if "EVA_Menu" in cls:
                popup_hwnd = hwnd

        win32gui.EnumWindows(_cb, None)
        if popup_hwnd:
            try:
                app = Application(backend="uia").connect(handle=popup_hwnd, timeout=1)
                return app.window(handle=popup_hwnd)
            except Exception as e:
                print(f"    [UIA] popup connect 실패: {e}", flush=True)
                return None
        time.sleep(0.15)
    return None


def _click_hamburger_via_uia(chat_hwnd: int) -> bool:
    """UIA로 ≡ 버튼 찾아서 invoke. 실패 시 False."""
    win = _connect_window(chat_hwnd)
    if win is None:
        return False

    if _debug_enabled():
        _dump_tree(win, CAPTURES_DIR / f"uia_chat_{chat_hwnd}.txt")

    # ≡ 버튼 탐색. 접근성 이름 후보:
    btn = _find_by_name_substr(win, MENU_NAMES_HAMBURGER, control_types=["Button"])
    if btn is None:
        # 일부 카톡 빌드는 ≡ 가 Button 이 아닌 Pane/Image. 더 넓게 시도.
        btn = _find_by_name_substr(
            win, MENU_NAMES_HAMBURGER,
            control_types=["Button", "Pane", "Image", "MenuItem", "Custom"],
        )
    if btn is None:
        print("    [UIA] ≡ 버튼을 접근성 트리에서 못 찾음", flush=True)
        mark("open_drawer.uia_hamburger", "fail", {"reason": "not in tree"})
        return False

    mark("open_drawer.uia_hamburger", "before", {"name": btn.element_info.name})
    return _invoke_safely(btn, "open_drawer.uia_hamburger")


def _click_drawer_item_via_uia(popup_win) -> bool:
    """팝업 메뉴에서 "채팅방 서랍" MenuItem 찾아서 invoke."""
    item = _find_by_name_substr(popup_win, MENU_NAMES_DRAWER, control_types=["MenuItem"])
    if item is None:
        # MenuItem 이 아닌 ListItem 일 수도 있음
        item = _find_by_name_substr(
            popup_win, MENU_NAMES_DRAWER,
            control_types=["MenuItem", "ListItem", "Button", "Text"],
        )
    if item is None:
        print("    [UIA] '채팅방 서랍' 메뉴 아이템 못 찾음", flush=True)
        if _debug_enabled():
            _dump_tree(popup_win, CAPTURES_DIR / "uia_popup.txt")
        mark("open_drawer.uia_drawer_item", "fail", {"reason": "not in popup"})
        return False

    mark("open_drawer.uia_drawer_item", "before", {"name": item.element_info.name})
    return _invoke_safely(item, "open_drawer.uia_drawer_item")


def _click_photo_tab_via_uia(timeout: float = 3.0) -> bool:
    """서브메뉴의 "사진/동영상" 찾아서 invoke.

    서브메뉴는 새로운 EVA_Menu 팝업으로 뜨거나 기존 팝업 내부 확장일 수 있음.
    Desktop 전체에서 보이는 모든 EVA_Menu 를 훑어서 찾는다.
    """
    if not PYWINAUTO_AVAILABLE:
        return False

    t0 = time.time()
    while time.time() - t0 < timeout:
        menu_hwnds: list[int] = []

        def _cb(hwnd, _):
            if not win32gui.IsWindowVisible(hwnd):
                return
            if win32gui.GetWindowText(hwnd):
                return
            cls = win32gui.GetClassName(hwnd) or ""
            if "EVA_Menu" in cls:
                menu_hwnds.append(hwnd)

        win32gui.EnumWindows(_cb, None)

        for hwnd in menu_hwnds:
            try:
                app = Application(backend="uia").connect(handle=hwnd, timeout=1)
                menu_win = app.window(handle=hwnd)
                item = _find_by_name_substr(
                    menu_win, MENU_NAMES_PHOTO_TAB,
                    control_types=["MenuItem", "ListItem", "Button", "Text"],
                )
                if item is not None:
                    mark("open_drawer.uia_photo_tab", "before", {"name": item.element_info.name})
                    return _invoke_safely(item, "open_drawer.uia_photo_tab")
            except Exception:
                continue
        time.sleep(0.2)

    print("    [UIA] '사진/동영상' 탭 서브메뉴에서 못 찾음", flush=True)
    mark("open_drawer.uia_photo_tab", "fail", {"reason": "submenu empty or item missing"})
    return False


def _wait_drawer_window(timeout: float = 6.0) -> int | None:
    """'채팅방 서랍' 창이 뜰 때까지 대기."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        results: list[int] = []

        def _cb(hwnd, lst):
            if win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd) == "채팅방 서랍":
                lst.append(hwnd)

        win32gui.EnumWindows(_cb, results)
        if results:
            return results[0]
        time.sleep(0.3)
    return None


# ═══════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════

def open_drawer_uia(chat_hwnd: int) -> int | None:
    """UIA 기반 드로어 오프너.

    Returns:
        int: 서랍 창 hwnd (성공)
        None: UIA 경로 실패 — 호출자가 기존 픽셀 경로로 폴백해야 함.
    """
    if not PYWINAUTO_AVAILABLE:
        print(f"    [UIA] pywinauto 사용 불가 — 픽셀 경로로 폴백", flush=True)
        return None

    if os.getenv("NENOVA_DRAWER_FORCE_PIXEL") == "1":
        print(f"    [UIA] 환경변수로 UIA 비활성 — 픽셀 경로로 폴백", flush=True)
        return None

    # 1) 블로킹 팝업 먼저 닫기
    dismiss_blocking_dialogs()

    # 2) 채팅창 활성화 (UIA가 숨겨진 창을 못 볼 수도)
    try:
        import win32con
        win32gui.ShowWindow(chat_hwnd, win32con.SW_RESTORE)
        SWP = 0x0002 | 0x0001 | 0x0040
        win32gui.SetWindowPos(chat_hwnd, -1, 0, 0, 0, 0, SWP)
        try:
            win32gui.SetForegroundWindow(chat_hwnd)
        except Exception:
            pass
    except Exception:
        pass
    time.sleep(0.3)

    # 3) ≡ 메뉴 열기 (UIA)
    if not _click_hamburger_via_uia(chat_hwnd):
        return None
    time.sleep(0.8)

    # 4) 팝업 탐지
    popup_win = _find_kakao_popup_menu(timeout=3.0)
    if popup_win is None:
        print("    [UIA] ≡ 팝업 창 미감지 (클릭은 성공했는데 EVA_Menu 안 뜸)", flush=True)
        mark("open_drawer.uia_popup_detected", "fail")
        return None
    mark("open_drawer.uia_popup_detected", "after")

    # 5) "채팅방 서랍" 클릭
    if not _click_drawer_item_via_uia(popup_win):
        return None
    time.sleep(1.0)

    # 6) "사진/동영상" 서브메뉴 클릭
    if not _click_photo_tab_via_uia(timeout=3.0):
        # 서브메뉴 없이 바로 서랍이 열리는 빌드도 있을 수 있음 → 서랍 창 확인
        drawer = _wait_drawer_window(timeout=2.0)
        if drawer:
            print(f"    [UIA] 서브메뉴 없이 서랍 직접 열림: hwnd={drawer}", flush=True)
            mark("open_drawer.uia_panel_opened", "after", {"hwnd": drawer})
            return drawer
        return None

    # 7) 서랍 창 대기
    drawer = _wait_drawer_window(timeout=6.0)
    if drawer:
        print(f"    [UIA] 서랍 열림: hwnd={drawer}", flush=True)
        mark("open_drawer.uia_panel_opened", "after", {"hwnd": drawer})
        return drawer

    print("    [UIA] 서랍 창 미감지 (≡→서랍→사진 클릭 성공했는데 창이 안 뜸)", flush=True)
    mark("open_drawer.uia_panel_opened", "fail")
    return None


# ═══════════════════════════════════════════════════════
# 진단: 단독 실행 시 UIA 트리 덤프
# ═══════════════════════════════════════════════════════

def probe(chat_hwnd: int | None = None):
    """주어진 chat_hwnd 또는 현재 포커스된 창의 UIA 트리를 덤프."""
    if not PYWINAUTO_AVAILABLE:
        print(f"pywinauto 미설치: {_IMPORT_ERR}")
        return

    if chat_hwnd is None:
        chat_hwnd = win32gui.GetForegroundWindow()

    title = win32gui.GetWindowText(chat_hwnd)
    rect = win32gui.GetWindowRect(chat_hwnd)
    print(f"[probe] hwnd={chat_hwnd} title={title!r} rect={rect}")

    win = _connect_window(chat_hwnd)
    if win is None:
        print("  → UIA connect 실패")
        return

    CAPTURES_DIR.mkdir(parents=True, exist_ok=True)
    out = CAPTURES_DIR / f"uia_probe_{chat_hwnd}.txt"
    _dump_tree(win, out, max_depth=8)
    print(f"  → 트리 덤프: {out}")

    # Button 하위 요약
    try:
        btns = win.descendants(control_type="Button")
        print(f"  → Button 총 {len(btns)}개:")
        for b in btns[:30]:
            nm = (b.element_info.name or "")[:40]
            print(f"     - {nm!r}")
    except Exception as e:
        print(f"  → Button enum 에러: {e}")


if __name__ == "__main__":
    import sys
    hwnd = None
    if len(sys.argv) > 1:
        try:
            hwnd = int(sys.argv[1])
        except ValueError:
            pass
    probe(hwnd)
