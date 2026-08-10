"""Durable, idempotent Kakao raw-message sink for mindmap-viewer."""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

ROOT = Path(__file__).resolve().parent.parent
OUTBOX_FILE = ROOT / "data" / "mindmap_kakao_outbox.json"
TOKEN_FILE = ROOT / "data" / "mindmap_import_token.secret"
DEFAULT_BASE = "https://mindmap-viewer-production-adb2.up.railway.app"
SEOUL = ZoneInfo("Asia/Seoul")
KAKAO_TIME_RE = re.compile(
    r"(?P<year>\d{4})년\s*(?P<month>\d{1,2})월\s*(?P<day>\d{1,2})일\s*"
    r"(?P<ampm>오전|오후)\s*(?P<hour>\d{1,2}):(?P<minute>\d{2})"
)


def configured() -> bool:
    return bool(_token())


def _base() -> str:
    return (os.getenv("MINDMAP_BASE") or DEFAULT_BASE).rstrip("/")


def _token() -> str:
    configured_token = os.getenv("MINDMAP_IMPORT_TOKEN", "").strip()
    if configured_token:
        return configured_token
    try:
        return TOKEN_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def timestamp_to_iso(value: str) -> tuple[str, bool]:
    match = KAKAO_TIME_RE.search(value or "")
    if not match:
        return datetime.now(SEOUL).isoformat(), True
    hour = int(match.group("hour")) % 12
    if match.group("ampm") == "오후":
        hour += 12
    parsed = datetime(
        int(match.group("year")), int(match.group("month")), int(match.group("day")),
        hour, int(match.group("minute")), tzinfo=SEOUL,
    )
    return parsed.isoformat(), False


def _load_outbox() -> list[dict]:
    if not OUTBOX_FILE.exists():
        return []
    try:
        value = json.loads(OUTBOX_FILE.read_text(encoding="utf-8"))
        return value if isinstance(value, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _save_outbox(rows: list[dict]) -> None:
    OUTBOX_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = OUTBOX_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(OUTBOX_FILE)


def enqueue_events(binding_id: str, title: str, events: list[dict]) -> int:
    if not configured() or not events:
        return 0
    pending = _load_outbox()
    known = {str(row.get("external_message_id") or "") for row in pending}
    added = 0
    for event in events:
        event_id = str(event.get("event_id") or "").strip()
        if not event_id or event_id in known:
            continue
        created_at, approximate = timestamp_to_iso(str(event.get("timestamp") or ""))
        pending.append({
            "external_message_id": event_id,
            "chat_id": binding_id,
            "chatroom": title,
            "user_id": str(event.get("sender_name") or ""),
            "sender": str(event.get("sender_name") or ""),
            "message": str(event.get("content") or ""),
            "type": "text",
            "source": "nenovakakao",
            "created_at": created_at,
            "timestamp_approximate": approximate,
        })
        known.add(event_id)
        added += 1
    if added:
        _save_outbox(pending)
    return added


def flush_pending(batch_size: int = 100) -> int:
    if not configured():
        return 0
    pending = _load_outbox()
    if not pending:
        return 0
    batch = pending[:batch_size]
    response = requests.post(
        f"{_base()}/api/kakao/import",
        headers={"Authorization": f"Bearer {_token()}"},
        json={"messages": batch, "source": "nenovakakao"},
        timeout=30,
    )
    response.raise_for_status()
    result = response.json()
    if int(result.get("imported", 0)) + int(result.get("skipped", 0)) != len(batch):
        raise RuntimeError("mindmap import did not account for the complete batch")
    _save_outbox(pending[len(batch):])
    return len(batch)


def _export_room_title(path: Path) -> str:
    try:
        first = path.read_text(encoding="utf-8").splitlines()[0].strip()
    except (OSError, UnicodeError, IndexError):
        return ""
    for suffix in (" 님과 카카오톡 대화", " 임과 카카오톡 대화"):
        if first.endswith(suffix):
            return first[:-len(suffix)].strip()
    return ""


def backfill_exports(server: str, secret: str) -> dict[str, int]:
    """Archive the newest complete Kakao export for every approved MOYI room."""
    from core.moyi_inbound import _candidate_export_roots, parse_export

    response = requests.get(
        f"{server.rstrip('/')}/kakao/agent/rooms",
        headers={"X-Company-Secret": secret}, timeout=20,
    )
    response.raise_for_status()
    approved = {
        str(room.get("exact_title") or "").strip(): str(room.get("room_binding_id") or "").strip()
        for room in response.json().get("items", [])
        if room.get("exact_title") and room.get("room_binding_id")
    }
    newest: dict[str, Path] = {}
    for root in _candidate_export_roots():
        if not root.exists():
            continue
        for path in root.rglob("*.txt"):
            title = _export_room_title(path)
            if title not in approved:
                continue
            current = newest.get(title)
            if current is None or path.stat().st_mtime_ns > current.stat().st_mtime_ns:
                newest[title] = path

    queued = 0
    event_count = 0
    for title, path in newest.items():
        events = parse_export(path.read_text(encoding="utf-8"), approved[title])
        event_count += len(events)
        queued += enqueue_events(approved[title], title, events)

    flushed = 0
    while _load_outbox():
        flushed += flush_pending()
    return {"rooms": len(newest), "events": event_count, "queued": queued, "flushed": flushed}
