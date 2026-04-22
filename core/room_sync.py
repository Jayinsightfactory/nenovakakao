"""
방 리스트 동기화 모듈.

Phase 1: mapping ↔ selected 동기화
  - room_mapping.json에 있지만 selected_rooms.json에 빠진 방을 자동 보충

Phase 2: 카톡 OCR 기반 신규 방 발견
  - 카톡 화면 OCR → mapping에 없는 새 방 알림
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data"
SELECTED_FILE = DATA_DIR / "selected_rooms.json"
MAPPING_FILE = DATA_DIR / "room_mapping.json"
NEW_ROOMS_FILE = DATA_DIR / "new_rooms_alert.json"


def sync_selected_from_mapping() -> list[str]:
    """mapping에 있지만 selected에 빠진 방을 자동 보충.
    selected_rooms.json 형식: [{"name": str, "order": int}, ...]
    Returns: 새로 추가된 방 이름 리스트.
    """
    try:
        sel = json.loads(SELECTED_FILE.read_text(encoding="utf-8"))
        if not isinstance(sel, list):
            sel = []
    except Exception:
        sel = []
    try:
        mapping = json.loads(MAPPING_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []

    # 기존 selected에 있는 방 이름 set
    existing_names = set()
    max_order = 0
    for item in sel:
        if isinstance(item, dict):
            n = item.get("name")
            if n:
                existing_names.add(n)
            o = item.get("order", 0)
            if isinstance(o, int) and o > max_order:
                max_order = o
        elif isinstance(item, str):
            existing_names.add(item)

    added: list[str] = []
    for k in mapping:
        if k and k not in existing_names:
            max_order += 1
            sel.append({"name": k, "order": max_order})
            added.append(k)

    if added:
        SELECTED_FILE.write_text(
            json.dumps(sel, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(
            f"[ROOM-SYNC] selected에 누락 방 {len(added)}개 자동 추가: "
            f"{added[:5]}{'...' if len(added) > 5 else ''}",
            flush=True,
        )
    return added


def discover_new_rooms_from_kakao(window) -> list[str]:
    """카톡 OCR로 방 목록 → mapping/selected에 없는 새 방 발견 → 알림 파일에 누적.
    Returns: 새로 발견된 방 이름 리스트.
    """
    try:
        from core.room_scanner import scan_rooms_full
        captures = ROOT / "captures"
        rooms = scan_rooms_full(window, captures)
    except Exception as e:
        print(f"[ROOM-SYNC] scan_rooms_full 실패: {e}", flush=True)
        return []

    try:
        mapping = json.loads(MAPPING_FILE.read_text(encoding="utf-8"))
    except Exception:
        mapping = {}
    try:
        sel = set(json.loads(SELECTED_FILE.read_text(encoding="utf-8")))
    except Exception:
        sel = set()

    known = set(mapping.keys()) | sel
    new_rooms = [r["name"] for r in rooms if r.get("name") and r["name"] not in known]

    if new_rooms:
        try:
            prev = json.loads(NEW_ROOMS_FILE.read_text(encoding="utf-8"))
        except Exception:
            prev = []
        seen = set(prev)
        for r in new_rooms:
            if r not in seen:
                prev.append(r)
                seen.add(r)
        NEW_ROOMS_FILE.write_text(
            json.dumps(prev, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[ROOM-SYNC] 카톡에서 신규 방 {len(new_rooms)}개 발견 → {NEW_ROOMS_FILE.name}: {new_rooms}", flush=True)
    return new_rooms
