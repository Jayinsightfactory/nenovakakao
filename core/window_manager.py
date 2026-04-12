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


def cleanup_popups():
    """
    잔여 팝업/서랍/다이얼로그 창을 모두 닫는다.
    카톡 메인, 카카오워크 메인은 건드리지 않음.
    """
    all_wins = gw.getAllWindows()
    closed = []

    for w in all_wins:
        if not w.title or not w.visible:
            continue
        # 메인 창은 보호
        if w.title == KAKAOTALK_TITLE or w.title == KAKAOWORK_TITLE:
            continue
        # 키워드 매칭
        for kw in POPUP_KEYWORDS:
            if kw in w.title:
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


def focus_kakaotalk():
    """
    카톡 메인 창 활성화 + 채팅탭 전환.
    Returns:
        KakaoWindow 인스턴스
    """
    window = activate_kakaotalk()
    switch_to_chat_tab(window)
    time.sleep(0.3)
    return window


def focus_kakaowork():
    """
    카카오워크 앱 활성화.
    Returns:
        pygetwindow.Win32Window
    """
    windows = gw.getWindowsWithTitle(KAKAOWORK_TITLE)
    if not windows:
        raise RuntimeError("카카오워크 앱이 실행 중이지 않습니다.")

    main = max(windows, key=lambda w: w.width * w.height)
    if main.isMinimized:
        main.restore()
        time.sleep(0.3)

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
