"""Fail-closed KakaoTalk-to-MOYI inbound polling for approved rooms."""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path

import pyautogui
import requests

from core.kakao_search import replace_room_search
from core.moyi_outbound import open_room_by_name
from core.safe_worker_room import close_room, open_unique_exact_room

ROOT = Path(__file__).resolve().parent.parent
STATE_FILE = ROOT / "data" / "moyi_inbound_state.json"
OUTBOUND_JOURNAL = ROOT / "data" / "moyi_outbound_journal.jsonl"
MESSAGE_RE = re.compile(r"^\[(?P<sender>.+?)\] \[(?P<ampm>오전|오후) (?P<time>\d{1,2}:\d{2})\] (?P<content>.*)$")
DATE_RE = re.compile(r"^-+ (?P<date>\d{4}년 \d{1,2}월 \d{1,2}일).*-+$")


def parse_export(text: str, binding_id: str) -> list[dict]:
    """Parse KakaoTalk text exports into stable, idempotent message events."""
    events: list[dict] = []
    date = ""
    current: dict | None = None
    for raw_line in text.replace("\r\n", "\n").split("\n"):
        date_match = DATE_RE.match(raw_line.strip())
        if date_match:
            date = date_match.group("date")
            continue
        message_match = MESSAGE_RE.match(raw_line)
        if message_match:
            if current:
                events.append(current)
            current = {
                "sender_name": message_match.group("sender").strip(),
                "timestamp": f"{date} {message_match.group('ampm')} {message_match.group('time')}",
                "content": message_match.group("content"),
            }
        elif current and raw_line:
            current["content"] += "\n" + raw_line
    if current:
        events.append(current)
    for event in events:
        identity = "\x1f".join(
            (binding_id, event["timestamp"], event["sender_name"], event["content"])
        )
        event["event_id"] = "kakao_" + hashlib.sha256(identity.encode()).hexdigest()
    return events


def _load_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(STATE_FILE)


def _recent_outbound_hashes(max_age_sec: int = 3600) -> set[str]:
    if not OUTBOUND_JOURNAL.exists():
        return set()
    cutoff = time.time() - max_age_sec
    hashes: set[str] = set()
    for line in OUTBOUND_JOURNAL.read_text(encoding="utf-8", errors="ignore").splitlines():
        try:
            row = json.loads(line)
            if float(row.get("at") or 0) >= cutoff and row.get("content_hash"):
                hashes.add(str(row["content_hash"]))
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
    return hashes


def _candidate_export_roots() -> tuple[Path, ...]:
    configured = Path(os.getenv("KAKAO_SAVE_DIR", str(Path.home() / "Downloads")))
    return tuple(dict.fromkeys((configured, Path.home() / "Downloads")))


def _txt_files() -> dict[Path, int]:
    files: dict[Path, int] = {}
    for root in _candidate_export_roots():
        if root.exists():
            for path in root.rglob("*.txt"):
                try:
                    files[path] = path.stat().st_mtime_ns
                except OSError:
                    continue
    return files


def has_unread_exact_room(title: str) -> bool:
    """Check the exact-title search result for an unread badge without opening it."""
    from core.badge_monitor import detect_badge_positions
    from core.window_detector import activate_kakaotalk, capture_room_list, switch_to_chat_tab

    window = activate_kakaotalk()
    switch_to_chat_tab(window)
    replace_room_search(window, title)
    image_name = hashlib.sha256(title.encode()).hexdigest()[:16] + ".png"
    image_path = capture_room_list(window, ROOT / "captures" / "inbound_unread" / image_name)
    badges = detect_badge_positions(image_path)
    pyautogui.press("esc")
    if len(badges) > 1:
        raise RuntimeError(f"unread badge conflict for exact room: {len(badges)} matches")
    return len(badges) == 1


def export_exact_room(title: str) -> str:
    """Open one exact room, export its text, and return the UTF-8 content."""
    open_room_by_name(title)
    hwnd = open_unique_exact_room(title)
    before = _txt_files()
    started = time.time_ns()
    try:
        pyautogui.hotkey("ctrl", "s")
        time.sleep(2)
        pyautogui.press("enter")
        time.sleep(2)
        pyautogui.press("enter")
        time.sleep(1)
        after = _txt_files()
        candidates = [
            path
            for path, modified in after.items()
            if modified >= started or before.get(path) != modified
        ]
        if not candidates:
            raise RuntimeError("KakaoTalk export file was not created")
        latest = max(candidates, key=lambda path: after[path])
        return latest.read_text(encoding="utf-8")
    finally:
        close_room(hwnd)


def poll_once(server: str, secret: str) -> dict[str, int]:
    """Open only unread/retry rooms and post messages newer than the baseline."""
    headers = {"X-Company-Secret": secret}
    response = requests.get(f"{server}/kakao/agent/rooms", headers=headers, timeout=20)
    response.raise_for_status()
    state = _load_state()
    retry_bindings = set(state.get("_needs_rescan", []))
    outbound_hashes = _recent_outbound_hashes()
    sent = 0
    initialized = 0
    for room in response.json().get("items", []):
        binding = str(room.get("room_binding_id") or "").strip()
        title = str(room.get("exact_title") or "").strip()
        if not binding or not title:
            continue
        needs_initialization = binding not in state
        has_unread = (
            has_unread_exact_room(title)
            if not needs_initialization and binding not in retry_bindings
            else False
        )
        if not needs_initialization and binding not in retry_bindings and not has_unread:
            continue
        # Opening a room clears KakaoTalk's unread badge. Persist a retry marker
        # first so a transient export/API failure cannot silently lose messages.
        retry_bindings.add(binding)
        state["_needs_rescan"] = sorted(retry_bindings)
        _save_state(state)
        text = export_exact_room(title)
        verify = requests.post(
            f"{server}/kakao/agent/verify-room",
            headers=headers,
            json={"room_binding_id": binding, "exact_title": title, "match_count": 1},
            timeout=20,
        )
        verify.raise_for_status()
        events = parse_export(text, binding)
        known_ids = state.get(binding, [])
        if not isinstance(known_ids, list):
            known_ids = []
        known = set(known_ids)
        if binding not in state:
            state[binding] = [event["event_id"] for event in events][-2000:]
            initialized += 1
            retry_bindings.discard(binding)
            state["_needs_rescan"] = sorted(retry_bindings)
            _save_state(state)
            continue
        for event in events:
            if event["event_id"] in known:
                continue
            content_hash = hashlib.sha256(event["content"].strip().encode()).hexdigest()
            if content_hash not in outbound_hashes:
                inbound = requests.post(
                    f"{server}/kakao/agent/inbound",
                    headers=headers,
                    json={
                        **event,
                        "room_binding_id": binding,
                        "external_room_id": title,
                        "origin": "kakao",
                        "attachments": [],
                    },
                    timeout=20,
                )
                inbound.raise_for_status()
                sent += 1
            known.add(event["event_id"])
            known_ids.append(event["event_id"])
            state[binding] = known_ids[-2000:]
            _save_state(state)
        retry_bindings.discard(binding)
        state["_needs_rescan"] = sorted(retry_bindings)
        _save_state(state)
    _save_state(state)
    return {"sent": sent, "initialized": initialized}
