"""
Phase 1.1: 카카오톡 창 감지 + 영역 캡처

카카오톡 메인 창(방 리스트가 있는 창)을 찾아서 위치/크기를 얻고,
방 리스트 영역을 캡처한다.

하네스 원칙:
- 모든 에러는 숨기지 말고 그대로 보고 (예외 raise)
- 재현 가능한 결과: 같은 창이면 같은 좌표
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pygetwindow as gw
import pyautogui
from PIL import Image

# 카카오톡 메인 창 타이틀 (한국어 Windows 기준)
KAKAOTALK_TITLE = "카카오톡"

# 방 리스트 영역 비율 (창 크기 대비)
# 카카오톡 PC 기본 레이아웃 기준:
# - 좌측 탭바: 약 60px
# - 방 리스트: 탭바 우측 ~ 창 우측 끝
# - 상단 검색바: 약 100px
# - 하단: 창 바닥까지
ROOM_LIST_LEFT_RATIO = 0.12    # 창 좌측에서 12% 지점부터 (탭바 제외)
ROOM_LIST_TOP_RATIO = 0.11     # 창 상단에서 11% 지점부터 (검색바 제외)
ROOM_LIST_RIGHT_RATIO = 1.0    # 창 우측 끝까지
ROOM_LIST_BOTTOM_RATIO = 0.97  # 창 하단 97% 지점까지 (상태바 제외)

# 사이드바 채팅 탭 아이콘 위치 (고정 픽셀 — 사이드바는 창 크기와 무관하게 고정)
# 2026-04 kakaotalk_full.png 분석 기준: 아이콘 간격 ~50px
CHAT_TAB_X_OFFSET = 33     # 현재 카카오톡 왼쪽 채팅 아이콘 중심
CHAT_TAB_Y_OFFSET = 115    # 현재 카카오톡 채팅 아이콘 중심


@dataclass
class KakaoWindow:
    """카카오톡 창 정보"""
    title: str
    left: int
    top: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height

    def room_list_bbox(self) -> tuple[int, int, int, int]:
        """방 리스트 영역의 절대 좌표 (left, top, right, bottom)"""
        left = self.left + int(self.width * ROOM_LIST_LEFT_RATIO)
        top = self.top + int(self.height * ROOM_LIST_TOP_RATIO)
        right = self.left + int(self.width * ROOM_LIST_RIGHT_RATIO)
        bottom = self.top + int(self.height * ROOM_LIST_BOTTOM_RATIO)
        return (left, top, right, bottom)


def find_kakaotalk_window() -> KakaoWindow:
    """
    카카오톡 메인 창을 찾는다.

    Returns:
        KakaoWindow 인스턴스

    Raises:
        RuntimeError: 창을 찾을 수 없거나 최소화된 경우
    """
    windows = gw.getWindowsWithTitle(KAKAOTALK_TITLE)
    if not windows:
        raise RuntimeError(
            f"'{KAKAOTALK_TITLE}' 창을 찾을 수 없습니다. "
            "카카오톡이 실행 중인지 확인해주세요."
        )

    # 여러 창 중 가장 큰 창을 메인 창으로 간주 (채팅방 창은 작음)
    main = max(windows, key=lambda w: w.width * w.height)

    if main.isMinimized:
        raise RuntimeError(
            "카카오톡 창이 최소화되어 있습니다. 창을 복원해주세요."
        )

    if main.width < 300 or main.height < 400:
        raise RuntimeError(
            f"카카오톡 창이 너무 작습니다: {main.width}x{main.height}. "
            "메인 창이 맞는지 확인해주세요."
        )

    return KakaoWindow(
        title=main.title,
        left=main.left,
        top=main.top,
        width=main.width,
        height=main.height,
    )


def activate_kakaotalk() -> KakaoWindow:
    """
    카카오톡 창을 활성화(포커스)하고 정보를 반환한다.
    최소화되어 있으면 복원한다.
    """
    windows = gw.getWindowsWithTitle(KAKAOTALK_TITLE)
    if not windows:
        raise RuntimeError("카카오톡이 실행 중이지 않습니다.")

    main = max(windows, key=lambda w: w.width * w.height)

    if main.isMinimized:
        main.restore()
        time.sleep(0.3)

    try:
        main.activate()
    except Exception:
        # 일부 Windows 환경에서 activate가 실패할 수 있음 → 재시도
        main.minimize()
        time.sleep(0.2)
        main.restore()
        time.sleep(0.3)

    time.sleep(0.2)
    return find_kakaotalk_window()


def switch_to_chat_tab(window: KakaoWindow) -> None:
    """
    채팅 탭 강제 진입: 사이드바의 채팅(말풍선) 아이콘을 클릭.
    기획서 지침: "스캔 실패 방지를 위해 사이드바의 채팅 아이콘을 무조건 클릭 후 시작"
    """
    x = window.left + CHAT_TAB_X_OFFSET
    y = window.top + CHAT_TAB_Y_OFFSET
    pyautogui.click(x, y)
    time.sleep(0.5)


def scroll_room_list(window: KakaoWindow, direction: int = -5) -> None:
    """
    방 리스트 영역에서 마우스 스크롤.
    direction: 음수 = 아래로, 양수 = 위로. 기본 -5 (아래로 5칸)
    """
    left, top, right, bottom = window.room_list_bbox()
    center_x = (left + right) // 2
    center_y = (top + bottom) // 2
    pyautogui.click(center_x, center_y)
    time.sleep(0.1)
    pyautogui.scroll(direction, x=center_x, y=center_y)
    time.sleep(0.5)


def scroll_room_list_to_top(window: KakaoWindow) -> None:
    """방 리스트를 맨 위로 스크롤"""
    for _ in range(30):
        scroll_room_list(window, direction=10)
    time.sleep(0.3)


def capture_full_window(window: KakaoWindow, save_path: Path) -> Path:
    """카카오톡 창 전체를 스크린샷으로 저장"""
    save_path.parent.mkdir(parents=True, exist_ok=True)
    screenshot = pyautogui.screenshot(
        region=(window.left, window.top, window.width, window.height)
    )
    screenshot.save(save_path)
    return save_path


def capture_room_list(window: KakaoWindow, save_path: Path) -> Path:
    """방 리스트 영역만 캡처"""
    save_path.parent.mkdir(parents=True, exist_ok=True)
    left, top, right, bottom = window.room_list_bbox()
    width = right - left
    height = bottom - top
    screenshot = pyautogui.screenshot(region=(left, top, width, height))
    screenshot.save(save_path)
    return save_path


if __name__ == "__main__":
    # 스탠드얼론 테스트: 카톡 창 감지 → 전체 창 + 방 리스트 캡처
    import sys

    print("[1/3] 카카오톡 창 감지 중...")
    try:
        window = activate_kakaotalk()
    except RuntimeError as e:
        print(f"[ERROR] {e}")
        sys.exit(1)

    print(f"      title: {window.title}")
    print(f"      pos:   ({window.left}, {window.top})")
    print(f"      size:  {window.width} x {window.height}")

    captures_dir = Path(__file__).parent.parent / "captures"

    print("[2/3] 전체 창 캡처 중...")
    full_path = capture_full_window(window, captures_dir / "kakaotalk_full.png")
    print(f"      → {full_path}")

    print("[3/3] 방 리스트 영역 캡처 중...")
    bbox = window.room_list_bbox()
    print(f"      bbox: {bbox}")
    room_path = capture_room_list(window, captures_dir / "kakaotalk_rooms.png")
    print(f"      → {room_path}")

    print("\n[OK] 캡처 완료. 다음 두 파일을 열어서 확인해주세요:")
    print(f"  1. {full_path}  (전체 창)")
    print(f"  2. {room_path}  (방 리스트 영역만 - 이게 정확히 방 목록만 잡혔는지 확인 필요)")
