"""
매핑 검증 v3 — 분리창 win32 title 추출 (OCR 불사용, 100% 정확).

전략:
  좌측 채팅 리스트의 각 행을 더블클릭 → 분리창 띄움 → win32gui.GetWindowText 로
  카톡 내부 텍스트 그대로 추출 → 분리창 닫음 → 다음 행.

  OCR 한 번도 안 거치니 한 글자 오인식 ("수아래/수야래", "엘리아리/여리아리" 등)
  자체가 발생 불가.

안전장치:
  - 시작 전 정지 버튼 (우상단 [🛑]) — 누르면 즉시 중단
  - 모든 액션 safe_click 통해서 부작용 자동 감지·회복
  - 시작 시 이미 떠있던 분리창은 보호 (건드리지 않음, init_titles 에 등록)
  - 새로 띄운 분리창만 처리 후 win32 WM_CLOSE 로 정리
  - 한 행당 최대 4 초, 전체 최대 4 분

행 좌표:
  카톡 메인창 (50,50,900,900) 고정 가정.
  좌측 패널 폭 ~280px, 행 높이 ~70px, 첫 행 y ≈ 150.
  광고 영역이 사이에 끼면 클릭 시 ForbiddenAction 또는 외부 창 부작용 →
  safe_click 이 자동 회복 후 그 행 스킵.

결과:
  data/mapping_verify_report_v3.json
    - all_detected_rooms: 카톡 분리창 title 로 추출된 정확한 방 이름 set
    - mapping_status: 각 mapping key 의 exact_match (bool)
    - extra_rooms_not_in_mapping
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env", override=True)


# ─────────────────────────────────────────────
# win32 helpers
# ─────────────────────────────────────────────
EXCLUDED_TITLES = frozenset({
    "카카오톡", "Claude", "카카오워크", "Program Manager", "ToastWindow",
    "Windows 입력 환경", "네노바 액션 로그 (Ctrl+C 복사 가능)", "네노바 상태",
    "네노바 자동화 정지", "계산기", "열기",
    "KakaoTalkShadowWnd", "KakaoTalkEdgeWnd",
    "",
})

EXCLUDED_CONTAINS = (" - Chrome", " - Edge", " - Firefox", " - Brave",
                     "Visual Studio", "VS Code", "Notepad", "Explorer",
                     "Cmd ", "PowerShell", "Terminal", "KakaoTalkShadow")


def _list_separate_windows() -> dict[int, str]:
    """카톡 채팅 분리창 후보 (hwnd → title) 매핑.

    크기 300+ × 300+, EXCLUDED_TITLES / CONTAINS 외.
    """
    import win32gui
    result: dict[int, str] = {}

    def _cb(h, _):
        try:
            if not win32gui.IsWindowVisible(h):
                return
            t = win32gui.GetWindowText(h) or ""
            if not t or t in EXCLUDED_TITLES:
                return
            if any(ek in t for ek in EXCLUDED_CONTAINS):
                return
            r = win32gui.GetWindowRect(h)
            w, hh = r[2] - r[0], r[3] - r[1]
            if w < 300 or hh < 300:
                return
            result[h] = t
        except Exception:
            pass

    try:
        win32gui.EnumWindows(_cb, None)
    except Exception:
        pass
    return result


def _close_window(hwnd: int) -> bool:
    """분리창 정상 닫기 (WM_CLOSE). 카톡 분리창은 가끔 무시함 → _move_window_aside 폴백."""
    import win32gui
    import win32con
    try:
        win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
        return True
    except Exception:
        return False


_move_offset_x = 1400  # 카톡 메인 (50, 50, 900, 900) 우측 바깥
_move_offset_y = 60
_move_stack: int = 0


def _move_window_aside(hwnd: int) -> bool:
    """분리창을 카톡 메인 안 가리는 곳으로 옮김. close 못 해도 화면에서 비킴.

    여러 개 옮길 때 살짝씩 어긋나게 쌓아서 모두 보이게.
    """
    global _move_stack
    import win32gui
    import win32con
    try:
        x = _move_offset_x + (_move_stack % 4) * 40
        y = _move_offset_y + (_move_stack % 6) * 30
        win32gui.SetWindowPos(
            hwnd, 0, x, y, 500, 700,
            win32con.SWP_NOZORDER | win32con.SWP_SHOWWINDOW,
        )
        _move_stack += 1
        return True
    except Exception:
        return False


import re as _re


def _normalize_title(t: str) -> str:
    """카톡 1:1 분리창 title 끝의 ' YYYY-MM-DD' 같은 날짜 꼬리 제거."""
    if not t:
        return t
    t = t.strip()
    t = _re.sub(r"\s+\d{4}-\d{1,2}-\d{1,2}(\s+.*)?$", "", t)
    return t.strip()


def _try_read_separate_title(before: dict[int, str], after: dict[int, str]) -> tuple[str, int | None, bool]:
    """before/after diff 로 새 분리창 title 추출.

    Returns:
        (title, new_hwnd_or_None, is_newly_opened)
        - 새로 열린 분리창이 있으면 그 title
        - 없으면 (이미 떠있던 분리창이 활성화된 경우) 포그라운드 title
    """
    import win32gui
    new = {h: t for h, t in after.items() if h not in before}
    if new:
        hwnd = next(iter(new))
        return _normalize_title(new[hwnd]), hwnd, True
    # 새 창 없음 → 포그라운드 검사 (이미 있던 분리창이 활성화된 경우)
    try:
        fg = win32gui.GetForegroundWindow()
        if fg in after and after[fg] not in EXCLUDED_TITLES:
            return _normalize_title(after[fg]), fg, False
    except Exception:
        pass
    return "", None, False


# ─────────────────────────────────────────────
# 메인 흐름
# ─────────────────────────────────────────────
FIRST_ROW_Y_REL = 150       # 카톡 좌측 패널 첫 행 시작 y (창 기준 상대)
ROW_HEIGHT = 70             # 행 높이 (광고/배너 영역 = 자동 감지 + 스킵)
ROW_X_REL = 140             # 행 클릭 x (창 기준 상대 — 가운데)
ROWS_PER_PAGE = 10          # 한 화면당 최대 행
MAX_PAGES = 6
ROW_WAIT_AFTER_DBLCLICK = 1.2  # 더블클릭 후 분리창 뜨기 대기


def process_one_row(window, x: int, y: int, init_titles: set[str]) -> dict:
    """한 행을 더블클릭해서 분리창 title 추출 + 새 창이면 닫음.

    Returns dict:
      - status: "ok_new" | "ok_existing" | "no_change" | "side_effect" | "forbidden"
      - title: 추출된 방 이름 (있을 때만)
      - hwnd: 분리창 hwnd
    """
    from core.safe_actions import safe_click, ForbiddenAction
    from core.side_effect_detector import SideEffectDetected
    import pyautogui

    before = _list_separate_windows()

    try:
        # 더블클릭 한 번에 분리창 띄움 (의도된 새 창 → expect_new_window=True)
        safe_click(
            x, y, clicks=2,
            intent=f"채팅 리스트 행 더블클릭 ({x},{y}) — 분리창 의도",
            kakaotalk_origin=(window.left, window.top),
            post_wait=ROW_WAIT_AFTER_DBLCLICK,
            expect_new_window=True,
        )
    except ForbiddenAction as e:
        return {"status": "forbidden", "reason": str(e), "xy": [x, y]}
    except SideEffectDetected as e:
        return {"status": "side_effect", "reason": str(e), "xy": [x, y]}

    after = _list_separate_windows()
    title, hwnd, is_new = _try_read_separate_title(before, after)

    if title:
        status = "ok_new" if is_new else "ok_existing"
        # 새로 띄운 창은 close 시도 → 실패 시 move 로 화면 우측에 치워둠
        # (close 못 하는 카톡 분리창 때문에 page 2+ 가 가려지던 문제 해결)
        if is_new and hwnd:
            _close_window(hwnd)
            time.sleep(0.25)
            try:
                import win32gui
                if win32gui.IsWindow(hwnd) and win32gui.IsWindowVisible(hwnd):
                    _move_window_aside(hwnd)
            except Exception:
                pass
            time.sleep(0.15)
        return {"status": status, "title": title, "hwnd": hwnd, "xy": [x, y]}

    return {"status": "no_change", "xy": [x, y]}


def main() -> int:
    import pyautogui
    import win32gui
    from core.stop_button import start_stop_button, stop_button_close, StopRequested, check_stop, set_status
    from core.window_manager import focus_kakaotalk
    from core.safe_actions import safe_press, ForbiddenAction
    from core.side_effect_detector import SideEffectDetected

    # 정지 버튼
    start_stop_button()
    print("  [STOP] 우상단 정지 버튼 활성. 누르면 즉시 모든 동작 멈춤.")

    # fail-safe 위치
    sw, sh = pyautogui.size()
    pyautogui.moveTo(sw // 2, sh // 2, duration=0)
    time.sleep(0.3)

    mapping = json.loads((ROOT / "data" / "room_mapping.json").read_text(encoding="utf-8"))
    mapping_keys = list(mapping.keys())
    print(f"검증 대상 mapping: {len(mapping_keys)}개")

    try:
        window = focus_kakaotalk()
    except Exception as e:
        print(f"  [INIT] 카톡 활성화 실패: {e}")
        stop_button_close()
        return 2
    time.sleep(0.5)
    print(f"카톡 메인창: ({window.left},{window.top}) {window.width}x{window.height}")
    origin = (window.left, window.top)

    init_titles_map = _list_separate_windows()
    init_titles = set(init_titles_map.values())
    print(f"시작 시 이미 떠있는 분리창: {len(init_titles)}개 (보호)")
    for t in sorted(init_titles):
        print(f"   • {t}")

    detected_rooms: set[str] = set(init_titles)  # 이미 떠있는 것도 카톡 방
    per_row_log: list[dict] = []

    # 화면 정체 자동 감지
    from core.stall_detector import StallTracker
    tracker = StallTracker(threshold=3, label="verify_v3")
    # baseline: 시작 시 떠있는 visible 창 모두 (다이얼로그 차분용)
    baseline = set(init_titles)
    try:
        import win32gui
        def _cb(h, _):
            if win32gui.IsWindowVisible(h):
                t = win32gui.GetWindowText(h) or ""
                if t:
                    baseline.add(t)
        win32gui.EnumWindows(_cb, None)
    except Exception:
        pass
    tracker.init_baseline(baseline)

    # 채팅 리스트 영역 클릭 + Home 으로 맨 위
    try:
        from core.safe_actions import safe_click
        safe_click(
            window.left + ROW_X_REL, window.top + 400,
            intent="채팅 리스트 영역 포커스", kakaotalk_origin=origin,
            expect_new_window=True,
        )
        safe_press("home", intent="채팅 리스트 맨 위", kakaotalk_origin=origin)
    except (ForbiddenAction, SideEffectDetected, StopRequested) as e:
        print(f"  [INIT] {type(e).__name__}: {e}")
        stop_button_close()
        return 1
    time.sleep(0.5)

    last_page_titles: set[str] = set()

    try:
        for page in range(MAX_PAGES):
            check_stop()
            set_status(f"page {page+1}/{MAX_PAGES} 검증 중...")
            print(f"\n=== Page {page+1} ===")
            page_new_titles: set[str] = set()

            for row in range(ROWS_PER_PAGE):
                check_stop()
                x = window.left + ROW_X_REL
                y = window.top + FIRST_ROW_Y_REL + row * ROW_HEIGHT

                # 카톡 창 밖으로 나가는 행은 스킵
                if y >= window.top + window.height - 40:
                    break

                set_status(f"page {page+1} row {row+1} y={y}")
                result = process_one_row(window, x, y, init_titles)
                result["page"] = page
                result["row"] = row
                per_row_log.append(result)

                title = result.get("title", "")
                status = result["status"]
                mark = {
                    "ok_new": "✅", "ok_existing": "♻️",
                    "no_change": "·", "side_effect": "⚠️", "forbidden": "🛑",
                }.get(status, "?")
                print(f"  {mark} page{page+1} row{row+1} y={y} status={status:<13} {title[:40]!r}")
                if title:
                    detected_rooms.add(title)
                    page_new_titles.add(title)
                    tracker.record_change()
                else:
                    # no_change / forbidden / side_effect → stall 카운트
                    stall = tracker.record_no_change()
                    if stall and stall.is_stall:
                        # blocking dialog 면 ESC 가 자동 적용됨 — 재시도
                        if stall.recovery_applied:
                            print(f"  ↻ stall 회복 후 재시도", flush=True)
                            result_retry = process_one_row(window, x, y, init_titles)
                            per_row_log.append({**result_retry, "page": page,
                                                "row": row, "retried_after_stall": True})
                            rt_title = result_retry.get("title", "")
                            if rt_title:
                                detected_rooms.add(rt_title)
                                page_new_titles.add(rt_title)
                                tracker.record_change()
                        else:
                            # 회복 불가 → 즉시 중단
                            print(f"  🛑 회복 불가능한 정체 — 작업 중단. "
                                  f"캡쳐: {stall.capture_path}", flush=True)
                            raise StopRequested("화면 정체 자동 회복 실패")

                time.sleep(0.2)

            # 동일 페이지면 스크롤 끝
            if page > 0 and page_new_titles == last_page_titles and page_new_titles:
                print(f"  → page {page+1} 가 이전 페이지와 동일 — 스크롤 종료")
                break
            last_page_titles = page_new_titles

            # PageDown
            try:
                safe_click(
                    window.left + ROW_X_REL, window.top + 400,
                    intent=f"PageDown 전 포커스 재진입 (page {page+2})",
                    kakaotalk_origin=origin,
                    expect_new_window=True,
                )
                safe_press("pagedown", intent=f"채팅 리스트 PageDown (page {page+2})",
                           kakaotalk_origin=origin)
            except (ForbiddenAction, SideEffectDetected) as e:
                print(f"  [SCROLL] {type(e).__name__}: {e} — 종료")
                break
            time.sleep(0.6)
    except StopRequested as e:
        print(f"\n🛑 [STOP] {e}")

    # 검증 결과
    print()
    print(f"=== 분리창 추출 결과: {len(detected_rooms)} 개 (init 포함) ===")
    final: list[dict] = []
    for key, cid in mapping.items():
        exact = key in detected_rooms
        item = {"mapping_key": key, "conv_id": cid, "exact_match": exact}
        final.append(item)
        mark = "✅" if exact else "❌"
        print(f"  {mark} {key:<35} ({cid})")

    extras = sorted(t for t in detected_rooms if t not in mapping)

    out = ROOT / "data" / "mapping_verify_report_v3.json"
    out.write_text(json.dumps({
        "init_separate_windows": sorted(init_titles),
        "all_detected_rooms": sorted(detected_rooms),
        "mapping_status": final,
        "extra_rooms_not_in_mapping": extras,
        "per_row_log": per_row_log,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n보고서: {out.name}")

    exact_n = sum(1 for f in final if f["exact_match"])
    miss_n = len(final) - exact_n
    print()
    print(f"=== 요약 ===")
    print(f"  ✅ mapping 정확 일치: {exact_n}/{len(final)}")
    print(f"  ❌ 미일치: {miss_n}")
    print(f"  카톡 분리창에서 확인됐지만 mapping 에 없는 방: {len(extras)}개")
    if extras:
        for r in extras[:30]:
            print(f"    • {r}")

    stop_button_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
