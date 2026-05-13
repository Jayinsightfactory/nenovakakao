"""
mapping 23개 키 vs 카톡 실제 방 이름 1:1 매칭 검증 — v2 (스크롤 OCR + 헤더 OCR).

v1 (verify_room_mapping.py) 가 실패한 이유:
  - Ctrl+F 검색은 카톡 분리창을 띄우지 않음 (메인 창 우측 패널에서만 선택)
  - `_get_visible_separate_windows` 가 "Program Manager" (Windows 데스크탑) 를
    분리창으로 오인 → 모든 결과가 "Program Manager" 로 잡혀 실패

v2 전략:
  Phase A — 좌측 채팅 리스트 전체 스크롤 OCR (1차, 기본)
    - Home 으로 맨 위 이동 → 좌측 패널 280px 캡쳐 + 2x 확대 + Claude Vision
    - mapping 키를 후보 힌트로 제공해 한글 오인식 회피
      ("네토바"→"네노바", "추인"→"수입" 같은 오인식 자동 보정)
    - PageDown 으로 스크롤하며 5~6 페이지 캡쳐
    - 모든 캡쳐 결과 합쳐 dedup → 실제 카톡에 존재하는 방 이름 집합 확보

  Phase B — (기본 비활성, --with-search 옵션 필요)
    검색바 클릭 + paste 가 카톡 통합검색을 띄우면서 "친구 추가" 팝업까지
    유발하는 부작용 확인 (2026-05-13). 자동화 위험성으로 기본 OFF.
    부득이 사용 시: 다른 자동화 모두 정지 + 관리자 옆에서 감시.

  결과: data/mapping_verify_report_v2.json
"""
from __future__ import annotations

import base64
import json
import os
import re
import sys
import time
from difflib import SequenceMatcher
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env", override=True)


PRIMARY_MODEL = "claude-haiku-4-5-20251001"
FALLBACK_MODEL = "claude-opus-4-7"


def _norm(s: str) -> str:
    s = re.sub(r"[\s\[\]\(\)\.\-_\"'&+,]+", "", s or "")
    return s.lower()


def _rooms_match(a: str, b: str) -> bool:
    """카톡 방 이름 fuzzy 매칭 (kakaowork_app._rooms_match 동일 로직)."""
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return False
    if na == nb or na in nb or nb in na:
        return True
    shorter = na if len(na) <= len(nb) else nb
    longer = nb if shorter is na else na
    if len(shorter) >= 3:
        best = 0.0
        span = len(shorter)
        for i in range(0, max(1, len(longer) - span + 1)):
            r = SequenceMatcher(None, shorter, longer[i:i + span]).ratio()
            if r > best:
                best = r
        if best >= 0.78:
            return True
    return SequenceMatcher(None, na, nb).ratio() >= 0.78


def _match_score(key: str, candidate: str) -> float:
    """key 와 candidate 의 매칭 점수 (높을수록 가까움).
    동률일 때 정확 일치 > 길이 가까운 후보 > fuzzy 순으로 우선.
    """
    nk, nc = _norm(key), _norm(candidate)
    if not nk or not nc:
        return 0.0
    if nk == nc:
        return 100.0
    # 길이 차 0 일수록 좋음 — 짧은 substring 매칭("조현욱"가 "조현욱,박성빈..."에 들어가는 케이스) 회피
    base = SequenceMatcher(None, nk, nc).ratio()  # 0~1
    len_penalty = abs(len(nk) - len(nc)) / max(len(nk), len(nc))  # 0~1
    return base * 50 - len_penalty * 30


def _best_match(key: str, rooms: list[str]) -> tuple[str | None, float]:
    """rooms 중 key 와 매칭되는 것 중 가장 점수 높은 것 선택. 임계 미달이면 None."""
    best: tuple[str | None, float] = (None, -999.0)
    for r in rooms:
        if not _rooms_match(key, r):
            continue
        sc = _match_score(key, r)
        if sc > best[1]:
            best = (r, sc)
    # 임계: 점수가 너무 낮으면 매칭 부정 (예: 너무 다른 길이의 substring)
    if best[0] is None or best[1] < 10.0:
        return (None, best[1])
    return best


