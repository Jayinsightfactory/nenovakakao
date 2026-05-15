"""
전체 카톡 채팅 방 한 번 정확히 추출 — 모든 후속 작업의 입력.

설계:
  1) 카톡 창 임시 (50, 50, 1300, 900) 으로 확대 → 좌측 패널 폭 280→430px
     (긴 이름 안 잘림. 끝나면 (50, 50, 900, 900) 복원)
  2) 채팅 리스트 안전 영역 클릭 → Home (맨 위)
  3) 페이지마다 좌측 패널 4x 확대 캡쳐 + safe_press("pagedown")
  4) 6 페이지 캡쳐 후 → Claude Vision Opus 에 multi-image 한 번 호출
     - 모든 방 이름 추출
     - 알려진 mapping keys + 거래처 패턴 힌트
     - 한 번에 보고 변형 자동 보정 (페이지 간 같은 방 dedup 도 LLM 이 처리)
  5) 결과 + 캡쳐 path 보고서 저장

안전:
  - 정지 버튼 (우상단), safe_actions (forbidden 좌표 자동 차단)
  - stall_detector (3 회 연속 무변화 → 자동 캡쳐 + ESC + 재시도)
  - 카톡 창 크기 변경은 verify 동안만 — 끝나면 무조건 복원

출력:
  data/all_chat_rooms_scan.json
  captures/scan_all/<ts>_page_<n>.png
"""
from __future__ import annotations

import base64
import json
import os
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env", override=True)


# 임시 카톡 창 크기 (좌측 패널 폭 증대 위해)
TEMP_W = 1300
TEMP_H = 900
ORIG_W = 900
ORIG_H = 900

MAX_PAGES = 6


def _move_kakaotalk(width: int, height: int) -> bool:
    """카톡 메인창을 (50, 50, width, height) 로 이동/리사이즈."""
    import win32gui
    import win32con
    hwnds: list[int] = []

    def _cb(h, lst):
        if win32gui.IsWindowVisible(h) and win32gui.GetWindowText(h) == "카카오톡":
            lst.append(h)

    win32gui.EnumWindows(_cb, hwnds)
    if not hwnds:
        return False
    try:
        hwnd = hwnds[0]
        placement = win32gui.GetWindowPlacement(hwnd)
        if placement[1] == win32con.SW_SHOWMINIMIZED:
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            time.sleep(0.2)
        win32gui.MoveWindow(hwnd, 50, 50, width, height, True)
        time.sleep(0.3)
        return True
    except Exception as e:
        print(f"  [WIN] 카톡 이동 실패: {e}", flush=True)
        return False


def _capture_left_panel(window_left: int, window_top: int, window_height: int,
                        panel_right: int, out_path: Path) -> bool:
    """좌측 채팅 리스트 영역 (검색바 아래 ~ 하단) 4x 확대 캡쳐."""
    from PIL import Image, ImageGrab
    bbox = (
        window_left,
        window_top + 130,
        window_left + panel_right,
        window_top + window_height - 30,
    )
    try:
        img = ImageGrab.grab(bbox=bbox)
    except Exception as e:
        print(f"  [CAP] ImageGrab 실패: {e}", flush=True)
        return False
    w, h = img.size
    img4x = img.resize((w * 4, h * 4), Image.LANCZOS)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img4x.save(out_path, optimize=True)
    return True


