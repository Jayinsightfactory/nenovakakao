"""
Claude Computer Use 기반 자율 회복.

우리 결정론 자동화가 실패한 시점(ctrl_s fail, 친구 추가 창 등)에 호출하면
Claude가 직접 화면을 보고 마우스/키보드를 조작해 정상 상태로 회복시킨다.

사용:
    from core.computer_use_recovery import recover
    ok = recover("카카오톡에 친구 추가 다이얼로그가 떠있음. 닫고 카톡 메인으로 돌아오게 해줘.")
"""
from __future__ import annotations

import base64
import io
import os
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env", override=True)

# Computer Use는 비용/지연이 크므로 Sonnet 권장 (Opus는 더 정밀하지만 비쌈)
RECOVERY_MODEL = "claude-sonnet-4-5"  # computer_20250124 도구 지원 모델

# 자율 학습용 결과 로그
SESSION_LOG = ROOT / "data" / "cu_recovery_log.jsonl"

# 비용 폭주 방지: 시간당 호출 한도
_call_history: list[float] = []
HOURLY_LIMIT = 30          # 시간당 최대 30회
CONSEC_FAIL_LIMIT = 5      # 연속 실패 5회 시 일정 쿨다운
_consec_fails: int = 0
_cooldown_until: float = 0.0
COOLDOWN_AFTER_FAILS = 1800  # 30분


def _check_quota() -> tuple[bool, str]:
    """호출 가능 여부 + 사유. False면 호출 거부."""
    global _call_history
    now = time.time()
    if now < _cooldown_until:
        return False, f"쿨다운 ({int(_cooldown_until - now)}s 남음)"
    _call_history = [t for t in _call_history if now - t < 3600]
    if len(_call_history) >= HOURLY_LIMIT:
        return False, f"시간당 한도 {HOURLY_LIMIT}회 초과"
    return True, ""


def _record_call(success: bool):
    global _call_history, _consec_fails, _cooldown_until
    _call_history.append(time.time())
    if success:
        _consec_fails = 0
    else:
        _consec_fails += 1
        if _consec_fails >= CONSEC_FAIL_LIMIT:
            _cooldown_until = time.time() + COOLDOWN_AFTER_FAILS
            _consec_fails = 0
            print(f"  [CU] 연속 실패 {CONSEC_FAIL_LIMIT}회 → {COOLDOWN_AFTER_FAILS}s 쿨다운", flush=True)
MAX_TOKENS = 4096
MAX_LOOP = 20          # 자율 액션 최대 횟수 (12→20 확장)
DISPLAY_W = 1920
DISPLAY_H = 1080