def _ocr_image(image_path: Path, prompt: str, model: str = PRIMARY_MODEL) -> str | None:
    """Claude Vision OCR 단발 호출."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print(f"  [OCR] ANTHROPIC_API_KEY 없음", flush=True)
        return None
    try:
        import anthropic
    except ImportError:
        print(f"  [OCR] anthropic 패키지 없음", flush=True)
        return None
    try:
        with open(image_path, "rb") as f:
            b64 = base64.standard_b64encode(f.read()).decode()
    except Exception as e:
        print(f"  [OCR] 파일 읽기 실패: {e}", flush=True)
        return None
    try:
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model=model,
            max_tokens=1024,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
                    {"type": "text", "text": prompt},
                ],
            }],
        )
        if not resp.content:
            return ""
        text = getattr(resp.content[0], "text", "") or ""
        return text.strip()
    except Exception as e:
        print(f"  [OCR] API 호출 실패 ({model}): {type(e).__name__}: {e}", flush=True)
        return None


def _ensure_kakao_top() -> None:
    """카톡 메인창을 포그라운드로 강제. 다른 창이 위에 있어 ImageGrab 이 잘못된 픽셀
    잡는 문제 회피 (2026-05-13 verify_v2 실행에서 Claude/GPT 패널이 잡힌 사고)."""
    try:
        import win32gui
        import win32con
        from core.window_manager import KAKAOTALK_TITLE, force_foreground
        hwnds: list[int] = []
        def _cb(h, lst):
            if win32gui.IsWindowVisible(h) and win32gui.GetWindowText(h) == KAKAOTALK_TITLE:
                lst.append(h)
        win32gui.EnumWindows(_cb, hwnds)
        if not hwnds:
            return
        hwnd = hwnds[0]
        # 최소화 해제 + 포그라운드
        try:
            placement = win32gui.GetWindowPlacement(hwnd)
            if placement[1] == win32con.SW_SHOWMINIMIZED:
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        except Exception:
            pass
        force_foreground(hwnd)
        time.sleep(0.3)
        # 잠깐 TOPMOST 로 올렸다가 NOTOPMOST 로 풀기 — z-order 만 최상위 유지
        try:
            SWP = win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW
            win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0, SWP)
            time.sleep(0.05)
            win32gui.SetWindowPos(hwnd, win32con.HWND_NOTOPMOST, 0, 0, 0, 0, SWP)
        except Exception:
            pass
    except Exception as e:
        print(f"  [TOP] 카톡 포그라운드 강제 실패: {e}", flush=True)


def _capture_left_panel(window, out_path: Path) -> Path | None:
    """카톡 메인창 좌측 채팅 리스트 영역을 캡쳐 + 2x 확대.

    캡쳐 직전 카톡을 포그라운드로 강제하여 다른 창의 픽셀이 잡히지 않도록 한다.
    """
    from PIL import Image, ImageGrab

    _ensure_kakao_top()
    time.sleep(0.25)

    bbox = (
        window.left,
        window.top + 130,
        window.left + 290,
        window.top + window.height - 30,
    )
    try:
        img = ImageGrab.grab(bbox=bbox)
    except Exception as e:
        print(f"  [CAP] ImageGrab 실패: {e}", flush=True)
        return None
    w, h = img.size
    img2x = img.resize((w * 2, h * 2), Image.LANCZOS)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img2x.save(out_path, optimize=True)
    return out_path


def _capture_right_header(window, out_path: Path) -> Path | None:
    """카톡 메인창 우측 패널 상단 헤더(현재 선택 방 이름)를 캡쳐 + 2x 확대."""
    from PIL import Image, ImageGrab
    bbox = (
        window.left + 290,
        window.top + 35,
        window.left + 870,
        window.top + 110,
    )
    try:
        img = ImageGrab.grab(bbox=bbox)
    except Exception as e:
        print(f"  [CAP] ImageGrab 실패: {e}", flush=True)
        return None
    w, h = img.size
    img2x = img.resize((w * 2, h * 2), Image.LANCZOS)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img2x.save(out_path, optimize=True)
    return out_path


def _parse_rooms_from_ocr(text: str) -> list[str]:
    """OCR 응답에서 라인 단위로 방 이름 추출."""
    lines = [ln.strip(" -•·*").strip() for ln in (text or "").splitlines() if ln.strip()]
    cleaned: list[str] = []
    for ln in lines:
        ln = re.sub(r"^\s*[\[\(]?\s*\d+[\.\)\]]\s*", "", ln).strip()
        if not ln:
            continue
        # 메타 라인 제거
        if ln.lower() in ("[", "]", "rooms:", "names:"):
            continue
        cleaned.append(ln)
    return cleaned


def phase_a_scroll_ocr(window, mapping_keys: list[str], debug_dir: Path) -> dict[str, list[str]]:
    """좌측 채팅 리스트 전체를 스크롤하며 OCR.

    모든 액션은 safe_actions 통해 부작용 감지 + 자동 회복.

    Returns:
        { "page_<n>": [room_name, ...], ... }
    """
    import pyautogui
    from core.safe_actions import safe_click, safe_press, ForbiddenAction
    from core.side_effect_detector import SideEffectDetected, capture_state, diagnose, recover

    print(f"\n=== Phase A: 좌측 채팅 리스트 스크롤 OCR ===")
    origin = (window.left, window.top)

    # 시작 전: 잔존 다이얼로그 (어제 친구추가 등) 확인 + 자동 회복
    s0 = capture_state()
    if any("친구" in t or "통합검색" in t for t in s0.visible_titles):
        print("  [PRE] 잔존 다이얼로그 감지 → ESC ESC 정리", flush=True)
        pyautogui.press("escape")
        time.sleep(0.4)
        pyautogui.press("escape")
        time.sleep(0.4)

    # 채팅 리스트 영역 클릭 (안전 영역: y=400 = 채팅 행)
    pyautogui.moveTo(window.left + 140, window.top + 400, duration=0)
    time.sleep(0.2)
    safe_click(
        window.left + 140, window.top + 400,
        intent="채팅 리스트 영역 포커스 (스크롤 사전 준비)",
        kakaotalk_origin=origin,
    )
    safe_press("home", intent="채팅 리스트 맨 위로 스크롤", kakaotalk_origin=origin)
    time.sleep(0.5)

    hint_str = ", ".join(f"'{k}'" for k in mapping_keys)
    prompt = (
        "이 카카오톡 채팅방 리스트 스크린샷에서 보이는 채팅방 이름을 위에서부터 순서대로 추출해.\n"
        "출력 형식: 한 줄에 한 방 이름. 번호/불릿/설명 금지. 멤버 수(예: 3), 시간, 미리보기 메시지, "
        "안 읽음 카운트, 광고는 모두 제외하고 순수한 방 이름만.\n\n"
        f"⚠️ 중요: 다음은 실제 존재하는 방 이름 후보야. 화면의 글자가 이 후보 중 하나와 비슷하면 "
        f"OCR 추측 대신 후보의 정확한 표기를 그대로 사용해. 부분만 일치하면 후보를 그대로 적되, "
        f"후보에 없는 새 방 이름은 화면 그대로 적어.\n"
        f"후보: {hint_str}"
    )

    # 페이지 최대 6번 (대화방 6 페이지 정도면 충분)
    pages: dict[str, list[str]] = {}
    last_text = ""
    for page in range(6):
        cap_path = debug_dir / f"chat_list_page_{page}.png"
        if not _capture_left_panel(window, cap_path):
            break
        text = _ocr_image(cap_path, prompt, PRIMARY_MODEL)
        if not text:
            print(f"  page {page}: OCR 실패", flush=True)
            text = ""
        rooms = _parse_rooms_from_ocr(text)
        pages[f"page_{page}"] = rooms
        print(f"  page {page}: {len(rooms)}개 방 인식")
        for r in rooms:
            print(f"      • {r}")

        # 같은 페이지가 반복되면 (스크롤 끝) 종료
        if text == last_text and page > 0:
            print(f"  page {page} 동일 → 스크롤 끝", flush=True)
            break
        last_text = text

        # PageDown 으로 다음 페이지 — 카톡 포그라운드 + 채팅 리스트 영역 클릭으로 포커스 유지
        _ensure_kakao_top()
        pyautogui.moveTo(window.left + 140, window.top + 400, duration=0)
        time.sleep(0.15)
        try:
            # 채팅 리스트 행을 다시 클릭해서 스크롤 가능한 위젯에 포커스
            safe_click(
                window.left + 140, window.top + 400,
                intent=f"채팅 리스트 포커스 재진입 (page {page+1} 전)",
                kakaotalk_origin=origin,
            )
            safe_press("pagedown", intent=f"채팅 리스트 PageDown (page {page+1})",
                       kakaotalk_origin=origin)
        except (ForbiddenAction, SideEffectDetected) as e:
            print(f"  [PHASE-A HALT] {type(e).__name__}: {e}", flush=True)
            break
        time.sleep(0.5)

    return pages


def phase_b_search_header(
    window, mapping_keys: list[str], detected_rooms: set[str], debug_dir: Path
) -> dict[str, dict]:
    """Phase A 에서 매칭 못한 키만 채팅 리스트 검색바 + 행 클릭 + 헤더 OCR 로 검증.

    ⚠️ 2026-05-13 사고로 이 경로는 친구추가 팝업 부작용 확인됨.
    safe_actions 래퍼를 통해 호출하면 forbidden_coords 룰북에서 즉시 차단되어야 함.
    """
    import win32gui
    from core.safe_actions import safe_click, safe_paste, safe_press, ForbiddenAction
    from core.side_effect_detector import SideEffectDetected
    from core.window_manager import focus_kakaotalk

    print(f"\n=== Phase B: 검색바 + 헤더 OCR 검증 ===")

    EXCLUDED_WIN = {
        "카카오톡", "Claude", "네노바 액션 로그 (Ctrl+C 복사 가능)", "네노바 상태",
        "Program Manager", "ToastWindow", "Windows 입력 환경", "카카오워크", "",
    }

    def list_separate_windows() -> set[str]:
        titles: set[str] = set()
        def cb(h, _):
            if not win32gui.IsWindowVisible(h):
                return
            t = win32gui.GetWindowText(h) or ""
            if t in EXCLUDED_WIN:
                return
            try:
                r = win32gui.GetWindowRect(h)
                w, hh = r[2] - r[0], r[3] - r[1]
                if w < 300 or hh < 300:
                    return
            except Exception:
                return
            titles.add(t)
        win32gui.EnumWindows(cb, None)
        return titles

    # Phase A 에서 매칭된 키 제외 (best_match 사용)
    unmatched: list[str] = []
    for key in mapping_keys:
        m, _ = _best_match(key, sorted(detected_rooms))
        if m is None:
            unmatched.append(key)
    if not unmatched:
        print("  Phase A 에서 모두 매칭됨 → Phase B 스킵")
        return {}

    print(f"  Phase A 미매칭: {len(unmatched)}개 → safe_actions 기반 검증")
    import pyautogui
    sw, sh = pyautogui.size()
    results: dict[str, dict] = {}

    origin = (window.left, window.top)
    SEARCH_BAR_X = window.left + 150
    SEARCH_BAR_Y = window.top + 105
    FIRST_ROW_X = window.left + 140
    FIRST_ROW_Y = window.top + 155

    for idx, key in enumerate(unmatched, 1):
        print(f"\n  [{idx}/{len(unmatched)}] {key[:35]}", flush=True)
        try:
            pyautogui.moveTo(sw // 2, sh // 2, duration=0)
            time.sleep(0.2)
            focus_kakaotalk()
            time.sleep(0.4)

            before_windows = list_separate_windows()

            # 1) 검색바 클릭 → forbidden_coords 에 걸려서 ForbiddenAction 으로 차단되어야 함
            safe_click(
                SEARCH_BAR_X, SEARCH_BAR_Y,
                intent=f"채팅 리스트 검색바 클릭 — key={key}",
                kakaotalk_origin=origin,
            )
            safe_paste(key, intent=f"방 이름 검색 paste — {key}", kakaotalk_origin=origin)
            safe_click(
                FIRST_ROW_X, FIRST_ROW_Y, clicks=2,
                intent=f"필터링된 첫 행 더블클릭 — {key}",
                kakaotalk_origin=origin,
            )

            after_windows = list_separate_windows()
            new_windows = after_windows - before_windows
            separate_title = next(iter(new_windows)) if new_windows else ""

            header = ""
            if not separate_title:
                cap_path = debug_dir / f"header_{idx:02d}.png"
                if _capture_right_header(window, cap_path):
                    prompt = (
                        f"이 카카오톡 채팅창 상단 헤더 이미지의 방 이름을 정확히 한 줄로만 반환해.\n"
                        f"예상: '{key}'\n"
                        f"보이는 한글/숫자/특수문자를 그대로 적되, 추측·설명 금지. "
                        f"멤버 수, 시간 표시 제외. 방 이름이 안 보이거나 검색 다이얼로그면 빈 문자열."
                    )
                    header = _ocr_image(cap_path, prompt, PRIMARY_MODEL) or ""
                    header = header.splitlines()[0].strip() if header.strip() else ""

            detected = separate_title or header
            ok = bool(detected) and _rooms_match(key, detected) and "통합검색" not in detected
            mark = "✅" if ok else ("⚠️" if detected else "❌")
            print(f"      {mark} separate={separate_title!r}  header={header!r}")
            results[key] = {
                "header_ocr": header,
                "separate_window_title": separate_title,
                "matched": ok,
            }
        except ForbiddenAction as e:
            print(f"      🛑 [BLOCKED] {e}")
            results[key] = {
                "header_ocr": "",
                "separate_window_title": "",
                "matched": False,
                "error": f"ForbiddenAction: {e}",
            }
            # 한 키가 차단되면 다음 키도 같은 좌표 시도할 거니까 전체 중단
            break
        except SideEffectDetected as e:
            print(f"      🛑 [HALT] {e}")
            results[key] = {
                "header_ocr": "",
                "separate_window_title": "",
                "matched": False,
                "error": f"SideEffectDetected: {e}",
            }
            break
        except Exception as e:
            print(f"      💥 예외: {type(e).__name__}: {e}")
            results[key] = {
                "header_ocr": "",
                "separate_window_title": "",
                "matched": False,
                "error": f"{type(e).__name__}: {e}",
            }

    return results


def main() -> int:
    import pyautogui
    from core.stop_button import start_stop_button, stop_button_close, StopRequested
    from core.window_manager import focus_kakaotalk

    # 항상 위에 떠있는 [🛑 즉시 정지] 버튼 띄움 (별도 스레드 tkinter)
    start_stop_button()
    print("  [STOP] 우상단에 정지 버튼 창이 떴습니다. 누르면 즉시 모든 동작 멈춤.")

    # fail-safe
    sw, sh = pyautogui.size()
    pyautogui.moveTo(sw // 2, sh // 2, duration=0)
    time.sleep(0.3)

    mapping_path = ROOT / "data" / "room_mapping.json"
    mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
    mapping_keys = list(mapping.keys())
    print(f"mapping 총 {len(mapping_keys)}개 검증 시작 (v2 — 스크롤 OCR + 헤더 OCR)")

    try:
        window = focus_kakaotalk()
    except Exception as e:
        print(f"  [INIT] focus_kakaotalk 실패: {e}", flush=True)
        stop_button_close()
        return 2
    time.sleep(0.6)
    print(f"카톡 메인창: ({window.left},{window.top}) {window.width}x{window.height}")

    debug_dir = ROOT / "captures" / "verify_room_v2"
    debug_dir.mkdir(parents=True, exist_ok=True)

    # === Phase A ===
    try:
        pages = phase_a_scroll_ocr(window, mapping_keys, debug_dir)
    except StopRequested as e:
        print(f"\n🛑 [STOP] {e}", flush=True)
        stop_button_close()
        return 1
    detected_rooms: set[str] = set()
    for rooms in pages.values():
        detected_rooms.update(rooms)
    print(f"\nPhase A 합계: 유니크 방 {len(detected_rooms)}개 인식")

    # === Phase A 매칭 분석 (가장 점수 높은 후보 선택) ===
    phase_a_matches: dict[str, str | None] = {}
    phase_a_scores: dict[str, float] = {}
    detected_sorted = sorted(detected_rooms)
    for key in mapping_keys:
        m, sc = _best_match(key, detected_sorted)
        phase_a_matches[key] = m
        phase_a_scores[key] = sc

    # === Phase B (기본 비활성: 검색바 paste 가 친구추가 팝업 유발하는 부작용) ===
    # 사용 시 --with-search-DANGEROUS 플래그 필요 + 관리자 감시 필수
    phase_b_results: dict[str, dict] = {}
    if "--with-search-DANGEROUS" in sys.argv:
        print("\n⚠️  --with-search-DANGEROUS 지정됨: Phase B 실행 (forbidden_coords 차단 예상)")
        try:
            phase_b_results = phase_b_search_header(window, mapping_keys, detected_rooms, debug_dir)
        except StopRequested as e:
            print(f"\n🛑 [STOP] Phase B 중단: {e}", flush=True)
    else:
        print("\nPhase B 자동 스킵 (검색바 paste 부작용 회피). 필요 시 --with-search-DANGEROUS 로 활성화.")

    # === 최종 결과 ===
    final: list[dict] = []
    for key, cid in mapping.items():
        pa_match = phase_a_matches.get(key)
        pa_score = phase_a_scores.get(key, 0.0)
        pb = phase_b_results.get(key, {})
        pb_separate = pb.get("separate_window_title") if pb else None
        pb_header = pb.get("header_ocr") if pb else None
        pb_detected = pb_separate or pb_header
        verified = bool(pa_match) or bool(pb and pb.get("matched"))
        exact_match = (pa_match == key) or (pb_detected == key)
        item = {
            "mapping_key": key,
            "conv_id": cid,
            "phase_a_match": pa_match,
            "phase_a_score": round(pa_score, 2),
            "phase_b_separate_window": pb_separate,
            "phase_b_header": pb_header,
            "phase_b_matched": pb.get("matched") if pb else None,
            "verified": verified,
            "exact_match": exact_match,
        }
        if pb and pb.get("error"):
            item["error"] = pb["error"]
        final.append(item)

    # 카톡에는 있지만 mapping 에 없는 방 (역방향)
    extra_in_chatlist: list[str] = []
    for r in sorted(detected_rooms):
        # mapping 키 어느 것에도 매칭 안 되면 추가
        if not any(_rooms_match(r, k) for k in mapping_keys):
            extra_in_chatlist.append(r)

    out = ROOT / "data" / "mapping_verify_report_v2.json"
    out.write_text(
        json.dumps({
            "phase_a_pages": pages,
            "phase_a_detected_rooms": sorted(detected_rooms),
            "phase_b_results": phase_b_results,
            "extra_rooms_in_chatlist_not_in_mapping": extra_in_chatlist,
            "final": final,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n보고서: {out.name}")
    print(f"\n카톡에 있지만 mapping 에 없는 방: {len(extra_in_chatlist)}개")
    for r in extra_in_chatlist[:30]:
        print(f"  • {r}")

    # 정지 버튼 창 닫기
    stop_button_close()

    # 요약
    exact = [r for r in final if r.get("exact_match")]
    verified = [r for r in final if r.get("verified") and not r.get("exact_match")]
    failed = [r for r in final if not r.get("verified")]
    print()
    print("=== 요약 ===")
    print(f"  ✅ 정확 일치 (mapping key == 인식 이름): {len(exact)}/{len(final)}")
    print(f"  🟡 매칭 OK (fuzzy 일치): {len(verified)}")
    print(f"  ❌ 미검증: {len(failed)}")
    if verified:
        print("\n매칭됐지만 정확 일치 아님 (mapping key 수정 후보):")
        for r in verified:
            actual = (
                r.get("phase_a_match")
                or r.get("phase_b_separate_window")
                or r.get("phase_b_header")
                or ""
            )
            print(f"  mapping={r['mapping_key']!r}")
            print(f"     실제={actual!r}  (score={r.get('phase_a_score')})")
    if failed:
        print("\n미검증 mapping 키:")
        for r in failed:
            print(f"  {r['mapping_key']!r}")
            if r.get("phase_b_separate_window"):
                print(f"      분리창 title={r['phase_b_separate_window']!r}")
            if r.get("phase_b_header"):
                print(f"      Phase B 헤더={r['phase_b_header']!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
