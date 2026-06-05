"""시트동기화 워커 — Option A: 별도 프로세스, 주기적 증분 동기화.

미러링(화면 점유)과 '완전히 분리된 별도 프로세스'. collected_data.jsonl(델타 큐)을
byte offset 기반으로 증분 읽어 gsheet_sync.classify_and_log_delta 로 구글시트에 append.
화면 미점유(파일읽기 + Sheets API) → 미러링과 병렬, 미러 지연 0. 크래시·시트오류는
이 프로세스에 격리되어 미러에 영향 없음.

상태/관찰(원자적 temp→os.replace):
  data/sync_state.json  {byte_offset, last_ts, initialized_ts}
  data/sync_status.json {last_run_ts, processed, appended, errors, last_error, pending_bytes}
정지: data/_STOP 공용 latch 존중(미러와 함께 멈춤).

중복 방지(핵심):
  - 첫 실행(state 없음) → offset=파일 끝. 기존 백로그는 '인라인 동기화'가 이미 올렸다고 보고
    재동기화하지 않는다(중복 append 방지). 이후 새 레코드만 처리.
  - offset 은 '시트 append 성공 후에만' 전진 → 실패 건은 다음 주기 재시도(누락 0).
  - 시트는 append-only(삭제/덮어쓰기 절대 안 함).

실행:
  python -m core.sync_worker          # 루프(기본 300초 주기)
  python -m core.sync_worker --once   # 1회 동기화 후 종료(스케줄러/수동용)
  환경: NENOVA_SYNC_INTERVAL=초(기본 300), NENOVA_SYNC_MAXREC=주기당 최대 레코드(기본 300)
"""
from __future__ import annotations

import os
import sys
import json
import time
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
COLLECTED = DATA / "collected_data.jsonl"
STATE_FILE = DATA / "sync_state.json"
STATUS_FILE = DATA / "sync_status.json"
STOP_FILE = DATA / "_STOP"
LOG_FILE = ROOT / "logs" / "sync_worker.log"

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def _log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _atomic_write_json(path: Path, obj: dict) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(DATA), prefix="_tmp_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)  # 원자적
    except Exception as e:
        try:
            os.unlink(tmp)
        except Exception:
            pass
        _log(f"원자적 저장 실패 {path.name}: {e}")


def _load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _stop_requested() -> bool:
    try:
        return STOP_FILE.exists()
    except Exception:
        return False


def sync_pending(max_records: int | None = None) -> dict:
    """offset 부터 collected_data 증분 → classify_and_log_delta → 시트. offset 원자 전진."""
    if max_records is None:
        try:
            max_records = int(os.environ.get("NENOVA_SYNC_MAXREC", "300"))
        except ValueError:
            max_records = 300

    state = _load_state()

    # 첫 실행: offset = 파일 끝 (기존 백로그 재동기화=중복 방지)
    if "byte_offset" not in state:
        try:
            end = COLLECTED.stat().st_size
        except Exception:
            end = 0
        state = {"byte_offset": end, "initialized_ts": time.strftime("%Y-%m-%d %H:%M:%S")}
        _atomic_write_json(STATE_FILE, state)
        _log(f"초기화: offset=파일끝({end}B). 기존 백로그는 인라인동기화 처리분으로 보고 스킵. 이후 신규만 동기화.")
        return {"processed": 0, "appended": 0, "errors": 0, "init": True}

    off = int(state.get("byte_offset", 0))
    try:
        size = COLLECTED.stat().st_size
    except Exception:
        return {"processed": 0, "appended": 0, "errors": 0, "pending_bytes": 0}

    if off > size:  # 파일 회전/재생성 감지 → 끝으로 리셋(중복 방지 우선)
        _log(f"⚠️ offset({off})>크기({size}) 파일회전 감지 → offset=끝 리셋")
        state["byte_offset"] = size
        _atomic_write_json(STATE_FILE, state)
        return {"processed": 0, "appended": 0, "errors": 0, "pending_bytes": 0}
    if off == size:
        return {"processed": 0, "appended": 0, "errors": 0, "pending_bytes": 0}

    # 증분 바이트만 읽기
    with open(COLLECTED, "rb") as f:
        f.seek(off)
        raw = f.read()
    text = raw.decode("utf-8", errors="replace")
    parts = text.split("\n")
    complete = parts[:-1]  # 마지막 조각은 미완성(쓰는 중)일 수 있어 보류

    # classify 는 늦게 import(시작 지연/크레딧 무관하게 워커 자체는 기동)
    from core.gsheet_sync import classify_and_log_delta

    processed = appended = errors = 0
    cur = off
    for line in complete:
        if _stop_requested():
            _log("정지 요청(_STOP) — 이번 배치 중단")
            break
        line_bytes = len(line.encode("utf-8")) + 1  # +"\n"
        s = line.strip()
        advance = True
        if s:
            try:
                rec = json.loads(s)
            except json.JSONDecodeError:
                _log(f"깨진 JSON 줄 스킵(offset {cur})")
                rec = None
            if rec is not None:
                room = rec.get("room_name")
                delta = rec.get("delta")
                if room and delta:
                    try:
                        n = classify_and_log_delta(room, delta)
                        appended += (n or 0)
                        processed += 1
                    except Exception as e:
                        # 시트/네트워크 등 일시 오류 → offset 미전진(여기서 멈춤) → 다음 주기 재시도
                        errors += 1
                        advance = False
                        _log(f"시트 기록 실패(offset {cur}, 재시도 대기): {type(e).__name__}: {e}")
        if not advance:
            break
        cur += line_bytes
        state["byte_offset"] = cur
        state["last_ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _atomic_write_json(STATE_FILE, state)
        if processed >= max_records:
            _log(f"배치 상한({max_records}) 도달 — 다음 주기 계속")
            break

    return {"processed": processed, "appended": appended, "errors": errors,
            "pending_bytes": max(0, size - cur)}


def run_loop(interval_sec: int) -> None:
    _log(f"시트동기화 워커 시작 (Option A 별도프로세스, 주기 {interval_sec}초)")
    while True:
        if _stop_requested():
            _log("정지 요청(_STOP) — 워커 종료")
            break
        try:
            r = sync_pending()
            if r.get("processed") or r.get("appended") or r.get("errors"):
                _log(f"동기화: 레코드 {r.get('processed',0)} / 시트행 {r.get('appended',0)} / 오류 {r.get('errors',0)}")
            _atomic_write_json(STATUS_FILE, {"last_run_ts": time.strftime("%Y-%m-%d %H:%M:%S"), **r})
        except Exception as e:
            _log(f"sync_pending 예외(무시, 다음 주기 재시도): {type(e).__name__}: {e}")
            _atomic_write_json(STATUS_FILE, {"last_run_ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                                             "last_error": f"{type(e).__name__}: {e}"})
        slept = 0
        while slept < interval_sec and not _stop_requested():
            time.sleep(min(2, interval_sec - slept))
            slept += 2


def _load_env() -> None:
    try:
        for ln in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if ln and not ln.startswith("#") and "=" in ln:
                k, v = ln.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
    except Exception:
        pass


if __name__ == "__main__":
    _load_env()
    try:
        interval = int(os.environ.get("NENOVA_SYNC_INTERVAL", "300"))
    except ValueError:
        interval = 300
    if "--once" in sys.argv:
        res = sync_pending()
        _log(f"--once 완료: {res}")
    else:
        run_loop(interval)
