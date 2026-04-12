# -*- coding: utf-8 -*-
"""
안전한 카카오톡 방 수집기 v2 (탐색 결과 기반 재설계)

검증된 원리만 사용:
  - 방 열기: 더블클릭 (Ctrl+F 불가 확인됨)
  - 방 확인: win32gui.GetWindowText → 방 이름 반환
  - 행 높이: 70px, 첫 방 Y = 카카오톡 상단 + 180
  - 활성화: Alt트릭 + SetForegroundWindow
  - 복구: ESC 3회 + 채팅탭 클릭 = 100% 복구
  - 저장: Ctrl+S → Enter → Enter → 새 파일 확인
  - 숨김 감지: IsWindowVisible → ShowWindow 복원

사용법:
  PYTHONIOENCODING=utf-8 python safe_collect.py
  PYTHONIOENCODING=utf-8 python safe_collect.py "네노바&선율"
"""
from __future__ import annotations

import ctypes
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import pyautogui
import win32gui
import win32con
from PIL import Image

sys.path.insert(0, "C:/Users/USER/nenova_agent")
from core.vision_guard import VisionGuard, compare_images, safe_screenshot
from core.message_extractor import (
    read_and_process_saved_file,
    KAKAO_SAVE_DIR,
)

pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0.05

# ---------------------------------------------------------------------------
# 검증된 상수 (탐색 결과)
# ---------------------------------------------------------------------------
ROW_HEIGHT = 70           # 방 리스트 행 높이 (px)
FIRST_ROW_OFFSET = 180    # 카카오톡 상단에서 첫 방까지 거리
CHAT_TAB_X = 27           # 채팅탭 아이콘 X (창 기준)
CHAT_TAB_Y = 115          # 채팅탭 아이콘 Y (창 기준)
ROOM_CLICK_X = 250        # 방 이름 클릭 X (창 기준)
MAX_VISIBLE_ROWS = 11     # 한 화면에 보이는 최대 방 수
SCROLL_AMOUNT = -5        # 스크롤 1회당 이동량
GUARD_DIR = Path("C:/Users/USER/nenova_agent/data/guard")

# 수집 대상
ROOMS_TO_COLLECT = ["네노바&선율", "발번호및 입고수량확인방"]


# ---------------------------------------------------------------------------
# 카카오톡 제어 (검증된 방법만)
# ---------------------------------------------------------------------------

def find_kakao() -> tuple[int, tuple[int, int, int, int]]:
    """카카오톡 메인 창 hwnd + rect. 숨김 상태도 찾음."""
    results = []
    def cb(hwnd, _):
        title = win32gui.GetWindowText(hwnd)
        cls = win32gui.GetClassName(hwnd)
        if "카카오톡" in title and cls == "EVA_Window_Dblclk":
            results.append(hwnd)
    win32gui.EnumWindows(cb, None)
    if not results:
        raise RuntimeError("카카오톡이 실행 중이지 않습니다!")
    hwnd = results[0]
    if not win32gui.IsWindowVisible(hwnd):
        activate(hwnd)
    rect = win32gui.GetWindowRect(hwnd)
    return hwnd, rect


def activate(hwnd: int) -> bool:
    """Alt트릭 + SetForegroundWindow (탐색에서 가장 안정적 확인)."""
    for attempt in range(5):
        win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        time.sleep(0.1)
        ctypes.windll.user32.keybd_event(0x12, 0, 0, 0)
        ctypes.windll.user32.keybd_event(0x12, 0, 2, 0)
        ctypes.windll.user32.SetForegroundWindow(hwnd)
        time.sleep(0.3)
        if win32gui.GetForegroundWindow() == hwnd:
            return True
        time.sleep(0.5)
    return False


def reset_to_chatlist(hwnd: int, rect: tuple) -> bool:
    """ESC 3회 + 채팅탭 클릭 = 100% 복구 (탐색에서 검증)."""
    for _ in range(3):
        pyautogui.press("escape")
        time.sleep(0.2)
    if not activate(hwnd):
        return False
    time.sleep(0.3)
    pyautogui.click(rect[0] + CHAT_TAB_X, rect[1] + CHAT_TAB_Y)
    time.sleep(0.5)
    return win32gui.GetForegroundWindow() == hwnd


def scroll_to_top(rect: tuple):
    """방 리스트 맨 위로 스크롤."""
    cx = rect[0] + ROOM_CLICK_X
    cy = rect[1] + 500
    for _ in range(30):
        pyautogui.scroll(10, x=cx, y=cy)
        time.sleep(0.05)
    time.sleep(0.3)


# ---------------------------------------------------------------------------
# 방 찾기 (더블클릭 + GetWindowText 검증)
# ---------------------------------------------------------------------------

