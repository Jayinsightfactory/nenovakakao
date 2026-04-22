"""
업로드 실패 텔레메트리 — 카카오워크 사진 업로드 단계의 실패를 누적 기록.

목적:
  - 매 실패 1건당 JSONL 한 줄로 적립 (ts, room, file, step, reason, frame_path)
  - traced_actions.mark(fail) 화면 캡처 + step_metrics 카운트와 동시 호출
  - reflection / failed_frame_analyzer 가 이 ledger 를 직접 소비

설계 원칙:
  - 메인 파이프라인을 절대 막지 않는다 (모든 호출이 try/except)
  - 한 번에 한 줄만 append (race condition 무시 — 단일 프로세스)
  - 14일 이상 묶음은 자동 archive (일자별 별도 파일로 회전)

사용:
    from core.upload_telemetry import log_upload_failure
    log_upload_failure(
        room="수입방",
        file_name="PHOTO_수입방_..._01.jpg",
        step="upload.dialog_opened",
        reason="dialog not detected within 4s",
        meta={"foreground_title": "MetaMask"},
    )
"""
from __future__ import annotations

import json
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data"
LEDGER_FILE = DATA_DIR / "upload_failures.jsonl"
ARCHIVE_DIR = DATA_DIR / "upload_failures_archive"

# 회전 임계치 (라인 수). 초과 시 일자별 archive 로 이동.
ROTATE_AFTER_LINES = 5000


def _save_frame(step: str, reason: str) -> str | None:
    """traced_actions._save_failed_frame 사용 (있으면), 결과 경로 문자열 반환."""
    try:
        from core.traced_actions import _save_failed_frame
        p = _save_failed_frame(step, reason)
        return str(p) if p else None
    except Exception:
        return None


def _record_metric(step: str, success: bool, reason: str = "") -> None:
    try:
        from core.traced_actions import _record
        _record(step, success, 0.0, 0, reason)
    except Exception:
        pass


def _maybe_rotate() -> None:
    """라인 수가 ROTATE_AFTER_LINES 초과 시 archive 로 이동."""
    if not LEDGER_FILE.exists():
        return
    try:
        with open(LEDGER_FILE, encoding="utf-8") as f:
            count = sum(1 for _ in f)
    except Exception:
        return
    if count < ROTATE_AFTER_LINES:
        return
    try:
        ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        target = ARCHIVE_DIR / f"upload_failures_{ts}.jsonl"
        LEDGER_FILE.rename(target)
    except Exception:
        pass


