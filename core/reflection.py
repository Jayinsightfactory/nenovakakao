"""
주기적 자기 회고 (reflection).

monitor 세션 종료 시 또는 N cycle마다 호출:
1. 통계 수집 (사진 성공률, ABORT 횟수, 가드 발동 등)
2. Claude에게 "다음 fix 우선순위" 분석 요청
3. 마크다운 보고서 작성

목표: 매 사이클을 같은 패턴으로 반복하지 않고, 데이터 기반 자체 진화.
"""
from __future__ import annotations

import json
import os
import re
import time
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env", override=True)

DATA_DIR = ROOT / "data"
LOGS_FILE = ROOT / "logs_monitor.log"
REFLECT_REPORT = DATA_DIR / "reflection_report.md"
MODEL = "claude-sonnet-4-6"


def collect_stats() -> dict:
    """logs_monitor.log에서 핵심 지표 수집."""
    stats = {
        "사진_다운_요청": 0,
        "사진_다운_완료": 0,
        "사진_업로드_누락": 0,
        "vision_ABORT": 0,
        "CU_fallback_시도": 0,
        "RECOVER_트리거": 0,
        "SAFE_가드_실패": 0,
        "친구추가_발생": 0,
        "광고_발생": 0,
        "스윕_시작": 0,
        "스윕_스킵": 0,
        "idle": 0,
        "다운로드_실패_원인": Counter(),
    }
    if not LOGS_FILE.exists():
        return stats

    try:
        text = LOGS_FILE.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return stats

    for line in text.splitlines():
        if "[사진]" in line and "감지" in line:
            m = re.search(r"\[사진\]\s*(\d+)", line)
            if m:
                stats["사진_다운_요청"] += int(m.group(1))
        elif "사진 다운로드 완료" in line:
            m = re.search(r"(\d+)개 사진", line)
            if m:
                stats["사진_다운_완료"] += int(m.group(1))
        elif "워크 전송 완료" in line:
            m = re.search(r"누락\s*(\d+)", line)
            if m:
                stats["사진_업로드_누락"] += int(m.group(1))
        elif "[ABORT]" in line and "vision" in line:
            stats["vision_ABORT"] += 1
        elif "ABORT-FALLBACK" in line:
            stats["CU_fallback_시도"] += 1
        elif "[RECOVER]" in line and "감지" in line:
            stats["RECOVER_트리거"] += 1
        elif "[SAFE]" in line and "가드 실패" in line:
            stats["SAFE_가드_실패"] += 1
        elif "친구 추가" in line:
            stats["친구추가_발생"] += 1
        elif "광고" in line:
            stats["광고_발생"] += 1
        elif "전체 스윕 시작" in line:
            stats["스윕_시작"] += 1
        elif "스윕 스킵" in line:
            stats["스윕_스킵"] += 1
        elif "idle (변화 없음)" in line:
            stats["idle"] += 1
        elif "다운로드 실패" in line:
            m = re.search(r"실패 \(([^)]+)\)", line)
            if m:
                stats["다운로드_실패_원인"][m.group(1)] += 1

    # 핵심 지표 계산
    req = stats["사진_다운_요청"]
    done = stats["사진_다운_완료"]
    miss = stats["사진_업로드_누락"]
    if req > 0:
        stats["사진_다운_성공률"] = round(done / req * 100, 1)
        stats["사진_업로드_손실률"] = round(miss / req * 100, 1)
    else:
        stats["사진_다운_성공률"] = None
        stats["사진_업로드_손실률"] = None
    return stats


def ask_claude_for_priorities(stats: dict) -> str:
    """Claude에게 통계 보여주고 다음 fix 우선순위 추천 받음."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return "(ANTHROPIC_API_KEY 없음)"
    try:
        import anthropic  # type: ignore
    except ImportError:
        return "(anthropic 모듈 없음)"

    # 카운터는 dict로 변환
    safe_stats = {**stats}
    if isinstance(safe_stats.get("다운로드_실패_원인"), Counter):
        safe_stats["다운로드_실패_원인"] = dict(safe_stats["다운로드_실패_원인"])
    body = json.dumps(safe_stats, ensure_ascii=False, indent=2)

    prompt = (
        "카카오톡→카카오워크 자동화 시스템 통계입니다. 100%로 만드는 다음 fix 우선순위 3개를 "
        "한 줄씩 추천하세요. 코드 변경이 필요하면 어디(파일/함수)를 어떻게 고쳐야 하는지 구체적으로.\n\n"
        f"```json\n{body}\n```\n\n"
        "형식:\n"
        "1. [핵심 문제]: [수정 위치] — [개선 방향]\n"
        "2. ...\n"
        "3. ..."
    )
    try:
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model=MODEL,
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}],
        )
        return (resp.content[0].text or "").strip()
    except Exception as e:
        return f"(Claude 호출 실패: {type(e).__name__}: {e})"


def reflect_and_write_report() -> Path:
    """통계 수집 + Claude 분석 → reflection_report.md 작성."""
    stats = collect_stats()
    rec = ask_claude_for_priorities(stats)

    safe_stats = {**stats}
    if isinstance(safe_stats.get("다운로드_실패_원인"), Counter):
        safe_stats["다운로드_실패_원인"] = dict(safe_stats["다운로드_실패_원인"])

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Monitor 세션 회고",
        f"생성: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## 통계",
        "```json",
        json.dumps(safe_stats, ensure_ascii=False, indent=2),
        "```",
        "",
        "## Claude 권장 다음 fix 우선순위",
        rec,
        "",
    ]
    REFLECT_REPORT.write_text("\n".join(lines), encoding="utf-8")
    print("\n" + "=" * 60, flush=True)
    print("[REFLECT] 세션 회고 보고서:", flush=True)
    print(f"  - 사진 다운: {safe_stats.get('사진_다운_완료', 0)}/{safe_stats.get('사진_다운_요청', 0)} ({safe_stats.get('사진_다운_성공률')}%)", flush=True)
    print(f"  - 사진 업로드 누락: {safe_stats.get('사진_업로드_누락', 0)}장 ({safe_stats.get('사진_업로드_손실률')}%)", flush=True)
    print(f"  - vision ABORT: {safe_stats.get('vision_ABORT', 0)} / CU fallback: {safe_stats.get('CU_fallback_시도', 0)}", flush=True)
    print(f"  - SAFE 가드 실패: {safe_stats.get('SAFE_가드_실패', 0)} / RECOVER 트리거: {safe_stats.get('RECOVER_트리거', 0)}", flush=True)
    print(f"  - 친구추가/광고: {safe_stats.get('친구추가_발생', 0)} / {safe_stats.get('광고_발생', 0)}", flush=True)
    print(f"  - idle: {safe_stats.get('idle', 0)} / 스윕 스킵: {safe_stats.get('스윕_스킵', 0)} / 스윕 시작: {safe_stats.get('스윕_시작', 0)}", flush=True)
    print(f"\n[REFLECT] Claude 권장 fix:\n{rec}", flush=True)
    print(f"\n[REFLECT] 전체 보고서 → {REFLECT_REPORT}", flush=True)
    print("=" * 60 + "\n", flush=True)
    return REFLECT_REPORT


if __name__ == "__main__":
    reflect_and_write_report()