def find_and_open_room(hwnd: int, rect: tuple, target: str,
                       guard: VisionGuard) -> int | None:
    """
    방 리스트에서 target 이름의 방을 찾아 더블클릭으로 연다.

    방법: 보이는 각 행을 더블클릭 → GetWindowText로 방 이름 확인 →
          일치하면 열린 채로 반환, 불일치면 닫고 다음 행.
          한 화면 다 탐색하면 스크롤 후 반복.

    Returns: 열린 방의 hwnd, 실패 시 None
    """
    guard._log(f"  방 찾기: '{target}'")

    scroll_to_top(rect)
    scrolled_pages = 0

    for page in range(5):  # 최대 5페이지 탐색
        guard._log(f"  페이지 {page} 탐색")

        for row in range(MAX_VISIBLE_ROWS):
            click_y = rect[1] + FIRST_ROW_OFFSET + row * ROW_HEIGHT + 35
            click_x = rect[0] + ROOM_CLICK_X

            # 화면 밖이면 다음 페이지
            if click_y > rect[3] - 50:
                break

            # 카카오톡이 포그라운드인지 확인
            if win32gui.GetForegroundWindow() != hwnd:
                if not activate(hwnd):
                    guard._log(f"    [!] 카카오톡 활성화 실패")
                    return None
                time.sleep(0.3)

            # 더블클릭
            pyautogui.doubleClick(click_x, click_y)
            time.sleep(1.0)

            fg = win32gui.GetForegroundWindow()

            # 카카오톡 메인이 그대로면 → 빈 영역 클릭 (방 없음)
            if fg == hwnd:
                continue

            # 열린 창 제목 확인
            fg_title = win32gui.GetWindowText(fg)
            fg_class = win32gui.GetClassName(fg)

            # 카카오톡 채팅방이 아닌 다른 창이 열린 경우 (Chrome 등)
            if fg_class != "EVA_Window_Dblclk":
                guard._log(f"    row {row}: 비카톡 창 '{fg_title}' — 스킵")
                # 카카오톡으로 복귀
                activate(hwnd)
                time.sleep(0.3)
                continue

            guard._log(f"    row {row}: '{fg_title}'")

            # 이름 매칭 (부분 매칭 허용 — OCR/인코딩 차이 대비)
            if _name_matches(target, fg_title):
                guard._log(f"    -> 일치! '{target}' == '{fg_title}'")
                return fg

            # 불일치 — 닫고 다음
            pyautogui.press("escape")
            time.sleep(0.3)
            if not activate(hwnd):
                return None
            time.sleep(0.3)

        # 스크롤 다운 (다음 페이지)
        cx = rect[0] + ROOM_CLICK_X
        cy = rect[1] + 500
        for _ in range(5):
            pyautogui.scroll(SCROLL_AMOUNT, x=cx, y=cy)
            time.sleep(0.1)
        time.sleep(0.5)
        scrolled_pages += 1

    guard._log(f"  방 '{target}' 찾지 못함 ({scrolled_pages}페이지 탐색)")
    return None


def _name_matches(target: str, actual: str) -> bool:
    """방 이름 매칭. 완전일치 또는 핵심 부분 포함."""
    if not actual:
        return False
    # 완전 일치
    if target == actual:
        return True
    # 부분 포함 (양방향)
    if target in actual or actual in target:
        return True
    # 공백/특수문자 무시 비교
    t_clean = target.replace(" ", "").replace("&", "").lower()
    a_clean = actual.replace(" ", "").replace("&", "").lower()
    if t_clean in a_clean or a_clean in t_clean:
        return True
    # 첫 4글자 매칭 (OCR 오류 대비)
    if len(target) >= 4 and len(actual) >= 4:
        if target[:4] == actual[:4]:
            return True
    # 2글자 이상 연속 일치 (인코딩 차이 대비: 빌번호 vs 발번호)
    for i in range(len(target) - 2):
        chunk = target[i:i+3]
        if chunk in actual:
            return True
    return False


# ---------------------------------------------------------------------------
# 안전한 Ctrl+S 저장
# ---------------------------------------------------------------------------

def safe_save_chat(room_hwnd: int, room_title: str,
                   guard: VisionGuard) -> Path | None:
    """
    열린 채팅방에서 Ctrl+S로 저장.
    파일 생성을 검증하고 경로 반환.
    """
    guard._log(f"  저장: '{room_title}'")

    # 저장 전 파일 목록
    before_files = set()
    if KAKAO_SAVE_DIR.exists():
        before_files = set(str(p) for p in KAKAO_SAVE_DIR.rglob("*.txt"))

    # 방 창이 포그라운드인지 확인
    if win32gui.GetForegroundWindow() != room_hwnd:
        ctypes.windll.user32.keybd_event(0x12, 0, 0, 0)
        ctypes.windll.user32.keybd_event(0x12, 0, 2, 0)
        ctypes.windll.user32.SetForegroundWindow(room_hwnd)
        time.sleep(0.5)

    # Ctrl+S
    pyautogui.hotkey("ctrl", "s")
    time.sleep(2.0)

    # 저장 다이얼로그 확인 (클래스 #32770)
    fg = win32gui.GetForegroundWindow()
    fg_title = win32gui.GetWindowText(fg)
    fg_class = win32gui.GetClassName(fg)
    guard._log(f"    Ctrl+S 후: '{fg_title}' ({fg_class})")

    if "저장" not in fg_title and fg_class != "#32770":
        guard._log(f"    [!] 저장 다이얼로그 미확인 — Enter 시도")

    # Enter (저장 확인)
    pyautogui.press("enter")
    time.sleep(2.0)

    # Enter (완료 팝업 닫기)
    pyautogui.press("enter")
    time.sleep(1.0)

    # 새 파일 확인
    if KAKAO_SAVE_DIR.exists():
        after_files = set(str(p) for p in KAKAO_SAVE_DIR.rglob("*.txt"))
        new_files = after_files - before_files
        if new_files:
            saved = Path(max(new_files, key=lambda f: Path(f).stat().st_mtime))
            size = saved.stat().st_size
            guard._log(f"    저장됨: {saved.name} ({size:,}B)")
            if size > 10:
                return saved
            guard._log(f"    [!] 파일 너무 작음")

    guard._log(f"    [!] 새 파일 없음")
    return None


