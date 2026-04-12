"""
Phase 1.5: 카카오톡 서랍 사진 다운로드 자동화

채팅방에서 Ctrl+K → 서랍 열기 → 사진/동영상 탭 → 사진 다운로드.

검증된 좌표 (2026-04-10, captures/drawer.png + drawer_photos.png 기준):
  서랍 창: 별도 윈도우 "채팅방 서랍", 약 840x600
  사진/동영상 탭: drawer.left + 120, drawer.top + 190
  첫번째 사진:   drawer.left + 150, drawer.top + 280
  다운로드 버튼:  drawer.left + width - 25, drawer.top + height - 25
  그리드: 3열, 열간격 ~140px, 행간격 ~140px
"""
from __future__ import annotations

import time
from pathlib import Path

import pyautogui
import pygetwindow as gw
import win32gui

# 사진 다운로드 경로 (카카오톡 기본값)
KAKAO_DOWNLOAD_DIR = Path("C:/Users/USER/Documents/카카오톡 받은 파일")

# 서랍 창 제목 키워드
DRAWER_TITLE_KEYWORD = "서랍"

# 서랍 내부 상대 좌표 (drawer.png / drawer_photos.png 분석 기준)
PHOTO_TAB_X_OFFSET = 120       # 사진/동영상 탭
PHOTO_TAB_Y_OFFSET = 190
FIRST_PHOTO_X_OFFSET = 150     # 첫번째 사진 썸네일
FIRST_PHOTO_Y_OFFSET = 280
DOWNLOAD_BTN_X_FROM_RIGHT = 25  # 다운로드 버튼 (우하단 기준)
DOWNLOAD_BTN_Y_FROM_BOTTOM = 25

# 그리드 레이아웃 (3열, 행/열 간격)
GRID_COLS = 3
GRID_COL_SPACING = 140         # 열 간격 (px)
GRID_ROW_SPACING = 140         # 행 간격 (px)
MAX_PHOTOS = 12                # 최대 다운로드 수 (안전장치)

# 캡처 저장 경로
CAPTURES_DIR = Path(__file__).parent.parent / "captures"


def _snapshot_downloads() -> set[str]:
    """다운로드 폴더의 현재 파일 목록 스냅샷"""
    if not KAKAO_DOWNLOAD_DIR.exists():
        return set()
    return {str(p) for p in KAKAO_DOWNLOAD_DIR.rglob("*") if p.is_file()}


def _capture_verify(label: str) -> Path:
    """
    동작 전 화면 캡처 → 저장. (캡처→확인→동작 룰)
    Returns: 캡처 파일 경로
    """
    CAPTURES_DIR.mkdir(parents=True, exist_ok=True)
    save_path = CAPTURES_DIR / f"drawer_verify_{label}.png"
    screenshot = pyautogui.screenshot()
    screenshot.save(str(save_path))
    return save_path


def _get_thumbnail_position(drawer, index: int) -> tuple[int, int]:
    """
    그리드 인덱스(0-based) → 절대 좌표 계산.
    행 = index // 3, 열 = index % 3
    """
    row = index // GRID_COLS
    col = index % GRID_COLS
    x = drawer.left + FIRST_PHOTO_X_OFFSET + (col * GRID_COL_SPACING)
    y = drawer.top + FIRST_PHOTO_Y_OFFSET + (row * GRID_ROW_SPACING)
    return x, y


def find_drawer_window():
    """
    서랍 창을 찾는다.
    pygetwindow 타이틀 검색 + 크기 필터 (width>500, height>300).

    Returns:
        pygetwindow.Win32Window

    Raises:
        RuntimeError: 서랍 창을 찾을 수 없을 때
    """
    windows = gw.getWindowsWithTitle(DRAWER_TITLE_KEYWORD)
    # 크기 필터: 너무 작은 창은 서랍이 아님
    candidates = [w for w in windows if w.width > 500 and w.height > 300]
    if not candidates:
        raise RuntimeError(
            f"'{DRAWER_TITLE_KEYWORD}' 창을 찾을 수 없습니다. "
            "Ctrl+K로 서랍이 열려있는지 확인해주세요."
        )
    return candidates[0]


def _activate_window(hwnd: int):
    """win32gui로 창 활성화 (포그라운드)"""
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        # 간혹 실패 → 최소화 후 복원으로 재시도
        win32gui.ShowWindow(hwnd, 6)   # SW_MINIMIZE
        time.sleep(0.2)
        win32gui.ShowWindow(hwnd, 9)   # SW_RESTORE
        time.sleep(0.3)
        win32gui.SetForegroundWindow(hwnd)
    time.sleep(0.3)


def open_drawer(chat_hwnd: int) -> object:
    """
    채팅방 창 활성화 → Ctrl+K → 서랍 창 찾기.

    Args:
        chat_hwnd: 열려있는 채팅방 창 핸들 (win32gui HWND)

    Returns:
        서랍 pygetwindow.Win32Window
    """
    _activate_window(chat_hwnd)
    time.sleep(0.3)

    pyautogui.hotkey("ctrl", "k")
    time.sleep(2.0)  # 서랍 로딩 대기

    drawer = find_drawer_window()
    _activate_window(drawer._hWnd)
    return drawer


