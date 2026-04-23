"""
업무 파이프라인 & 트리거 추적 엔진.

핵심 개념:
  체인 ID   = (차수, 품목카테고리 OR 거래처) — 동일 업무의 연속 이벤트 묶음
  트리거    = 체인의 최초 이벤트 (예: '15-1 장미 선발주' in 영업방)
  진행 단계 = 같은 체인 ID 의 후속 이벤트 (방/시각/event_type 기록)
  상태      = OPEN / PROGRESS / STALLED / CLOSED

저장:
  data/work_chains.json  — 체인 전체 상태 (영구)

사용 (gsheet_sync 에서):
  from core.pipeline_tracker import tracker
  tracker.on_event(parsed_event, room_name, sender)
  stalled = tracker.get_stalled(hours=4)  # 4시간 미완결 조회
"""
from __future__ import annotations

import json
import time
import threading
from pathlib import Path
from datetime import datetime
from typing import Optional

ROOT = Path(__file__).parent.parent
CHAIN_FILE = ROOT / "data" / "work_chains.json"

# 트리거로 인정할 event_type (신규 업무 시작 신호)
TRIGGER_EVENTS = {
    "ORDER_CHANGE",   # 발주/추가/취소/변경
    "ARRIVAL",        # 입고/도착
    "DEFECT",         # 불량/클레임
    "SHIPMENT",       # 출고/배차
}

# 완결 event_type (체인 CLOSED 전환)
CLOSE_EVENTS = {
    "DECISION",       # 승인/확정/완료
}
CLOSE_KEYWORDS = ("완료", "확정", "종결", "마감", "close", "done")

STALLED_HOURS = 4  # 트리거 후 N시간 동안 후속 없으면 STALLED


