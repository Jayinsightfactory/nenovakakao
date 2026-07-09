"""core/talkhub_router.py — 카톡 델타를 MOYI(talkhub) 브릿지로 미러하는 어댑터.

kakaowork_router.send_delta_interleaved 의 **공개 시그니처/반환을 미러링**해서
mirror_dispatch(MIRROR_TARGET) 로 드롭인 교체 가능. 카카오워크 봇 API 대신 talkhub REST 사용:
  - 텍스트:      POST {base}/bridge/kakao/inbound   (external_room_id = 카톡방 이름)
  - 이미지/파일: POST {base}/files/upload → file_id → inbound attachments
  - 방 준비(멱등): POST {base}/bridge/kakao/ensure-room

인증: 헤더 X-Bridge-Secret (env TALKHUB_BRIDGE_SECRET).
멱등: external_id(메시지 해시, 백엔드 dedup용) + 로컬 SentLedger(중복 API 호출 방지).
델타 파서/원장은 kakaowork_router / sent_ledger 를 재사용(동일 규칙).

⚠️ 2026-07-08 talkhub 배포 상태 기준:
  inbound 는 현재 '텍스트만' 처리(첨부·external_id·시크릿인증·ensure-room 미배포).
  → 지금은 '텍스트 경로'만 실제 동작. 이 어댑터는 합의 contract(이슈 T1~T5)에 맞춰 선구현했고,
    백엔드 보강 반영 시 이미지/파일/멱등/멤버가 그대로 작동한다(코드 수정 불필요).

환경변수:
  TALKHUB_BASE_URL        기본 https://api.nowlink.kr
  TALKHUB_BRIDGE_SECRET   inbound/ensure-room/upload 인증 헤더값 (백엔드 O2 와 공유)
  TALKHUB_MIRROR_ENABLED  "1" 이어야 실제 전송(기본 "0"=드라이/무동작, 안전)
  TALKHUB_TIMEOUT         HTTP 타임아웃 초 (기본 15)
  data/talkhub_mapping.json  {카톡방이름: {"external_room_id":..,"name":..,"members":[..]}} (선택)
"""
from __future__ import annotations

import os
import json
import time
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
MAPPING_FILE = DATA / "talkhub_mapping.json"

_DEFAULT_BASE = "https://api.nowlink.kr"


def _cfg() -> dict:
    base = (os.environ.get("TALKHUB_BASE_URL") or _DEFAULT_BASE).rstrip("/")
    try:
        timeout = float(os.environ.get("TALKHUB_TIMEOUT", "15"))
    except ValueError:
        timeout = 15.0
    return {
        "base": base,
        "secret": os.environ.get("TALKHUB_BRIDGE_SECRET") or "",
        "enabled": os.environ.get("TALKHUB_MIRROR_ENABLED", "0") == "1",
        "timeout": timeout,
    }


def is_enabled() -> bool:
    return _cfg()["enabled"]


def _headers() -> dict:
    c = _cfg()
    h = {"Content-Type": "application/json"}
    if c["secret"]:
        h["X-Bridge-Secret"] = c["secret"]
    return h