def download_photos(drawer, max_count: int = 0) -> list[Path]:
    """
    서랍에서 여러 사진을 순회 다운로드.

    그리드 썸네일을 순서대로 클릭 → 다운로드 → ESC → 다음 썸네일.
    각 단계마다 캡처→확인 수행.

    Args:
        drawer: pygetwindow.Win32Window (서랍 창)
        max_count: 다운로드할 최대 사진 수 (0 = 가능한 모든 사진, MAX_PHOTOS까지)

    Returns:
        새로 다운로드된 파일 경로 리스트
    """
    before_all = _snapshot_downloads()
    limit = min(max_count, MAX_PHOTOS) if max_count > 0 else MAX_PHOTOS

    # 1. 사진/동영상 탭 클릭
    _capture_verify("before_photo_tab")
    tab_x = drawer.left + PHOTO_TAB_X_OFFSET
    tab_y = drawer.top + PHOTO_TAB_Y_OFFSET
    pyautogui.click(tab_x, tab_y)
    time.sleep(1.0)
    _capture_verify("after_photo_tab")

    all_new_files = []
    consecutive_empty = 0  # 연속 빈 다운로드 카운터 (종료 조건)

    for i in range(limit):
        before_one = _snapshot_downloads()

        # 2. 썸네일 클릭
        thumb_x, thumb_y = _get_thumbnail_position(drawer, i)

        # 서랍 영역 밖이면 중단
        if thumb_y > drawer.top + drawer.height - 60:
            print(f"       [DRAWER] 썸네일 {i}: 서랍 영역 밖 → 중단")
            break

        _capture_verify(f"before_thumb_{i}")
        print(f"       [DRAWER] 썸네일 {i} 클릭 ({thumb_x}, {thumb_y})")
        pyautogui.click(thumb_x, thumb_y)
        time.sleep(1.0)

        # 3. 다운로드 버튼 클릭 (우하단)
        _capture_verify(f"before_download_{i}")
        dl_x = drawer.left + drawer.width - DOWNLOAD_BTN_X_FROM_RIGHT
        dl_y = drawer.top + drawer.height - DOWNLOAD_BTN_Y_FROM_BOTTOM
        pyautogui.click(dl_x, dl_y)
        time.sleep(2.5)

        # 4. 팝업/미리보기 닫기 → 그리드로 복귀
        pyautogui.press("escape")
        time.sleep(0.5)

        # 5. 새로 생긴 파일 확인
        after_one = _snapshot_downloads()
        new_in_round = after_one - before_one
        if new_in_round:
            consecutive_empty = 0
            print(f"       [DRAWER] 썸네일 {i}: {len(new_in_round)}개 파일 다운로드")
        else:
            consecutive_empty += 1
            print(f"       [DRAWER] 썸네일 {i}: 새 파일 없음 (이미 다운로드됨?)")
            if consecutive_empty >= 3:
                print(f"       [DRAWER] 연속 3회 빈 다운로드 → 중단")
                break

        # 서랍 창 다시 활성화 (ESC 후 포커스 잃을 수 있음)
        try:
            _activate_window(drawer._hWnd)
        except Exception:
            pass
        time.sleep(0.3)

    # 전체 새 파일 집계
    after_all = _snapshot_downloads()
    all_new_files = sorted(
        [Path(f) for f in (after_all - before_all)],
        key=lambda p: p.stat().st_mtime,
    )
    _capture_verify("download_complete")
    return all_new_files


def close_drawer():
    """서랍 창 닫기 (ESC)"""
    pyautogui.press("escape")
    time.sleep(0.5)


def extract_photos_from_room(chat_hwnd: int, photo_count: int = 0) -> list[Path]:
    """
    전체 시퀀스: 서랍 열기 → 사진 다운로드 (여러 장) → 서랍 닫기.

    Args:
        chat_hwnd: 열려있는 채팅방 창 핸들
        photo_count: 다운로드할 사진 수 (0 = 가능한 모든 사진)

    Returns:
        다운로드된 파일 경로 리스트 (빈 리스트 = 사진 없음/실패)
    """
    try:
        _capture_verify("before_open_drawer")
        drawer = open_drawer(chat_hwnd)
        _capture_verify("after_open_drawer")
        files = download_photos(drawer, max_count=photo_count)
        close_drawer()
        _capture_verify("after_close_drawer")
        return files
    except RuntimeError as e:
        print(f"       [DRAWER] 서랍 열기 실패: {e}")
        # 실패 시에도 캡처 남기기
        _capture_verify("drawer_error")
        return []


if __name__ == "__main__":
    import sys

    print("[drawer_handler] 스탠드얼론 테스트")
    print("  사전조건: 카카오톡 채팅방이 열려있어야 합니다.")
    print()

    # 채팅방 창 찾기 (제목에 "-" 포함된 창 = 채팅방)
    all_wins = gw.getAllWindows()
    chat_wins = [
        w for w in all_wins
        if w.title and "카카오톡" not in w.title
        and w.width > 300 and w.height > 400
        and w.visible
    ]

    # win32gui로 현재 포그라운드 창을 채팅방으로 사용
    fg_hwnd = win32gui.GetForegroundWindow()
    title = win32gui.GetWindowText(fg_hwnd)
    print(f"  현재 포그라운드 창: '{title}'")
    print(f"  이 창에서 Ctrl+K 서랍을 열겠습니다.")
    print()

    input("  Enter를 누르면 3초 후 시작합니다...")
    time.sleep(3)

    files = extract_photos_from_room(fg_hwnd)
    if files:
        print(f"\n  [OK] {len(files)}개 파일 다운로드 완료:")
        for f in files:
            print(f"    → {f}")
    else:
        print("\n  [WARN] 다운로드된 파일 없음")
