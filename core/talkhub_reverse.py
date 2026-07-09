"""core/talkhub_reverse.py — MOYI(talkhub) → 카톡 역방향 릴레이.

talkhub outbound 큐(GET /bridge/kakao/outbound/pending)를 폴링 → kakao_win32 로 카톡 방에 타이핑 송신.

⚠️⚠️ 거래처 카톡방에 **실제 발송**된다. 그래서 REVERSE_RELAY=1 을 명시적으로 켜야만 폴링·송신한다.
안전장치(메모리 talkhub_migration '역전송 범위 확정' 준수):
  ① REVERSE_RELAY 킬스위치 + data/_STOP        (즉시 정지)
  ② 발신자 라벨 "[MOYI·이름] 본문" 강제          (누가 보냈는지 + 에코 판별)
  ③ 로컬 dedup ledger                           (같은 항목 재송신 방지)
  ④ kakao_lock                                  (모니터/역방향 입력 경합 방지)
  ⑤ 감사로그 data/talkhub_reverse_audit.jsonl   (누가/언제/무엇을 보냈나)
  ⑥ 송신 실패 로그(+원장 미기록 → 다음에 재시도 가능)

⚠️ 드레인 주의: /outbound/pending 은 조회 시 서버 큐를 비운다. 그래서 REVERSE_RELAY!=1 이면
   **아예 폴링하지 않는다**(dry 상태로 폴링하면 항목이 소실되기 때문). 실테스트는 REVERSE_RELAY=1 로.
   (백엔드 outbound 는 현재 in-memory + ack 없음 = 이슈 T7. 100% 보장은 T7 반영 후.)

env:
  TALKHUB_BASE_URL / TALKHUB_BRIDGE_SECRET   (forward 와 공유)
  REVERSE_RELAY        "1" 이어야 폴링·송신(기본 0 = OFF, 아무것도 안 함)
  REVERSE_INTERVAL     폴링 주기 초(기본 5)
  TALKHUB_SELF_LABEL   발신자 접두(기본 "MOYI") → "[MOYI·홍길동] 본문"
  TALKHUB_TIMEOUT      HTTP 타임아웃(기본 15)
실행: python -m core.talkhub_reverse        (루프)
      python -m core.talkhub_reverse --once (1회)
"""
from __future__ import annotations

import os
import sys
import json
import time
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
MAPPING_FILE = DATA / "talkhub_mapping.json"
LEDGER_FILE = DATA / "talkhub_reverse_ledger.json"
AUDIT_FILE = DATA / "talkhub_reverse_audit.jsonl"
STOP_FILE = DATA / "_STOP"

_DEFAULT_BASE = "https://api.nowlink.kr"


def _cfg() -> dict:
    base = (os.environ.get("TALKHUB_BASE_URL") or _DEFAULT_BASE).rstrip("/")
    try:
        interval = float(os.environ.get("REVERSE_INTERVAL", "5"))
    except ValueError:
        interval = 5.0
    try:
        timeout = float(os.environ.get("TALKHUB_TIMEOUT", "15"))
    except ValueError:
        timeout = 15.0
    return {
        "base": base,
        "secret": os.environ.get("TALKHUB_BRIDGE_SECRET") or "",
        "enabled": os.environ.get("REVERSE_RELAY", "0") == "1",
        "interval": interval,
        "label": (os.environ.get("TALKHUB_SELF_LABEL") or "MOYI").strip(),
        "timeout": timeout,
    }


def _stop_requested() -> bool:
    try:
        return STOP_FILE.exists()
    except Exception:
        return False


def _reverse_kakao_room(external_room_id: str) -> str:
    """talkhub external_room_id → 카톡 방 이름. 매핑에 external_room_id 로 저장돼 있으면 그 키(카톡명),
    없으면 external_room_id 자체(= forward 에서 카톡명을 그대로 external_room_id 로 썼으므로 동일)."""
    try:
        m = json.loads(MAPPING_FILE.read_text(encoding="utf-8"))
        for kakao_name, v in (m or {}).items():
            if (v or {}).get("external_room_id") == external_room_id:
                return kakao_name
    except Exception:
        pass
    return external_room_id


def _load_ledger() -> set:
    try:
        return set(json.loads(LEDGER_FILE.read_text(encoding="utf-8")))
    except Exception:
        return set()


