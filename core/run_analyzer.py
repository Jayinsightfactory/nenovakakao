"""
실행 로그 자동 분석 — 이슈 패턴 추출 + 누적 학습.

원시 actions 로그를 건드리지 않고, 별도 채널(issues.jsonl + learning.md)에
'왜 이런 일이 생겼나'와 '어떻게 해결했나'를 기록.

구조:
  logs/issues.jsonl        : 이슈 이벤트 append-only (구조화 JSON)
  logs/learning.md         : 누적 패턴 설명 (사람이 읽기)

사용:
  from core.run_analyzer import log_issue, summarize_recent
  log_issue("dialog_not_appeared", room="수입방", context={...})
  summarize_recent(window_cycles=1)  # 이번 사이클 요약
"""
from __future__ import annotations

import json
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional

LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
ISSUES_FILE = LOG_DIR / "issues.jsonl"
LEARNING_FILE = LOG_DIR / "learning.md"


# 이슈 유형 정의 + 자동 학습 코멘트
ISSUE_KNOWLEDGE: dict[str, dict] = {
    "chat_didnt_open": {
        "label": "분리창 미열림",
        "likely_cause": "doubleClick 직후 포커스 경쟁으로 카톡이 분리창을 생성 안 함, 또는 해당 행이 빈 영역/광고",
        "fix_attempted": "click_room에서 EnumWindows로 새 분리창 감지 + force_foreground",
        "fix_status": "부분적 해결 — 포커스 경쟁 여전",
    },
    "dialog_not_appeared": {
        "label": "저장 다이얼로그 미출현",
        "likely_cause": "Ctrl+S 시점에 카톡 본창이 포커스 (분리창 아님)",
        "fix_attempted": "chat_hwnd force_foreground 8회 재시도 후 Ctrl+S",
        "fix_status": "포커스 경쟁 상황엔 여전히 실패 가능",
    },
    "focus_stolen": {
        "label": "포커스 탈취됨",
        "likely_cause": "Claude/로그창/Program Manager가 자동화 중 포그라운드 진입",
        "fix_attempted": "force_foreground (AttachThreadInput), 로그창 WS_EX_NOACTIVATE",
        "fix_status": "일부 해결 — Claude 응답 시 여전히 간헐적 탈취",
    },
    "safety_abort": {
        "label": "Safety guard abort",
        "likely_cause": "액션 직전 foreground가 expected와 불일치",
        "fix_attempted": "다이얼로그 구간은 원시 pyautogui 사용",
        "fix_status": "구조 개선됨",
    },
    "program_manager_focus": {
        "label": "바탕화면(Program Manager) 포커스",
        "likely_cause": "창 전환 중 순간적으로 포그라운드가 비어 바탕화면이 잡힘",
        "fix_attempted": "Enter/단축키 자동 실행 시 위험 (시스템 종료 팝업 위험)",
        "fix_status": "⚠️ 위험 — Enter 차단 or 재시도 필요",
    },
    "misidentified_as_room": {
        "label": "시스템 창을 방으로 오인",
        "likely_cause": "Program Manager, Explorer 등 시스템 창이 분리창 감지에 잡힘",
        "fix_attempted": "_list_chat_room_titles EXCLUDED 키워드 확장",
        "fix_status": "해결됨 (2026-04-18) — 'Program Manager', 'Windows', 'Explorer' 등 추가",
    },
    "unread_filter_stuck": {
        "label": "안읽음 필터 갱신 실패",
        "likely_cause": "방 닫아도 필터에 남아있음 — 해당 방의 다른 unread 메시지 or 카톡이 unread 유지 or 포커스 문제",
        "fix_attempted": "필터 재클릭 (전체 → 안읽음)",
        "fix_status": "자동 복구 시도 중",
    },
    "drawer_popup_wrong_position": {
        "label": "서랍 팝업이 엉뚱한 위치",
        "likely_cause": "≡ 클릭은 했는데 실제 메뉴 대신 다른 EVA_Menu (토스트/시스템 팝업)가 감지됨",
        "fix_attempted": "팝업 거리 임계값 350px 필터 추가 (클릭 위치에서 멀면 제외)",
        "fix_status": "2026-04-20 추가 — 임계값 추가됨",
    },
    "kakaowork_window_size_mismatch": {
        "label": "카카오워크 창 크기 요청 무시됨",
        "likely_cause": "KakaoWork 최소 크기 제약으로 요청한 900x900 대신 900x1100로 유지",
        "fix_attempted": "upload_file_to_room에서 win32gui.GetWindowRect로 실제 rect 재확인",
        "fix_status": "2026-04-20 수정됨",
    },
    "no_change_repeat": {
        "label": "같은 방 변경없음 반복",
        "likely_cause": "MD5 해시 동일 → read_and_process_saved_file None 반환 → processed 미추가 → 재클릭",
        "fix_attempted": "_no_change 플래그로 processed에 추가",
        "fix_status": "해결됨",
    },
    "duplicate_skip": {
        "label": "중복 방 (processed_this_cycle 매칭)",
        "likely_cause": "카톡 리스트 재정렬 or 동일 스크롤 위치에서 같은 방",
        "fix_attempted": "Ctrl+S 전 조기 스킵",
        "fix_status": "해결됨",
    },
    "not_target_room": {
        "label": "감시 대상 아닌 방 열림",
        "likely_cause": "DM이나 공지방이 특정 Y 좌표에 위치. selected_rooms 밖.",
        "fix_attempted": "Ctrl+S 후 스킵 (현재)",
        "fix_status": "개선 여지 — Ctrl+S 전에 selected_rooms 체크로 조기 스킵 가능",
    },
}


