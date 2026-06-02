"""
워크→카톡 자동 양방향 브릿지 (Vision 룸리스트 델타 기반).

설계:
  1. 주기적(N초)으로 KW 룸리스트 캡처 → Claude Opus 추출
  2. 직전 상태와 diff → preview 가 변경된 방 = 새 메시지 도착
  3. 그 메시지가 "우리 봇이 워크로 보낸 메시지"면 skip(무한루프 방지)
  4. 매핑된 카톡 방 이름으로 해석 → kakao_win32.send_message_to_room 으로 카톡 송신
  5. 카톡 락(_kakao_lock) 우선 요청 → 모니터/답장서버와 자동 조정

상태 파일:
  data/work_vision_state.json   — 직전 사이클 룸리스트 (delta 비교용)
  data/work_sent_recent.json    — 우리가 워크로 보낸 최근 메시지 (방당 최대 N건)

CLI:
  python main.py work-bridge                 # 데몬 (interval 20s, 실제 송신)
  python main.py work-bridge --dry-run       # 송신 안 함, 감지/필터링만 로그
  python main.py work-bridge --once          # 1사이클만 (테스트)
  python main.py work-bridge --interval 30   # 30초마다
"""
from __future__ import annotations

import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SENT_RECENT = DATA / "work_sent_recent.json"
VISION_STATE = DATA / "work_vision_state.json"
MAX_PER_ROOM = 40  # 방당 최근 N건 보관 (loop 필터링)
SENT_TTL_SEC = 7200  # 2시간 — 그 이후 entry 만료 (메모리/판별 단순화)


# ─────────────────────────────────────────────────────────
# 1) 우리 봇이 워크로 보낸 메시지 기록 (loop 방지)
# ─────────────────────────────────────────────────────────