def _load_mapping() -> dict:
    try:
        return json.loads(MAPPING_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _room_cfg(kakao_name: str) -> dict:
    """카톡방 이름 → {external_room_id, name, members}. 매핑 없으면 이름 자체를 external_room_id 로."""
    m = _load_mapping().get(kakao_name) or {}
    return {
        "external_room_id": m.get("external_room_id") or kakao_name,
        "name": m.get("name") or kakao_name,
        "members": m.get("members") or [],
    }


def _external_id(external_room_id: str, msg: dict) -> str:
    s = f"{external_room_id}|{msg.get('sender','')}|{msg.get('time','')}|{(msg.get('content') or '')[:200]}"
    return hashlib.md5(s.encode("utf-8", errors="ignore")).hexdigest()


# ─────────────────────────────────────────────────────────
# HTTP 호출 (requests, 실패는 예외 대신 False/None 반환 → 모니터 크래시 방지)
# ─────────────────────────────────────────────────────────
def _post_json(path: str, payload: dict) -> tuple[bool, dict | None]:
    import requests
    c = _cfg()
    try:
        r = requests.post(c["base"] + path, json=payload, headers=_headers(), timeout=c["timeout"])
        if r.status_code < 300:
            try:
                return True, r.json()
            except Exception:
                return True, None
        print(f"  [talkhub] {path} → HTTP {r.status_code}: {r.text[:120]}", flush=True)
        return False, None
    except Exception as e:
        print(f"  [talkhub] {path} 예외: {type(e).__name__}: {e}", flush=True)
        return False, None


def ensure_room(kakao_name: str, member_user_ids: list[str] | None = None) -> str | None:
    """방+매핑+멤버 멱등 준비. 반환: internal_room_id (실패/미배포 시 None)."""
    rc = _room_cfg(kakao_name)
    members = list(member_user_ids or rc["members"])
    ok, data = _post_json("/bridge/kakao/ensure-room", {
        "external_room_id": rc["external_room_id"],
        "name": rc["name"],
        "member_user_ids": members,
    })
    if ok and data:
        return data.get("internal_room_id")
    # ensure-room 미배포(404 등) → 매핑이 이미 있으면 inbound 는 동작. 경고만.
    return None


def _upload_file(path) -> str | None:
    """이미지/파일을 talkhub 스토리지에 업로드 → file_id. (multipart /files/upload)"""
    import requests
    c = _cfg()
    p = Path(path)
    if not p.exists():
        return None
    headers = {}
    if c["secret"]:
        headers["X-Bridge-Secret"] = c["secret"]
    try:
        with open(p, "rb") as f:
            r = requests.post(c["base"] + "/files/upload", files={"file": (p.name, f)},
                              headers=headers, timeout=max(c["timeout"], 30))
        if r.status_code < 300:
            return (r.json() or {}).get("file_id")
        print(f"  [talkhub] /files/upload → HTTP {r.status_code}: {r.text[:120]}", flush=True)
    except Exception as e:
        print(f"  [talkhub] /files/upload 예외: {type(e).__name__}: {e}", flush=True)
    return None


def _inbound(external_room_id: str, external_id: str, sender: str, content: str,
             timestamp: str | None, attachments: list[dict]) -> bool:
    ok, _ = _post_json("/bridge/kakao/inbound", {
        "external_room_id": external_room_id,
        "external_id": external_id,
        "sender_name": sender or "?",
        "content": content,
        "timestamp": timestamp or None,
        "attachments": attachments,
    })
    return ok


# ─────────────────────────────────────────────────────────
# 공개 인터페이스 — kakaowork_router.send_delta_interleaved 미러
# ─────────────────────────────────────────────────────────
def send_delta_interleaved(kakaotalk_name: str, delta: str, photo_files: list | None = None,
                           *, delay: float = 0.3) -> dict:
    """카톡 delta 를 시간순으로 talkhub 브릿지에 미러. 반환 shape = kakaowork_router 와 동일."""
    from core.kakaowork_router import parse_delta_to_messages
    from core.sent_ledger import SentLedger

    stats = {
        "total_messages": 0, "text_sent": 0, "text_failed": 0, "text_skipped": 0,
        "photos_uploaded": 0, "photos_missing": 0, "trailing_uploaded": 0,
    }

    if not is_enabled():
        # 안전: 명시적으로 켜지 않으면 무동작(드라이). 로그만.
        print(f"  [talkhub] MIRROR 비활성(TALKHUB_MIRROR_ENABLED!=1) — '{kakaotalk_name}' 스킵", flush=True)
        return stats

    rc = _room_cfg(kakaotalk_name)
    ext_room = rc["external_room_id"]
    messages = parse_delta_to_messages(delta)
    stats["total_messages"] = len(messages)
    photo_iter = iter(list(photo_files or []))
    ledger = SentLedger(kakaotalk_name)

    for m in messages:
        content = (m.get("content") or "").strip()
        if not content and m.get("photo_count", 0) == 0:
            continue
        h = ledger.hash_msg(m)
        if ledger.seen(h):
            stats["text_skipped"] += 1
            # 이미 보낸 메시지의 사진도 이미 처리됐다고 보고 iter 소비
            for _ in range(m.get("photo_count", 0)):
                next(photo_iter, None)
            continue

        # 이 메시지에 배정된 사진 업로드 → attachments
        attachments: list[dict] = []
        for _ in range(m.get("photo_count", 0)):
            p = next(photo_iter, None)
            if p is None:
                stats["photos_missing"] += 1
                continue
            fid = _upload_file(p)
            if fid:
                attachments.append({"file_id": fid, "type": "image"})
                stats["photos_uploaded"] += 1
            else:
                stats["photos_missing"] += 1

        ext_id = _external_id(ext_room, m)
        if _inbound(ext_room, ext_id, m.get("sender", ""), content or "[사진]",
                    m.get("time"), attachments):
            stats["text_sent"] += 1
            ledger.add(h)
        else:
            stats["text_failed"] += 1
        time.sleep(delay)

    # 남은(초과) 사진 → trailing 첨부 1건으로 몰아 전송
    trailing = [p for p in photo_iter]
    if trailing:
        atts = []
        for p in trailing:
            fid = _upload_file(p)
            if fid:
                atts.append({"file_id": fid, "type": "image"})
        if atts:
            ext_id = hashlib.md5(f"{ext_room}|trailing|{time.strftime('%Y%m%d%H%M')}".encode()).hexdigest()
            if _inbound(ext_room, ext_id, "(사진)", "[사진]", None, atts):
                stats["trailing_uploaded"] += len(atts)

    print(f"  [talkhub] '{kakaotalk_name}'→'{ext_room}': 텍스트 {stats['text_sent']} "
          f"/ 사진 {stats['photos_uploaded']} / 스킵 {stats['text_skipped']} "
          f"/ 실패 {stats['text_failed']} / 누락 {stats['photos_missing']}", flush=True)
    return stats