def _save_ledger(s: set) -> None:
    try:
        DATA.mkdir(parents=True, exist_ok=True)
        LEDGER_FILE.write_text(json.dumps(list(s)[-5000:], ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _dedup_key(item: dict) -> str:
    s = f"{item.get('external_room_id','')}|{(item.get('content') or '')[:200]}"
    return hashlib.md5(s.encode("utf-8", errors="ignore")).hexdigest()


def _audit(entry: dict) -> None:
    try:
        DATA.mkdir(parents=True, exist_ok=True)
        entry = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), **entry}
        with open(AUDIT_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _poll_pending() -> list[dict]:
    """⚠️ 서버 큐를 드레인(비움)한다. REVERSE_RELAY=1 일 때만 호출."""
    import requests
    c = _cfg()
    headers = {}
    if c["secret"]:
        headers["X-Bridge-Secret"] = c["secret"]
    try:
        r = requests.get(c["base"] + "/bridge/kakao/outbound/pending", headers=headers, timeout=c["timeout"])
        if r.status_code < 300:
            return (r.json() or {}).get("items", []) or []
        print(f"  [talkhub-rev] pending → HTTP {r.status_code}: {r.text[:120]}", flush=True)
    except Exception as e:
        print(f"  [talkhub-rev] pending 예외: {type(e).__name__}: {e}", flush=True)
    return []


def _send_to_kakao(kakao_room: str, text: str) -> bool:
    """kakao_lock 확보 후 분리창 찾기/열기 → 전면검증 송신. work_bridge._forward_to_kakao 패턴 재사용."""
    from core import kakao_win32 as kw
    from core import kakao_lock as klock
    import win32gui as _w32

    klock.request()
    if not klock.acquire("talkhub_reverse", timeout=30, respect_request=False):
        print("  [talkhub-rev] 카톡 락 확보 실패 — 보류", flush=True)
        klock.clear_request()
        return False
    try:
        hwnd = kw.find_chat_window(kakao_room)
        if hwnd is None:
            ores = kw.search_and_open_room(kakao_room)
            oh = ores.get("hwnd")
            for _ in range(33):
                hwnd = kw.find_chat_window(kakao_room)
                if hwnd:
                    break
                if oh and _w32.IsWindow(oh) and (_w32.GetWindowText(oh) or "") == kakao_room:
                    hwnd = oh
                    break
                time.sleep(0.3)
        if hwnd is None:
            print(f"  [talkhub-rev] ❌ 카톡 '{kakao_room}' 방 못 엶", flush=True)
            return False
        res = kw.send_message_to_room(kakao_room, text)
        return bool(res.get("success"))
    except Exception as e:
        print(f"  [talkhub-rev] '{kakao_room}' 송신 예외: {type(e).__name__}: {e}", flush=True)
        return False
    finally:
        klock.release("talkhub_reverse")
        klock.clear_request()


def poll_once() -> dict:
    c = _cfg()
    stats = {"pending": 0, "sent": 0, "skipped_dup": 0, "failed": 0}
    if not c["enabled"]:
        return stats  # REVERSE_RELAY!=1 → 폴링 자체 안 함(드레인/유실 방지)

    items = _poll_pending()
    stats["pending"] = len(items)
    if not items:
        return stats

    ledger = _load_ledger()
    for it in items:
        if _stop_requested():
            print("  [talkhub-rev] _STOP — 송신 중단", flush=True)
            break
        content = (it.get("content") or "").strip()
        ext_room = it.get("external_room_id") or ""
        if not content or not ext_room:
            continue
        key = _dedup_key(it)
        if key in ledger:
            stats["skipped_dup"] += 1
            continue
        kakao_room = _reverse_kakao_room(ext_room)
        sender = (it.get("sender_name") or "").strip()
        label = f"[{c['label']}·{sender}]" if sender else f"[{c['label']}]"
        text = f"{label} {content}"
        ok = _send_to_kakao(kakao_room, text)
        _audit({"external_room_id": ext_room, "kakao_room": kakao_room,
                "text": text[:200], "ok": ok})
        if ok:
            stats["sent"] += 1
            ledger.add(key)
            print(f"  [talkhub-rev] ✅ '{kakao_room}' ← '{text[:50]}'", flush=True)
        else:
            stats["failed"] += 1
            print(f"  [talkhub-rev] ❌ '{kakao_room}' 송신실패 — '{text[:50]}'", flush=True)
        time.sleep(0.5)
    _save_ledger(ledger)
    return stats


def run_loop(interval: float) -> None:
    c = _cfg()
    if not c["enabled"]:
        print("[talkhub-rev] REVERSE_RELAY!=1 — 역방향 OFF(폴링 안 함). 켜려면 REVERSE_RELAY=1.", flush=True)
        return
    print(f"[talkhub-rev] 역방향 릴레이 시작 (주기 {interval}s, 라벨 [{c['label']}·..])", flush=True)
    while True:
        if _stop_requested():
            print("[talkhub-rev] _STOP 감지 — 종료", flush=True)
            return
        try:
            r = poll_once()
            if r.get("sent") or r.get("failed") or r.get("pending"):
                print(f"[talkhub-rev] pending {r['pending']} / 송신 {r['sent']} / 중복 {r['skipped_dup']} / 실패 {r['failed']}", flush=True)
        except Exception as e:
            print(f"[talkhub-rev] poll_once 예외(무시): {type(e).__name__}: {e}", flush=True)
        slept = 0.0
        while slept < interval and not _stop_requested():
            time.sleep(min(2.0, interval - slept))
            slept += 2.0


def _load_env() -> None:
    try:
        for ln in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if ln and not ln.startswith("#") and "=" in ln:
                k, v = ln.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
    except Exception:
        pass


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    _load_env()
    if "--once" in sys.argv:
        print(f"[talkhub-rev] --once: {poll_once()}", flush=True)
    else:
        run_loop(_cfg()["interval"])
