"""
실패 프레임 자동 분석 보고서.

`data/anchor_candidates/failed/<step>/<ts>.png` 폴더를 순회하며
스텝별 최신 N개 프레임을 Claude Vision으로 분석한다. 결과:

1. `data/failed_frame_report.md` — 사람이 읽을 마크다운
2. 콘솔 요약 출력 (스텝별 한 줄)

사용:
    from core.failed_frame_analyzer import analyze_recent
    analyze_recent(within_seconds=3600)  # 최근 1시간 내 실패만
"""
from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env", override=True)

DATA_DIR = ROOT / "data"
FAILED_DIR = DATA_DIR / "anchor_candidates" / "failed"
REPORT_FILE = DATA_DIR / "failed_frame_report.md"

ANALYZER_MODEL = "claude-haiku-4-5-20251001"
PER_STEP_LIMIT = 2  # 스텝별 최대 분석 프레임 수


def _analyze_one(image_path: Path) -> str:
    """프레임 1개를 Claude Vision으로 분석. 한 줄 요약 반환."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return "[no API key]"
    try:
        import anthropic  # type: ignore
    except ImportError:
        return "[no anthropic]"
    try:
        b = base64.standard_b64encode(image_path.read_bytes()).decode()
    except Exception as e:
        return f"[read err: {e}]"

    prompt = (
        "이 화면 캡처를 분석해 카카오톡/카카오워크 자동화 실패 원인을 알려주세요. JSON 한 줄로:\n"
        '{"summary": "한 줄 원인", "popup_keyword": "닫을 창의 제목 키워드(2-15자) 또는 빈 문자열"}\n\n'
        "popup_keyword 등록 조건 (반드시):\n"
        "1) 카카오톡 또는 카카오워크 자체 다이얼로그/모달일 것\n"
        "2) 자동화 진행을 실제로 막는 것 (포커스 강탈, 입력 차단 등)\n\n"
        "popup_keyword 빈 문자열 처리:\n"
        "- 외부 브라우저(Chrome/Edge/Firefox), 타 앱(VSCode, Notepad 등)이면 빈 문자열\n"
        "- 카카오톡/카카오워크 메인창 자체면 빈 문자열\n"
        "- 정상 채팅창/방 분리창이면 빈 문자열\n"
        "- 화면이 정상이면 빈 문자열\n\n"
        "summary는 항상 한 줄로 객관적 사실 (외부 창이면 '외부 [Chrome] 창 — 차단 원인 아님' 식)."
    )
    try:
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model=ANALYZER_MODEL,
            max_tokens=200,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image",
                     "source": {"type": "base64", "media_type": "image/png", "data": b}},
                    {"type": "text", "text": prompt},
                ],
            }],
        )
        raw = (resp.content[0].text or "").strip()
        # JSON 파싱 시도; 실패 시 원문 반환
        try:
            import re as _re
            m = _re.search(r"\{.*\}", raw, _re.DOTALL)
            if m:
                obj = json.loads(m.group(0))
                summary = obj.get("summary", "").strip()
                kw = obj.get("popup_keyword", "").strip()
                # 학습용 키워드를 summary 끝에 [[KW:...]] 마커로 부착
                if kw:
                    return f"{summary} [[KW:{kw}]]"[:400]
                return summary[:300]
        except Exception:
            pass
        return raw.replace("\n", " ")[:300]
    except Exception as e:
        return f"[api err: {type(e).__name__}: {e}]"


def analyze_recent(within_seconds: int = 3600) -> dict:
    """최근 within_seconds 내 실패 프레임만 스텝별 PER_STEP_LIMIT개 분석.
    Returns: {step: [{frame, ts, summary, reason}, ...]}
    """
    if not FAILED_DIR.exists():
        print("[ANALYZER] failed/ 디렉토리 없음 - 분석 대상 0개")
        return {}

    cutoff = time.time() - within_seconds
    report: dict[str, list[dict]] = {}

    for step_dir in sorted(FAILED_DIR.iterdir()):
        if not step_dir.is_dir():
            continue
        pngs = sorted(
            (p for p in step_dir.glob("*.png") if p.stat().st_mtime >= cutoff),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )[:PER_STEP_LIMIT]
        if not pngs:
            continue
        rows: list[dict] = []
        for p in pngs:
            reason_path = p.with_suffix(".txt")
            reason = reason_path.read_text(encoding="utf-8", errors="ignore")[:200] \
                if reason_path.exists() else ""
            summary = _analyze_one(p)
            rows.append({
                "frame": p.name,
                "ts": int(p.stat().st_mtime),
                "reason": reason,
                "summary": summary,
            })
            print(f"  [ANALYZER] {step_dir.name}/{p.name} → {summary[:120]}", flush=True)
        report[step_dir.name] = rows

    if report:
        write_report(report)
        # 자체 학습: 팝업 키워드 자동 추출 + 영구 저장
        try:
            from core.popup_auto_learner import learn_from_report
            learn_from_report(report)
        except Exception as e:
            print(f"[AUTO-LEARN] 학습 실패: {e}", flush=True)
    return report


def write_report(report: dict) -> Path:
    """마크다운 보고서 작성."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# 실패 프레임 자동 분석 보고서",
        f"생성: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
    ]
    for step, rows in sorted(report.items()):
        lines.append(f"## `{step}` ({len(rows)}건)")
        for r in rows:
            ts = time.strftime("%H:%M:%S", time.localtime(r["ts"]))
            lines.append(f"- **{ts}** `{r['frame']}` → {r['summary']}")
            if r["reason"]:
                lines.append(f"  - reason: `{r['reason']}`")
        lines.append("")
    REPORT_FILE.write_text("\n".join(lines), encoding="utf-8")
    print(f"[ANALYZER] 보고서 작성 → {REPORT_FILE}", flush=True)
    return REPORT_FILE


if __name__ == "__main__":
    import sys
    secs = int(sys.argv[1]) if len(sys.argv) > 1 else 3600
    analyze_recent(secs)
