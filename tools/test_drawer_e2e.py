"""
드로어 전체 파이프라인 E2E 테스트 (모니터 없이 단독).

1. 카톡 첫 방 더블클릭으로 분리창 열기
2. drawer_handler.open_drawer 호출
3. 성공 시 download_n_from_drawer("photo", 3) 로 사진 3장 시도
4. 서랍 닫기

실제 모니터의 사진 파이프라인과 동일한 호출 순서. 실패 지점 즉시 판명.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

# UTF-8 stdout (이모지 안전)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import pyautogui
import win32gui
import win32con

pyautogui.FAILSAFE = False


def find_chat_separator(min_height: int = 500):
    """열린 분리창 찾기."""
    results = []
    def cb(h, _):
        if not win32gui.IsWindowVisible(h):
            return
        t = win32gui.GetWindowText(h)
        if not t or t == "카카오톡":
            return
        cls = win32gui.GetClassName(h) or ""
        if not cls.startswith("EVA_"):
            return
        r = win32gui.GetWindowRect(h)
        w, hh = r[2]-r[0], r[3]-r[1]
        if 300 <= w <= 900 and min_height <= hh <= 1000:
            results.append((h, t, r))
    win32gui.EnumWindows(cb, None)
    return results[0] if results else None


def ensure_separator():
    """분리창 있으면 그대로, 없으면 첫 방 더블클릭으로 생성."""
    sep = find_chat_separator()
    if sep:
        return sep
    # 카톡 메인 복원 + 첫 방 더블클릭
    kh = None
    def cb(h, _):
        nonlocal kh
        if win32gui.IsWindowVisible(h) and win32gui.GetWindowText(h) == "카카오톡":
            kh = h
    win32gui.EnumWindows(cb, None)
    if not kh:
        print("  카톡 메인 없음"); return None
    win32gui.ShowWindow(kh, win32con.SW_RESTORE)
    time.sleep(0.5)
    try:
        win32gui.SetForegroundWindow(kh)
    except Exception:
        pass
    time.sleep(0.5)
    # 첫 방 (y=150 영역) 더블클릭
    pyautogui.doubleClick(450, 150)
    time.sleep(2.0)
    return find_chat_separator()


def main():
    print("[E2E] 드로어 파이프라인 테스트\n")

    # 1. 분리창 준비
    sep = ensure_separator()
    if not sep:
        print("[FAIL] 분리창 생성 실패")
        sys.exit(1)
    hwnd, title, rect = sep
    print(f"[분리창] hwnd={hwnd} title={title!r} rect={rect}")

    # 2. open_drawer 호출
    print(f"\n[1] open_drawer 호출")
    from core.drawer_handler import open_drawer
    drawer_hwnd = open_drawer(hwnd)
    if not drawer_hwnd:
        print("[FAIL] open_drawer 실패")
        sys.exit(2)
    print(f"[SUCCESS] drawer_hwnd={drawer_hwnd}")

    # 3. 사진 3장 다운로드 시도
    print(f"\n[2] 사진 3장 다운로드")
    from core.drawer_layout_auto import download_n_from_drawer
    files = download_n_from_drawer("photo", 3)
    print(f"[RESULT] {len(files)}장 다운로드:")
    for f in files:
        print(f"  - {f.name}")

    # 4. ESC 2회로 정리
    print(f"\n[3] 서랍 닫기")
    pyautogui.press("escape")
    time.sleep(0.5)
    pyautogui.press("escape")
    time.sleep(0.5)

    print(f"\n[E2E] 완료: {'성공' if files else '실패'}")


if __name__ == "__main__":
    main()
