"""
자동화 안전 가드.

pre_action_guard(expected="kakaotalk"|"kakaowork"|"dialog"|"viewer"):
  현재 foreground가 의도한 대상인지 검증.
  위험한 창이 떠있으면 자동 처치(ESC) → 재검증.
  여전히 안전하지 않으면 False (호출자가 액션 스킵).

이 한 함수만 모든 자동화 액션 직전에 호출하면 끝.
"""
from __future__ import annotations

import time

KAKAOTALK_PATTERNS = ("KakaoTalk", "카카오톡")
KAKAOWORK_PATTERNS = ("Kakao Work", "카카오워크", "Kakaowork")
DIALOG_PATTERNS = ("저장", "Save", "다른 이름", "확인", "Open", "열기")
EXTERNAL_PATTERNS = (" - Chrome", " - Edge", " - Firefox", "VS Code",
                     "Notepad", "Explorer", "Cmd ", "PowerShell", "Terminal",
                     "Code -", "MetaMask")


def get_foreground_title() -> str:
    try:
        import win32gui
        return win32gui.GetWindowText(win32gui.GetForegroundWindow()) or ""
    except Exception:
        return ""


def _matches(title: str, expected: str) -> bool:
    if not title:
        return False
    if expected == "kakaotalk":
        # 메인 또는 채팅 분리창 (방 이름 매칭은 호출자가 검증)
        if any(k in title for k in KAKAOTALK_PATTERNS):
            return True
        # 카톡 채팅 분리창 — 보통 방 이름이 그대로 title
        try:
            import json
            from pathlib import Path
            sel = Path(__file__).parent.parent / "data" / "selected_rooms.json"
            if sel.exists():
                rooms = json.loads(sel.read_text(encoding="utf-8"))
                names = {r["name"] if isinstance(r, dict) else r for r in rooms}
                if title in names:
                    return True
        except Exception:
            pass
        return False
    if expected == "kakaowork":
        return any(k in title for k in KAKAOWORK_PATTERNS)
    if expected == "dialog":
        return any(k in title for k in DIALOG_PATTERNS)
    if expected == "viewer":
        # 카톡 사진 뷰어: "발신자 YYYY-MM-DD" 형식
        import re
        return bool(re.search(r"\d{4}-\d{2}-\d{2}", title))
    return False


def _try_close_risk_popup(title: str) -> bool:
    """위험 키워드 매칭되면 다단계 종료 시도: ESC → WM_CLOSE → Alt+F4.
    카톡 내부 모달은 win32로 안 잡히므로 좌표 기반 X 버튼 클릭도 시도.
    """
    try:
        from core.window_manager import POPUP_KEYWORDS
        from core.popup_auto_learner import load_learned_keywords
    except Exception:
        return False
    risk_kws = list({*POPUP_KEYWORDS, *load_learned_keywords()})
    if not any(kw and kw in title for kw in risk_kws):
        return False

    print(f"  [SAFE] 위험 창 '{title[:60]}' → 다단계 종료 시도", flush=True)
    try:
        import pyautogui
        import win32gui
        import win32con

        # 1. ESC
        pyautogui.press("escape")
        time.sleep(0.4)
        if get_foreground_title() != title:
            return True

        # 2. WM_CLOSE (별도 win32 창인 경우)
        try:
            hwnd = win32gui.GetForegroundWindow()
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
            time.sleep(0.5)
            if get_foreground_title() != title:
                return True
        except Exception:
            pass

        # 3. Alt+F4 (강제 종료)
        pyautogui.hotkey("alt", "f4")
        time.sleep(0.5)
        if get_foreground_title() != title:
            return True

        # 4. 카톡 내부 모달일 가능성 — 카톡 메인창의 모달 X 버튼 좌표 클릭
        # 친구 추가 다이얼로그 X 버튼: 카톡 창 (28,106) 기준 모달 우상단 약 (587, 184)
        # 정확하지 않아도 ESC 한 번 더로 안전 처리
        pyautogui.press("escape")
        time.sleep(0.4)
        return get_foreground_title() != title
    except Exception as e:
        print(f"  [SAFE] 종료 시도 에러: {e}", flush=True)
        return False


