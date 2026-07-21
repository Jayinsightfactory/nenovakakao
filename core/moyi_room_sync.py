"""Discover KakaoTalk rooms and report them to MOYI for admin approval."""
from __future__ import annotations
import hashlib, os, time
from datetime import datetime, timezone
from pathlib import Path
import requests
from dotenv import load_dotenv
from core.window_detector import activate_kakaotalk, switch_to_chat_tab
from core.room_scanner import scan_rooms_full

ROOT = Path(__file__).resolve().parent.parent

def _config():
    load_dotenv(ROOT / ".env")
    server = (os.getenv("MOYI_SERVER") or os.getenv("MOYI_API_BASE") or "").rstrip("/")
    secret = os.getenv("MOYI_BRIDGE_SECRET", "")
    if not server or not secret: raise RuntimeError("MOYI_SERVER와 MOYI_BRIDGE_SECRET가 필요합니다")
    return server, secret

def sync_once() -> dict:
    server, secret = _config()
    window = activate_kakaotalk(); switch_to_chat_tab(window)
    rooms = scan_rooms_full(window, ROOT / "captures" / "room_sync")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    discovery_id = "disc_" + hashlib.sha256((stamp + "|" + "|".join(r.get("name", "") for r in rooms)).encode()).hexdigest()[:24]
    payload = {"agent_id": os.getenv("MOYI_AGENT_ID", "nenova-owner-pc"), "discovery_id": discovery_id, "observed_at": datetime.now(timezone.utc).isoformat(), "rooms": [{"candidate_key": hashlib.sha256(r.get("name", "").encode()).hexdigest()[:16], "exact_title": r["name"], "observed_order": r.get("order")} for r in rooms]}
    response = requests.post(f"{server}/kakao/agent/rooms/discover", headers={"X-Company-Secret": secret}, json=payload, timeout=30)
    response.raise_for_status()
    result = response.json(); print(f"[MOYI] room sync: {len(result.get('items', []))}건")
    return result

def watch() -> int:
    interval = max(60, int(os.getenv("MOYI_ROOM_SCAN_INTERVAL_SEC", "900")))
    print(f"[MOYI] room watch started ({interval}s)")
    while True:
        try: sync_once()
        except Exception as exc: print(f"[MOYI] room watch failed: {exc}")
        time.sleep(interval)
