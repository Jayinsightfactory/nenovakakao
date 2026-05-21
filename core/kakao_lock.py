"""
카톡 PC 창 동시 제어 충돌 방지 락 (cross-process, 파일 기반).

monitor(카톡→워크)와 reactive(워크→카톡 답장)는 서로 다른 프로세스이면서
같은 카톡 창/마우스/키보드를 제어한다. 동시에 작동하면 포커스·커서·키 입력이
충돌해 답장이 엉뚱한 방에 가거나 monitor 작업이 깨진다. 이 락으로 한 번에
하나만 카톡을 제어하도록 조정한다.

우선순위: 답장(reactive)은 사용자가 기다리는 작업이라 우선.
  - reactive 가 request() 로 우선 요청을 남기면,
  - monitor 는 현재 방 처리를 마친 뒤 다음 방 획득을 양보(acquire→False)하고,
  - reactive 가 락을 잡아 송신 → release()+clear_request() → monitor 재개.

파일:
  data/_kakao_lock          — 현재 소유자  "owner|pid|epoch"
  data/_kakao_lock_request  — 우선 획득 요청 (reactive 생성, epoch)

견고성:
  - 원자적 생성(O_CREAT|O_EXCL)으로 경쟁 방지.
  - 소유 프로세스가 죽었거나(PID 부재) 락이 STALE_AFTER 초 이상 묵으면 탈취.
  - 요청 파일도 STALE_AFTER 지나면 자동 정리(reactive 크래시 대비).
"""
from __future__ import annotations

import os
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCK_FILE = ROOT / "data" / "_kakao_lock"
REQUEST_FILE = ROOT / "data" / "_kakao_lock_request"

# 락/요청이 이보다 오래되면 죽은 것으로 간주하고 정리(초)
STALE_AFTER = 120.0


def _now() -> float:
    return time.time()


def _pid_alive(pid: int) -> bool:
    """PID 프로세스 생존 확인 (Windows). 모르면 살아있다고 가정(안전)."""
    try:
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        h = ctypes.windll.kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid)
        )
        if not h:
            return False
        ctypes.windll.kernel32.CloseHandle(h)
        return True
    except Exception:
        return True


def _read_lock():
    try:
        raw = LOCK_FILE.read_text(encoding="utf-8").strip()
        owner, pid, ts = raw.split("|")
        return owner, int(pid), float(ts)
    except Exception:
        return None


def _is_stale() -> bool:
    info = _read_lock()
    if info is None:
        return True
    _owner, pid, ts = info
    if _now() - ts > STALE_AFTER:
        return True
    if not _pid_alive(pid):
        return True
    return False


# ── 우선 요청 (reactive) ─────────────────────────────────
def request() -> None:
    """우선 획득 요청 표시. monitor 가 다음 방을 양보하게 만든다."""
    try:
        REQUEST_FILE.parent.mkdir(parents=True, exist_ok=True)
        REQUEST_FILE.write_text(f"{_now()}", encoding="utf-8")
    except Exception:
        pass


def clear_request() -> None:
    try:
        if REQUEST_FILE.exists():
            REQUEST_FILE.unlink()
    except Exception:
        pass


def is_requested() -> bool:
    try:
        if not REQUEST_FILE.exists():
            return False
        if _now() - REQUEST_FILE.stat().st_mtime > STALE_AFTER:
            clear_request()
            return False
        return True
    except Exception:
        return False


# ── 락 획득/해제 ─────────────────────────────────────────
def _try_create(owner: str) -> bool:
    """O_CREAT|O_EXCL 원자적 생성. 성공 시 True."""
    try:
        fd = os.open(str(LOCK_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        try:
            os.write(fd, f"{owner}|{os.getpid()}|{_now()}".encode("utf-8"))
        finally:
            os.close(fd)
        return True
    except FileExistsError:
        return False
    except Exception:
        return False


def acquire(
    owner: str,
    timeout: float = 30.0,
    respect_request: bool = False,
    poll: float = 0.2,
) -> bool:
    """락 획득. 성공 True / 타임아웃·양보 시 False.

    respect_request=True (monitor 용): 우선 요청(reactive)이 있으면 즉시 양보(False).
    """
    try:
        LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    deadline = _now() + timeout
    while True:
        if respect_request and is_requested():
            return False  # 답장 우선 — 양보
        if LOCK_FILE.exists() and _is_stale():
            try:
                LOCK_FILE.unlink()
            except Exception:
                pass
        if _try_create(owner):
            return True
        if _now() >= deadline:
            return False
        time.sleep(poll)


def release(owner: str) -> None:
    """내가 소유한 락만 해제."""
    info = _read_lock()
    if info is None:
        return
    if info[0] == owner and info[1] == os.getpid():
        try:
            LOCK_FILE.unlink()
        except Exception:
            pass


def force_release() -> None:
    """소유 무관 강제 해제 (시작 시 stale 정리용)."""
    try:
        if LOCK_FILE.exists():
            LOCK_FILE.unlink()
    except Exception:
        pass
