"""
정지 버튼 미리보기 — 마우스/카톡 안 건드림.

실행하면 우상단에 [🛑 즉시 정지] 창이 뜨고, 메인 스레드는 1초마다 카운트만 함.
버튼을 누르거나 창 X 를 닫으면 즉시 종료.

실제 자동화 (verify_room_mapping_v2 등) 전에 이 도구로 정지 버튼이
정상 작동하는지 한 번 확인 권장.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.stop_button import (  # noqa: E402
    start_stop_button,
    stop_button_close,
    check_stop,
    set_status,
    StopRequested,
)


def main() -> int:
    print("정지 버튼 미리보기 시작.")
    print("우상단 빨간 [🛑 즉시 정지] 누르거나 창 X 누르면 종료됩니다.")
    print("이 도구는 마우스/키보드 자동화를 하지 않습니다.")
    start_stop_button()

    try:
        for i in range(1, 121):  # 최대 2분
            check_stop()
            set_status(f"카운트 {i}/120 — 마우스 키보드 안 건드림")
            print(f"  카운트 {i} (정지하려면 우상단 버튼)", flush=True)
            time.sleep(1.0)
        print("타임아웃 — 정지 버튼 없이 자연 종료")
    except StopRequested as e:
        print(f"\n🛑 정지 요청 받음: {e}")
        print("✅ 정지 버튼 정상 작동 확인. 이제 실제 자동화 도구도 같은 방식으로 멈출 수 있습니다.")
        return 0
    finally:
        stop_button_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