def _multi_image_ocr(image_paths: list[Path], mapping_keys: list[str]) -> str:
    """N 장 페이지 캡쳐를 Claude Opus 에 한 번에 전달 → 모든 방 이름 추출."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return "[ERROR] ANTHROPIC_API_KEY 없음"
    try:
        import anthropic
    except ImportError:
        return "[ERROR] anthropic 패키지 없음"

    content: list[dict] = []
    for i, p in enumerate(image_paths, 1):
        try:
            with open(p, "rb") as f:
                b64 = base64.standard_b64encode(f.read()).decode()
        except Exception as e:
            print(f"  [OCR] {p.name} 읽기 실패: {e}", flush=True)
            continue
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": b64},
        })
        content.append({"type": "text", "text": f"[페이지 {i}]"})

    hint_str = ", ".join(f"'{k}'" for k in mapping_keys)
    prompt = (
        f"위 {len(image_paths)} 장은 같은 카카오톡 채팅 리스트를 위에서 아래로 스크롤하며 "
        f"순차 캡쳐한 것이야. 페이지 간 일부 행은 겹친다. 모든 페이지를 종합해서 "
        f"실제 존재하는 채팅방 이름을 위에서부터 순서대로, 중복 제거해서 한 줄에 한 방씩만 출력해.\n\n"
        f"⚠️ 매우 중요한 규칙:\n"
        f"1. OCR 추측 금지. 후보 리스트의 정확한 표기와 시각적으로 일치하면 후보 표기 그대로 사용.\n"
        f"2. 멤버 수(예: 3, 12), 시간(오후 4:30), 미리보기 메시지, 안 읽음 카운트, 광고는 모두 제외.\n"
        f"3. 한 글자 다른 변형 (예: '네노바' vs '네토바') 이 페이지마다 나오면 가장 많이 나오는 표기 채택.\n"
        f"4. 한국어 자모 분해/조합 실수 금지 (예: '수아래' vs '수야래' — 잘 분간해서).\n"
        f"5. 출력 형식: 줄당 하나의 방 이름. 번호/불릿/설명 없음.\n\n"
        f"알려진 후보 (이게 보이면 정확히 이 표기 사용): {hint_str}\n\n"
        f"그 외 거래처 단체방은 '거래처명 + 네노바' 또는 '거래처명 & 네노바' 형식이 일반적. "
        f"숫자가 끝에 붙은 건 (예: '참좋은 &네노바 5') 멤버 수일 가능성이 높으니 숫자만 제거. "
        f"1:1 채팅은 사람 이름 그대로 (예: '조현욱', '변진형과장')."
    )
    content.append({"type": "text", "text": prompt})

    try:
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model="claude-opus-4-7",
            max_tokens=2048,
            messages=[{"role": "user", "content": content}],
        )
        if not resp.content:
            return ""
        return (getattr(resp.content[0], "text", "") or "").strip()
    except Exception as e:
        return f"[ERROR] API 호출 실패: {type(e).__name__}: {e}"


def main() -> int:
    import pyautogui
    from core.safe_actions import safe_click, safe_press, ForbiddenAction
    from core.side_effect_detector import SideEffectDetected
    from core.stall_detector import StallTracker
    from core.stop_button import (
        start_stop_button, stop_button_close, check_stop, set_status, StopRequested,
    )
    from core.window_manager import focus_kakaotalk

    start_stop_button()
    print("  [STOP] 우상단 정지 버튼 활성. 누르면 즉시 중단.")

    sw, sh = pyautogui.size()
    pyautogui.moveTo(sw // 2, sh // 2, duration=0)
    time.sleep(0.3)

    mapping_path = ROOT / "data" / "room_mapping.json"
    mapping = json.loads(mapping_path.read_text(encoding="utf-8")) if mapping_path.exists() else {}
    mapping_keys = list(mapping.keys())
    print(f"기존 mapping: {len(mapping_keys)}개 (힌트로 사용)")

    try:
        # 카톡 활성화 후 임시 확대
        window = focus_kakaotalk()
        time.sleep(0.4)
        print(f"카톡 메인창 (확대 전): ({window.left},{window.top}) {window.width}x{window.height}")
        if not _move_kakaotalk(TEMP_W, TEMP_H):
            print("  [INIT] 카톡 창 확대 실패")
            stop_button_close()
            return 2
        time.sleep(0.4)
        window = focus_kakaotalk()
        time.sleep(0.3)
        print(f"카톡 메인창 (확대 후): ({window.left},{window.top}) {window.width}x{window.height}")
        origin = (window.left, window.top)

        # 좌측 패널 폭 추정 — 1300 폭일 때 약 380~430
        panel_right = 380

        debug_dir = ROOT / "captures" / "scan_all"
        debug_dir.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        page_paths: list[Path] = []

        tracker = StallTracker(threshold=3, label="scan_all")
        baseline: set[str] = set()
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

        # 채팅 리스트 영역 클릭 + Home
        try:
            safe_click(
                window.left + 200, window.top + 400,
                intent="채팅 리스트 영역 포커스 (스캔 시작)",
                kakaotalk_origin=origin,
                expect_new_window=True,
            )
            safe_press("home", intent="채팅 리스트 맨 위", kakaotalk_origin=origin)
        except (ForbiddenAction, SideEffectDetected) as e:
            print(f"  [INIT] {type(e).__name__}: {e}")
            return 1
        time.sleep(0.6)

        for page in range(MAX_PAGES):
            check_stop()
            set_status(f"page {page+1}/{MAX_PAGES} 캡쳐 중...")
            cap_path = debug_dir / f"{ts}_page_{page}.png"
            ok = _capture_left_panel(
                window.left, window.top, window.height, panel_right, cap_path,
            )
            if not ok:
                stall = tracker.record_no_change()
                if stall and stall.is_stall:
                    if not stall.recovery_applied:
                        print("  🛑 회복 불가 정체 — 중단")
                        break
                continue
            page_paths.append(cap_path)
            size = cap_path.stat().st_size
            print(f"  page {page+1}: 캡쳐 {cap_path.name} ({size:,}B)")
            tracker.record_change()

            if page < MAX_PAGES - 1:
                try:
                    safe_click(
                        window.left + 200, window.top + 400,
                        intent=f"PageDown 전 포커스 (page {page+2})",
                        kakaotalk_origin=origin,
                        expect_new_window=True,
                    )
                    safe_press(
                        "pagedown", intent=f"PageDown (page {page+2})",
                        kakaotalk_origin=origin,
                    )
                except (ForbiddenAction, SideEffectDetected) as e:
                    print(f"  [SCROLL] {type(e).__name__}: {e} — 종료")
                    break
                time.sleep(0.6)

        # 다중 이미지 OCR
        if not page_paths:
            print("  [OCR] 캡쳐된 페이지가 없음")
            return 3

        print(f"\n=== Opus multi-image OCR ({len(page_paths)} 페이지) ===")
        set_status(f"Opus OCR 중 ({len(page_paths)} 페이지)")
        text = _multi_image_ocr(page_paths, mapping_keys)
        print(text)

        # 결과 파싱
        import re
        rooms: list[str] = []
        for ln in text.splitlines():
            ln = ln.strip(" -•·*").strip()
            if not ln:
                continue
            ln = re.sub(r"^\s*[\[\(]?\s*\d+[\.\)\]]\s*", "", ln).strip()
            if ln and ln.lower() not in ("rooms:", "names:", "[페이지", "페이지"):
                rooms.append(ln)

        # mapping 매칭
        def norm(s: str) -> str:
            return re.sub(r"[\s\[\]\(\)\.\-_\"'&+,]+", "", s or "").lower()

        in_mapping = []
        extras = []
        for r in rooms:
            nr = norm(r)
            matched = any(nr == norm(k) for k in mapping_keys)
            (in_mapping if matched else extras).append(r)

        # 결과 저장
        out = ROOT / "data" / "all_chat_rooms_scan.json"
        out.write_text(json.dumps({
            "ts": ts,
            "page_captures": [str(p) for p in page_paths],
            "raw_opus_output": text,
            "parsed_rooms": rooms,
            "in_mapping": in_mapping,
            "extra_rooms_not_in_mapping": extras,
            "mapping_keys": mapping_keys,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n보고서: {out.name}")
        print(f"  ✅ mapping 일치: {len(in_mapping)}/{len(mapping_keys)}")
        print(f"  ➕ 신규 (mapping 외): {len(extras)}")
        for r in extras[:40]:
            print(f"    • {r}")
        return 0
    except StopRequested as e:
        print(f"\n🛑 [STOP] {e}")
        return 1
    finally:
        # 카톡 창 원래 크기 복원
        print("\n복원: 카톡 창 (50, 50, 900, 900)")
        _move_kakaotalk(ORIG_W, ORIG_H)
        time.sleep(0.3)
        stop_button_close()


if __name__ == "__main__":
    sys.exit(main())
