"""
부작용 자동 감지·회복·학습이 통합된 안전한 마우스/키보드 액션 래퍼.

설계:
  - 모든 click/paste/hotkey 호출 직전: forbidden_coords / forbidden_sequences 차단 체크
  - 호출 직후: capture_state → diagnose → 부작용이면 recover + learn
  - critical 부작용은 SideEffectDetected 예외 발생 → 호출자 즉시 정지

사용 예:
    from core.safe_actions import safe_click, safe_paste, safe_hotkey, ForbiddenAction
    from core.side_effect_detector import SideEffectDetected

    win = focus_kakaotalk()
    origin = (win.left, win.top)

    try:
        safe_click(win.left + 140, win.top + 200, intent="채팅 리스트 첫 행 클릭",
                   kakaotalk_origin=origin)
        safe_paste("수입방", intent="검색바 입력")  # 이전 click 이 통합검색 영역이면 차단됨
    except ForbiddenAction as e:
        print(f"[BLOCKED] {e}")
    except SideEffectDetected as e:
        print(f"[HALT] {e}")
"""
from __future__ import annotations

import time

from core.side_effect_detector import (
    SideEffectDetected,
    capture_state,
    diagnose,
    is_coord_forbidden,
    is_sequence_forbidden,
    learn_side_effect,
    recover,
)
from core.stop_button import StopRequested, check_stop, set_status


class ForbiddenAction(AssertionError):
    """forbidden_coords / forbidden_sequences 룰에 걸린 시도."""


def _post_action_check(
    intent: str,
    coord: tuple[int, int] | None,
    before,
    kakaotalk_origin: tuple[int, int] | None,
    post_wait: float,
) -> None:
    """액션 후 진단 → 부작용이면 회복 + 학습 + halt 시 예외."""
    time.sleep(post_wait)
    after = capture_state()
    diag = diagnose(before, after)
    if not diag.has_side_effect:
        return
    print(
        f"  [SIDE-EFFECT] intent={intent!r} → kind={diag.side_effect_kind!r} "
        f"title={diag.detected_title!r} sev={diag.severity!r}",
        flush=True,
    )
    recover(diag)
    learn_side_effect(
        intent=intent,
        coord=coord,
        diag=diag,
        kakaotalk_origin=kakaotalk_origin,
    )
    if diag.should_halt:
        raise SideEffectDetected(
            f"{diag.side_effect_kind}: {diag.detected_title} (intent={intent!r})"
        )


def safe_click(
    x: int,
    y: int,
    *,
    intent: str,
    kakaotalk_origin: tuple[int, int] | None = None,
    button: str = "left",
    clicks: int = 1,
    post_wait: float = 0.5,
) -> None:
    """
    정지 체크 → forbidden 체크 → 클릭 → 부작용 진단.

    Args:
        kakaotalk_origin: 카톡 메인창의 (left, top). 좌표 기반 forbidden 체크에 필요.
    """
    import pyautogui

    check_stop()
    set_status(f"click({x},{y}) — {intent}")
    rule = is_coord_forbidden(x, y, kakaotalk_origin)
    if rule:
        raise ForbiddenAction(
            f"좌표 ({x},{y}) 는 차단 영역 '{rule.get('name')}'.\n"
            f"  사유: {rule.get('reason')}"
        )
    before = capture_state()
    if clicks == 2:
        pyautogui.doubleClick(x, y, button=button)
    else:
        pyautogui.click(x, y, button=button)
    _post_action_check(intent, (x, y), before, kakaotalk_origin, post_wait)


def safe_paste(
    text: str,
    *,
    intent: str,
    kakaotalk_origin: tuple[int, int] | None = None,
    post_wait: float = 0.5,
) -> None:
    """정지 체크 → 클립보드 paste → 부작용 진단."""
    import pyautogui
    import pyperclip

    check_stop()
    set_status(f"paste({text[:20]}...) — {intent}")
    before = capture_state()
    pyperclip.copy(text)
    pyautogui.hotkey("ctrl", "v")
    _post_action_check(intent, None, before, kakaotalk_origin, post_wait)


def safe_hotkey(
    *keys: str,
    intent: str,
    kakaotalk_origin: tuple[int, int] | None = None,
    post_wait: float = 0.5,
) -> None:
    """정지 체크 → forbidden 체크 → 단축키 → 부작용 진단."""
    import pyautogui

    check_stop()
    set_status(f"hotkey({'+'.join(keys)}) — {intent}")
    rule = is_sequence_forbidden(keys)
    if rule:
        raise ForbiddenAction(
            f"단축키 시퀀스 {keys} 는 차단됨 '{rule.get('name')}'.\n"
            f"  사유: {rule.get('reason')}"
        )
    before = capture_state()
    pyautogui.hotkey(*keys)
    _post_action_check(intent, None, before, kakaotalk_origin, post_wait)


def safe_press(
    key: str,
    *,
    intent: str,
    kakaotalk_origin: tuple[int, int] | None = None,
    post_wait: float = 0.4,
) -> None:
    """정지 체크 → 단일 키 입력 → 부작용 진단."""
    import pyautogui

    check_stop()
    set_status(f"press({key}) — {intent}")
    before = capture_state()
    pyautogui.press(key)
    _post_action_check(intent, None, before, kakaotalk_origin, post_wait)
