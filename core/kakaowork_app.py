"""
카카오워크 PC 앱 화면 자동화 — 이미지/파일 업로드

검증 완료된 작동 방식 (2026-04-10):
1. Bot API로 대상 방에 텍스트 전송 → 방이 목록 맨 위로 올라옴
2. 카카오워크 앱 활성화 → 왼쪽 패널 첫 번째 방 클릭
3. 채팅 입력란 클릭 (포커스 확보)
4. Ctrl+T → Windows 파일 다이얼로그 → 경로 붙여넣기 → Enter
5. 전송 확인 팝업 → Enter
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pyautogui
import pygetwindow as gw
import pyperclip
import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env", override=True)

KAKAOWORK_TITLE = "카카오워크"
DATA_DIR = Path(__file__).parent.parent / "data"
NV_MAPPING_FILE = DATA_DIR / "room_mapping_nv.json"

# 왼쪽 패널 첫 번째 방 (창 기준 상대좌표)
FIRST_ROOM_X_OFFSET = 80
FIRST_ROOM_Y_OFFSET = 60

# 채팅방 설정 자동화 좌표 (창 우측 기준 상대 — 사용자 환경에서 미세조정 필요)
# 환경변수로 오버라이드 가능: NENOVA_GEAR_X / NENOVA_GEAR_Y / NENOVA_PENCIL_X / NENOVA_PENCIL_Y
GEAR_FROM_RIGHT = int(os.getenv("NENOVA_GEAR_FROM_RIGHT", "85"))   # 우상단에서 안쪽 px
GEAR_FROM_TOP   = int(os.getenv("NENOVA_GEAR_FROM_TOP",   "95"))   # 헤더 영역 높이
PENCIL_FROM_RIGHT = int(os.getenv("NENOVA_PENCIL_FROM_RIGHT", "130"))  # 패널 안 우측
PENCIL_FROM_TOP   = int(os.getenv("NENOVA_PENCIL_FROM_TOP",   "200"))  # 아바타+이름 영역


def _load_nv_mapping() -> dict:
    if NV_MAPPING_FILE.exists():
        with open(NV_MAPPING_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _build_header_candidates(kakaotalk_room_name: str) -> list[str]:
    """
    OCR 헤더 검증용 후보군 생성.

    미러 방 이름이 다양한 포맷으로 존재할 수 있음:
      - "[미러] 수입방"                (최초 생성 직후)
      - "NV01:수입방"                   (관리자가 NV## prefix로 rename)
      - "NV01: 수입방"                  (콜론 뒤 공백)
      - "NV01"                          (헤더가 잘려서 코드만 보임)
      - "수입방"                        (raw 이름)

    `_rooms_match` 퍼지 매칭과 조합되어 OCR 오인식/공백/괄호에 관용.
    """
    candidates: list[str] = []
    base = (kakaotalk_room_name or "").strip()
    if base:
        candidates.append(base)
        candidates.append(f"[미러] {base}")
    try:
        info = _load_nv_mapping().get(base) or {}
    except Exception:
        info = {}
    nv_code = (info.get("nv_code") or "").strip()
    nv_name = (info.get("nv_name") or "").strip()
    if nv_name:
        candidates.append(nv_name)
    if nv_code and base:
        candidates.append(f"{nv_code}:{base}")
        candidates.append(f"{nv_code}: {base}")
    # 주의: 순수 nv_code("NV04")는 후보에서 제외한다.
    #       4~5글자 짧은 코드는 1글자 차이(NV02↔NV04)만으로 퍼지 임계(0.75)에 걸려
    #       false positive를 만들기 때문. NV 코드 단독 매칭이 필요한 케이스는
    #       `_header_has_nv_code`(단어경계 exact match)가 강매칭으로 별도 처리.
    # 중복 제거, 순서 보존
    seen, uniq = set(), []
    for c in candidates:
        if c and c not in seen:
            seen.add(c)
            uniq.append(c)
    return uniq


def _header_has_nv_code(header: str, kakaotalk_room_name: str) -> bool:
    """
    OCR 헤더에 기대 방의 NV 코드(예: NV04)가 단어 경계로 포함되는지.
    한글 오인식이 심해도 영문+숫자 코드는 높은 확률로 정확히 읽히므로
    강력한 보조 시그널이 된다.
    """
    if not header:
        return False
    try:
        info = _load_nv_mapping().get(kakaotalk_room_name) or {}
    except Exception:
        return False
    code = (info.get("nv_code") or "").strip().upper()
    if not code:
        return False
    import re
    return re.search(rf"\b{re.escape(code)}\b", header.upper()) is not None


def _send_bot_api(conv_id: str, text: str) -> bool:
    """Bot API로 텍스트 전송 (방을 목록 맨 위로 올리기 위함)"""
    token = os.getenv("KAKAOWORK_BOT_TOKEN")
    if not token:
        return False
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        resp = requests.post(
            "https://api.kakaowork.com/v1/messages.send",
            headers=headers,
            json={"conversation_id": conv_id, "text": text},
            timeout=10,
        )
        return resp.json().get("success", False)
    except Exception:
        return False


def find_kakaowork_window():
    """카카오워크 앱 창을 찾아 활성화.
    1차: 타이틀 "카카오워크"
    2차: class HwndWrapper[KakaoWork.exe] 중 ToastWindow/0x0 제외한 메인
    3차: 최소화된(숨김) 창이라도 SW_RESTORE
    """
    # 1차: 타이틀 매칭
    windows = gw.getWindowsWithTitle(KAKAOWORK_TITLE)
    if windows:
        main = max(windows, key=lambda w: w.width * w.height)
        if main.isMinimized:
            main.restore()
            time.sleep(0.3)
        try:
            main.activate()
        except Exception:
            main.minimize()
            time.sleep(0.2)
            main.restore()
            time.sleep(0.3)
        time.sleep(0.5)
        return main

    # 2차: 클래스 기반
    import win32gui as _w32
    import win32con as _wc
    kw_hwnds: list[int] = []

    def _f(h, lst):
        if not _w32.IsWindowVisible(h):
            return
        cls = _w32.GetClassName(h) or ""
        if "KakaoWork" not in cls:
            return
        title = _w32.GetWindowText(h) or ""
        if "Toast" in title:
            return
        r = _w32.GetWindowRect(h)
        if (r[2] - r[0]) < 100 or (r[3] - r[1]) < 100:
            return
        kw_hwnds.append(h)

    _w32.EnumWindows(_f, kw_hwnds)
    if kw_hwnds:
        best = max(kw_hwnds, key=lambda h: (
            (_w32.GetWindowRect(h)[2] - _w32.GetWindowRect(h)[0]) *
            (_w32.GetWindowRect(h)[3] - _w32.GetWindowRect(h)[1])
        ))
        try:
            _w32.SetForegroundWindow(best)
            time.sleep(0.5)
            title = _w32.GetWindowText(best) or ""
            wraps = gw.getWindowsWithTitle(title) if title else []
            if wraps:
                return wraps[0]
        except Exception:
            pass

    # 3차: 모든 KakaoWork 창 (IsWindowVisible=False까지) → SW_RESTORE
    all_kw: list[int] = []
    def _all(h, lst):
        cls = _w32.GetClassName(h) or ""
        if "KakaoWork" in cls:
            lst.append(h)
    _w32.EnumWindows(_all, all_kw)
    for h in all_kw:
        r = _w32.GetWindowRect(h)
        if (r[2] - r[0]) > 100 and (r[3] - r[1]) > 100:
            try:
                _w32.ShowWindow(h, _wc.SW_RESTORE)
                _w32.ShowWindow(h, _wc.SW_SHOW)
                time.sleep(0.3)
                _w32.SetForegroundWindow(h)
                time.sleep(0.5)
                t = _w32.GetWindowText(h) or ""
                wraps = gw.getWindowsWithTitle(t) if t else []
                if wraps:
                    return wraps[0]
            except Exception:
                continue

    raise RuntimeError("카카오워크 앱이 실행 중이지 않습니다.")


def _wait_for_dialog(timeout: float = 4.0, poll: float = 0.25) -> bool:
    """Windows 파일 선택 다이얼로그가 foreground에 뜨는지 대기.

    safety_guard._matches(title, 'dialog') 로 판정 (한/영 키워드 모두).
    """
    try:
        from core.safety_guard import get_foreground_title, _matches
    except Exception:
        # safety_guard 미가용 환경 → 보수적으로 True 반환 (기존 동작 유지)
        time.sleep(min(timeout, 1.5))
        return True
    deadline = time.time() + timeout
    while time.time() < deadline:
        title = get_foreground_title()
        if _matches(title, "dialog"):
            return True
        time.sleep(poll)
    return False


def upload_file_to_room(file_path: Path, window) -> bool:
    """현재 열린 방에 파일 1개 업로드 (Ctrl+T 방식).

    안전 계약 (보강):
      - 시작 전 foreground가 카카오워크임을 확인 (아니면 False)
      - 입력란 클릭 직전 잔여 팝업 ESC 클리어
      - Ctrl+T 후 파일 다이얼로그가 실제로 떴는지 polling 확인 (고정 sleep 대신)
      - 각 단계 실패 시 반환 False → 호출자가 원본 보존/격리 가능

    학습 포인트: upload.{precheck,focus_input,ctrl_t,dialog_opened,paste_path,file_selected,sent}
    """
    from core.traced_actions import mark

    if not file_path.exists():
        mark("upload.file_missing", "fail", {"path": str(file_path)})
        return False

    # 0) 사전 안전 검증: 카카오워크가 foreground 여야 함.
    try:
        from core.safety_guard import pre_action_guard, get_foreground_title
    except Exception:
        pre_action_guard = None  # type: ignore[assignment]
        get_foreground_title = lambda: ""  # type: ignore[assignment]

    mark("upload.precheck", "before", {"file": file_path.name})
    if pre_action_guard is not None and not pre_action_guard("kakaowork", recover=True):
        fg_title = get_foreground_title()[:60]
        print(f"       [SAFE] '{file_path.name}' 카카오워크 foreground 아님 → 업로드 스킵", flush=True)
        mark("upload.precheck", "fail", {"title": fg_title})
        try:
            from core.upload_telemetry import log_upload_failure
            log_upload_failure(
                room="", file_name=file_path.name,
                step="upload.precheck",
                reason=f"foreground not kakaowork: '{fg_title}'",
                meta={"title": fg_title},
            )
        except Exception:
            pass
        return False
    mark("upload.precheck", "after")

    # 잔여 팝업/메뉴 정리 (ESC 2회 — 한 번은 IME, 한 번은 메뉴)
    try:
        pyautogui.press("escape")
        time.sleep(0.15)
        pyautogui.press("escape")
        time.sleep(0.15)
    except Exception:
        pass

    # 채팅 입력란 클릭 (포커스) - 실제 rect를 win32gui로 재확인 (pygetwindow 캐시 회피)
    try:
        import win32gui as _w32
        rect = _w32.GetWindowRect(window._hWnd)
        real_left, real_top = rect[0], rect[1]
        real_w, real_h = rect[2] - rect[0], rect[3] - rect[1]
    except Exception:
        real_left, real_top = window.left, window.top
        real_w, real_h = window.width, window.height
    chat_x = real_left + real_w // 3
    chat_y = real_top + real_h - 50
    mark("upload.focus_input", "before", {"xy": [chat_x, chat_y], "real_size": [real_w, real_h]})
    pyautogui.click(chat_x, chat_y)
    time.sleep(0.3)
    mark("upload.focus_input", "after")

    # Ctrl+T 직전 카카오워크 TOPMOST + 강제 포커스 + 입력란 재클릭
    # (IME hangul 토글 제거 - Ctrl+T 작동 방해 가능성)
    try:
        import win32gui as _w32
        from core.window_manager import force_foreground as _ff
        SWP = 0x0002 | 0x0001 | 0x0040
        _w32.SetWindowPos(window._hWnd, -1, 0, 0, 0, 0, SWP)
        time.sleep(0.1)
        _ff(window._hWnd)
        time.sleep(0.2)
        pyautogui.click(chat_x, chat_y)
        time.sleep(0.3)
    except Exception:
        pass

    # Ctrl+T → 파일 다이얼로그
    mark("upload.ctrl_t", "before")
    pyautogui.hotkey("ctrl", "t")
    mark("upload.ctrl_t", "after")

    # 다이얼로그가 실제로 뜰 때까지 대기 (최대 4초 polling)
    opened = _wait_for_dialog(timeout=4.0)
    if not opened:
        fg_title = get_foreground_title()[:60]
        print(f"       [WARN] '{file_path.name}' 파일 다이얼로그 미검출 - 업로드 실패 반환", flush=True)
        mark("upload.dialog_opened", "fail", {"title": fg_title})
        try:
            from core.upload_telemetry import log_upload_failure
            log_upload_failure(
                room="", file_name=file_path.name,
                step="upload.dialog_opened",
                reason=f"file dialog not detected within 4s; foreground='{fg_title}'",
                meta={"title": fg_title},
            )
        except Exception:
            pass
        # 혹시 모를 잔여 상태 정리
        try:
            pyautogui.press("escape")
        except Exception:
            pass
        return False
    mark("upload.dialog_opened", "after")

    # 파일 경로 붙여넣기
    mark("upload.paste_path", "before", {"path": str(file_path)})
    pyperclip.copy(str(file_path.resolve()))
    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.5)
    mark("upload.paste_path", "after")

    # Enter → 파일 선택
    mark("upload.file_selected", "before")
    pyautogui.press("enter")
    time.sleep(2.0)
    mark("upload.file_selected", "after")

    # Enter → 전송 확인 (파일 다이얼로그가 닫힌 상태에서만)
    mark("upload.sent", "before")
    pyautogui.press("enter")
    time.sleep(1.0)

    # 사후 확인: 다이얼로그가 아직 떠 있으면 업로드가 미완료 → 실패
    try:
        from core.safety_guard import _matches
        still_dialog = _matches(get_foreground_title(), "dialog")
    except Exception:
        still_dialog = False
    if still_dialog:
        fg_title = get_foreground_title()[:60]
        print(f"       [WARN] '{file_path.name}' 전송 후에도 다이얼로그 잔존 - 실패 반환", flush=True)
        mark("upload.sent", "fail", {"title": fg_title})
        try:
            from core.upload_telemetry import log_upload_failure
            log_upload_failure(
                room="", file_name=file_path.name,
                step="upload.sent",
                reason=f"dialog still open after send; foreground='{fg_title}'",
                meta={"title": fg_title},
            )
        except Exception:
            pass
        try:
            pyautogui.press("escape")
        except Exception:
            pass
        return False
    mark("upload.sent", "after", {"file": file_path.name})
    # 성공 메트릭은 호출자(_upload_one)가 정확히 room을 알므로 거기서 기록.
    return True


_OCR_HEADER_MODEL_PRIMARY = "claude-haiku-4-5-20251001"
_OCR_HEADER_MODEL_FALLBACK = "claude-opus-4-7"


def _ocr_once(crop_path: Path, model: str, expected: str | None = None) -> str | None:
    """헤더 이미지를 한 번 OCR. expected가 주어지면 'X와 일치하는지' 질의."""
    import base64
    import os
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    try:
        import anthropic  # type: ignore
    except ImportError:
        return None
    try:
        with open(crop_path, "rb") as f:
            b = base64.standard_b64encode(f.read()).decode()
    except Exception:
        return None

    if expected:
        cand_list = _build_header_candidates(expected) if "_build_header_candidates" in globals() else [expected, f"[미러] {expected}"]
        cand_str = " / ".join(f"'{c}'" for c in cand_list[:5])
        prompt = (
            f"이 카카오워크 채팅방 헤더 이미지의 제목을 정확히 읽어주세요.\n"
            f"예상 후보: {cand_str}\n"
            f"이미지에 보이는 한글/기호/영문/숫자를 그대로 한 줄로만 반환하세요. "
            f"'NV'로 시작하는 영문+숫자 코드(예: NV01, NV04)가 있으면 반드시 포함해 주세요. "
            f"추측/수정/요약 금지. 글자가 흐리면 가장 그럴듯한 글자로 읽되 추가 설명 금지."
        )
    else:
        prompt = (
            "이 이미지에 보이는 채팅방 이름을 한 줄로만 반환. "
            "설명 없이, 이미지의 한글을 그대로. 이름이 안 보이면 빈 문자열."
        )

    try:
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model=model,
            max_tokens=80,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b}},
                    {"type": "text", "text": prompt},
                ],
            }],
        )
        return (resp.content[0].text or "").strip()
    except Exception as e:
        print(f"       [OCR] API 호출 실패 ({model.split('-')[1]}): {type(e).__name__}: {e}", flush=True)
        return None


def _ocr_chat_header(window, *, expected: str | None = None) -> str | None:
    """카카오워크 현재 열린 방의 상단 헤더(방 이름) OCR.
    Haiku 1차 → 매칭 실패 또는 빈 결과면 Opus 2차 재시도. 둘 다 실패시 None.
    """
    try:
        from PIL import ImageGrab
    except ImportError:
        print("       [OCR] PIL.ImageGrab 없음", flush=True)
        return None

    left = window.left + 320
    top = window.top + 30
    right = min(window.left + window.width - 30, left + 500)
    bottom = window.top + 95
    bbox = (left, top, right, bottom)
    print(f"       [OCR] 캡처영역 {bbox}", flush=True)

    try:
        img = ImageGrab.grab(bbox=bbox)
    except Exception as e:
        print(f"       [OCR] ImageGrab 실패: {e}", flush=True)
        return None

    crop = Path(__file__).parent.parent / "captures" / "kakaowork_header_ocr.png"
    try:
        crop.parent.mkdir(parents=True, exist_ok=True)
        img.save(crop)
    except Exception as e:
        print(f"       [OCR] 저장 실패: {e}", flush=True)
        return None

    primary = _ocr_once(crop, _OCR_HEADER_MODEL_PRIMARY, expected=expected)
    # Haiku 결과가 비었거나 expected 후보군 중 어떤 것과도 매칭 실패 + NV 코드도 없으면 Opus 2차
    def _primary_matches_any() -> bool:
        if not primary:
            return False
        if not expected:
            return True
        for c in _build_header_candidates(expected):
            if _rooms_match(primary, c):
                return True
        if _header_has_nv_code(primary, expected):
            return True
        return False

    if not _primary_matches_any():
        print(f"       [OCR] Haiku='{primary}' → Opus 재시도", flush=True)
        secondary = _ocr_once(crop, _OCR_HEADER_MODEL_FALLBACK, expected=expected)
        if secondary:
            return secondary
    return primary


def _rooms_match(a: str, b: str) -> bool:
    """정규화 후 동일/포함/퍼지 매칭 (한글 OCR 오인식 관용)."""
    import re
    from difflib import SequenceMatcher
    def norm(s):
        s = re.sub(r"[\s\[\]\(\)\.\-_\"'&+]+", "", s or "")
        return s.lower()
    na, nb = norm(a), norm(b)
    if not na or not nb:
        return False
    if na == nb or na in nb or nb in na:
        return True
    # 퍼지 매칭: OCR 오인식(예: "네노바"↔"네모바") 허용
    shorter = na if len(na) <= len(nb) else nb
    longer = nb if shorter is na else na
    if len(shorter) >= 3:
        best = 0.0
        span = len(shorter)
        for i in range(0, max(1, len(longer) - span + 1)):
            r = SequenceMatcher(None, shorter, longer[i:i + span]).ratio()
            if r > best:
                best = r
        if best >= 0.75:
            return True
    return SequenceMatcher(None, na, nb).ratio() >= 0.75


def upload_to_nv_room(kakaotalk_room_name: str, files: list[Path]):
    """
    카카오워크 미러 방에 파일 업로드.

    검증된 라우팅 시퀀스:
      1. Bot API bump → 미러방 목록 맨 위로
      2. 카카오워크 앱 활성화 → 첫 번째 방 클릭
      3. **OCR로 상단 헤더 방 이름 검증** — 일치 안 하면 ESC 후 중단
      4. 검증 통과 시에만 Ctrl+T 파일 업로드

    Args:
        kakaotalk_room_name: 카카오톡 원본 방 이름 (room_mapping.json 키)
        files: 업로드할 파일 목록
    """
    # NV 매핑(room_mapping_nv.json) 우선, 없으면 room_mapping.json fallback
    nv_mapping = _load_nv_mapping()
    info = nv_mapping.get(kakaotalk_room_name)
    conv_id = None
    label = kakaotalk_room_name

    if info:
        conv_id = info["conv_id"]
        label = info.get("nv_code", kakaotalk_room_name)
    else:
        # fallback: 기본 미러 매핑 사용
        try:
            import json as _json
            rm_path = DATA_DIR / "room_mapping.json"
            if rm_path.exists():
                rm = _json.load(open(rm_path, encoding="utf-8"))
                conv_id = rm.get(kakaotalk_room_name)
                # 공백 무시 fallback
                if not conv_id:
                    normalized = kakaotalk_room_name.replace(" ", "")
                    for k, v in rm.items():
                        if k.replace(" ", "") == normalized:
                            conv_id = v
                            break
        except Exception as e:
            print(f"       [WARN] room_mapping 로드 실패: {e}")

    if not conv_id:
        print(f"       [WARN] '{kakaotalk_room_name}' 미러 매핑 없음")
        return

    from core.traced_actions import mark

    # 1. Bot API → 방을 맨 위로 (+ 알림 메시지)
    mark("upload.bump_room", "before", {"conv_id": conv_id, "label": label})
    _send_bot_api(conv_id, f"[미러] {label} - 사진 {len(files)}장 전송 중")
    time.sleep(1.5)
    mark("upload.bump_room", "after")

    # 2. 카카오워크 앱 활성화
    try:
        window = find_kakaowork_window()
        mark("upload.app_activated", "after", {"left": window.left, "top": window.top})
    except Exception as e:
        print(f"       [ERROR] 워크 창 없음: {e}")
        mark("upload.app_activated", "fail", {"error": str(e)})
        return

    # 3. 첫 번째 방 클릭 + OCR 검증 + 재시도
    expected_label = kakaotalk_room_name
    verified = False
    for attempt in range(3):
        mark("upload.first_room_clicked", "before",
             {"xy": [window.left + FIRST_ROOM_X_OFFSET, window.top + FIRST_ROOM_Y_OFFSET], "attempt": attempt})
        pyautogui.click(window.left + FIRST_ROOM_X_OFFSET, window.top + FIRST_ROOM_Y_OFFSET)
        time.sleep(1.5)
        mark("upload.first_room_clicked", "after")

        # OCR로 헤더 방 이름 검증
        header = _ocr_chat_header(window, expected=expected_label)
        # NV## prefix / "[미러] " / raw / nv_name 모두 후보에 포함
        candidates = _build_header_candidates(expected_label)
        ok = any(_rooms_match(header or "", c) for c in candidates)
        # 한글 전체가 망가져도 NV 코드는 정확히 읽히는 경우가 많음 → 강매칭 보조
        if not ok and _header_has_nv_code(header or "", expected_label):
            ok = True
            print(f"       [OCR-NVCODE] 한글 MISMATCH이나 NV 코드 일치 → OK", flush=True)
        print(f"       [OCR] 헤더='{header}' 후보={candidates} → {'OK' if ok else 'MISMATCH'}", flush=True)
        if ok:
            verified = True
            mark("upload.room_verified", "after", {"header": header, "expected": expected_label})
            break

        # 불일치 → 한번 더 bump + 재시도
        print(f"       [VERIFY-RETRY] {attempt+1}/3 - bump 재시도", flush=True)
        mark("upload.room_verified", "fail", {"header": header, "expected": expected_label, "attempt": attempt})
        _send_bot_api(conv_id, f"[VERIFY-RETRY {attempt+1}] {label}")
        time.sleep(2.0)

    if not verified:
        print(f"       [ABORT] '{expected_label}' 방 검증 3회 실패 - 업로드 중단 (엉뚱한 방 방지)", flush=True)
        # 검증 실패 알림을 정확히 그 conv에 (Bot API)
        _send_bot_api(conv_id, f"⚠️ [업로드 중단] 카카오워크 앱이 다른 방을 띄우고 있어 사진 {len(files)}장 업로드 취소됨. 관리자 수동 처리 필요.")
        return

    # 4. 각 파일 업로드 (검증 후에만)
    for f in files:
        try:
            ok = upload_file_to_room(f, window)
            if ok:
                print(f"       [UPLOAD] {label} {f.name} OK")
            else:
                print(f"       [WARN] {f.name} upload failed")
        except Exception as e:
            print(f"       [ERROR] {label} {f.name}: {e}")


# ═══════════════════════════════════════════════════════════════════
# 채팅방 이름 변경 자동화 (⚙️ 톱니바퀴 → ✏️ 볼펜 → 입력)
# ═══════════════════════════════════════════════════════════════════

def rename_room_via_app(conv_id: str, new_name: str, *, dry_run: bool = False) -> bool:
    """
    워크 앱 UI 자동화로 채팅방 이름 변경.

    시퀀스:
      1) Bot API _bump → 대상 방을 목록 맨 위로
      2) 워크 앱 활성화 → 첫 번째 방 클릭 (window+80, 60)
      3) 채팅창 우상단 ⚙️ 톱니바퀴 클릭 → 채팅방 설정 패널 펼침
      4) 패널 안 채팅방 이름 옆 ✏️ 볼펜 클릭 → 입력란 활성화
      5) Ctrl+A 로 기존 텍스트 선택 → 클립보드 복사 → Ctrl+V → Enter
      6) ESC 로 패널 닫기

    좌표는 창 우측/상단 기준 상대값으로 환경변수 오버라이드 가능:
      NENOVA_GEAR_FROM_RIGHT / NENOVA_GEAR_FROM_TOP
      NENOVA_PENCIL_FROM_RIGHT / NENOVA_PENCIL_FROM_TOP
    """
    print(f"  [RENAME-APP] conv={conv_id} → '{new_name}' (dry_run={dry_run})", flush=True)

    # 1) bump
    if not dry_run:
        ok = _send_bot_api(conv_id, "⁣")  # invisible char (zero-width non-joiner U+2063)
        print(f"    [1/6] _bump OK={ok}", flush=True)
        time.sleep(1.2)

    # 2) 워크 앱 활성화 + 첫 방 클릭
    try:
        win = find_kakaowork_window()
    except Exception as e:
        print(f"    [ERROR] 워크 앱 창 없음: {e}", flush=True)
        return False
    print(f"    [2/6] 워크 앱 활성화: left={win.left}, top={win.top}, w={win.width}, h={win.height}", flush=True)

    first_xy = (win.left + FIRST_ROOM_X_OFFSET, win.top + FIRST_ROOM_Y_OFFSET)
    if not dry_run:
        pyautogui.click(*first_xy)
        time.sleep(1.2)
    print(f"    [2/6] 첫 방 클릭 xy={first_xy}", flush=True)

    # 3) ⚙️ 톱니바퀴 클릭
    gear_xy = (win.left + win.width - GEAR_FROM_RIGHT, win.top + GEAR_FROM_TOP)
    if not dry_run:
        pyautogui.click(*gear_xy)
        time.sleep(1.0)
    print(f"    [3/6] 톱니바퀴 클릭 xy={gear_xy}", flush=True)

    # 4) ✏️ 볼펜 클릭
    pencil_xy = (win.left + win.width - PENCIL_FROM_RIGHT, win.top + PENCIL_FROM_TOP)
    if not dry_run:
        pyautogui.click(*pencil_xy)
        time.sleep(0.8)
    print(f"    [4/6] 볼펜 클릭 xy={pencil_xy}", flush=True)

    # 5) Ctrl+A → Ctrl+V → Enter
    if not dry_run:
        pyautogui.hotkey('ctrl', 'a')
        time.sleep(0.2)
        pyperclip.copy(new_name)
        time.sleep(0.2)
        pyautogui.hotkey('ctrl', 'v')
        time.sleep(0.3)
        pyautogui.press('enter')
        time.sleep(0.8)
    print(f"    [5/6] 입력 + Enter (text='{new_name}')", flush=True)

    # 6) ESC 로 패널 닫기
    if not dry_run:
        pyautogui.press('escape')
        time.sleep(0.4)
    print(f"    [6/6] ESC 로 패널 닫기", flush=True)

    return True