# ---------------------------------------------------------------------------
# 한 방 수집 전체 플로우
# ---------------------------------------------------------------------------

def collect_one_room(hwnd: int, rect: tuple, target: str,
                     guard: VisionGuard) -> dict | None:
    """
    한 방을 안전하게 수집한다.

    플로우:
      1. 채팅목록 복구
      2. 맨 위 스크롤
      3. 행별 더블클릭 → GetWindowText로 방 이름 확인
      4. 일치하면 Ctrl+S 저장
      5. 파일 읽기 + 처리
      6. 방 닫기 + 채팅목록 복구

    Returns: 수집 결과 dict 또는 None
    """
    guard._log(f"\n{'='*50}")
    guard._log(f"수집: '{target}'")
    guard._log(f"{'='*50}")

    # 1. 깨끗한 상태 확보
    if not reset_to_chatlist(hwnd, rect):
        guard._log(f"[!] 채팅목록 복구 실패")
        return None

    # 2. 방 찾기 + 열기
    room_hwnd = find_and_open_room(hwnd, rect, target, guard)
    if room_hwnd is None:
        guard._log(f"[!] '{target}' 찾기 실패")
        reset_to_chatlist(hwnd, rect)
        return None

    room_title = win32gui.GetWindowText(room_hwnd)

    # 3. 캡처 (열린 방 상태)
    room_rect = win32gui.GetWindowRect(room_hwnd)
    rw, rh = room_rect[2] - room_rect[0], room_rect[3] - room_rect[1]
    cap_img = safe_screenshot((room_rect[0], room_rect[1], rw, rh))
    cap_path = GUARD_DIR / f"room_{target[:10]}_{datetime.now().strftime('%H%M%S')}.png"
    cap_img.save(cap_path)
    guard._log(f"  방 캡처: {cap_path.name}")

    # 4. Ctrl+S 저장
    saved_path = safe_save_chat(room_hwnd, room_title, guard)

    # 5. 방 닫기
    pyautogui.press("escape")
    time.sleep(0.5)
    reset_to_chatlist(hwnd, rect)

    if saved_path is None:
        guard._log(f"[!] '{target}' 저장 실패")
        return None

    # 6. 파일 읽기 + 처리
    result = read_and_process_saved_file(saved_path)
    if result:
        guard._log(f"수집 완료: '{result['room_name']}' / 델타 {len(result['delta'])}자")
    else:
        guard._log(f"변경 없음 (이전과 동일)")

    return result


# ---------------------------------------------------------------------------
# 메인
# ---------------------------------------------------------------------------

def main():
    print("=" * 60, flush=True)
    print("  안전 수집기 v2 (더블클릭 기반)", flush=True)
    print(f"  {datetime.now().isoformat()}", flush=True)
    print("=" * 60, flush=True)

    # 대상 결정
    if len(sys.argv) > 1:
        rooms = [sys.argv[1]]
    else:
        rooms = ROOMS_TO_COLLECT.copy()

    print(f"수집 대상: {rooms}", flush=True)

    # 카카오톡 찾기
    hwnd, rect = find_kakao()
    print(f"카카오톡: hwnd={hwnd}, rect={rect}", flush=True)

    # VisionGuard 초기화
    GUARD_DIR.mkdir(parents=True, exist_ok=True)
    guard = VisionGuard(capture_dir=GUARD_DIR)

    # 초기 활성화
    if not activate(hwnd):
        print("[FATAL] 카카오톡 활성화 불가!", flush=True)
        sys.exit(1)

    # 수집
    collected = []
    for room in rooms:
        result = collect_one_room(hwnd, rect, room, guard)
        collected.append({"room": room, "result": result})
        time.sleep(1.0)

    # 요약
    print("\n" + "=" * 60, flush=True)
    print("수집 결과:", flush=True)
    for c in collected:
        room = c["room"]
        if c["result"]:
            delta_len = len(c["result"]["delta"])
            print(f"  OK  {room}: {delta_len}자 수집됨", flush=True)
        else:
            print(f"  FAIL {room}: 수집 실패", flush=True)

    guard.save_run_log()
    print("=" * 60, flush=True)


if __name__ == "__main__":
    main()
