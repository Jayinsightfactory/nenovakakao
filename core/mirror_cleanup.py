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


if __name__ == "__main__":
    import sys
    dry = "--dry-run" in sys.argv
    ui = "--ui" in sys.argv
    cleanup_duplicates(dry_run=dry, use_ui=ui)
