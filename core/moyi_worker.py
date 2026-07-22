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
    server = (os.getenv("MOYI_SERVER") or os.getenv("MOYI_API_BASE") or "").rstrip("/")
    secret = os.getenv("MOYI_BRIDGE_SECRET", "")
    if not server or not secret:
        raise RuntimeError("MOYI_SERVER와 MOYI_BRIDGE_SECRET가 필요합니다")
    return server, secret

def _headers(secret: str) -> dict[str, str]:
    return {"X-Company-Secret": secret}

def _journal_key(item: dict) -> str:
    return str(item.get("delivery_key") or hashlib.sha256(f"{item.get('room_binding_id')}:{item.get('id')}".encode()).hexdigest())

def _append_journal(item: dict, part_id: str, result: str, text: str = "") -> None:
    JOURNAL.parent.mkdir(parents=True, exist_ok=True)
    with JOURNAL.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"delivery_key": _journal_key(item), "outbox_id": item.get("id"), "part_id": part_id, "result": result, "content_hash": hashlib.sha256(text.strip().encode()).hexdigest() if text else "", "at": time.time()}) + "\n")

def _event(item: dict | None, state: str, detail: str = "") -> None:
    EVENT_LOG.parent.mkdir(parents=True, exist_ok=True)
    record = {"at": time.time(), "state": state, "detail": detail}
    if item:
        record.update({"outbox_id": item.get("id"), "delivery_key": item.get("delivery_key"), "room": item.get("external_room_id"), "part_id": item.get("current_part_id")})
    with EVENT_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

def _load_journal() -> dict[tuple[str, str], str]:
    sent = {}
    if not JOURNAL.exists(): return sent
    for line in JOURNAL.read_text(encoding="utf-8", errors="ignore").splitlines():
        try:
            row = json.loads(line)
            sent[(_journal_key(row), str(row.get("part_id") or ""))] = row.get("result", "")
        except json.JSONDecodeError:
            continue
    return sent

def _assert_room(hwnd: int, title: str) -> None:
    import win32gui
    if win32gui.GetForegroundWindow() != hwnd or win32gui.GetWindowText(hwnd) != title:
        raise RuntimeError("전송 직전 카카오톡 방 포커스/제목이 변경되었습니다")

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
    journal = _load_journal()
    try:
        for part in sorted(item.get("parts") or [], key=lambda p: p.get("sequence", 0)):
            part_id = str(part.get("part_id") or "")
            if not part_id or part_id in completed:
                continue
            previous = journal.get((_journal_key(item), part_id))
            if previous in ("sent", "unknown_result"):
                completed.add(part_id)
                _event(item, "journal_hold", f"part={part_id}, previous={previous}")
                continue
            if part.get("type") != "text":
                raise RuntimeError(f"not_sent: 지원하지 않는 part type: {part.get('type')}")
            _assert_room(hwnd, title)
            _event(item, "paste_started", f"part={part_id}")
            _send_text(str(part.get("text") or ""))
            _event(item, "enter_pressed", f"part={part_id}")
            completed.add(part_id)
            _append_journal(item, part_id, "sent", str(part.get("text") or ""))
            _event(item, "sent", f"part={part_id}")
            response = requests.post(f"{server}/kakao/agent/ack/{item['id']}", headers=_headers(secret), json={"ok": True, "lease_token": item.get("lease_token"), "completed_part_ids": sorted(completed), "current_part_id": part_id}, timeout=20)
            response.raise_for_status()
        requests.post(f"{server}/kakao/agent/ack/{item['id']}", headers=_headers(secret), json={"ok": True, "final": True, "outcome": "sent", "lease_token": item.get("lease_token"), "completed_part_ids": sorted(completed)}, timeout=20).raise_for_status()
    finally:
        close_room(hwnd)

def run() -> int:
    server, secret = _config()
    from core.moyi_inbound import poll_once as poll_inbound_once
    inbound_interval = max(15, int(os.getenv("MOYI_INBOUND_SCAN_SEC", "30")))
    next_inbound_at = 0.0
    print("[MOYI] Kakao connector worker started (fail-closed)")
    while True:
        response = requests.get(f"{server}/kakao/agent/pending", headers=_headers(secret), params={"limit": 10}, timeout=20)
        response.raise_for_status()
        for item in response.json().get("items", []):
            _event(item, "leased", "server queue lease acquired")
            try:
                process_item(server, secret, item)
            except Exception as exc:
                detail = str(exc)
                state = "failed_not_sent" if detail.startswith("not_sent:") or "방 제목" in detail or "exact room" in detail else "unknown_result"
                print(f"[MOYI] {state} {item.get('id')}: {detail}")
                _event(item, state, detail)
                requests.post(f"{server}/kakao/agent/ack/{item['id']}", headers=_headers(secret), json={"ok": False, "outcome": "unknown_result", "lease_token": item.get("lease_token"), "error": str(exc)[:500]}, timeout=20).raise_for_status()
        if time.monotonic() >= next_inbound_at:
            try:
                result = poll_inbound_once(server, secret)
                if result["sent"] or result["initialized"]:
                    print(f"[MOYI] inbound: {result['sent']} sent, {result['initialized']} initialized")
            except Exception as exc:
                print(f"[MOYI] inbound scan failed: {exc}")
            next_inbound_at = time.monotonic() + inbound_interval
        time.sleep(5)
