"""
pyautogui + win32 자동화 액션에 투명한 캡처·마킹 훅을 추가한 래퍼.

목적:
  - 모든 UI 자동화 스텝을 LearningRecorder로 기록
  - 각 스텝 후 AnchorVerifier로 예상 상태 검증
  - 실패 프레임을 failed_candidates/로 수집 → 다음 학습 세션에 반영
  - 스텝별 성공/실패/재시도/시간을 step_metrics.json에 누적

사용 예:
    from core.traced_actions import traced_click, traced_hotkey, mark, scoped_step

    with scoped_step("drawer.open_menu", expected_anchor="drawer.popup_visible"):
        traced_click(chat_rect[2] - 18, chat_rect[1] + 55)

    with scoped_step("drawer.hover_submenu"):
        traced_move(popup_cx, popup_cy + 82)
        time.sleep(1.5)

모든 함수는 녹화/검증이 비활성화되어도 정상 동작하는 no-op 폴백을 가진다.
"""
from __future__ import annotations

import json
import time
import traceback
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

import pyautogui

from core import learning_recorder

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data"
METRICS_FILE = DATA_DIR / "step_metrics.json"
FAILED_DIR = DATA_DIR / "anchor_candidates" / "failed"

# 자동 락 임계값: 연속 N회 성공 시 앵커 검증 스킵 (성능 + 안정화)
AUTO_LOCK_THRESHOLD = 20


# ═══════════════════════════════════════════════════════
# 메트릭 로드/저장
# ═══════════════════════════════════════════════════════

def _default_step() -> dict:
    return {
        "total": 0,
        "success": 0,
        "fail": 0,
        "retries_used": 0,
        "streak": 0,             # 연속 성공 카운트
        "locked": False,         # 자동 락
        "avg_time_ms": 0.0,
        "last_success_ts": 0.0,
        "last_fail_ts": 0.0,
        "last_fail_reason": "",
    }