def append_sent(kakaotalk_room: str, text: str) -> None:
    """워크 미러방에 봇으로 보낸 메시지 1건 기록. 키 = 카톡 방 이름.

    호출자: kakaowork_router._send_single 직후 (텍스트), send_to_mirror_room,
    send_reply_button, kakaowork_reactive._post_send_confirmation 등.
    """
    if not kakaotalk_room or not text:
        return
    try:
        DATA.mkdir(parents=True, exist_ok=True)
        try:
            data = json.loads(SENT_RECENT.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        if not isinstance(data, dict):
            data = {}
        now = time.time()
        bucket = data.get(kakaotalk_room) or []
        bucket = [e for e in bucket if isinstance(e, dict) and now - (e.get("ts") or 0) < SENT_TTL_SEC]
        bucket.append({"text": text, "ts": now})
        if len(bucket) > MAX_PER_ROOM:
            bucket = bucket[-MAX_PER_ROOM:]
        data[kakaotalk_room] = bucket
        SENT_RECENT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _is_our_message(kakaotalk_room: str, preview: str) -> bool:
    """preview 가 우리가 보낸 최근 메시지와 '접두사 일치'면 True (loop 차단).

    Vision 의 preview 는 KW UI 의 최근 메시지를 앞에서부터 잘라 보여주므로
    실제 봇 메시지 텍스트의 prefix(또는 그 반대)이어야 한다. 단순 substring 매칭은
    '답장'·'수입방' 같은 공통 단어가 사용자 메시지에 우연히 들어있을 때
    오스킵(=사용자 메시지 유실)을 일으켜 사용 금지. (code-review 2026-06-01)
    """
    if not preview or not kakaotalk_room:
        return False
    try:
        data = json.loads(SENT_RECENT.read_text(encoding="utf-8"))
    except Exception:
        return False
    bucket = data.get(kakaotalk_room) or []
    p = preview.strip()
    if not p:
        return False
    now = time.time()
    for ent in bucket:
        if not isinstance(ent, dict):
            continue
        if now - (ent.get("ts") or 0) >= SENT_TTL_SEC:
            continue
        t = (ent.get("text") or "").strip()
        if not t:
            continue
        # 접두사 일치 — 어느 한 쪽이 다른 쪽으로 시작하면 동일 메시지로 본다
        if t.startswith(p) or p.startswith(t):
            return True
        # "[발신자] [시각] 본문" 헤더 형식이면 본문 부분도 접두사로 비교
        if "] " in t:
            body = t.split("] ", 2)[-1].strip()
            if body and (body.startswith(p) or p.startswith(body)):
                return True
    return False


# ─────────────────────────────────────────────────────────
# 2) Vision 상태 저장/로드
# ─────────────────────────────────────────────────────────

def _load_state() -> list:
    try:
        return (json.loads(VISION_STATE.read_text(encoding="utf-8")) or {}).get("rooms", [])
    except Exception:
        return []


def _save_state(rows: list) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    VISION_STATE.write_text(
        json.dumps({"ts": time.time(), "rooms": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ─────────────────────────────────────────────────────────
# 3) 워크 방 이름 → 카톡 방 이름 매핑 해석
# ─────────────────────────────────────────────────────────

def _resolve_kakao_room(work_room: str, mapping: dict) -> str | None:
    """워크 방 이름을 정규화해 room_mapping(카톡이름→conv_id)의 키와 매칭.
    "[미러] X" → "X", "NV01:X" → "X" 처리. 공백 무시 fallback.
    """
    name = (work_room or "").strip()
    if name.startswith("[미러] "):
        name = name[len("[미러] "):].strip()
    elif name.startswith("[미러]"):
        name = name[len("[미러]"):].strip()
    # NV## 또는 NV### prefix
    if ":" in name:
        head = name.split(":", 1)[0].strip()
        if head.upper().startswith("NV"):
            name = name.split(":", 1)[1].strip()
    if name in mapping:
        return name
    norm = name.replace(" ", "")
    for k in mapping:
        if k.replace(" ", "") == norm:
            return k
    return None


# ─────────────────────────────────────────────────────────
# 4) 1회 사이클 — 캡처/diff/필터/포워딩
# ─────────────────────────────────────────────────────────

def cycle_once(*, forward: bool = True, verbose: bool = True) -> dict:
    """1회 사이클. 반환 통계 dict.
    forward=False 면 dry-run (송신 안 함).
    """
    from core.work_vision_reader import read_room_list_state, diff_room_list
    from core.kakaowork_router import _load_room_mapping

    prev = _load_state()
    rows, cap = read_room_list_state()
    if not rows:
        return {"err": "no_rows"}
    diff = diff_room_list(prev, rows)
    mapping = _load_room_mapping()

    stats = {"rows": len(rows), "diff": len(diff), "forwarded": 0,
             "self_loop_skipped": 0, "unmapped_skipped": 0,
             "new_room_skipped": 0, "first_baseline": int(not prev)}

    # 첫 사이클(baseline) 은 diff 가 전부 new_room — 송신 안 함
    if not prev:
        if verbose:
            print(f"  [WORK→KK] baseline {len(rows)} 방 기록만 — 송신 없음", flush=True)
        _save_state(rows)
        return stats

    to_forward: list[tuple[str, str, str]] = []  # (kakaotalk_room, preview, work_room)
    for d in diff:
        kind = d.get("_kind")
        work_room = d.get("room", "")
        preview = (d.get("preview") or "").strip()
        if kind == "new_room":
            stats["new_room_skipped"] += 1
            if verbose:
                print(f"  [WORK→KK] new_room (skip): {work_room}", flush=True)
            continue
        kk = _resolve_kakao_room(work_room, mapping)
        if not kk:
            stats["unmapped_skipped"] += 1
            if verbose:
                print(f"  [WORK→KK] unmapped (skip): '{work_room}'", flush=True)
            continue
        if _is_our_message(kk, preview):
            stats["self_loop_skipped"] += 1
            if verbose:
                print(f"  [WORK→KK] self-loop skip: {kk} '{preview[:40]}'", flush=True)
            continue
        to_forward.append((kk, preview, work_room))

    # 실제 포워딩: 락 한 번 잡고 일괄 송신
    if to_forward and forward:
        from core import kakao_lock as klock
        from core import kakao_win32 as kw
        klock.request()
        got = klock.acquire("work_bridge", timeout=30, respect_request=False)
        if not got:
            print(f"  [WORK→KK] 락 확보 실패 — {len(to_forward)}건 보류 (다음 사이클 재시도)", flush=True)
            klock.clear_request()
            # 상태 저장은 미루기 (다음 사이클에 같은 diff 다시 잡히게)
            return stats
        try:
            for kk, preview, work_room in to_forward:
                try:
                    res = kw.send_message_to_room(kk, preview)
                    ok = res.get("success", False)
                    if ok:
                        stats["forwarded"] += 1
                        print(f"  [WORK→KK] ✅ '{kk}' ← '{preview[:60]}'", flush=True)
                        # 포워딩 후엔 우리가 워크에 그 텍스트를 다시 보내진 않지만,
                        # 모니터가 곧 카톡→워크로 다시 mirror 할 것. 그 mirror 가
                        # work_bridge 의 self-loop 필터에 잡혀야 함 → 이미 sent_ledger
                        # 와 work_sent_recent 양쪽이 모니터 송신 시 기록되므로 OK.
                    else:
                        print(f"  [WORK→KK] ❌ '{kk}' 송신실패: {res.get('error','')}", flush=True)
                except Exception as e:
                    print(f"  [WORK→KK] '{kk}' 예외: {type(e).__name__}: {e}", flush=True)
                time.sleep(0.5)
        finally:
            klock.release("work_bridge")
            klock.clear_request()
    elif to_forward and not forward:
        for kk, preview, _w in to_forward:
            print(f"  [WORK→KK] (dry) '{kk}' ← '{preview[:60]}'", flush=True)

    _save_state(rows)
    return stats


def daemon(*, interval_sec: int = 20, once: bool = False,
           dry_run: bool = False) -> int:
    """워크→카톡 브릿지 데몬. Ctrl+C 종료."""
    print(f"[WORK→KK] 데몬 시작 interval={interval_sec}s dry={dry_run} once={once}", flush=True)
    cycle = 0
    while True:
        cycle += 1
        try:
            print(f"\n[WORK→KK] === cycle {cycle} ===", flush=True)
            stats = cycle_once(forward=not dry_run, verbose=True)
            print(f"[WORK→KK] cycle {cycle} stats: {stats}", flush=True)
        except KeyboardInterrupt:
            print("\n[WORK→KK] Ctrl+C — 종료", flush=True)
            return 0
        except Exception as e:
            print(f"[WORK→KK] cycle 예외: {type(e).__name__}: {e}", flush=True)
        if once:
            return 0
        time.sleep(interval_sec)
