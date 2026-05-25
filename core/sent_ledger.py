"""
워크(KakaoWork) 미러방 전송 멱등 원장 — per-room sent ledger.

목적:
  extract_delta 가 '마지막 3줄 매칭' 실패로 대화 전체를 다시 신규(delta)로 반환해도,
  이미 워크에 보낸 메시지는 재전송하지 않도록 방별로 '전송 완료 메시지 해시'를
  누적 저장한다. (사용자 요청: "이미 워크에 톡내용이 있으면 제외하고 업로드")

저장:
  data/sent_ledger/<safe_room>.json  — 해시 리스트(JSON), 최근이 끝쪽.

해시:
  md5(f"{sender}|{time}|{content}")  — 같은 발신자/시각/내용이면 동일 메시지로 간주.
  (같은 사람이 같은 분(分)에 완전히 동일한 내용을 두 번 보낸 희귀 케이스는 1건으로
   합쳐지지만, 그건 실질적으로 중복이라 무방.)

보존:
  방당 최근 MAX_HASHES 개만 유지 (무한 증가 방지).

사용:
  led = SentLedger(room_name)
  h = led.hash_msg(msg)            # msg = parse_delta_to_messages 의 dict
  if led.seen(h): continue          # 이미 보냄 → 스킵
  if _send_single(...): led.add(h)  # 성공 시에만 기록
  ...
  led.flush()                       # 마지막에 1회 저장
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
LEDGER_DIR = DATA_DIR / "sent_ledger"
MAX_HASHES = 8000


def _safe_filename(name: str) -> str:
    keep = []
    for ch in name:
        if ch.isalnum() or ch in (" ", "_", "-"):
            keep.append(ch)
        else:
            keep.append("_")
    return "".join(keep).strip() or "room"


def msg_hash(msg: dict) -> str:
    """메시지 dict → 안정적 해시 (sender|time|content)."""
    sender = (msg.get("sender") or "").strip()
    tstr = (msg.get("time") or "").strip()
    content = (msg.get("content") or "").strip()
    raw = f"{sender}|{tstr}|{content}"
    return hashlib.md5(raw.encode("utf-8", errors="ignore")).hexdigest()


class SentLedger:
    """방별 전송 완료 원장. 한 번 로드 → 메모리 체크 → flush 1회 저장."""

    def __init__(self, room_name: str):
        self.room = room_name
        self._path = LEDGER_DIR / f"{_safe_filename(room_name)}.json"
        self._order: list[str] = []
        self._set: set[str] = set()
        self._dirty = False
        self._load()

    def _load(self) -> None:
        try:
            if self._path.exists():
                data = json.loads(self._path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    self._order = [str(h) for h in data]
                    self._set = set(self._order)
        except Exception:
            self._order = []
            self._set = set()

    def hash_msg(self, msg: dict) -> str:
        return msg_hash(msg)

    def seen(self, h: str) -> bool:
        return h in self._set

    def add(self, h: str) -> None:
        if h in self._set:
            return
        self._set.add(h)
        self._order.append(h)
        self._dirty = True

    def flush(self) -> None:
        if not self._dirty:
            return
        try:
            LEDGER_DIR.mkdir(parents=True, exist_ok=True)
            order = self._order
            if len(order) > MAX_HASHES:
                order = order[-MAX_HASHES:]
                self._order = order
                self._set = set(order)
            self._path.write_text(
                json.dumps(order, ensure_ascii=False), encoding="utf-8"
            )
            self._dirty = False
        except Exception:
            pass