def pre_action_guard(expected: str = "kakaotalk", *, recover: bool = True) -> bool:
    """액션 직전 안전 검증 + 자동 처치.
    Args:
        expected: 'kakaotalk' | 'kakaowork' | 'dialog' | 'viewer'
        recover: 외부 창이면 카톡/워크 재활성화 시도
    Returns: True면 액션 진행 가능, False면 호출자가 스킵해야 함.
    """
    title = get_foreground_title()
    if _matches(title, expected):
        return True

    # 1. 위험 팝업 키워드 매칭 시 ESC 처치
    if _try_close_risk_popup(title):
        title = get_foreground_title()
        if _matches(title, expected):
            return True

    # 2. 외부 앱이면 카톡/워크 재활성화
    if any(p in title for p in EXTERNAL_PATTERNS) or not _matches(title, expected):
        if recover:
            try:
                if expected == "kakaowork":
                    from core.kakaowork_app import find_kakaowork_window
                    find_kakaowork_window()
                else:
                    from core.window_detector import activate_kakaotalk
                    activate_kakaotalk()
                time.sleep(0.4)
            except Exception:
                pass
        title = get_foreground_title()
        if _matches(title, expected):
            return True

    print(f"  [SAFE] 가드 실패 (expected={expected}, current='{title[:60]}') → 액션 스킵", flush=True)
    return False


# 하위호환
def is_safe_foreground() -> tuple[bool, str]:
    t = get_foreground_title()
    return _matches(t, "kakaotalk") or _matches(t, "kakaowork") or _matches(t, "dialog"), t


def ensure_safe_kakaotalk() -> tuple[bool, str]:
    ok = pre_action_guard("kakaotalk")
    return ok, get_foreground_title()


# ═══════════════════════════════════════════════════════
# 키 입력 직전 매번 검증하는 안전 wrapper
# 검증 실패 시 SafetyAbort 발생 → 호출자가 try/except로 처리
# ═══════════════════════════════════════════════════════

class SafetyAbort(Exception):
    """안전 가드가 액션을 막을 때 발생."""


def _check_or_abort(expected: str, op: str):
    """검증 실패 시 SafetyAbort. expected가 'any'면 학습된 위험 키워드만 차단."""
    title = get_foreground_title()
    if expected == "any":
        # 학습된 위험 키워드(친구 추가/비밀번호/광고 등)만 차단.
        # POPUP_KEYWORDS의 일반 단어("저장", "사진" 등)는 차단 안 함.
        try:
            from core.popup_auto_learner import load_learned_keywords
            risk = load_learned_keywords()
        except Exception:
            risk = []
        for kw in risk:
            if kw and kw in title:
                raise SafetyAbort(f"{op} 차단 — 위험 창 '{title[:50]}'")
        return
    if not _matches(title, expected):
        # 1회 처치 시도
        if not _try_close_risk_popup(title):
            raise SafetyAbort(f"{op} 차단 — expected={expected}, current='{title[:50]}'")
        # 처치 후 재검증
        if not _matches(get_foreground_title(), expected):
            raise SafetyAbort(f"{op} 차단 — 처치 후도 expected={expected} 아님")


def safe_press(key: str, *, expected: str = "any"):
    import pyautogui
    _check_or_abort(expected, f"press({key})")
    pyautogui.press(key)


def safe_hotkey(*keys: str, expected: str = "any"):
    import pyautogui
    _check_or_abort(expected, f"hotkey({'+'.join(keys)})")
    pyautogui.hotkey(*keys)


def safe_paste(text: str, *, expected: str = "any"):
    """클립보드 복사 + Ctrl+V (검증 후 매 단계)."""
    import pyautogui
    try:
        import pyperclip
    except ImportError:
        raise SafetyAbort("pyperclip 없음")
    _check_or_abort(expected, "paste")
    pyperclip.copy(text)
    _check_or_abort(expected, "paste(Ctrl+V)")
    pyautogui.hotkey("ctrl", "v")


def safe_typewrite(text: str, *, interval: float = 0.02, expected: str = "any"):
    import pyautogui
    _check_or_abort(expected, f"typewrite({len(text)} chars)")
    pyautogui.typewrite(text, interval=interval)


def safe_click(x: int, y: int, *, expected: str = "any"):
    import pyautogui
    _check_or_abort(expected, f"click({x},{y})")
    pyautogui.click(x, y)
