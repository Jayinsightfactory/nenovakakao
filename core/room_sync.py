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


# ─────────────────────────────────────────────────────────────
# 신규 초대방 자동 채택 (그룹방만 → 워크 미러 자동 생성 + 등록)
# 분류 휴리스틱은 tools/classify_new_rooms.py 와 동일 (core 자기완결 위해 복제)
# ─────────────────────────────────────────────────────────────
import re as _re

_SYSTEM_KEYWORDS = ["알림톡", "뱅크", "은행", "카드", "결제", "게시판",
                    "공지", "모아보기", "덧글", "댓글", "광고", "스팸"]
_JOB_TITLES = ["차장", "과장", "대리", "부장", "팀장", "이사", "사장", "대표", "님"]


def _is_system_room(name: str) -> bool:
    return any(kw in name for kw in _SYSTEM_KEYWORDS)


def _is_1to1_room(name: str) -> bool:
    """단일 한국 이름 또는 직책으로 끝나는 1:1 채팅 추정."""
    for t in _JOB_TITLES:
        if name.endswith(t):
            return True
    if not _re.search(r"[\s+&,]", name) and _re.fullmatch(r"[가-힣]{2,4}", name):
        return True
    return False


def _is_group_room(name: str) -> bool:
    """단체방 시그널: +, &, 쉼표 다수, '방'/'팀' 접미, '네노바' 포함 거래처 단체방."""
    if "+" in name or "&" in name:
        return True
    if name.count(",") >= 2:
        return True
    # '방'/'팀'으로 끝나면 거의 단체방 (수입방/견적방/현장단체방/영업지원팀 등).
    # 사람 이름이 '방/팀'으로 끝나는 경우는 극히 드물어 무조건 그룹으로 본다.
    if name.endswith("방") or name.endswith("팀"):
        return True
    if "네노바" in name and len(name) > 3 and not _is_1to1_room(name):
        return True
    return False


def adopt_new_rooms(window=None, *, auto_create: bool = True) -> dict:
    """카톡 신규 초대방 자동 채택.

    흐름: 카톡 OCR 스캔 → mapping/selected 에 없는 새 방 → '명확한 그룹방'만 골라
          워크 미러 자동 생성(ensure_mirror_for_rooms) + room_mapping/selected 등록.
    1:1·시스템·모호한 외부방은 생성하지 않고 검토 목록으로만 남긴다(오생성 방지).

    Returns:
        {scanned, new, adopted:[...], created, skipped_personal:[...],
         skipped_system:[...], review_external:[...]}
    """
    from core.room_scanner import scan_rooms_full

    if window is None:
        from core.window_detector import find_kakaotalk_window
        window = find_kakaotalk_window()

    captures = ROOT / "captures"
    try:
        rooms = scan_rooms_full(window, captures)
    except Exception as e:
        print(f"[ADOPT] 스캔 실패: {e}", flush=True)
        return {"error": str(e), "scanned": 0, "new": 0, "adopted": [], "created": 0}

    try:
        mapping = json.loads(MAPPING_FILE.read_text(encoding="utf-8"))
    except Exception:
        mapping = {}

    # OCR 잡음 방어: 단순 공백제거가 아니라 fuzzy(글자 변형) 매칭으로 비교.
    # (2026-05-25 사고: OCR 이 같은 방을 수십 변형으로 읽어 85개 junk 방 생성됨)
    from difflib import SequenceMatcher

    def _normf(s: str) -> str:
        return _re.sub(r"[\s\[\]()._\-\"'&+,/]+", "", (s or "")).lower()

    def _similar(a: str, b: str) -> float:
        na, nb = _normf(a), _normf(b)
        if not na or not nb:
            return 0.0
        return SequenceMatcher(None, na, nb).ratio()

    FUZZ = 0.82
    existing_keys = list(mapping.keys())
    existing_norm = {_normf(k) for k in existing_keys}

    def _matches_existing(n: str) -> bool:
        if _normf(n) in existing_norm:
            return True
        return any(_similar(n, k) >= FUZZ for k in existing_keys)

    scanned_names = [r.get("name", "").strip() for r in rooms if r.get("name")]
    # 1) 기존 매핑과 fuzzy 일치(OCR 변형 포함)면 신규 아님
    new_names = [n for n in scanned_names if n and not _matches_existing(n)]
    # 2) 신규 후보들 사이 OCR 변형 흡수 — 대표 1개만 (긴 이름 우선)
    reps: list[str] = []
    for n in sorted(set(new_names), key=lambda x: (-len(x), x)):
        if any(_similar(n, r) >= FUZZ for r in reps):
            continue
        reps.append(n)

    adopted, skip_personal, skip_system, review_ext = [], [], [], []
    for n in reps:
        if _is_system_room(n):
            skip_system.append(n)
        elif _is_group_room(n):
            adopted.append(n)
        elif _is_1to1_room(n):
            skip_personal.append(n)
        else:
            review_ext.append(n)  # 모호 → 자동생성 안 함, 검토용

    # 3) 안전장치: 채택 후보가 비정상적으로 많으면 OCR 잡음 → 자동생성 중단(검토만)
    MAX_AUTO = 12
    if auto_create and len(adopted) > MAX_AUTO:
        print(f"[ADOPT] 채택 후보 {len(adopted)}개 > {MAX_AUTO} — OCR 잡음 의심 → "
              f"자동생성 중단(검토 목록만). 확인 후 소수일 때만 생성하세요.", flush=True)
        review_ext = adopted + review_ext
        adopted = []
        auto_create = False

    created = 0
    if adopted and auto_create:
        from core.kakaowork_router import ensure_mirror_for_rooms
        res = ensure_mirror_for_rooms(adopted)
        created = res.get("created", 0)
        # mapping 갱신됐으니 selected 도 보충
        sync_selected_from_mapping()

    # 로그
    try:
        log = DATA_DIR / "adopt_new_rooms_log.json"
        from datetime import datetime as _dt
        rec = {
            "ts": _dt.now().isoformat(timespec="seconds"),
            "scanned": len(scanned_names), "new": len(new_names),
            "adopted": adopted, "created": created,
            "skipped_personal": skip_personal, "skipped_system": skip_system,
            "review_external": review_ext,
        }
        prev = []
        if log.exists():
            try:
                prev = json.loads(log.read_text(encoding="utf-8"))
            except Exception:
                prev = []
        prev.append(rec)
        log.write_text(json.dumps(prev, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

    print(f"[ADOPT] 스캔 {len(scanned_names)} / 신규 {len(new_names)} → "
          f"그룹방 채택 {len(adopted)}(생성 {created}) / "
          f"1:1 제외 {len(skip_personal)} / 시스템 제외 {len(skip_system)} / "
          f"검토필요 {len(review_ext)}", flush=True)
    if adopted:
        print(f"        채택: {adopted}", flush=True)
    if review_ext:
        print(f"        검토필요(자동생성 안 함): {review_ext}", flush=True)

    return {
        "scanned": len(scanned_names), "new": len(new_names),
        "adopted": adopted, "created": created,
        "skipped_personal": skip_personal, "skipped_system": skip_system,
        "review_external": review_ext,
    }


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
