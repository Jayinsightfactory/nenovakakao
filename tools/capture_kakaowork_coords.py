"""
카카오워크 앱 자동화용 좌표 캡처 도구.

사용법:
  PYTHON main.py rename-via-app 좌표가 안 맞을 때 실행 →
  안내에 따라 마우스 호버 → 좌표 자동 저장.

순서:
  1) 첫 번째 채팅방 (좌측 패널 가장 위 방)
  2) 채팅창 우상단 ⚙️ 톱니바퀴
  3) 채팅방 설정 패널의 ✏️ 볼펜 (톱니바퀴 클릭한 후 패널 펼쳐진 상태에서)

저장 위치:
  data/kakaowork_app_coords.json
"""
from __future__ import annotations
import json
import time
from pathlib import Path

import pyautogui

DATA_DIR = Path(__file__).parent.parent / "data"
COORDS_FILE = DATA_DIR / "kakaowork_app_coords.json"


def capture(label: str, wait_sec: int = 5) -> tuple[int, int]:
    print(f"\n[{label}] 위에 마우스 올려주세요. {wait_sec}초 후 캡처...")
    for i in range(wait_sec, 0, -1):
        print(f"  {i}...", end="\r", flush=True)
        time.sleep(1)
    pos = pyautogui.position()
    print(f"  → 캡처: ({pos.x}, {pos.y})            ")
    return (int(pos.x), int(pos.y))


def main():
    print("=" * 60)
    print("카카오워크 앱 자동화 좌표 캡처")
    print("=" * 60)
    print()
    print("준비:")
    print("  - 워크 앱이 평소 사용 위치/크기로 열려있어야 합니다")
    print("  - 좌측 패널에 채팅방 리스트가 보여야 합니다")
    print()
    input("준비됐으면 [Enter] 누르세요...")

    coords = {}

    # 1) 첫 방 (좌측 패널 가장 위)
    coords["first_room"] = capture("첫 번째 채팅방 (좌측 패널 가장 위)")

    print("\n다음: ⚙️ 톱니바퀴 좌표 캡처")
    print("  → 먼저 그 첫 방을 직접 클릭해서 채팅창을 여세요")
    input("  → 채팅창 열렸으면 [Enter]...")
    coords["gear"] = capture("⚙️ 톱니바퀴 (채팅창 우상단)")

    print("\n다음: ✏️ 볼펜 좌표 캡처")
    print("  → 먼저 ⚙️ 톱니바퀴를 직접 클릭해서 채팅방 설정 패널을 여세요")
    input("  → 설정 패널 펼쳐졌으면 [Enter]...")
    coords["pencil"] = capture("✏️ 볼펜 (채팅방 이름 옆)")

    # 워크 앱 창 정보도 함께 (좌표 정합성 검증용)
    try:
        import pygetwindow as gw
        wins = [w for w in gw.getAllWindows() if "카카오워크" in (w.title or "")]
        if wins:
            w = wins[0]
            coords["window"] = {"left": w.left, "top": w.top, "width": w.width, "height": w.height}
    except Exception:
        pass

    # 저장
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    COORDS_FILE.write_text(
        json.dumps(coords, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n=== 저장 완료: {COORDS_FILE} ===")
    print(json.dumps(coords, ensure_ascii=False, indent=2))

    # 환경변수 형태로도 안내
    if "window" in coords:
        w = coords["window"]
        gear = coords["gear"]
        pencil = coords["pencil"]
        first = coords["first_room"]
        print("\n환경변수 또는 코드 직접 적용:")
        print(f"  FIRST_ROOM_X_OFFSET = {first[0] - w['left']}")
        print(f"  FIRST_ROOM_Y_OFFSET = {first[1] - w['top']}")
        print(f"  NENOVA_GEAR_FROM_RIGHT = {w['left'] + w['width'] - gear[0]}")
        print(f"  NENOVA_GEAR_FROM_TOP = {gear[1] - w['top']}")
        print(f"  NENOVA_PENCIL_FROM_RIGHT = {w['left'] + w['width'] - pencil[0]}")
        print(f"  NENOVA_PENCIL_FROM_TOP = {pencil[1] - w['top']}")


if __name__ == "__main__":
    main()
