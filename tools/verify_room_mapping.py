"""
mapping 의 23개 키 vs 카톡 실제 방 이름 1:1 매칭 검증.

각 mapping key 를 카톡 Ctrl+F 로 검색 → Enter → 분리창 열림 →
win32gui.GetWindowText 로 분리창 title 추출 → mapping key 와 비교.

결과를 data/mapping_verify_report.json 에 저장.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env", override=True)


def _get_visible_separate_windows() -> list[str]:
    """카톡 분리창 후보 (카카오톡 메인/Claude/액션로그 제외)."""
    import win32gui
    excluded = {"카카오톡", "Claude", "네노바 액션 로그 (Ctrl+C 복사 가능)", "네노바 상태", ""}
    results: list[tuple[int, str, int]] = []  # (hwnd, title, age)

    def cb(h, _):
        if not win32gui.IsWindowVisible(h):
            return
        t = win32gui.GetWindowText(h) or ""
        if t in excluded:
            return
        # 작은 다이얼로그 제외
        try:
            r = win32gui.GetWindowRect(h)
            w, hh = r[2] - r[0], r[3] - r[1]
            if w < 300 or hh < 200:
                return
        except Exception:
            return
        results.append((h, t, 0))

    win32gui.EnumWindows(cb, None)
    return [t for _, t, _ in results]


def main() -> int:
    import pyautogui
    import pyperclip
    import win32gui
    from core.window_manager import focus_kakaotalk

    sw, sh = pyautogui.size()
    pyautogui.moveTo(sw // 2, sh // 2, duration=0)
    time.sleep(0.3)

    mapping_path = ROOT / "data" / "room_mapping.json"
    mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
    print(f"mapping 총 {len(mapping)}개 검증 시작")
    print()

    window = focus_kakaotalk()
    time.sleep(0.6)

    results = []
    for idx, (name, cid) in enumerate(mapping.items(), 1):
        t0 = time.time()
        print(f"[{idx:2d}/{len(mapping)}] {name[:35]}", end=" ", flush=True)

        # 이전 분리창들 (이번 검색 전 상태) 기록
        before = set(_get_visible_separate_windows())

        try:
            pyautogui.moveTo(sw // 2, sh // 2, duration=0)
            time.sleep(0.2)
            focus_kakaotalk()
            time.sleep(0.4)

            pyautogui.hotkey("ctrl", "f")
            time.sleep(0.5)
            pyperclip.copy(name)
            pyautogui.hotkey("ctrl", "v")
            time.sleep(0.4)
            pyautogui.press("enter")
            time.sleep(1.0)
            pyautogui.press("escape")
            time.sleep(0.4)

            # 검색 후 새로 나타난 분리창 또는 포커스 받은 창
            after = _get_visible_separate_windows()
            new_windows = [t for t in after if t not in before]

            # 포그라운드 윈도우 title
            fg_hwnd = win32gui.GetForegroundWindow()
            fg_title = win32gui.GetWindowText(fg_hwnd) or ""

            # 가장 신뢰할 만한 분리창 title
            detected_title = ""
            if new_windows:
                detected_title = new_windows[0]
            elif fg_title and fg_title not in ("카카오톡", "Claude", "네노바 액션 로그 (Ctrl+C 복사 가능)", "네노바 상태"):
                detected_title = fg_title
            elif after:
                # fallback: 가장 마지막 분리창
                detected_title = after[-1]

            ok = (detected_title == name)
            mark = "✅" if ok else "⚠️" if detected_title else "❌"
            print(f"{mark} title={detected_title!r} ({time.time()-t0:.1f}s)")

            results.append({
                "mapping_key": name,
                "conv_id": cid,
                "detected_title": detected_title,
                "exact_match": ok,
                "before_windows": list(before),
                "new_windows": new_windows,
                "fg_title": fg_title,
            })
        except Exception as e:
            print(f"❌ 예외: {type(e).__name__}: {e}")
            results.append({
                "mapping_key": name,
                "conv_id": cid,
                "exception": f"{type(e).__name__}: {e}",
                "exact_match": False,
            })

    # 결과 저장
    out = ROOT / "data" / "mapping_verify_report.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print()
    print(f"보고서: {out.name}")

    # 요약
    exact = [r for r in results if r.get("exact_match")]
    mismatch = [r for r in results if not r.get("exact_match") and r.get("detected_title")]
    notfound = [r for r in results if not r.get("detected_title") and not r.get("exception")]
    exc = [r for r in results if r.get("exception")]

    print()
    print(f"=== 요약 ===")
    print(f"  ✅ 정확 매칭: {len(exact)}/{len(results)}")
    print(f"  ⚠️ 이름 불일치: {len(mismatch)}")
    print(f"  ❌ 카톡에 없음: {len(notfound)}")
    print(f"  💥 예외: {len(exc)}")
    print()
    if mismatch:
        print("이름 불일치 항목:")
        for r in mismatch:
            print(f"  mapping={r['mapping_key']!r}")
            print(f"     실제={r['detected_title']!r}")
    if notfound:
        print()
        print("카톡에 없는 mapping 키:")
        for r in notfound:
            print(f"  {r['mapping_key']!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
