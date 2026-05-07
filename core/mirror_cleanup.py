"""
카카오워크 미러 방 중복 자동 청소.

중복 발생 원인:
- 카톡 방 이름 공백/오타 변형으로 동일 방이 여러 번 매핑됨
  예: "빌번호 및 입고수량확인방" vs "빌번호및 입고수량확인방"
- 각 이름 변형마다 create_mirror_room이 호출되어 별도 conv가 생김

청소 전략:
1. Bot API conversations.list로 봇이 속한 모든 방 수집
2. "[미러] X" 패턴의 X를 정규화(_normalize_room_name)하여 그룹핑
3. 각 그룹의 카노니컬 선택:
   - room_mapping.json에서 해당 정규화 키에 가장 먼저 등장하는 conv_id
   - 없으면 conv id 중 숫자가 가장 작은 것 (=가장 오래된 것)
4. 나머지 = 중복:
   - conversations.edit로 방 이름 → "[중복삭제] X" 리네이밍
   - 관리자는 카카오워크 앱에서 해당 방을 쉽게 찾아 나가기 가능
5. room_mapping.json 정리: 중복 conv_id 항목 제거, 정규화 키당 1개만 남김
6. (옵션) --ui 모드: Kakaowork 앱 검색창에서 "[중복삭제]" 방을 찾아
   pyautogui로 나가기 메뉴 실행 (best-effort)

Bot API에는 leave/delete 엔드포인트가 없으므로 최종 삭제는
관리자 UI 혹은 본 모듈의 --ui 자동화가 담당.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

from core.kakaowork_router import (
    API_BASE,
    _get_all_bot_conv_ids,
    _headers,
    _load_room_mapping,
    _save_room_mapping,
)
from core.drawer_handler import _normalize_room_name

load_dotenv(Path(__file__).parent.parent / ".env", override=True)

DATA_DIR = Path(__file__).parent.parent / "data"
CLEANUP_LOG = DATA_DIR / "mirror_cleanup_log.json"

MIRROR_PREFIX = "[미러] "
DELETE_MARK = "[중복삭제] "


# ═══════════════════════════════════════════════════════
# 1. 봇 방 전체 수집 (이름 포함)
# ═══════════════════════════════════════════════════════

def fetch_all_bot_conversations() -> list[dict]:
    """봇이 속한 모든 대화 (id + name)."""
    convs = []
    cursor = None
    for _ in range(10):  # 최대 10페이지 (1000개)
        params = {"limit": 100}
        if cursor:
            params["cursor"] = cursor
        try:
            r = requests.get(
                f"{API_BASE}/conversations.list",
                headers=_headers(),
                params=params,
                timeout=15,
            )
            d = r.json()
        except Exception as e:
            print(f"  [ERROR] conversations.list 실패: {e}", flush=True)
            break
        for c in d.get("conversations", []):
            convs.append({
                "id": str(c.get("id")),
                "name": c.get("name") or "",
                "type": c.get("type") or "",
                "users_count": c.get("users_count") or 0,
            })
        cursor = d.get("cursor")
        if not cursor:
            break
    return convs


# ═══════════════════════════════════════════════════════
# 2. 중복 탐지
# ═══════════════════════════════════════════════════════

def _extract_mirror_name(conv_name: str) -> str | None:
    """'[미러] X' 또는 '[중복삭제] X' → X. 아니면 None."""
    for prefix in (MIRROR_PREFIX, DELETE_MARK):
        if conv_name.startswith(prefix):
            return conv_name[len(prefix):].strip()
    return None


def find_duplicates() -> dict:
    """
    중복 미러 방 탐지 결과.

    Returns:
        {
            "all_mirrors": [{id, name, normalized}, ...],
            "groups": {normalized: [conv, conv, ...]},  # 그룹마다 여러 conv
            "duplicate_groups": {normalized: [conv, ...]},  # len > 1만
            "mapping_dup_keys": {conv_id: [key1, key2, ...]},  # 같은 id를 가리키는 키
            "orphan_mappings": [key, ...],  # mapping엔 있는데 실제 봇 방엔 없음
        }
    """
    mapping = _load_room_mapping()
    all_convs = fetch_all_bot_conversations()

    mirrors = []
    for c in all_convs:
        x = _extract_mirror_name(c["name"])
        if x:
            mirrors.append({
                "id": c["id"],
                "name": c["name"],
                "mirror_name": x,
                "normalized": _normalize_room_name(x),
                "users_count": c["users_count"],
            })

    # 그룹핑
    groups: dict[str, list[dict]] = {}
    for m in mirrors:
        groups.setdefault(m["normalized"], []).append(m)

    duplicate_groups = {k: v for k, v in groups.items() if len(v) > 1}

    # mapping.json에서 같은 conv_id를 참조하는 키들
    conv_to_keys: dict[str, list[str]] = {}
    for key, conv_id in mapping.items():
        conv_to_keys.setdefault(str(conv_id), []).append(key)
    mapping_dup_keys = {k: v for k, v in conv_to_keys.items() if len(v) > 1}

    # orphan: mapping엔 있지만 실제 봇 방엔 없음
    bot_ids = {m["id"] for m in mirrors}
    orphan_mappings = [k for k, v in mapping.items() if str(v) not in bot_ids]

    return {
        "all_mirrors": mirrors,
        "groups": groups,
        "duplicate_groups": duplicate_groups,
        "mapping_dup_keys": mapping_dup_keys,
        "orphan_mappings": orphan_mappings,
        "mapping": mapping,
    }


def choose_canonical(group: list[dict], mapping: dict) -> dict:
    """
    그룹에서 카노니컬 conv 선택.

    우선순위:
    1. room_mapping.json에서 가장 먼저 참조되는 conv_id
    2. conv_id 숫자 최소값 (=가장 오래된 방)
    """
    mapped_ids = set(str(v) for v in mapping.values())
    # 1순위: mapping 참조
    for m in group:
        if m["id"] in mapped_ids:
            return m
    # 2순위: id 최소 (오래된 방)
    return min(group, key=lambda m: int(m["id"]) if m["id"].isdigit() else float("inf"))


# ═══════════════════════════════════════════════════════
# 3. API로 이름 변경 (중복 마킹)
# ═══════════════════════════════════════════════════════

def rename_conversation(conv_id: str, new_name: str) -> bool:
    """conversations.edit로 방 이름 변경."""
    try:
        r = requests.post(
            f"{API_BASE}/conversations/{conv_id}/edit",
            headers=_headers(),
            json={"name": new_name},
            timeout=10,
        )
        data = r.json()
        ok = data.get("success", False)
        if not ok:
            print(f"  [RENAME] {conv_id} 실패: {data}", flush=True)
        return ok
    except Exception as e:
        print(f"  [RENAME] {conv_id} 예외: {e}", flush=True)
        return False


# ═══════════════════════════════════════════════════════
# 4. UI 자동화: Kakaowork 앱에서 방 찾아 나가기
# ═══════════════════════════════════════════════════════

def leave_marked_rooms_via_ui(marked: list[tuple[str, str]]) -> dict:
    """
    카카오워크 앱에서 [중복삭제] 표시 방을 나가기.

    검증된 패턴 (CLAUDE.md 기준):
      1. Bot API로 해당 방에 "↓ 청소 중" 메시지 전송 → 목록 맨 위로 이동
      2. 카카오워크 앱 활성화 → 첫 번째 방 클릭 (80, 60)
      3. ≡ 메뉴 클릭 → "채팅방 나가기" 선택 → 확인
    (Ctrl+F 검색은 포커스 점유 문제로 기피 — memory에 기록됨)

    Args:
        marked: [(conv_id, new_name), ...] — 리네이밍된 중복 방 목록

    Returns:
        {'left': int, 'failed': list[(conv_id, new_name)]}
    """
    import pyautogui
    from core.kakaowork_app import find_kakaowork_window

    left = 0
    failed: list[tuple[str, str]] = []

    try:
        win = find_kakaowork_window()
    except Exception as e:
        print(f"  [UI] 카카오워크 창 없음: {e}", flush=True)
        return {"left": 0, "failed": marked}

    CLEANUP_MARKER = "↓ 청소 중 ↓"

    for conv_id, name in marked:
        try:
            # 1) Bot API → 해당 방에 마커 전송 → 목록 맨 위로
            ok = False
            try:
                r = requests.post(
                    f"{API_BASE}/messages.send",
                    headers=_headers(),
                    json={"conversation_id": conv_id, "text": CLEANUP_MARKER},
                    timeout=10,
                )
                ok = r.json().get("success", False)
            except Exception as e:
                print(f"  [UI] {name} bump 실패: {e}", flush=True)
            if not ok:
                failed.append((conv_id, name))
                continue
            time.sleep(1.5)

            # 2) 카카오워크 창 재활성화
            try:
                win = find_kakaowork_window()
            except Exception:
                pass

            # 3) 첫 번째 방 클릭 (검증된 좌표 80, 60)
            pyautogui.click(win.left + 80, win.top + 60)
            time.sleep(1.2)

            # 4) ≡ 메뉴 클릭 — 채팅방 우상단 (win.right - 30, win.top + 90)
            menu_x = win.left + win.width - 30
            menu_y = win.top + 90
            pyautogui.click(menu_x, menu_y)
            time.sleep(1.0)

            # 5) 메뉴 최하단 "나가기" 선택 — End + Enter
            #    (실측 필요 — 카카오워크 메뉴 구조에 따라 달라짐)
            pyautogui.press("end")
            time.sleep(0.3)
            pyautogui.press("enter")
            time.sleep(1.2)

            # 6) 확인 다이얼로그 → Enter
            pyautogui.press("enter")
            time.sleep(1.0)

            left += 1
            print(f"  [UI] '{name}' ({conv_id}) 나가기 시도 완료", flush=True)

        except Exception as e:
            print(f"  [UI] '{name}' 실패: {e}", flush=True)
            failed.append((conv_id, name))
        # ESC로 팝업/메뉴 잔여 정리
        try:
            pyautogui.press("escape")
            pyautogui.press("escape")
        except Exception:
            pass
        time.sleep(0.5)

    return {"left": left, "failed": failed}


# ═══════════════════════════════════════════════════════
# 5. 오케스트레이션
# ═══════════════════════════════════════════════════════

def cleanup_duplicates(*, dry_run: bool = False, use_ui: bool = False) -> dict:
    """
    중복 미러 방 청소 파이프라인.

    Args:
        dry_run: True면 실제 변경 없이 리포트만
        use_ui: True면 Kakaowork 앱 UI 자동화로 나가기도 시도

    Returns:
        {
            "duplicates_found": int,
            "renamed": int,
            "mapping_cleaned": int,
            "ui_left": int,
            "report": {...},
        }
    """
    info = find_duplicates()
    dup_groups = info["duplicate_groups"]
    mapping = dict(info["mapping"])

    print(f"\n=== 중복 미러 방 탐지 결과 ===")
    print(f"  전체 미러 방: {len(info['all_mirrors'])}개")
    print(f"  중복 그룹: {len(dup_groups)}개")
    print(f"  mapping 중복 키: {len(info['mapping_dup_keys'])}개")
    print(f"  orphan mapping: {len(info['orphan_mappings'])}개")
    print()

    renamed_conv_ids: list[str] = []
    marked: list[tuple[str, str]] = []  # (conv_id, new_name)

    for norm, group in dup_groups.items():
        canonical = choose_canonical(group, mapping)
        non_canon = [m for m in group if m["id"] != canonical["id"]]
        print(
            f"  그룹 [{canonical['mirror_name']}]: 총 {len(group)}개 "
            f"(카노니컬 {canonical['id']}, 중복 {len(non_canon)}개)"
        )
        for m in non_canon:
            new_name = f"{DELETE_MARK}{m['mirror_name']}"
            print(f"    - {m['id']} '{m['name']}' → '{new_name}'")
            if not dry_run:
                ok = rename_conversation(m["id"], new_name)
                if ok:
                    renamed_conv_ids.append(m["id"])
                    marked.append((m["id"], new_name))

    # mapping.json 정리
    mapping_cleaned = 0
    if not dry_run:
        new_mapping: dict[str, str] = {}
        seen_ids: set[str] = set()
        seen_norms: set[str] = set()

        # 삭제 대상 conv_id 집합
        removed = set(renamed_conv_ids)

        # 원래 순서 유지하되 정규화 키당 1개, conv_id 유일성 보장
        for key, cid in mapping.items():
            cid_s = str(cid)
            if cid_s in removed:
                continue  # 리네이밍된 중복 제거
            norm = _normalize_room_name(key)
            if cid_s in seen_ids:
                continue  # 같은 conv_id 재등장 스킵 (예: 오타 키)
            if norm in seen_norms:
                # 정규화 키가 이미 있음 → 중복 키 제거
                continue
            new_mapping[key] = cid_s
            seen_ids.add(cid_s)
            seen_norms.add(norm)

        mapping_cleaned = len(mapping) - len(new_mapping)
        _save_room_mapping(new_mapping)

    # UI 자동화
    ui_result = {"left": 0, "failed": []}
    if use_ui and marked and not dry_run:
        print(f"\n  [UI] Kakaowork 앱에서 {len(marked)}개 방 나가기 시도...")
        ui_result = leave_marked_rooms_via_ui(marked)

    # 로그 저장
    log_entry = {
        "timestamp": time.time(),
        "dry_run": dry_run,
        "duplicates_found": sum(len(g) - 1 for g in dup_groups.values()),
        "renamed": len(renamed_conv_ids),
        "mapping_cleaned": mapping_cleaned,
        "ui_left": ui_result["left"],
        "renamed_ids": renamed_conv_ids,
        "ui_failed": ui_result["failed"],
    }

    if not dry_run:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        try:
            existing = json.loads(CLEANUP_LOG.read_text(encoding="utf-8")) if CLEANUP_LOG.exists() else []
        except Exception:
            existing = []
        if not isinstance(existing, list):
            existing = []
        existing.append(log_entry)
        CLEANUP_LOG.write_text(
            json.dumps(existing, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    print(f"\n=== 청소 결과 ===")
    print(f"  탐지된 중복: {log_entry['duplicates_found']}개")
    print(f"  리네이밍 완료: {log_entry['renamed']}개")
    print(f"  mapping 정리: {log_entry['mapping_cleaned']}개")
    if use_ui:
        print(f"  UI 나가기 성공: {log_entry['ui_left']}개, 실패: {len(ui_result['failed'])}개")
    if dry_run:
        print(f"  (dry-run 모드 — 실제 변경 없음)")
    print()

    return {
        "duplicates_found": log_entry["duplicates_found"],
        "renamed": log_entry["renamed"],
        "mapping_cleaned": log_entry["mapping_cleaned"],
        "ui_left": log_entry["ui_left"],
        "report": info,
    }


# ═══════════════════════════════════════════════════════
# 6. NV## prefix 일괄 적용 (room_mapping.json 기준)
# ═══════════════════════════════════════════════════════

NV_MAPPING_FILE = DATA_DIR / "room_mapping_nv.json"
RENAME_NV_LOG = DATA_DIR / "rename_nv_log.json"


def _strip_nv_prefix(name: str) -> str:
    """기존 'NV01:수입방' 또는 'NV01: 수입방' 또는 '[미러] 수입방' → '수입방'."""
    import re
    s = (name or "").strip()
    m = re.match(r"^NV\d{1,3}\s*:\s*(.+)$", s)
    if m:
        return m.group(1).strip()
    if s.startswith(MIRROR_PREFIX):
        return s[len(MIRROR_PREFIX):].strip()
    return s


def apply_nv_naming(*, dry_run: bool = False) -> dict:
    """
    room_mapping.json 순서대로 미러 방 이름을 'NV{NN}:원본이름' 으로 일괄 변경.

    규칙:
      - 인덱스는 room_mapping.json 의 삽입 순서 (1부터 zero-pad 2자리: NV01..NV99)
      - 이미 'NV{NN}:원본이름' 이면 스킵 (idempotent)
      - '[중복삭제]' 마킹된 방은 건너뜀
      - mapping 의 conv_id 가 봇 방 목록에 없으면 orphan 으로 보고만 함
      - 성공한 항목으로 room_mapping_nv.json 자동 갱신

    Returns:
        {
          "planned": [{idx, room, conv_id, current, target, action}],
          "renamed": int,
          "skipped": int,
          "failed": [{conv_id, target, error}],
          "orphans": [room],
        }
    """
    mapping = _load_room_mapping()
    if not mapping:
        print("[NV-RENAME] room_mapping.json 비어있음 — 중단", flush=True)
        return {"planned": [], "renamed": 0, "skipped": 0, "failed": [], "orphans": []}

    bot_convs = fetch_all_bot_conversations()
    by_id = {c["id"]: c for c in bot_convs}

    planned: list[dict] = []
    failed: list[dict] = []
    orphans: list[str] = []
    renamed = 0
    skipped = 0
    new_nv_mapping: dict = {}

    for idx, (room, conv_id) in enumerate(mapping.items(), start=1):
        cid = str(conv_id)
        nv_code = f"NV{idx:02d}"
        target = f"{nv_code}:{room}"
        nv_entry = {"conv_id": cid, "nv_code": nv_code, "nv_name": target}

        conv = by_id.get(cid)
        if not conv:
            orphans.append(room)
            planned.append({
                "idx": idx, "room": room, "conv_id": cid,
                "current": "(봇 방 목록에 없음)", "target": target, "action": "ORPHAN",
            })
            continue

        current = conv["name"] or ""
        # 중복삭제 마킹된 방은 손대지 않음
        if current.startswith(DELETE_MARK):
            planned.append({
                "idx": idx, "room": room, "conv_id": cid,
                "current": current, "target": target, "action": "SKIP_DELETED",
            })
            skipped += 1
            continue

        if current == target:
            planned.append({
                "idx": idx, "room": room, "conv_id": cid,
                "current": current, "target": target, "action": "OK_ALREADY",
            })
            skipped += 1
            new_nv_mapping[room] = nv_entry
            continue

        planned.append({
            "idx": idx, "room": room, "conv_id": cid,
            "current": current, "target": target, "action": "RENAME",
        })
        if dry_run:
            continue

        ok = rename_conversation(cid, target)
        if ok:
            renamed += 1
            new_nv_mapping[room] = nv_entry
        else:
            failed.append({"conv_id": cid, "target": target, "error": "API false"})
        time.sleep(0.4)  # API rate-limit 보호

    # 출력 표
    print(f"\n=== NV## 리네이밍 계획 (총 {len(planned)}개) ===")
    print(f"  {'#':>2} {'ACTION':<14} {'CURRENT':<40} → {'TARGET'}")
    for p in planned:
        cur = (p["current"] or "")[:38]
        tgt = (p["target"] or "")[:40]
        print(f"  {p['idx']:>2} {p['action']:<14} {cur:<40} → {tgt}")
    print(f"\n  실행: rename={renamed}, skip={skipped}, fail={len(failed)}, orphan={len(orphans)}")
    if dry_run:
        print(f"  (dry-run — 실제 변경 없음)")

    if not dry_run:
        # nv mapping 갱신 (renamed/already_ok 만 포함)
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        try:
            existing_nv = json.loads(NV_MAPPING_FILE.read_text(encoding="utf-8")) if NV_MAPPING_FILE.exists() else {}
        except Exception:
            existing_nv = {}
        if not isinstance(existing_nv, dict):
            existing_nv = {}
        existing_nv.update(new_nv_mapping)
        NV_MAPPING_FILE.write_text(
            json.dumps(existing_nv, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # 로그 누적
        log_entry = {
            "timestamp": time.time(),
            "renamed": renamed,
            "skipped": skipped,
            "failed": failed,
            "orphans": orphans,
            "planned": planned,
        }
        try:
            existing_log = json.loads(RENAME_NV_LOG.read_text(encoding="utf-8")) if RENAME_NV_LOG.exists() else []
        except Exception:
            existing_log = []
        if not isinstance(existing_log, list):
            existing_log = []
        existing_log.append(log_entry)
        RENAME_NV_LOG.write_text(
            json.dumps(existing_log, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    return {
        "planned": planned,
        "renamed": renamed,
        "skipped": skipped,
        "failed": failed,
        "orphans": orphans,
    }


# ═══════════════════════════════════════════════════════
# 7. '[미러] X' → 'X' prefix 제거 (원본 이름 그대로)
# ═══════════════════════════════════════════════════════

STRIP_LOG = DATA_DIR / "strip_mirror_log.json"


def strip_mirror_prefixes(*, dry_run: bool = False) -> dict:
    """
    봇 방의 '[미러] X' prefix를 제거해 'X' 로 리네이밍.

    - '[중복삭제]' 마킹된 방은 보호 (건드리지 않음)
    - 이미 prefix 없는 방은 스킵 (멱등)
    - 같은 target 으로 수렴하는 미러방 다수 → conv_id 가 가장 작은 것(=오래된 것)만
      변경하고 나머지는 SKIP_DUP. (cleanup-mirrors 를 먼저 돌리면 사라짐)
    - target 과 동일한 이름의 비-미러 방이 별도 존재하면 SKIP_NAMECLASH
    """
    bot_convs = fetch_all_bot_conversations()

    # 미러 방 추출
    mirror_list: list[tuple[str, str, str]] = []  # (cid, current, target)
    for c in bot_convs:
        name = c["name"] or ""
        if name.startswith(MIRROR_PREFIX):
            target = name[len(MIRROR_PREFIX):].strip()
            if target:
                mirror_list.append((c["id"], name, target))

    # target 별로 동명 미러 묶기 → 카노니컬 선택 (id 작은 것 = 오래된 것)
    by_target: dict[str, list[tuple[str, str]]] = {}
    for cid, cur, tgt in mirror_list:
        by_target.setdefault(tgt, []).append((cid, cur))
    canonical_per_target = {
        tgt: min(group, key=lambda x: int(x[0]) if x[0].isdigit() else float("inf"))[0]
        for tgt, group in by_target.items()
    }

    # 비-미러 방 이름 (충돌 검사용)
    non_mirror_names = {
        c["name"] for c in bot_convs
        if c["name"] and not c["name"].startswith(MIRROR_PREFIX)
        and not c["name"].startswith(DELETE_MARK)
    }

    planned: list[dict] = []
    failed: list[dict] = []
    conflicts: list[dict] = []
    renamed = 0
    skipped = 0

    for cid, current, target in mirror_list:
        # 1) 동명 미러 다수 → 카노니컬만 진행
        if cid != canonical_per_target[target]:
            planned.append({
                "id": cid, "current": current, "target": target,
                "action": "SKIP_DUP",
            })
            conflicts.append({
                "id": cid, "current": current, "target": target,
                "reason": f"동명 미러 다수 (카노니컬={canonical_per_target[target]})",
            })
            continue

        # 2) 비-미러 방과 이름 충돌
        if target in non_mirror_names:
            planned.append({
                "id": cid, "current": current, "target": target,
                "action": "SKIP_NAMECLASH",
            })
            conflicts.append({
                "id": cid, "current": current, "target": target,
                "reason": "동명 비-미러방 존재",
            })
            continue

        # 3) 멱등 — 이미 target 인 케이스는 mirror_list 단계에서 제외됨
        planned.append({
            "id": cid, "current": current, "target": target,
            "action": "RENAME",
        })
        if dry_run:
            continue

        ok = rename_conversation(cid, target)
        if ok:
            renamed += 1
        else:
            failed.append({"id": cid, "target": target, "error": "API false"})
        time.sleep(0.4)  # API rate-limit 보호

    # 출력
    rename_cnt = sum(1 for p in planned if p["action"] == "RENAME")
    print(f"\n=== '[미러] X' → 'X' prefix 제거 계획 (총 {len(planned)}개) ===")
    print(f"  {'ACTION':<16} {'CURRENT':<42} → {'TARGET'}")
    for p in planned:
        cur = (p["current"] or "")[:40]
        tgt = (p["target"] or "")[:42]
        print(f"  {p['action']:<16} {cur:<42} → {tgt}")
    print(
        f"\n  계획: rename={rename_cnt}, dup={sum(1 for p in planned if p['action']=='SKIP_DUP')}, "
        f"clash={sum(1 for p in planned if p['action']=='SKIP_NAMECLASH')}"
    )
    print(f"  실행: renamed={renamed}, failed={len(failed)}")
    if dry_run:
        print(f"  (dry-run — 실제 변경 없음)")

    if not dry_run:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        log_entry = {
            "timestamp": time.time(),
            "renamed": renamed,
            "failed": failed,
            "conflicts": conflicts,
            "planned": planned,
        }
        try:
            existing = json.loads(STRIP_LOG.read_text(encoding="utf-8")) if STRIP_LOG.exists() else []
        except Exception:
            existing = []
        if not isinstance(existing, list):
            existing = []
        existing.append(log_entry)
        STRIP_LOG.write_text(
            json.dumps(existing, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    return {
        "planned": planned,
        "renamed": renamed,
        "skipped": skipped,
        "conflicts": conflicts,
        "failed": failed,
    }


# ═══════════════════════════════════════════════════════
# 8. 미러 방에 멤버 일괄 초대 (그룹 사이즈 ≥ 3 만들기)
# ═══════════════════════════════════════════════════════

INVITE_LOG = DATA_DIR / "invite_member_log.json"

# 카카오워크 Bot API 의 invite 엔드포인트는 공식 문서가 다소 모호하여
# 후보 3개를 순차 시도. 404/실패면 다음 후보로.
def _invite_attempts(conv_id: str, user_ids: list[str]) -> list[tuple[str, str, dict]]:
    """(label, full_url, json_body) 후보 리스트."""
    uids_int = [int(u) for u in user_ids]
    return [
        ("path-invite",
         f"{API_BASE}/conversations/{conv_id}/invite",
         {"user_ids": uids_int}),
        ("body-invite",
         f"{API_BASE}/conversations.invite",
         {"conversation_id": int(conv_id), "user_ids": uids_int}),
        ("users-invite",
         f"{API_BASE}/conversations/{conv_id}/users/invite",
         {"user_ids": uids_int}),
    ]


def invite_users_to_conv(conv_id: str, user_ids: list[str]) -> tuple[bool, str]:
    """단일 conv 에 user_ids 초대. 엔드포인트 후보 순회."""
    last_detail = ""
    for label, url, body in _invite_attempts(conv_id, user_ids):
        try:
            r = requests.post(url, headers=_headers(), json=body, timeout=10)
            if r.status_code == 404:
                last_detail = f"{label} 404"
                continue
            try:
                data = r.json()
            except Exception:
                last_detail = f"{label} non-json {r.status_code} {r.text[:80]}"
                continue
            if data.get("success"):
                return True, f"{label} OK"
            err = (data.get("error") or {}).get("code", "") or data.get("message", "") or ""
            err_str = str(err).lower()
            if any(k in err_str for k in ("already", "duplicate", "exist")):
                return True, f"{label} ALREADY_MEMBER"
            last_detail = f"{label} {data}"
        except Exception as e:
            last_detail = f"{label} 예외: {e}"
    return False, last_detail or "all endpoints failed"


def invite_users_to_mirrors(
    user_ids: list[str],
    *,
    dry_run: bool = False,
    skip_marked: bool = True,
    id_prefix: str | None = None,
) -> dict:
    """
    봇이 속한 group conv 들에 user_ids 초대.

    Args:
        user_ids: 초대할 카카오워크 user_id 리스트 (문자열 ID)
        dry_run: True 면 API 호출 없이 계획만 출력
        skip_marked: True 면 '[중복삭제]' prefix 방은 건너뜀
        id_prefix: 지정 시 conv_id 가 해당 prefix 로 시작하는 방만 대상
                   (예: '968666' 으로 미러방만 좁히기)

    Returns:
        {planned, invited, skipped, failed}
    """
    bot_convs = fetch_all_bot_conversations()

    # 대상 = group type + (옵션) [중복삭제] 제외 + (옵션) id_prefix 일치
    targets: list[dict] = []
    for c in bot_convs:
        name = c.get("name") or ""
        if c.get("type") != "group":
            continue
        if skip_marked and name.startswith(DELETE_MARK):
            continue
        if id_prefix and not str(c.get("id", "")).startswith(id_prefix):
            continue
        targets.append(c)

    planned: list[dict] = []
    invited = 0
    skipped = 0
    failed: list[dict] = []

    for c in targets:
        cid = c["id"]
        users_now = int(c.get("users_count") or 0)
        # 이미 N+ 명이면 (봇 + 임재용 본인 + 초대 대상) 스킵 가능. 단, 어떤 user_id 가
        # 들어가있는지 모르므로 일괄 시도하고 ALREADY_MEMBER 응답 받기.
        planned.append({
            "id": cid, "name": c["name"], "users_before": users_now,
            "user_ids": user_ids,
        })
        if dry_run:
            continue

        ok, detail = invite_users_to_conv(cid, user_ids)
        if ok:
            invited += 1
            planned[-1]["result"] = detail
        else:
            failed.append({"id": cid, "name": c["name"], "detail": detail})
            planned[-1]["result"] = f"FAIL: {detail}"
        time.sleep(0.4)

    print(f"\n=== 미러방 초대 계획 (대상 {len(targets)}개, user_ids={user_ids}) ===")
    print(f"  {'id':>20}  {'before':>6}  {'name':<30}  result")
    for p in planned:
        res = p.get("result", "(dry-run)")
        nm = p["name"][:28]
        print(f"  {p['id']:>20}  {p['users_before']:>6}  {nm:<30}  {res}")
    print(f"\n  실행: invited={invited}, failed={len(failed)}")
    if dry_run:
        print(f"  (dry-run — 실제 변경 없음)")

    if not dry_run:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        log_entry = {
            "timestamp": time.time(),
            "user_ids": user_ids,
            "invited": invited,
            "failed": failed,
            "planned": planned,
        }
        try:
            existing = json.loads(INVITE_LOG.read_text(encoding="utf-8")) if INVITE_LOG.exists() else []
        except Exception:
            existing = []
        if not isinstance(existing, list):
            existing = []
        existing.append(log_entry)
        INVITE_LOG.write_text(
            json.dumps(existing, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    return {
        "planned": planned,
        "invited": invited,
        "skipped": skipped,
        "failed": failed,
    }


# ═══════════════════════════════════════════════════════
# 9. room_mapping.json 을 현재 봇 conv 기반으로 자동 갱신
# ═══════════════════════════════════════════════════════

ROOM_MAPPING_FILE = DATA_DIR / "room_mapping.json"
SYNC_MAPPING_LOG = DATA_DIR / "sync_mapping_log.json"


def sync_room_mapping(*, dry_run: bool = False) -> dict:
    """
    room_mapping.json 의 키(카톡 방 이름)와 봇 conv 의 name 을 매칭해
    conv_id 를 실제 봇 ID 로 갱신.

    매칭 규칙:
      1. 정확 매칭: mapping_key == conv_name
      2. 정규화 매칭: _normalize_room_name(mapping_key) == _normalize_room_name(conv_name)
      3. (옵션) '[중복삭제]' 마킹된 conv 는 제외

    Returns:
        {planned, updated, unchanged, unmatched, ambiguous}
    """
    mapping = _load_room_mapping()
    if not mapping:
        print("[SYNC] room_mapping.json 비어있음", flush=True)
        return {"planned": [], "updated": 0, "unchanged": 0, "unmatched": [], "ambiguous": []}

    bot_convs = fetch_all_bot_conversations()
    # name 기준 인덱스 — [중복삭제] 제외, group only
    name_to_convs: dict[str, list[dict]] = {}
    norm_to_convs: dict[str, list[dict]] = {}
    for c in bot_convs:
        nm = c.get("name") or ""
        if not nm or nm.startswith(DELETE_MARK):
            continue
        if c.get("type") != "group":
            continue
        name_to_convs.setdefault(nm, []).append(c)
        norm_to_convs.setdefault(_normalize_room_name(nm), []).append(c)

    planned: list[dict] = []
    new_mapping: dict[str, str] = {}
    updated = 0
    unchanged = 0
    unmatched: list[str] = []
    ambiguous: list[dict] = []

    for room_name, old_cid in mapping.items():
        old_cid_s = str(old_cid)

        # 1) 정확 매칭
        candidates = name_to_convs.get(room_name) or []
        match_kind = "EXACT"
        # 2) 정규화 매칭
        if not candidates:
            candidates = norm_to_convs.get(_normalize_room_name(room_name)) or []
            match_kind = "NORMALIZED"

        if not candidates:
            planned.append({
                "room": room_name, "old_cid": old_cid_s, "new_cid": None,
                "action": "UNMATCHED",
            })
            unmatched.append(room_name)
            new_mapping[room_name] = old_cid_s  # 기존 값 보존
            continue

        if len(candidates) > 1:
            chosen = min(candidates, key=lambda c: int(c["id"]) if c["id"].isdigit() else float("inf"))
            ambiguous.append({
                "room": room_name,
                "candidates": [{"id": c["id"], "name": c["name"]} for c in candidates],
                "chosen": chosen["id"],
            })
        else:
            chosen = candidates[0]

        new_cid = chosen["id"]
        if new_cid == old_cid_s:
            planned.append({
                "room": room_name, "old_cid": old_cid_s, "new_cid": new_cid,
                "action": "UNCHANGED", "match": match_kind,
            })
            unchanged += 1
            new_mapping[room_name] = new_cid
        else:
            planned.append({
                "room": room_name, "old_cid": old_cid_s, "new_cid": new_cid,
                "action": "UPDATE", "match": match_kind,
            })
            updated += 1
            new_mapping[room_name] = new_cid

    # 출력
    print(f"\n=== room_mapping.json 동기화 (총 {len(planned)}개) ===")
    for p in planned:
        room = p["room"][:30]
        if p["action"] == "UNMATCHED":
            print(f"  UNMATCHED      {room:<32}  old={p['old_cid']}  → (봇 conv 에 동명 없음)")
        elif p["action"] == "UNCHANGED":
            print(f"  UNCHANGED      {room:<32}  cid={p['new_cid']}")
        else:
            print(f"  UPDATE[{p['match']:<10}] {room:<32}  {p['old_cid']} → {p['new_cid']}")
    if ambiguous:
        print(f"\n  ⚠️  ambiguous {len(ambiguous)}건 (동명 conv 다수 — 가장 작은 id 선택):")
        for a in ambiguous:
            print(f"    {a['room']}: chosen={a['chosen']} from {[c['id'] for c in a['candidates']]}")
    print(f"\n  결과: update={updated}, unchanged={unchanged}, unmatched={len(unmatched)}, ambiguous={len(ambiguous)}")
    if dry_run:
        print(f"  (dry-run — 파일 변경 없음)")

    if not dry_run and (updated > 0 or unmatched):
        # 백업 + 저장
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        backup = ROOM_MAPPING_FILE.with_suffix(".json.bak")
        try:
            if ROOM_MAPPING_FILE.exists():
                backup.write_text(ROOM_MAPPING_FILE.read_text(encoding="utf-8"), encoding="utf-8")
        except Exception as e:
            print(f"  [WARN] 백업 실패: {e}")
        _save_room_mapping(new_mapping)

        log_entry = {
            "timestamp": time.time(),
            "updated": updated,
            "unchanged": unchanged,
            "unmatched": unmatched,
            "ambiguous": ambiguous,
            "planned": planned,
        }
        try:
            existing = json.loads(SYNC_MAPPING_LOG.read_text(encoding="utf-8")) if SYNC_MAPPING_LOG.exists() else []
        except Exception:
            existing = []
        if not isinstance(existing, list):
            existing = []
        existing.append(log_entry)
        SYNC_MAPPING_LOG.write_text(
            json.dumps(existing, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    return {
        "planned": planned,
        "updated": updated,
        "unchanged": unchanged,
        "unmatched": unmatched,
        "ambiguous": ambiguous,
    }


if __name__ == "__main__":
    import sys
    if "rename-nv" in sys.argv or "rename_nv" in sys.argv:
        apply_nv_naming(dry_run="--dry-run" in sys.argv)
    elif "strip-mirror" in sys.argv or "strip_mirror" in sys.argv:
        strip_mirror_prefixes(dry_run="--dry-run" in sys.argv)
    elif "invite-member" in sys.argv or "invite_member" in sys.argv:
        uids = [a for a in sys.argv[1:] if a.isdigit()]
        invite_users_to_mirrors(uids, dry_run="--dry-run" in sys.argv)
    elif "sync-mapping" in sys.argv or "sync_mapping" in sys.argv:
        sync_room_mapping(dry_run="--dry-run" in sys.argv)
    else:
        dry = "--dry-run" in sys.argv
        ui = "--ui" in sys.argv
        cleanup_duplicates(dry_run=dry, use_ui=ui)
