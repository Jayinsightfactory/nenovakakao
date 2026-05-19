"""
core/kakao_win32 자가 테스트.

단계별 — 각 단계 결과 확인 후 다음 단계 진행. read-only 부터:

  Step 1 [READ-ONLY]: is_kakaotalk_running + list_chat_windows
  Step 2 [READ-ONLY]: 현재 떠있는 분리창 중 1개 선택해서 hwnd + child windows 출력
  Step 3 [WRITE]: 그 분리창에서 read_chat_messages → 클립보드 텍스트 추출

사용:
  python tools/test_kakao_win32.py             # Step 1 만 (안전)
  python tools/test_kakao_win32.py --step2     # Step 1 + 2
  python tools/test_kakao_win32.py --step3     # Step 1+2+3 (Ctrl+A/C 실행)
  python tools/test_kakao_win32.py --step4 <방이름>  # Step 4: search_and_open_room
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import kakao_win32 as kw  # noqa: E402
import win32gui  # noqa: E402


def step1() -> bool:
    print("=" * 60)
    print("Step 1: is_kakaotalk_running + list_chat_windows")
    print("=" * 60)
    state = kw.is_kakaotalk_running()
    print(f"  running={state['running']} hwnd={state['hwnd']} pid={state['pid']}")
    if not state["running"]:
        print("  ❌ 카톡 미실행. 종료")
        return False

    windows = kw.list_chat_windows()
    print(f"\n  현재 열려있는 카톡 분리창: {len(windows)}개")
    for i, w in enumerate(windows, 1):
        print(f"    {i:2d}. hwnd={w['hwnd']} title={w['title']!r}")

    if not windows:
        print("\n  ⚠️ 분리창 0개. Step 2/3 진행하려면 카톡에서 채팅방 1개 더블클릭으로 띄우세요.")
    return True


def step2(room_name: str | None = None) -> str | None:
    print()
    print("=" * 60)
    print("Step 2: 분리창 1개 선택 + child window 트리")
    print("=" * 60)
    windows = kw.list_chat_windows()
    if not windows:
        print("  분리창 없음. Step 2 스킵")
        return None
    target = windows[0]
    if room_name:
        for w in windows:
            if w["title"] == room_name:
                target = w
                break
    print(f"  선택: {target['title']!r} (hwnd={target['hwnd']})")

    # child window 트리 (depth 3 까지)
    print("\n  Child windows:")
    def dump(parent, depth=0):
        if depth > 3:
            return
        kids: list[int] = []
        def cb(h, _):
            kids.append(h)
            return True
        try:
            win32gui.EnumChildWindows(parent, cb, None)
        except Exception:
            return
        for k in kids:
            cls = win32gui.GetClassName(k) or "?"
            title = win32gui.GetWindowText(k) or ""
            print(f"    {'  ' * depth}└─ {cls!r:30s} title={title[:40]!r}")
            dump(k, depth + 1)
    dump(target["hwnd"])

    # ListControl + RICHEDIT 확인
    list_h = kw.find_child_window_recursive(target["hwnd"], kw.KAKAO_LIST_CONTROL_CLASS)
    edit_h = kw.find_child_window_recursive(target["hwnd"], kw.KAKAO_EDIT_CLASS)
    print(f"\n  EVA_VH_ListControl_Dblclk: hwnd={list_h}")
    print(f"  RICHEDIT50W:               hwnd={edit_h}")
    return target["title"]


def step3(room_name: str) -> None:
    print()
    print("=" * 60)
    print(f"Step 3: read_chat_messages('{room_name}') — Ctrl+A + Ctrl+C")
    print("⚠️ 카톡 분리창에 잠시 포커스 옮김. 클립보드 덮어쓰기.")
    print("=" * 60)
    res = kw.read_chat_messages(room_name)
    if not res["success"]:
        print(f"  ❌ 실패: {res.get('error')}")
        return
    raw = res["raw_text"]
    print(f"  ✅ 클립보드 텍스트: {len(raw)}자")
    print()
    print("  --- 처음 800자 ---")
    print(raw[:800])
    print("  --- 끝 ---")


def step4(room_name: str) -> None:
    print()
    print("=" * 60)
    print(f"Step 4: search_and_open_room('{room_name}')")
    print("  ⚠️ 카톡 메인 포커스 + Ctrl+F (채팅탭 검색 활성)")
    print("  ⚠️ WM_CHAR 글자별 송신 + Enter → 분리창 띄움")
    print("  ⚠️ 부작용 (친구추가 팝업 등) 발생 시 즉시 ESC ESC")
    print("=" * 60)
    res = kw.search_and_open_room(room_name)
    print(f"\n  결과: success={res['success']}")
    if res["success"]:
        print(f"    message: {res.get('message')}")
        print(f"    hwnd: {res.get('hwnd')}")
    else:
        print(f"    error: {res.get('error')}")
    print("\n  현재 분리창 목록:")
    for w in kw.list_chat_windows():
        print(f"    • hwnd={w['hwnd']} title={w['title']!r}")


def main() -> int:
    args = sys.argv[1:]
    if not step1():
        return 1
    if "--step2" in args or "--step3" in args:
        room = step2()
        if "--step3" in args and room:
            step3(room)
    if "--step4" in args:
        idx = args.index("--step4")
        if idx + 1 >= len(args):
            print("\n--step4 다음에 방 이름 필요. 예: --step4 주광 담당")
            return 1
        room_name = " ".join(args[idx + 1:])
        step4(room_name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