def log_issue(
    issue_type: str,
    *,
    cycle: Optional[int] = None,
    page: Optional[int] = None,
    row: Optional[int] = None,
    room: Optional[str] = None,
    context: Optional[dict] = None,
) -> None:
    """이슈 한 건을 issues.jsonl에 기록."""
    entry = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "type": issue_type,
        "cycle": cycle,
        "page": page,
        "row": row,
        "room": room,
        "context": context or {},
    }
    try:
        with open(ISSUES_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _load_issues(since_cycle: Optional[int] = None) -> list[dict]:
    """issues.jsonl 읽기. since_cycle 주면 그 사이클 이상만."""
    out: list[dict] = []
    if not ISSUES_FILE.exists():
        return out
    try:
        with open(ISSUES_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except Exception:
                    continue
                if since_cycle is not None and (e.get("cycle") or 0) < since_cycle:
                    continue
                out.append(e)
    except Exception:
        pass
    return out


def summarize_cycle(cycle_num: int) -> str:
    """특정 사이클의 이슈를 집계해서 학습 문자열 반환."""
    cycle_issues = [e for e in _load_issues() if e.get("cycle") == cycle_num]
    if not cycle_issues:
        return f"사이클 {cycle_num}: 이슈 없음"

    type_counts = Counter(e["type"] for e in cycle_issues)
    room_counts: dict[str, Counter] = defaultdict(Counter)
    for e in cycle_issues:
        if e.get("room"):
            room_counts[e["room"]][e["type"]] += 1

    lines = [f"사이클 {cycle_num} 이슈 요약:"]
    for t, cnt in type_counts.most_common():
        k = ISSUE_KNOWLEDGE.get(t, {})
        label = k.get("label", t)
        cause = k.get("likely_cause", "(미분류)")
        lines.append(f"  • {label} × {cnt}회 — {cause}")
        fix = k.get("fix_attempted")
        status = k.get("fix_status")
        if fix:
            lines.append(f"     시도된 조치: {fix} [{status or '?'}]")
    # 방별 문제 Top
    if room_counts:
        top = sorted(room_counts.items(),
                     key=lambda kv: sum(kv[1].values()), reverse=True)[:5]
        lines.append("  방별 이슈 Top 5:")
        for room, c in top:
            sigs = ", ".join(f"{t}:{n}" for t, n in c.most_common(3))
            lines.append(f"     {room[:25]}: {sigs}")
    return "\n".join(lines)


def append_learning_md(cycle_num: int, summary: str) -> None:
    """learning.md에 사이클 요약 append. 오래된 내용 삭제 안 함."""
    try:
        with open(LEARNING_FILE, "a", encoding="utf-8") as f:
            f.write(f"\n## {datetime.now():%Y-%m-%d %H:%M:%S} — 사이클 {cycle_num}\n\n")
            f.write(summary + "\n")
    except Exception:
        pass


def summarize_recent(window_cycles: int = 1) -> str:
    """최근 N개 사이클의 누적 이슈 집계."""
    all_issues = _load_issues()
    if not all_issues:
        return "누적 이슈 없음"
    max_cycle = max((e.get("cycle") or 0) for e in all_issues)
    min_cycle = max(0, max_cycle - window_cycles + 1)
    recent = [e for e in all_issues if (e.get("cycle") or 0) >= min_cycle]
    type_counts = Counter(e["type"] for e in recent)
    lines = [f"최근 {window_cycles}사이클 누적 이슈:"]
    for t, cnt in type_counts.most_common():
        k = ISSUE_KNOWLEDGE.get(t, {})
        lines.append(f"  • {k.get('label', t)} × {cnt}회")
    return "\n".join(lines)


def dump_session_learning() -> Path:
    """전체 세션 이슈를 한 번에 묶어서 learning_session_{ts}.md로 저장."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = LOG_DIR / f"learning_session_{ts}.md"
    all_issues = _load_issues()
    if not all_issues:
        out.write_text("(이슈 없음)", encoding="utf-8")
        return out

    max_cycle = max((e.get("cycle") or 0) for e in all_issues)
    with open(out, "w", encoding="utf-8") as f:
        f.write(f"# 네노바 자동화 세션 학습 보고서\n\n")
        f.write(f"- 기록 시각: {datetime.now()}\n")
        f.write(f"- 이슈 총 건수: {len(all_issues)}\n")
        f.write(f"- 사이클: 1 ~ {max_cycle}\n\n")
        f.write("## 이슈 유형별 빈도\n\n")
        type_counts = Counter(e["type"] for e in all_issues)
        for t, cnt in type_counts.most_common():
            k = ISSUE_KNOWLEDGE.get(t, {})
            f.write(f"### {k.get('label', t)} × {cnt}회\n\n")
            f.write(f"- **가능 원인**: {k.get('likely_cause', '미분류')}\n")
            f.write(f"- **시도된 조치**: {k.get('fix_attempted', '-')}\n")
            f.write(f"- **현재 상태**: {k.get('fix_status', '-')}\n\n")
        f.write("\n## 사이클별 상세\n\n")
        for c in range(1, max_cycle + 1):
            f.write(f"### 사이클 {c}\n\n")
            f.write("```\n")
            f.write(summarize_cycle(c))
            f.write("\n```\n\n")
    return out
