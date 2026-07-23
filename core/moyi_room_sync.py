"""Discover allowlisted KakaoTalk rooms and report them to MOYI."""
from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
import pyautogui
from dotenv import load_dotenv

from core.kakao_search import clear_room_search, replace_room_search

ROOT = Path(__file__).resolve().parent.parent


def _parse_allowlist(raw: str) -> tuple[str, ...]:
    """Parse a JSON array or comma/newline-separated exact room titles."""
    raw = raw.strip()
    if not raw:
        return ()
    if raw.startswith("["):
        try:
            values = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError("MOYI_ROOM_ALLOWLIST JSON 형식이 올바르지 않습니다") from exc
        if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
            raise RuntimeError("MOYI_ROOM_ALLOWLIST JSON은 문자열 배열이어야 합니다")
    else:
        values = raw.replace("\r", "\n").replace(",", "\n").split("\n")

    titles = tuple(dict.fromkeys(value.strip() for value in values if value.strip()))
    if not titles:
        raise RuntimeError("MOYI_ROOM_ALLOWLIST에 방 이름이 하나 이상 필요합니다")
    return titles


def _config() -> tuple[str, str, str, str, tuple[str, ...]]:
    load_dotenv(ROOT / ".env", override=True)
    server = (os.getenv("MOYI_SERVER") or os.getenv("MOYI_API_BASE") or "").rstrip("/")
    secret = os.getenv("MOYI_BRIDGE_SECRET", "")
    workspace_id = os.getenv("MOYI_WORKSPACE_ID", "").strip()
    agent_id = os.getenv("MOYI_AGENT_ID", "nenova-owner-pc").strip()
    allowlist = _parse_allowlist(os.getenv("MOYI_ROOM_ALLOWLIST", ""))
    if not server or not secret:
        raise RuntimeError("MOYI_SERVER와 MOYI_BRIDGE_SECRET가 필요합니다")
    if not workspace_id:
        raise RuntimeError("MOYI_WORKSPACE_ID가 필요합니다")
    if not agent_id:
        raise RuntimeError("MOYI_AGENT_ID가 필요합니다")
    if not allowlist:
        raise RuntimeError("안전을 위해 MOYI_ROOM_ALLOWLIST가 필요합니다")
    return server, secret, workspace_id, agent_id, allowlist


def _filter_rooms(rooms: list[dict], allowlist: tuple[str, ...]) -> list[dict]:
    """Keep exact-title matches only and fail when an allowlisted room is absent."""
    allowed = set(allowlist)
    filtered = [room for room in rooms if room.get("name") in allowed]
    found = {room["name"] for room in filtered}
    missing = [title for title in allowlist if title not in found]
    if missing:
        raise RuntimeError(
            "allowlist 방을 감지하지 못했습니다: "
            + ", ".join(repr(title) for title in missing)
        )
    return filtered


def _scan_allowlisted_rooms(window, allowlist: tuple[str, ...]) -> list[dict]:
    """Search each approved title without opening rooms or clearing unread state."""
    from core.room_scanner import scan_rooms_single
    from core.window_detector import capture_room_list, switch_to_chat_tab

    captures = ROOT / "captures" / "room_sync_exact"
    found: list[dict] = []
    switch_to_chat_tab(window)
    for order, title in enumerate(allowlist, start=1):
        replace_room_search(window, title)
        image_name = hashlib.sha256(title.encode()).hexdigest()[:16] + ".png"
        image_path = capture_room_list(window, captures / image_name)
        matches = [room for room in scan_rooms_single(image_path) if room.get("name") == title]
        clear_room_search(window)
        switch_to_chat_tab(window)
        if len(matches) > 1:
            raise RuntimeError(f"동일한 제목의 카카오톡 방이 여러 개 감지됐습니다: {title!r}")
        if matches:
            found.append({**matches[0], "order": order})
    return _filter_rooms(found, allowlist)


def sync_once() -> dict:
    server, secret, workspace_id, agent_id, allowlist = _config()
    from core.window_detector import activate_kakaotalk, switch_to_chat_tab

    window = activate_kakaotalk()
    switch_to_chat_tab(window)
    rooms = _scan_allowlisted_rooms(window, allowlist)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    room_names = "|".join(room.get("name", "") for room in rooms)
    discovery_id = "disc_" + hashlib.sha256(
        (stamp + "|" + room_names).encode()
    ).hexdigest()[:24]
    payload = {
        "workspace_id": workspace_id,
        "agent_id": agent_id,
        "discovery_id": discovery_id,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "rooms": [
            {
                "candidate_key": hashlib.sha256(room["name"].encode()).hexdigest()[:16],
                "exact_title": room["name"],
                "observed_order": room.get("order"),
            }
            for room in rooms
        ],
    }
    response = requests.post(
        f"{server}/kakao/agent/rooms/discover",
        headers={"X-Company-Secret": secret},
        json=payload,
        timeout=30,
    )
    response.raise_for_status()
    result = response.json()
    print(f"[MOYI] room sync: {len(result.get('items', []))}건")
    return result


def watch() -> int:
    interval = max(60, int(os.getenv("MOYI_ROOM_SCAN_INTERVAL_SEC", "900")))
    print(f"[MOYI] room watch started ({interval}s)")
    while True:
        try:
            sync_once()
        except Exception as exc:
            print(f"[MOYI] room watch failed: {exc}")
        time.sleep(interval)
