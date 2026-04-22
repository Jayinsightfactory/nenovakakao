"""
≡ 클릭 진단 — 분리창 직접 열고, 클릭 전/후 전체 상태 덤프.

실패 원인 가설 검증:
  A) 클릭이 ≡ 아닌 다른 곳에 감
  B) ≡ 클릭되지만 팝업이 예상과 다른 위치에 뜸
  C) TOPMOST Z-order 경쟁으로 클릭이 다른 창에 전달
  D) KakaoTalk 이 background 앱으로 간주해서 팝업 suppression

실행:
  1. 카톡 실행 + 아무 채팅방 열기 (자동으로 분리창 유도)
  2. "$PYTHON" tools/diagnose_hamburger_click.py

결과:
  - captures/diag/before_click.png  (≡ 클릭 전)
  - captures/diag/after_click.png   (클릭 0.5초 후)
  - captures/diag/after_click_1s.png (1.5초 후)
  - captures/diag/windows_before.txt
  - captures/diag/windows_after.txt
  - 콘솔: 상세 로그
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import pyautogui
import win32gui
import win32con
import win32api
from PIL import ImageGrab

pyautogui.FAILSAFE = False

DIAG_DIR = ROOT / "captures" / "diag"
DIAG_DIR.mkdir(parents=True, exist_ok=True)


def find_first_chat_separator() -> tuple[int, str, tuple] | None:
    """카톡 분리창 첫 번째 찾기."""
    KAKAO_EXCLUDE = {"카카오톡"}
    candidates: list = []

    def cb(h, _):
        if not win32gui.IsWindowVisible(h):
            return
        t = win32gui.GetWindowText(h)
        if not t or t in KAKAO_EXCLUDE:
            return
        cls = win32gui.GetClassName(h) or ""
        if not cls.startswith("EVA_"):
            return
        r = win32gui.GetWindowRect(h)
        w, hh = r[2] - r[0], r[3] - r[1]
        if 300 <= w <= 900 and 500 <= hh <= 1000:
            candidates.append((h, t, r))

    win32gui.EnumWindows(cb, None)
    return candidates[0] if candidates else None


def dump_windows(out: Path, label: str):
    """현재 보이는 모든 창 덤프."""
    lines = [f"=== {label} ===\n"]

    def cb(h, _):
        if not win32gui.IsWindowVisible(h):
            return
        r = win32gui.GetWindowRect(h)
        w, hh = r[2] - r[0], r[3] - r[1]
        if w < 50 or hh < 50:
            return
        t = win32gui.GetWindowText(h) or ""
        cls = win32gui.GetClassName(h) or ""
        ex = win32gui.GetWindowLong(h, win32con.GWL_EXSTYLE)
        topmost = "T" if (ex & win32con.WS_EX_TOPMOST) else " "
        iconic = "I" if win32gui.IsIconic(h) else " "
        lines.append(
            f"  [{topmost}{iconic}] hwnd={h:>8} rect=({r[0]:>5},{r[1]:>4},{r[2]:>5},{r[3]:>4}) "
            f"{w:>4}x{hh:<4} cls={cls:<40} title={t[:40]!r}"
        )

    win32gui.EnumWindows(cb, None)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"  → {out.name}: {len(lines)-1} 창")


def set_topmost(hwnd: int):
    SWP = win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW
    win32gui.SetWindowPos(hwnd, -1, 0, 0, 0, 0, SWP)


def click_mouse_at(x: int, y: int, method: str = "pyautogui"):
    """다양한 클릭 방식 시도."""
    if method == "pyautogui":
        pyautogui.click(x, y)
    elif method == "win32_sendinput":
        # SendInput with absolute coordinates
        pyautogui.moveTo(x, y)
        time.sleep(0.05)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        time.sleep(0.02)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
    elif method == "postmessage":
        # PostMessage to the window at that coordinate
        target_hwnd = win32gui.WindowFromPoint((x, y))
        print(f"    WindowFromPoint({x},{y}) = hwnd={target_hwnd} title={win32gui.GetWindowText(target_hwnd)!r}")
        # convert to client coords
        client_x, client_y = win32gui.ScreenToClient(target_hwnd, (x, y))
        lparam = (client_y << 16) | (client_x & 0xFFFF)
        win32gui.PostMessage(target_hwnd, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, lparam)
        win32gui.PostMessage(target_hwnd, win32con.WM_LBUTTONUP, 0, lparam)


def capture_and_dump(chat_rect: tuple, tag: str):
    """분리창 영역 캡처 + 전체 창 덤프."""
    img = ImageGrab.grab(bbox=chat_rect)
    png_path = DIAG_DIR / f"{tag}.png"
    img.save(png_path)
    print(f"  → {png_path.name} ({img.size})")
    dump_windows(DIAG_DIR / f"{tag}_windows.txt", tag)


def probe_windowfrompoint(x: int, y: int):
    """지정 좌표의 Window 체인 추적."""
    h = win32gui.WindowFromPoint((x, y))
    t = win32gui.GetWindowText(h) or ""
    cls = win32gui.GetClassName(h) or ""
    print(f"  WindowFromPoint({x},{y}) = hwnd={h} cls={cls} title={t[:50]!r}")
    # 부모 체인
    parent = h
    chain = []
    for _ in range(5):
        p = win32gui.GetParent(parent)
        if not p or p == parent:
            break
        chain.append(p)
        parent = p
    for p in chain:
        tt = win32gui.GetWindowText(p) or ""
        cc = win32gui.GetClassName(p) or ""
        print(f"    parent hwnd={p} cls={cc} title={tt[:50]!r}")


def main():
    print("[DIAG] ≡ 클릭 진단 시작\n")

    # 1. 분리창 찾기
    sep = find_first_chat_separator()
    if not sep:
        print("[ERR] 분리창 없음. 카톡 방 하나를 더블클릭해서 분리창으로 먼저 여세요.")
        sys.exit(1)
    hwnd, title, rect = sep
    print(f"[분리창] hwnd={hwnd} title={title!r} rect={rect}")

    # 2. 안전 위치로 이동 + TOPMOST
    print(f"\n[이동] (910, 50, 600, 800) + TOPMOST")
    win32gui.SetWindowPos(
        hwnd, 0, 910, 50, 600, 800,
        win32con.SWP_NOZORDER | win32con.SWP_SHOWWINDOW,
    )
    time.sleep(0.3)
    set_topmost(hwnd)
    time.sleep(0.5)
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception as e:
        print(f"  SetForegroundWindow 실패 (정상, 무시): {e}")
    time.sleep(0.5)

    new_rect = win32gui.GetWindowRect(hwnd)
    print(f"[이동후] rect={new_rect}")

    # 3. ≡ 예상 좌표 (rect[2]-30, rect[1]+55 — 구버전 공식)
    menu_x_old = new_rect[2] - 30
    menu_y_old = new_rect[1] + 55

    # 4. BEFORE 캡처
    print(f"\n[BEFORE] 캡처")
    capture_and_dump(new_rect, "before_click")
    print(f"  예상 ≡ 좌표 (old formula): ({menu_x_old}, {menu_y_old})")
    probe_windowfrompoint(menu_x_old, menu_y_old)

    # 5. Vision 으로 ≡ 찾기
    print(f"\n[VISION] ≡ 위치 찾기")
    try:
        from core.vision_clicker import find_and_click
        v = find_and_click(
            new_rect,
            "카카오톡 채팅 분리창 상단 툴바의 햄버거 메뉴(≡) 아이콘. "
            "방 제목 옆 오른쪽. 전화/영상/검색 아이콘과 함께 있음. "
            "Windows 닫기(X) 버튼이 아님.",
            tag="diag.hamburger",
            min_confidence=0.55,
            dry_run=True,  # 클릭 안 함 - 좌표만
        )
        if v.found:
            menu_x, menu_y = v.x, v.y
            print(f"  Vision ≡: ({menu_x}, {menu_y}) conf={v.confidence:.2f}")
            probe_windowfrompoint(menu_x, menu_y)
        else:
            print(f"  Vision 실패: {v.debug[:100]}")
            menu_x, menu_y = menu_x_old, menu_y_old
    except Exception as e:
        print(f"  Vision 예외: {e}")
        menu_x, menu_y = menu_x_old, menu_y_old

    # 6. 클릭 방식별 테스트
    for method in ["pyautogui", "win32_sendinput", "postmessage"]:
        print(f"\n[CLICK] method={method} at ({menu_x}, {menu_y})")
        # TOPMOST 재확인
        set_topmost(hwnd)
        try:
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            pass
        time.sleep(0.3)

        click_mouse_at(menu_x, menu_y, method=method)
        time.sleep(1.0)

        capture_and_dump(new_rect, f"after_{method}")

        # 팝업 감지 (EVA_Window_Dblclk 클래스 + 작은 크기 or EVA_Menu)
        popups_found = []

        def check_popup(h, _):
            if not win32gui.IsWindowVisible(h):
                return
            if win32gui.GetWindowText(h):
                return  # 제목 있으면 팝업 아님
            r = win32gui.GetWindowRect(h)
            w, hh = r[2] - r[0], r[3] - r[1]
            if not (80 <= w <= 500 and 100 <= hh <= 700):
                return
            cls = win32gui.GetClassName(h) or ""
            if not cls.startswith("EVA_"):
                return
            popups_found.append((h, r, cls))

        win32gui.EnumWindows(check_popup, None)
        print(f"  팝업 후보: {len(popups_found)}개")
        for h, r, c in popups_found[:5]:
            print(f"    hwnd={h} cls={c} rect={r}")

        # ESC로 팝업 닫고 다음 방식 시도
        pyautogui.press("escape")
        time.sleep(0.8)

    print("\n[DIAG] 완료. captures/diag/ 확인.")


if __name__ == "__main__":
    main()
