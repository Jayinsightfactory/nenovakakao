"""
전체 방 리스트 캡처 전용 스크립트 (OCR 없음)
스크롤하면서 페이지별 스크린샷만 저장한다.
저장된 이미지는 Claude Code가 직접 분석한다.
"""
import time
from pathlib import Path

from core.window_detector import (
    activate_kakaotalk,
    switch_to_chat_tab,
    capture_room_list,
    scroll_room_list,
    scroll_room_list_to_top,
)

MAX_PAGES = 8
CAPTURES_DIR = Path(__file__).parent / "captures" / "pages"


def main():
    CAPTURES_DIR.mkdir(parents=True, exist_ok=True)

    print("[1/3] 카카오톡 활성화 + 채팅 탭 전환...")
    window = activate_kakaotalk()
    switch_to_chat_tab(window)
    time.sleep(0.5)

    print("[2/3] 맨 위로 스크롤...")
    scroll_room_list_to_top(window)
    time.sleep(0.5)

    print(f"[3/3] 페이지별 캡처 시작 (최대 {MAX_PAGES}페이지)...")
    for page in range(MAX_PAGES):
        path = capture_room_list(window, CAPTURES_DIR / f"page_{page:02d}.png")
        print(f"       page_{page:02d}.png 저장 완료")

        # 스크롤 다운 (큰 폭으로)
        scroll_room_list(window, direction=-20)
        time.sleep(0.5)

    print(f"\n[OK] {MAX_PAGES}페이지 캡처 완료!")
    print(f"     저장 위치: {CAPTURES_DIR.resolve()}")
    print("     이제 Claude Code에게 분석을 요청하세요.")


if __name__ == "__main__":
    main()