def _screenshot_b64() -> str | None:
    """현재 화면 → base64 PNG. mss 사용."""
    try:
        import mss
        from PIL import Image
        with mss.mss() as sct:
            img = sct.grab(sct.monitors[0])
            pil = Image.frombytes("RGB", img.size, img.rgb)
            # 1920x1080 표준에 맞춰 리사이즈 (Claude Computer Use 권장 해상도)
            if pil.size != (DISPLAY_W, DISPLAY_H):
                pil = pil.resize((DISPLAY_W, DISPLAY_H), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            pil.save(buf, format="PNG")
            return base64.standard_b64encode(buf.getvalue()).decode()
    except Exception as e:
        print(f"  [CU] screenshot 실패: {e}", flush=True)
        return None


def _execute_computer_action(action: str, params: dict) -> dict:
    """Claude Computer Use 도구 호출 → 실제 pyautogui 실행 → tool_result 반환.
    Returns: {"type": "tool_result", "content": [...]} 형식의 dict.
    """
    import pyautogui
    pyautogui.FAILSAFE = False

    try:
        if action == "screenshot":
            b64 = _screenshot_b64()
            if not b64:
                return {"type": "text", "text": "screenshot 실패"}
            return {
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": b64},
            }
        elif action == "left_click":
            coord = params.get("coordinate") or [0, 0]
            pyautogui.click(coord[0], coord[1])
            time.sleep(0.3)
            return {"type": "text", "text": f"left_click at {coord}"}
        elif action == "right_click":
            coord = params.get("coordinate") or [0, 0]
            pyautogui.rightClick(coord[0], coord[1])
            time.sleep(0.3)
            return {"type": "text", "text": f"right_click at {coord}"}
        elif action == "double_click":
            coord = params.get("coordinate") or [0, 0]
            pyautogui.doubleClick(coord[0], coord[1])
            time.sleep(0.3)
            return {"type": "text", "text": f"double_click at {coord}"}
        elif action == "triple_click":
            coord = params.get("coordinate") or [0, 0]
            pyautogui.tripleClick(coord[0], coord[1])
            time.sleep(0.3)
            return {"type": "text", "text": f"triple_click at {coord}"}
        elif action == "mouse_move":
            coord = params.get("coordinate") or [0, 0]
            pyautogui.moveTo(coord[0], coord[1])
            time.sleep(0.2)
            return {"type": "text", "text": f"mouse_move to {coord}"}
        elif action == "left_click_drag":
            start = params.get("start_coordinate") or params.get("coordinate") or [0, 0]
            end = params.get("coordinate") or [0, 0]
            pyautogui.moveTo(start[0], start[1])
            pyautogui.dragTo(end[0], end[1], button="left")
            time.sleep(0.3)
            return {"type": "text", "text": f"drag {start} → {end}"}
        elif action == "key":
            key = params.get("text", "")
            # "ctrl+s" 같은 조합도 처리
            if "+" in key:
                pyautogui.hotkey(*[k.strip() for k in key.split("+")])
            else:
                pyautogui.press(key)
            time.sleep(0.3)
            return {"type": "text", "text": f"key {key}"}
        elif action == "type":
            text = params.get("text", "")
            pyautogui.typewrite(text, interval=0.02)
            time.sleep(0.3)
            return {"type": "text", "text": f"typed {text[:40]}"}
        elif action == "cursor_position":
            x, y = pyautogui.position()
            return {"type": "text", "text": f"cursor at ({x}, {y})"}
        elif action == "wait":
            secs = float(params.get("duration", 1))
            time.sleep(min(secs, 5))
            return {"type": "text", "text": f"waited {secs}s"}
        else:
            return {"type": "text", "text": f"unsupported action: {action}"}
    except Exception as e:
        return {"type": "text", "text": f"action {action} 실패: {e}"}


def agentic_action(
    goal: str,
    *,
    max_loop: int = MAX_LOOP,
    success_marker: str = "DONE",
    extra_system: str = "",
) -> bool:
    """범용 agentic loop: 매 step Claude가 화면 보고 다음 액션 결정.
    Args:
        goal: 달성할 목표 (한글로 자세히)
        max_loop: 최대 액션 횟수
        success_marker: Claude가 이 단어로 시작하는 답변하면 성공
        extra_system: 시스템 프롬프트 추가
    Returns: 성공 여부
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return False
    try:
        import anthropic
    except ImportError:
        return False

    ok_quota, reason = _check_quota()
    if not ok_quota:
        print(f"  [AGENT] 호출 거부: {reason}", flush=True)
        return False

    print(f"  [AGENT] 목표: {goal[:80]}", flush=True)
    client = anthropic.Anthropic(api_key=api_key)

    base_system = (
        "카카오톡/카카오워크 자동화 도우미. 매 step에서 화면을 보고 목표 달성에 필요한 다음 액션 1개만 수행. "
        "사용자의 다른 작업창(브라우저, VSCode, Claude 등)은 절대 닫지 말 것. "
        "카톡 채팅방 분리창도 닫지 말 것 (메인창 활성화만). "
        f"목표 달성하면 도구 호출 없이 '{success_marker}' 한 단어로 답변."
    )
    system_prompt = base_system + ("\n" + extra_system if extra_system else "")

    messages: list[dict] = [{
        "role": "user",
        "content": [{"type": "text", "text": f"목표: {goal}\n현재 화면을 확인하고 첫 액션을 수행하세요."}],
    }]
    tools = [{
        "type": "computer_20250124",
        "name": "computer",
        "display_width_px": DISPLAY_W,
        "display_height_px": DISPLAY_H,
        "display_number": 1,
    }]

    for loop_i in range(max_loop):
        try:
            resp = client.beta.messages.create(
                model=RECOVERY_MODEL,
                max_tokens=MAX_TOKENS,
                system=system_prompt,
                tools=tools,
                messages=messages,
                extra_headers={"anthropic-beta": "computer-use-2025-01-24"},
            )
        except Exception as e:
            print(f"  [AGENT] API 실패: {type(e).__name__}: {e}", flush=True)
            _record_call(success=False)
            return False

        assistant_content: list[dict] = []
        tool_uses: list = []
        for block in resp.content:
            if block.type == "text":
                assistant_content.append({"type": "text", "text": block.text})
                if block.text.strip().upper().startswith(success_marker.upper()):
                    print(f"  [AGENT] {success_marker} (loop {loop_i + 1})", flush=True)
                    _record_call(success=True)
                    return True
            elif block.type == "tool_use":
                assistant_content.append({
                    "type": "tool_use", "id": block.id,
                    "name": block.name, "input": block.input,
                })
                tool_uses.append(block)
        messages.append({"role": "assistant", "content": assistant_content})

        if resp.stop_reason == "end_turn" and not tool_uses:
            _record_call(success=True)
            return True
        if not tool_uses:
            _record_call(success=False)
            return False

        tool_results = []
        for tu in tool_uses:
            action = tu.input.get("action", "")
            print(f"  [AGENT] {action} {dict((k, v) for k, v in tu.input.items() if k != 'action')}", flush=True)
            result_block = _execute_computer_action(action, tu.input)
            tool_results.append({
                "type": "tool_result", "tool_use_id": tu.id,
                "content": [result_block],
            })
        messages.append({"role": "user", "content": tool_results})

    print(f"  [AGENT] max_loop {max_loop} 도달", flush=True)
    _record_call(success=False)
    return False


def recover(situation: str, *, max_loop: int = MAX_LOOP) -> bool:
    """Claude Computer Use를 호출해 현재 화면을 자율적으로 정상화.
    Returns: 성공 여부.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("  [CU] ANTHROPIC_API_KEY 없음 — 회복 불가", flush=True)
        return False
    try:
        import anthropic
    except ImportError:
        print("  [CU] anthropic 모듈 없음", flush=True)
        return False

    # 비용 폭주 가드
    ok_quota, reason = _check_quota()
    if not ok_quota:
        print(f"  [CU] 호출 거부: {reason}", flush=True)
        return False

    print(f"  [CU] 자율 회복 시작: {situation[:80]}", flush=True)
    client = anthropic.Anthropic(api_key=api_key)

    system_prompt = (
        "당신은 카카오톡/카카오워크 자동화 도우미입니다. 현재 화면을 보고 자동화 흐름을 "
        "방해하는 요소(친구 추가/광고/팝업/외부창 포커스 등)를 닫고, 카카오톡 메인창을 "
        "활성화해 정상 상태로 만드세요. 다음 원칙을 지키세요:\n"
        "1. 사용자의 다른 작업창(브라우저, VSCode 등)은 절대 닫지 마세요.\n"
        "2. 카카오톡 자체 모달(친구 추가, 광고)은 닫기 X 버튼이나 ESC로 닫으세요.\n"
        "3. 카카오톡 채팅방 분리창은 닫지 마세요. 메인창만 활성화하면 됩니다.\n"
        "4. 작업 완료 후 더 할 게 없으면 도구 호출 없이 'DONE'이라고만 답하세요.\n"
        "5. 한 번에 한 액션씩 신중하게 진행하세요."
    )

    initial = {
        "role": "user",
        "content": [
            {"type": "text", "text": f"상황: {situation}\n현재 화면을 확인하고 정상화해주세요."},
        ],
    }

    messages: list[dict] = [initial]
    tools = [{
        "type": "computer_20250124",
        "name": "computer",
        "display_width_px": DISPLAY_W,
        "display_height_px": DISPLAY_H,
        "display_number": 1,
    }]

    for loop_i in range(max_loop):
        try:
            resp = client.beta.messages.create(
                model=RECOVERY_MODEL,
                max_tokens=MAX_TOKENS,
                system=system_prompt,
                tools=tools,
                messages=messages,
                extra_headers={"anthropic-beta": "computer-use-2025-01-24"},
            )
        except Exception as e:
            print(f"  [CU] API 호출 실패: {type(e).__name__}: {e}", flush=True)
            return False

        # 어시스턴트 응답을 messages에 추가
        assistant_content: list[dict] = []
        tool_uses: list[Any] = []
        for block in resp.content:
            if block.type == "text":
                assistant_content.append({"type": "text", "text": block.text})
                if block.text.strip().upper().startswith("DONE"):
                    print(f"  [CU] 회복 완료 (loop {loop_i + 1})", flush=True)
                    _record_call(success=True)
                    return True
            elif block.type == "tool_use":
                assistant_content.append({
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                })
                tool_uses.append(block)
        messages.append({"role": "assistant", "content": assistant_content})

        # stop_reason 확인
        if resp.stop_reason == "end_turn" and not tool_uses:
            print(f"  [CU] end_turn (loop {loop_i + 1})", flush=True)
            return True

        if not tool_uses:
            # 도구 호출 없는데 end_turn도 아님 — 종료
            print(f"  [CU] 도구 호출 없이 종료 (loop {loop_i + 1})", flush=True)
            return False

        # 모든 tool_use 실행 → tool_result 반환
        tool_results = []
        for tu in tool_uses:
            action = tu.input.get("action", "")
            print(f"  [CU] action={action} input={ {k: v for k, v in tu.input.items() if k != 'action'} }", flush=True)
            result_block = _execute_computer_action(action, tu.input)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tu.id,
                "content": [result_block],
            })
        messages.append({"role": "user", "content": tool_results})

    print(f"  [CU] max_loop {max_loop} 도달 — 회복 미완", flush=True)
    _record_call(success=False)
    return False
