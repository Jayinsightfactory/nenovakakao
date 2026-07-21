"""Fail-closed MOYI/KakaoTalk bridge worker."""
from __future__ import annotations
import hashlib, json, os, time
from pathlib import Path
import pyautogui, requests
from dotenv import load_dotenv
from core.safe_worker_room import open_unique_exact_room, close_room

ROOT = Path(__file__).resolve().parent.parent
JOURNAL = ROOT / "data" / "moyi_outbound_journal.jsonl"
EVENT_LOG = ROOT / "data" / "moyi_events.jsonl"

def _config() -> tuple[str, str]:
    load_dotenv(ROOT / ".env")
    server, secret = os.getenv("MOYI_SERVER", "").rstrip("/"), os.getenv("MOYI_BRIDGE_SECRET", "")
    if not server or not secret:
        raise RuntimeError("MOYI_SERVER와 MOYI_BRIDGE_SECRET가 필요합니다")
    return server, secret

def _headers(secret: str) -> dict[str, str]:
    return {"X-Company-Secret": secret}

def _journal_key(item: dict) -> str:
    return str(item.get("delivery_key") or hashlib.sha256(f"{item.get('room_binding_id')}:{item.get('id')}".encode()).hexdigest())

def _append_journal(item: dict, part_id: str, result: str) -> None:
    JOURNAL.parent.mkdir(parents=True, exist_ok=True)
    with JOURNAL.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"delivery_key": _journal_key(item), "outbox_id": item.get("id"), "part_id": part_id, "result": result, "at": time.time()}) + "\n")

def _event(item: dict | None, state: str, detail: str = "") -> None:
    EVENT_LOG.parent.mkdir(parents=True, exist_ok=True)
    record = {"at": time.time(), "state": state, "detail": detail}
    if item:
        record.update({"outbox_id": item.get("id"), "delivery_key": item.get("delivery_key"), "room": item.get("external_room_id"), "part_id": item.get("current_part_id")})
    with EVENT_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

def _send_text(text: str) -> None:
    pyperclip = __import__("pyperclip")
    pyperclip.copy(text)
    pyautogui.hotkey("ctrl", "v")
    pyautogui.press("enter")
    time.sleep(0.4)

def process_item(server: str, secret: str, item: dict) -> None:
    title, binding = str(item.get("external_room_id") or "").strip(), str(item.get("room_binding_id") or "").strip()
    if not title or not binding:
        raise RuntimeError("방 제목 또는 room_binding_id가 없습니다")
    hwnd = open_unique_exact_room(title)
    verify = requests.post(
        f"{server}/kakao/agent/verify-room", headers=_headers(secret),
        json={"room_binding_id": binding, "exact_title": title, "match_count": 1},
        timeout=20,
    )
    verify.raise_for_status()
    _event(item, "room_verified", title)
    completed = set(item.get("completed_part_ids") or [])
    try:
        for part in sorted(item.get("parts") or [], key=lambda p: p.get("sequence", 0)):
            part_id = str(part.get("part_id") or "")
            if not part_id or part_id in completed:
                continue
            if part.get("type") != "text":
                raise RuntimeError(f"지원하지 않는 part type: {part.get('type')}")
            _send_text(str(part.get("text") or ""))
            completed.add(part_id)
            _append_journal(item, part_id, "sent")
            _event(item, "sent", f"part={part_id}")
            response = requests.post(f"{server}/kakao/agent/ack/{item['id']}", headers=_headers(secret), json={"ok": True, "lease_token": item.get("lease_token"), "completed_part_ids": sorted(completed), "current_part_id": part_id}, timeout=20)
            response.raise_for_status()
    finally:
        close_room(hwnd)

def run() -> int:
    server, secret = _config()
    print("[MOYI] Kakao connector worker started (fail-closed)")
    while True:
        response = requests.get(f"{server}/kakao/agent/pending", headers=_headers(secret), params={"limit": 10}, timeout=20)
        response.raise_for_status()
        for item in response.json().get("items", []):
            _event(item, "leased", "server queue lease acquired")
            try:
                process_item(server, secret, item)
            except Exception as exc:
                print(f"[MOYI] HOLD {item.get('id')}: {exc}")
                _event(item, "unknown_result", str(exc))
                requests.post(f"{server}/kakao/agent/ack/{item['id']}", headers=_headers(secret), json={"ok": False, "outcome": "unknown_result", "lease_token": item.get("lease_token"), "error": str(exc)[:500]}, timeout=20).raise_for_status()
        time.sleep(5)