def log_upload_failure(
    *,
    room: str,
    file_name: str | None,
    step: str,
    reason: str,
    meta: dict[str, Any] | None = None,
    capture_frame: bool = True,
) -> None:
    """업로드 실패 1건을 ledger 에 append + 화면 캡처 + 메트릭 누적.

    절대 예외를 raise 하지 않는다 — 호출처의 main loop 보호 우선.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    frame_path = _save_frame(step, reason) if capture_frame else None
    entry = {
        "ts": time.time(),
        "ts_iso": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "room": room or "",
        "file": file_name or "",
        "step": step,
        "reason": (reason or "")[:300],
        "frame": frame_path,
        "meta": (meta or {}),
    }
    try:
        with open(LEDGER_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass
    _record_metric(step, success=False, reason=reason)
    _maybe_rotate()


def log_upload_success(
    *,
    room: str,
    file_name: str | None,
    step: str = "upload.sent",
    meta: dict[str, Any] | None = None,
) -> None:
    """성공도 메트릭에는 반영해야 streak/lock 산정이 정상."""
    _record_metric(step, success=True)


# ═════════════════════════════════════════════════════
# 분석 / 권장 (CLI)
# ═════════════════════════════════════════════════════

def _iter_recent(within_hours: int | None = 24) -> list[dict]:
    if not LEDGER_FILE.exists():
        return []
    cutoff = time.time() - (within_hours * 3600) if within_hours else 0
    out: list[dict] = []
    try:
        with open(LEDGER_FILE, encoding="utf-8") as f:
            for line in f:
                try:
                    e = json.loads(line)
                except Exception:
                    continue
                if e.get("ts", 0) >= cutoff:
                    out.append(e)
    except Exception:
        return []
    return out


def summarize(within_hours: int | None = 24) -> dict:
    """최근 within_hours 내 실패 요약. None이면 전체."""
    entries = _iter_recent(within_hours)
    by_step: Counter = Counter(e.get("step", "?") for e in entries)
    by_room: Counter = Counter(e.get("room", "?") for e in entries)
    by_reason: Counter = Counter(_short_reason(e.get("reason", "")) for e in entries)
    by_step_room: dict = defaultdict(Counter)
    for e in entries:
        by_step_room[e.get("step", "?")][e.get("room", "?")] += 1
    recommendations = _build_recommendations(by_step, by_room, by_reason)
    return {
        "window_hours": within_hours,
        "total_failures": len(entries),
        "by_step": dict(by_step.most_common()),
        "by_room": dict(by_room.most_common()),
        "by_reason": dict(by_reason.most_common(10)),
        "by_step_room": {s: dict(rc.most_common(5)) for s, rc in by_step_room.items()},
        "recommendations": recommendations,
    }


def _short_reason(s: str) -> str:
    s = (s or "").strip().lower()
    # 공통 토큰만 남겨서 군집화 (숫자/경로 노이즈 제거)
    import re
    s = re.sub(r"\d+", "#", s)
    s = re.sub(r"[a-z]:\\\S+", "<path>", s)
    return s[:80]


def _build_recommendations(
    by_step: Counter,
    by_room: Counter,
    by_reason: Counter,
) -> list[str]:
    recs: list[str] = []
    if not by_step:
        return ["실패 기록 없음. (또는 ledger 비활성)"]
    top_step, top_step_n = by_step.most_common(1)[0]
    recs.append(f"가장 자주 실패한 스텝: {top_step} ({top_step_n}건). "
                f"core.traced_actions.unlock_step('{top_step}') 후 학습 사이클 권장.")
    if by_room:
        top_room, top_room_n = by_room.most_common(1)[0]
        if top_room_n >= 3:
            recs.append(f"가장 자주 실패한 방: '{top_room}' ({top_room_n}건). "
                        f"room_mapping_nv.json 의 nv_code/nv_name 정확도 점검.")
    if by_reason:
        top_r, top_r_n = by_reason.most_common(1)[0]
        if top_r_n >= 3 and "dialog" in top_r:
            recs.append("파일 다이얼로그 미검출 다수. _wait_for_dialog timeout 을 6.0초로 상향 검토.")
        if top_r_n >= 3 and "foreground" in top_r:
            recs.append("foreground 가드 실패 다수. 다른 앱이 포커스 뺏는 시점 파악 (popup_auto_learner 적용).")
    return recs


def render_text(within_hours: int | None = 24) -> str:
    s = summarize(within_hours)
    lines = []
    lines.append(f"=== 업로드 실패 분석 (최근 {within_hours}h) ===")
    lines.append(f"총 실패: {s['total_failures']}건")
    lines.append("")
    lines.append("[스텝별]")
    for step, n in s["by_step"].items():
        lines.append(f"  {step:<30} {n}")
    lines.append("")
    lines.append("[방별]")
    for room, n in s["by_room"].items():
        lines.append(f"  {room:<30} {n}")
    lines.append("")
    lines.append("[원인 군집 top 10]")
    for reason, n in s["by_reason"].items():
        lines.append(f"  {n:>4}  {reason}")
    lines.append("")
    lines.append("[권장 조치]")
    for r in s["recommendations"]:
        lines.append(f"  - {r}")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    hours = int(sys.argv[1]) if len(sys.argv) > 1 else 24
    print(render_text(hours))