def load_metrics() -> dict:
    if not METRICS_FILE.exists():
        return {}
    try:
        return json.loads(METRICS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_metrics(m: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    METRICS_FILE.write_text(
        json.dumps(m, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _record(step: str, success: bool, duration_ms: float, retries: int, reason: str = "") -> None:
    m = load_metrics()
    s = m.get(step, _default_step())
    s["total"] += 1
    s["retries_used"] += retries
    # 이동평균 (단순)
    if s["avg_time_ms"] == 0:
        s["avg_time_ms"] = duration_ms
    else:
        s["avg_time_ms"] = 0.7 * s["avg_time_ms"] + 0.3 * duration_ms

    if success:
        s["success"] += 1
        s["streak"] += 1
        s["last_success_ts"] = time.time()
        if s["streak"] >= AUTO_LOCK_THRESHOLD and not s["locked"]:
            s["locked"] = True
            print(f"[METRICS] {step} LOCK ({s['streak']} consec success)", flush=True)
    else:
        s["fail"] += 1
        s["streak"] = 0
        s["locked"] = False  # 실패 시 락 해제
        s["last_fail_ts"] = time.time()
        s["last_fail_reason"] = reason[:200]
    m[step] = s
    save_metrics(m)


def unlock_step(step: str) -> bool:
    """수동 락 해제 (재학습 대상으로 복귀)."""
    m = load_metrics()
    if step in m:
        m[step]["locked"] = False
        m[step]["streak"] = 0
        save_metrics(m)
        return True
    return False


def is_locked(step: str) -> bool:
    m = load_metrics()
    return bool(m.get(step, {}).get("locked", False))


# ═══════════════════════════════════════════════════════
# 실패 프레임 캡처
# ═══════════════════════════════════════════════════════

def _save_failed_frame(step: str, reason: str) -> Path | None:
    """현재 화면을 captures/failed_candidates/<step>/<ts>.png 로 저장."""
    try:
        import mss
        import cv2
        import numpy as np
    except ImportError:
        return None
    try:
        target_dir = FAILED_DIR / step
        target_dir.mkdir(parents=True, exist_ok=True)
        ts = int(time.time() * 1000)
        path = target_dir / f"{ts}.png"
        with mss.mss() as sct:
            img = np.array(sct.grab(sct.monitors[0]))
        cv2.imwrite(str(path), cv2.cvtColor(img, cv2.COLOR_BGRA2BGR))
        # reason 같이 저장
        (target_dir / f"{ts}.txt").write_text(reason[:500], encoding="utf-8")
        return path
    except Exception:
        return None


# ═══════════════════════════════════════════════════════
# 기본 마킹
# ═══════════════════════════════════════════════════════

def mark(step: str, phase: str, meta: dict | None = None) -> None:
    """learning_recorder.mark 프록시 + phase=='fail'이면 화면 자동 캡처."""
    try:
        learning_recorder.mark(step, phase, meta)
    except Exception:
        pass
    if phase == "fail":
        try:
            reason = json.dumps(meta or {}, ensure_ascii=False)[:300]
        except Exception:
            reason = str(meta)[:300]
        _save_failed_frame(step, reason)


# ═══════════════════════════════════════════════════════
# 액션 래퍼 (before mark → action → after mark)
# ═══════════════════════════════════════════════════════

def traced_click(x: int, y: int, *, step: str = "", meta: dict | None = None, button: str = "left") -> None:
    if step:
        mark(step, "before", meta)
    pyautogui.click(x, y, button=button)
    if step:
        mark(step, "after", meta)


def traced_double_click(x: int, y: int, *, step: str = "", meta: dict | None = None) -> None:
    if step:
        mark(step, "before", meta)
    pyautogui.doubleClick(x, y)
    if step:
        mark(step, "after", meta)


def traced_move(x: int, y: int, *, duration: float = 0.0, step: str = "", meta: dict | None = None) -> None:
    if step:
        mark(step, "before", meta)
    pyautogui.moveTo(x, y, duration=duration)
    if step:
        mark(step, "after", meta)


def traced_press(key: str, *, step: str = "", meta: dict | None = None) -> None:
    if step:
        mark(step, "before", meta)
    pyautogui.press(key)
    if step:
        mark(step, "after", meta)


def traced_hotkey(*keys: str, step: str = "", meta: dict | None = None) -> None:
    if step:
        mark(step, "before", meta)
    pyautogui.hotkey(*keys)
    if step:
        mark(step, "after", meta)


def traced_typewrite(text: str, *, interval: float = 0.02, step: str = "", meta: dict | None = None) -> None:
    if step:
        mark(step, "before", meta)
    pyautogui.typewrite(text, interval=interval)
    if step:
        mark(step, "after", meta)


# ═══════════════════════════════════════════════════════
# scoped_step — 스텝 단위 블록 실행 + 앵커 검증 + 재시도 + 메트릭
# ═══════════════════════════════════════════════════════

@contextmanager
def scoped_step(
    step: str,
    *,
    expected_anchor: str | None = None,
    anchor_timeout: float = 3.0,
    meta: dict | None = None,
):
    """
    with scoped_step("drawer.open_menu", expected_anchor="drawer.popup_visible"):
        traced_click(...)

    블록 종료 후:
      - expected_anchor가 있으면 AnchorVerifier.wait_for로 검증
      - 성공/실패를 메트릭에 누적
      - 실패 시 현재 프레임을 failed_candidates/에 저장
    """
    if is_locked(step):
        # 락된 스텝은 검증·녹화 스킵 (성능 우선)
        yield {"locked": True}
        return

    mark(step, "before", meta)
    t0 = time.time()
    ctx: dict[str, Any] = {"ok": False, "error": None, "locked": False}
    try:
        yield ctx
    except Exception as e:
        ctx["error"] = f"{type(e).__name__}: {e}"
        duration_ms = (time.time() - t0) * 1000
        mark(step, "fail", {"error": ctx["error"], **(meta or {})})
        _save_failed_frame(step, f"exception: {ctx['error']}\n{traceback.format_exc()}")
        _record(step, False, duration_ms, 0, ctx["error"])
        raise
    else:
        # 앵커 검증
        ok = True
        if expected_anchor:
            try:
                from core.anchor_verifier import get as get_verifier
                ok = get_verifier().wait_for(expected_anchor, timeout=anchor_timeout)
            except Exception as e:
                ok = False
                ctx["error"] = f"anchor error: {e}"
        duration_ms = (time.time() - t0) * 1000
        if ok:
            mark(step, "after", meta)
            _record(step, True, duration_ms, 0)
            ctx["ok"] = True
        else:
            mark(step, "fail", {"anchor": expected_anchor, **(meta or {})})
            _save_failed_frame(step, f"anchor wait_for timeout: {expected_anchor}")
            _record(step, False, duration_ms, 0, f"anchor {expected_anchor} not found")
            ctx["ok"] = False


def run_step(
    step: str,
    action: Callable[[], Any],
    *,
    expected_anchor: str | None = None,
    anchor_timeout: float = 3.0,
    retries: int = 2,
    retry_delay: float = 0.5,
    meta: dict | None = None,
) -> tuple[bool, Any]:
    """
    action()을 실행하고 expected_anchor 검증. 실패 시 retries만큼 재시도.

    Returns:
        (success, action_result)
    """
    if is_locked(step):
        try:
            return True, action()
        except Exception as e:
            # 락됐어도 예외는 실패로 집계
            _record(step, False, 0, 0, f"locked-but-exc: {e}")
            raise

    last_err = ""
    result: Any = None
    t0 = time.time()

    for attempt in range(retries + 1):
        mark(step, "before", meta)
        try:
            result = action()
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
            mark(step, "fail", {"attempt": attempt, "error": last_err, **(meta or {})})
            _save_failed_frame(step, f"attempt {attempt}: {last_err}\n{traceback.format_exc()}")
            if attempt >= retries:
                _record(step, False, (time.time() - t0) * 1000, attempt, last_err)
                return False, None
            time.sleep(retry_delay)
            continue

        # 앵커 검증
        if expected_anchor:
            try:
                from core.anchor_verifier import get as get_verifier
                if get_verifier().wait_for(expected_anchor, timeout=anchor_timeout):
                    mark(step, "after", meta)
                    _record(step, True, (time.time() - t0) * 1000, attempt)
                    return True, result
                last_err = f"anchor {expected_anchor} timeout"
            except Exception as e:
                last_err = f"anchor error: {e}"
            mark(step, "fail", {"attempt": attempt, "error": last_err, **(meta or {})})
            _save_failed_frame(step, f"attempt {attempt}: {last_err}")
            if attempt >= retries:
                _record(step, False, (time.time() - t0) * 1000, attempt, last_err)
                return False, None
            time.sleep(retry_delay)
        else:
            # 앵커 없음 = 성공으로 간주
            mark(step, "after", meta)
            _record(step, True, (time.time() - t0) * 1000, attempt)
            return True, result

    return False, None
