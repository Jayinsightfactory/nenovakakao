"""
이슈 트래커 재검증 리플레이 하네스 (G).

collected_data.jsonl 전체를 분류기(parse_message) + 체인트래커(ChainTracker)에
재생하여 현재 알고리즘의 baseline 지표를 측정한다. 사이드이펙트 없음
(샌드박스 트래커 — _save / 시트 / pending 큐 모두 no-op, 실제 work_chains.json 미변경).

목적:
  코드 수정 "전/후"를 같은 하네스로 돌려 수치로 비교 → 진짜 재검증.

실행:
  python tools/replay_validate.py
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from core.gsheet_sync import parse_message  # noqa: E402
from core.kakaowork_router import parse_delta_to_messages  # noqa: E402
from core.pipeline_tracker import ChainTracker  # noqa: E402

COLLECTED = ROOT / "data" / "collected_data.jsonl"
DEFECT_KW = ["불량", "클레임", "차감", "파손", "총체", "사육", "지연",
             "딜레이", "누락", "반품", "오발송"]


def _is_iso(s: str) -> bool:
    try:
        datetime.fromisoformat(s)
        return True
    except Exception:
        return False


_KK = re.compile(r"\s*(오전|오후)\s*(\d{1,2}):(\d{2})")


def _iso_from(record_ts: str, kakao_time: str) -> str:
    """record 일자(ISO) + 카톡 표시 시각("오전 10:04") → 진짜 ISO datetime.

    실데이터로 STALLED 를 측정하려면 메시지의 실제 시각이 필요(라이브와 달리
    과거 일자를 보존). 파싱 실패 시 record_ts 그대로.
    """
    try:
        base = datetime.fromisoformat(record_ts)
    except Exception:
        return record_ts or ""
    m = _KK.match(kakao_time or "")
    if not m:
        return base.isoformat(timespec="seconds")
    ampm, hh, mm = m.group(1), int(m.group(2)), int(m.group(3))
    if ampm == "오후" and hh != 12:
        hh += 12
    elif ampm == "오전" and hh == 12:
        hh = 0
    if 0 <= hh <= 23 and 0 <= mm <= 59:
        base = base.replace(hour=hh, minute=mm, second=0, microsecond=0)
    return base.isoformat(timespec="seconds")


def load_events() -> list[dict]:
    """collected_data.jsonl → 시간순 메시지 이벤트 리스트."""
    if not COLLECTED.exists():
        raise SystemExit(f"데이터 없음: {COLLECTED}")
    recs = []
    for ln in COLLECTED.open(encoding="utf-8", errors="ignore"):
        try:
            j = json.loads(ln)
        except Exception:
            continue
        room = (j.get("room_name") or "").strip()
        ts = j.get("timestamp") or ""
        delta = j.get("delta") or ""
        recs.append((ts, room, delta))
    recs.sort(key=lambda r: r[0])  # ISO record ts 로 대략적 시간순
    events: list[dict] = []
    for ts, room, delta in recs:
        for m in parse_delta_to_messages(delta):
            body = (m.get("content") or "").strip()
            if not body:
                continue
            events.append({
                "record_ts": ts, "room": room,
                "sender": m.get("sender", ""), "time": m.get("time", ""),
                "iso_ts": _iso_from(ts, m.get("time", "")),
                "content": body,
            })
    return events


def _sandbox_tracker() -> ChainTracker:
    t = ChainTracker()
    t._chains = {}
    t._save = lambda: None                       # type: ignore
    t._sync_chain_to_sheet = lambda c: None       # type: ignore
    t._enqueue_pending_order = lambda c: None     # type: ignore
    return t


def main() -> int:
    events = load_events()
    print(f"재생 이벤트: {len(events):,}건  (출처 {COLLECTED.name})\n")

    et = Counter()
    defect_msgs = 0
    seq_ok = chain_buildable = 0
    t = _sandbox_tracker()

    # 트래커 print 소음 억제 (요약만 보이게)
    with contextlib.redirect_stdout(io.StringIO()):
        for e in events:
            p = parse_message(e["content"], e["room"])
            etype = p.get("event_type", "INFO")
            et[etype] += 1
            if p.get("sequence"):
                seq_ok += 1
            if p.get("sequence") and (p.get("product") or p.get("supplier")):
                chain_buildable += 1
            if any(k in e["content"] for k in DEFECT_KW):
                defect_msgs += 1
            if etype == "INFO":
                continue
            # 메시지 실제 시각(record일자+카톡시각)을 ISO 로 전달
            t.on_event(p, e["room"], e["sender"], timestamp=e["iso_ts"])

        st = t.stats()
        stalled = t.get_stalled(hours=4, only_new=False)
    defect_chains = sum(
        1 for c in t._chains.values()
        if c.get("trigger_event") in ("DEFECT", "DEFECT_REPORT")
    )
    iso_ok = sum(1 for c in t._chains.values() if _is_iso(c.get("last_update", "")))
    n = len(events)

    print("=" * 60)
    print("리플레이 결과 — 현재 코드 기준")
    print("=" * 60)
    print(f"event_type 분포: {dict(et.most_common())}")
    print(f"차수(sequence) 있음: {seq_ok}/{n} ({100*seq_ok//max(n,1)}%)")
    print(f"체인 생성 가능(차수+품목|거래처): {chain_buildable}/{n} ({100*chain_buildable//max(n,1)}%)")
    print("-" * 60)
    print(f"체인 총수      : {st['total']}")
    print(f"  상태별       : {st['by_status']}")
    print(f"  트리거별     : {st['by_stage']}")
    print(f"CLOSED(종결)   : {st['by_status'].get('CLOSED', 0)}")
    print(f"불량 키워드 메시지 {defect_msgs}건 → DEFECT 체인 {defect_chains}건")
    print("-" * 60)
    print(f"get_stalled(4h) 반환: {len(stalled)}건   ← 알람이 실제로 잡는 미완결 수")
    print(f"last_update ISO 파싱 가능: {iso_ok}/{st['total']}   (0이면 알람 무력화)")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