class ChainTracker:
    """업무 체인 상태 관리자 (싱글톤)."""

    def __init__(self):
        self._lock = threading.RLock()
        self._chains: dict[str, dict] = self._load()

    def _load(self) -> dict[str, dict]:
        if CHAIN_FILE.exists():
            try:
                return json.loads(CHAIN_FILE.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}

    def _save(self):
        try:
            CHAIN_FILE.parent.mkdir(parents=True, exist_ok=True)
            CHAIN_FILE.write_text(
                json.dumps(self._chains, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            print(f"  [TRACKER] 저장 실패: {e}", flush=True)

    @staticmethod
    def _chain_id(sequence: str, product: str, supplier: str) -> Optional[str]:
        """차수 + (품목 우선, 없으면 거래처) 조합으로 체인 ID."""
        if not sequence:
            return None
        key2 = product or supplier
        if not key2:
            return None
        return f"{sequence}::{key2}"

    def _sync_chain_to_sheet(self, chain: dict):
        """체인 한 개를 구글시트 '업무체인' 탭에 upsert (실패 무시)."""
        try:
            from core.gsheet_sync import _get_sheet, _ensure_worksheets
            _ensure_worksheets()
            sh = _get_sheet()
            ws = sh.worksheet("업무체인")
            # 기존 행 검색 (체인ID로)
            cid = chain["chain_id"]
            events = chain.get("events", [])
            last = events[-1] if events else {}
            summary_tail = " → ".join(
                f"[{e.get('event_type','?')}]{e.get('room','?')[:8]}"
                for e in events[-5:]
            )
            row = [
                cid, chain.get("sequence", ""), chain.get("product", ""),
                chain.get("supplier", ""), chain.get("status", ""),
                chain.get("trigger_event", ""), chain.get("trigger_room", ""),
                chain.get("trigger_time", ""), chain.get("trigger_sender", ""),
                len(events), last.get("time", ""), last.get("room", ""),
                last.get("event_type", ""), summary_tail[:500],
            ]
            # 간단히 append (중복은 시트에서 pivot/쿼리로 관리)
            ws.append_row(row, value_input_option="USER_ENTERED")
        except Exception as e:
            # 시트 쓰기 실패는 체인 추적 영향 안 줌
            pass

    def _enqueue_pending_order(self, chain: dict):
        """신규 트리거(ORDER_CHANGE) 체인을 제안 큐에 추가.
        기존 data/pending_orders.json 포맷(list)와 충돌 방지 위해
        별도 파일 data/pending_chains.json 에 저장.
        """
        try:
            pending_path = ROOT / "data" / "pending_chains.json"
            pending_path.parent.mkdir(parents=True, exist_ok=True)
            data: list = []
            if pending_path.exists():
                try:
                    loaded = json.loads(pending_path.read_text(encoding="utf-8"))
                    if isinstance(loaded, list):
                        data = loaded
                except Exception:
                    data = []
            existing_ids = {p.get("chain_id") for p in data}
            if chain["chain_id"] in existing_ids:
                return
            data.append({
                "chain_id": chain["chain_id"],
                "sequence": chain.get("sequence", ""),
                "product": chain.get("product", ""),
                "supplier": chain.get("supplier", ""),
                "trigger_room": chain.get("trigger_room", ""),
                "trigger_sender": chain.get("trigger_sender", ""),
                "trigger_time": chain.get("trigger_time", ""),
                "trigger_summary": (chain.get("events") or [{}])[0].get("summary", ""),
                "status": "PROPOSED",
            })
            pending_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as e:
            print(f"  [TRACKER] pending_chains 추가 실패 (무시): {e}", flush=True)

    def on_event(
        self,
        parsed: dict,
        room_name: str,
        sender: str = "",
        timestamp: Optional[str] = None,
    ) -> Optional[str]:
        """
        새 이벤트를 체인에 기록.
        Returns: chain_id (체인 매칭된 경우) 또는 None.
        """
        event_type = parsed.get("event_type", "INFO")
        sequence = parsed.get("sequence", "")
        product = parsed.get("product", "")
        supplier = parsed.get("supplier", "")
        summary = parsed.get("summary", "")[:200]

        cid = self._chain_id(sequence, product, supplier)
        if not cid:
            # 체인 키 구성 불가 (차수 없음 or 품목/거래처 없음) — 무시
            return None

        now = timestamp or datetime.now().isoformat(timespec="seconds")

        with self._lock:
            chain = self._chains.get(cid)
            if chain is None:
                # 신규 체인 — 트리거 이벤트여야 생성 (노이즈 방지)
                if event_type not in TRIGGER_EVENTS:
                    return None
                chain = {
                    "chain_id": cid,
                    "sequence": sequence,
                    "product": product,
                    "supplier": supplier,
                    "status": "OPEN",
                    "trigger_event": event_type,
                    "trigger_room": room_name,
                    "trigger_sender": sender,
                    "trigger_time": now,
                    "events": [
                        {"time": now, "room": room_name, "sender": sender,
                         "event_type": event_type, "summary": summary},
                    ],
                    "last_update": now,
                }
                self._chains[cid] = chain
                print(f"  [TRACKER] [NEW CHAIN]: {cid} [{event_type}] by {sender} in {room_name}", flush=True)
            else:
                # 기존 체인 — 이벤트 추가
                chain["events"].append({
                    "time": now, "room": room_name, "sender": sender,
                    "event_type": event_type, "summary": summary,
                })
                chain["last_update"] = now
                # 상태 전환: OPEN → PROGRESS
                if chain["status"] == "OPEN":
                    chain["status"] = "PROGRESS"
                # CLOSE 조건: 특정 event_type 또는 키워드
                if event_type in CLOSE_EVENTS or any(k in summary for k in CLOSE_KEYWORDS):
                    chain["status"] = "CLOSED"
                    chain["close_time"] = now
                print(f"  [TRACKER] +event: {cid} [{event_type}] ({chain['status']}) #{len(chain['events'])}", flush=True)

            self._save()

            # 2차: 구글시트 업무체인 탭 upsert (비동기 성격, 실패 무시)
            try:
                self._sync_chain_to_sheet(chain)
            except Exception:
                pass

            # 4차: 신규 ORDER_CHANGE 트리거면 ERP pending_orders 제안 큐에 추가
            if (len(chain["events"]) == 1
                 and chain.get("trigger_event") == "ORDER_CHANGE"):
                self._enqueue_pending_order(chain)
        return cid

    def get_stalled(self, hours: int = STALLED_HOURS) -> list[dict]:
        """트리거 후 N시간 이상 추가 이벤트 없는 체인."""
        now = datetime.now()
        out: list[dict] = []
        with self._lock:
            for cid, ch in self._chains.items():
                if ch.get("status") == "CLOSED":
                    continue
                try:
                    last = datetime.fromisoformat(ch["last_update"])
                    diff = (now - last).total_seconds() / 3600
                    if diff >= hours and len(ch["events"]) == 1:
                        # 트리거 1개 있고 후속 없음
                        ch2 = dict(ch)
                        ch2["stalled_hours"] = round(diff, 1)
                        out.append(ch2)
                        # 상태 전환
                        ch["status"] = "STALLED"
                except Exception:
                    continue
        if out:
            self._save()
        return out

    def stats(self) -> dict:
        """전체 체인 통계."""
        from collections import Counter
        with self._lock:
            c = Counter(ch.get("status", "UNKNOWN") for ch in self._chains.values())
            return {
                "total": len(self._chains),
                "by_status": dict(c),
                "by_stage": Counter(
                    ch.get("trigger_event") for ch in self._chains.values()
                ),
            }


# 싱글톤
tracker = ChainTracker()


if __name__ == "__main__":
    # 스모크 테스트
    t = ChainTracker()
    t._chains = {}  # 초기화

    events = [
        # 체인 1: 15-1 장미 발주 → 입고 → 출고 완료
        ({"event_type": "ORDER_CHANGE", "sequence": "15-1", "product": "장미",
           "supplier": "꽃샘원예", "summary": "15-1 장미 선발주"},
         "네노바 + 꽃샘원예", "정재훈대리"),
        ({"event_type": "ARRIVAL", "sequence": "15-1", "product": "장미",
           "supplier": "", "summary": "15-1 장미 입고 완료"},
         "수입방", "김원빈"),
        ({"event_type": "SHIPMENT", "sequence": "15-1", "product": "장미",
           "supplier": "", "summary": "15-1 장미 출고 배차"},
         "현장 추가취소방", "정재훈"),
        ({"event_type": "DECISION", "sequence": "15-1", "product": "장미",
           "supplier": "", "summary": "15-1 장미 완료 확정"},
         "네노바 + 꽃샘원예", "정재훈대리"),
        # 체인 2: 17 카네이션 (트리거만, 후속 없음 → 향후 STALLED)
        ({"event_type": "ORDER_CHANGE", "sequence": "17", "product": "카네이션",
           "supplier": "", "summary": "17-1 카네이션 추가 10단"},
         "네노바 영업", "정재훈대리"),
        # 체인 3: 16-1 불량
        ({"event_type": "DEFECT", "sequence": "16-1", "product": "장미",
           "supplier": "", "summary": "16-1 장미 클레임"},
         "네노바 수입(불량 공유방)", "김원빈"),
    ]

    for parsed, room, sender in events:
        t.on_event(parsed, room, sender)

    print()
    print("=== 체인 통계 ===")
    print(json.dumps(t.stats(), ensure_ascii=False, indent=2))
    print()
    print("=== 전체 체인 ===")
    for cid, ch in t._chains.items():
        print(f"\n[{cid}] status={ch['status']} events={len(ch['events'])}")
        for e in ch["events"]:
            print(f"  {e['time']} [{e['event_type']}] {e['room']} / {e['sender']}: {e['summary']}")
