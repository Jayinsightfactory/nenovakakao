"""
학습 현황 대시보드 — 다른 터미널에서 실시간 확인

Usage:
  python dashboard.py          # 1회 출력
  python dashboard.py live     # 실시간 갱신 (5초마다)
  python dashboard.py ai       # AI 분석 결과만 출력
"""
from __future__ import annotations

import json
import os
import sys
import time
import textwrap
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent
LEARNING_DIR = ROOT / "data" / "learning"
COLLECTED_DATA = ROOT / "data" / "collected_data.jsonl"

# 로컬 분석 파일
ANALYSIS_LOG = LEARNING_DIR / "analysis_log.jsonl"
PATTERNS_FILE = LEARNING_DIR / "patterns.json"
ROOM_PROFILES = LEARNING_DIR / "room_profiles.json"
MESSAGE_STATS = LEARNING_DIR / "message_stats.json"

# AI 분석 파일
ROOM_ANALYSIS_FILE = LEARNING_DIR / "room_analysis.json"
CROSS_ROOM_MAP_FILE = LEARNING_DIR / "cross_room_map.json"
AUTOMATION_FILE = LEARNING_DIR / "automation_opportunities.json"


def clear():
    os.system("cls" if os.name == "nt" else "clear")


def _load_json(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8", errors="ignore"))
        except json.JSONDecodeError:
            return {}
    return {}


def _count_lines(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for _ in path.read_text(encoding="utf-8", errors="ignore").strip().splitlines() if _.strip())


def _recent_logs(n: int = 10) -> list[dict]:
    if not ANALYSIS_LOG.exists():
        return []
    lines = ANALYSIS_LOG.read_text(encoding="utf-8", errors="ignore").strip().splitlines()
    result = []
    for line in lines[-n:]:
        try:
            result.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return result


def _wrap(text: str, width: int = 50, indent: int = 6) -> str:
    """긴 텍스트를 줄바꿈"""
    prefix = " " * indent
    lines = textwrap.wrap(text, width=width)
    return ("\n" + prefix).join(lines)


def render_local():
    """로컬 분석 대시보드"""
    stats = _load_json(MESSAGE_STATS)
    patterns = _load_json(PATTERNS_FILE)
    profiles = _load_json(ROOM_PROFILES)
    collected_count = _count_lines(COLLECTED_DATA)
    log_count = _count_lines(ANALYSIS_LOG)

    # 수집 현황
    print()
    print(f"  [수집 데이터]")
    print(f"    레코드:     {collected_count}")
    print(f"    방 수:      {stats.get('rooms_analyzed', '-')}")
    print(f"    갱신:       {stats.get('updated_at', '-')}")

    # 태그 분포
    tags = stats.get("global_tags", {})
    if tags:
        print()
        print(f"  [메시지 유형]")
        for tag, count in sorted(tags.items(), key=lambda x: -x[1]):
            bar = "#" * min(count // 10, 40)
            print(f"    {tag:12s} {count:5d}  {bar}")

    # 품목 빈도
    products = stats.get("global_products", {})
    if products:
        print()
        print(f"  [품목 빈도]")
        for prod, count in sorted(products.items(), key=lambda x: -x[1])[:10]:
            bar = "#" * min(count // 5, 30)
            print(f"    {prod:10s} {count:5d}  {bar}")

    # 발신자
    senders = stats.get("top_senders", {})
    if senders:
        print()
        print(f"  [주요 발신자]")
        for sender, count in sorted(senders.items(), key=lambda x: -x[1])[:8]:
            print(f"    {sender:15s} {count:5d}")

    # 방별 프로파일
    if profiles:
        print()
        print(f"  [방별 프로파일]")
        for room, prof in profiles.items():
            dominant = max(prof.get("tags", {"?": 0}), key=prof["tags"].get) if prof.get("tags") else "?"
            msgs = prof.get("parsed_messages", 0)
            seqs = prof.get("sequences", [])
            seq_str = ", ".join(seqs[:5]) if seqs else "-"
            print(f"    {room[:25]:25s}  msgs:{msgs:5d}  type:{dominant:10s}  seqs:{seq_str}")

    # 발견된 패턴
    rules = patterns.get("rules", [])
    if rules:
        print()
        print(f"  [발견 패턴] ({len(rules)}개)")
        for r in rules[:10]:
            if r.get("type") == "room_category":
                conf = r.get("confidence", 0)
                print(f"    {r['room'][:25]:25s} -> {r['dominant_type']:10s} (conf: {conf:.0%})")
            elif r.get("type") == "frequent_products":
                prods = list(r.get("products", {}).keys())[:5]
                print(f"    빈출 품목: {', '.join(prods)}")


def render_ai():
    """AI 분석 결과 대시보드"""
    room_analysis = _load_json(ROOM_ANALYSIS_FILE)
    cross_room = _load_json(CROSS_ROOM_MAP_FILE)
    automation = _load_json(AUTOMATION_FILE)

    if not room_analysis and not cross_room and not automation:
        print()
        print("  [AI 분석] 아직 실행되지 않음")
        print("    실행: python learning.py ai")
        return

    # 방별 AI 분석
    if room_analysis:
        print()
        print(f"  [AI 방별 심층 분석] ({len(room_analysis)}개 방)")
        print(f"  {'─' * 55}")
        for room_name, analysis in room_analysis.items():
            if analysis.get("건너뜀") or analysis.get("error"):
                continue

            purpose = analysis.get("방_목적", "분석 없음")
            importance = analysis.get("방_중요도", "?")
            summary = analysis.get("요약", "")

            print(f"\n    [{room_name}] (중요도: {importance})")
            print(f"      목적: {_wrap(purpose, 48, 12)}")

            # 트리거 이벤트
            triggers = analysis.get("트리거_이벤트", [])
            if triggers:
                print(f"      트리거:")
                for t in triggers[:3]:
                    if isinstance(t, dict):
                        freq = t.get("빈도", "")
                        print(f"        - {t.get('트리거', '?')} ({freq})")

            # 자동화 기회
            auto = analysis.get("자동화_기회", [])
            if auto:
                print(f"      자동화:")
                for a in auto[:2]:
                    if isinstance(a, dict):
                        pri = a.get("우선순위", "?")
                        print(f"        - [{pri}] {a.get('작업', '?')}")

            # 메시지 패턴
            patterns = analysis.get("메시지_패턴", [])
            if patterns:
                print(f"      패턴:")
                for p in patterns[:2]:
                    if isinstance(p, dict):
                        print(f"        - {p.get('패턴_이름', '?')}")

    # 방간 관계
    if cross_room and not cross_room.get("error"):
        print()
        print(f"  [AI 방간 관계 분석]")
        print(f"  {'─' * 55}")

        # 업무 흐름
        flows = cross_room.get("업무_흐름", [])
        if flows:
            print(f"\n    업무 흐름:")
            for flow in flows[:5]:
                if isinstance(flow, dict):
                    rooms = ", ".join(flow.get("관련_방", [])[:3])
                    print(f"      - {flow.get('흐름_이름', '?')}")
                    print(f"        방: {rooms}")

        # 핵심 허브
        hubs = cross_room.get("핵심_허브_방", [])
        if hubs:
            print(f"\n    핵심 허브 방:")
            for hub in hubs[:3]:
                if isinstance(hub, dict):
                    print(f"      - {hub.get('방', '?')}: {hub.get('역할', '?')}")

        # 병목 지점
        bottlenecks = cross_room.get("병목_지점", [])
        if bottlenecks:
            print(f"\n    병목 지점:")
            for b in bottlenecks[:3]:
                if isinstance(b, str):
                    print(f"      - {b}")

        # 전체 요약
        summary = cross_room.get("전체_요약", "")
        if summary:
            print(f"\n    전체 요약:")
            print(f"      {_wrap(summary, 48, 6)}")

    # 자동화 기회
    if automation and automation.get("기회_목록"):
        opps = automation["기회_목록"]
        print()
        print(f"  [자동화 기회] ({automation.get('총_기회', 0)}개)")
        print(f"  {'─' * 55}")
        print(f"    {automation.get('요약', '')}")
        print()

        for i, opp in enumerate(opps[:10], 1):
            if not isinstance(opp, dict):
                continue
            pri = opp.get("우선순위", opp.get("순위", "?"))
            task = opp.get("작업", "?")
            source = opp.get("출처_방", opp.get("관련_방", "?"))
            effect = opp.get("예상_효과", "")
            print(f"    {i:2d}. [{pri}] {task}")
            if isinstance(source, list):
                source = ", ".join(source)
            print(f"        출처: {source}")
            if effect:
                print(f"        효과: {effect}")


def render():
    """전체 대시보드 렌더링"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_count = _count_lines(ANALYSIS_LOG)

    print("=" * 60)
    print(f"  NENOVA AI AGENT - LEARNING DASHBOARD v2")
    print(f"  {now}")
    print("=" * 60)

    # 로컬 분석
    render_local()

    # AI 분석
    render_ai()

    # 최근 로그
    logs = _recent_logs(5)
    if logs:
        print()
        print(f"  [최근 활동] (로그: {log_count}건)")
        for log in logs:
            ts = log.get("timestamp", "")[-8:]
            evt = log.get("event", "?")
            detail = ""
            if "rooms" in log:
                detail = f" (방: {log['rooms']})"
            elif "rooms_analyzed" in log:
                detail = f" (방: {log['rooms_analyzed']})"
            print(f"    {ts}  {evt}{detail}")

    print()
    print("=" * 60)


def live_mode(interval: int = 5):
    """실시간 갱신"""
    try:
        while True:
            clear()
            render()
            print(f"  ({interval}초마다 갱신, Ctrl+C 중지)")
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n  대시보드 중지.")


if __name__ == "__main__":
    args = sys.argv[1:]
    if "live" in args:
        live_mode()
    elif "ai" in args:
        clear()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print("=" * 60)
        print(f"  NENOVA AI AGENT - AI ANALYSIS DASHBOARD")
        print(f"  {now}")
        print("=" * 60)
        render_ai()
        print()
        print("=" * 60)
    else:
        render()
