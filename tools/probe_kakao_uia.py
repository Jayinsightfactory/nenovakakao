"""
카톡 창의 UIAutomation 트리 진단.

사용법:
  1. 카톡에서 채팅방 하나를 연 상태로 둠 (분리창이든 메인창이든)
  2. 해당 창을 클릭해서 foreground 로 만들기
  3. 5초 이내에 아래 명령 실행:

    "$PYTHON" tools/probe_kakao_uia.py

출력:
  - captures/uia_probe_<hwnd>.txt : 접근성 트리 전체 덤프
  - 콘솔: Button 요약, MenuItem 요약, ≡/서랍 후보 요약

이걸로 ≡ 버튼이 UIA 에서 어떤 접근성 이름으로 노출되는지 확인.
만약 Button 목록에 '메뉴'/'더보기' 가 없다면 카톡이 DirectUI 커스텀 렌더링을 쓰는 것.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import win32gui  # noqa: E402


def main():
    # 5초 카운트다운
    for i in range(5, 0, -1):
        print(f"  [{i}] 카톡 채팅방 창을 클릭해서 foreground 로 만들어주세요...", flush=True)
        time.sleep(1)

    hwnd = win32gui.GetForegroundWindow()
    title = win32gui.GetWindowText(hwnd)
    rect = win32gui.GetWindowRect(hwnd)
    cls = win32gui.GetClassName(hwnd)
    print(f"\n[foreground] hwnd={hwnd} title={title!r} cls={cls} rect={rect}")

    if not title:
        print("  ⚠️ 제목 없는 창입니다. 카톡 메인창이나 채팅 분리창을 선택하세요.")
        return

    from core.drawer_uia import probe, PYWINAUTO_AVAILABLE
    if not PYWINAUTO_AVAILABLE:
        print("pywinauto 사용 불가")
        return

    probe(hwnd)

    # 메뉴 후보 스캔
    try:
        from pywinauto import Application
        app = Application(backend="uia").connect(handle=hwnd, timeout=3)
        win = app.window(handle=hwnd)

        print("\n[≡/메뉴/더보기 후보 스캔]")
        for ct in ["Button", "MenuItem", "Pane", "Custom", "Image", "Hyperlink"]:
            try:
                items = win.descendants(control_type=ct)
            except Exception as e:
                print(f"  {ct}: enum 에러 {e}")
                continue
            cand = []
            for el in items:
                nm = (el.element_info.name or "")
                if not nm:
                    continue
                if any(k in nm for k in ["메뉴", "Menu", "더보기", "More", "≡"]):
                    cand.append(nm)
            if cand:
                print(f"  [{ct}] {cand[:10]}")
    except Exception as e:
        print(f"  후보 스캔 에러: {e}")


if __name__ == "__main__":
    main()
