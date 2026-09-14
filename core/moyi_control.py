"""Cross-process controls shared by the MOYI console and worker."""
from __future__ import annotations

from pathlib import Path
import json
import time

ROOT = Path(__file__).resolve().parent.parent
PAUSE_FILE = ROOT / "data" / "moyi_worker.pause"


class OperationPaused(RuntimeError):
    """A user pause interrupted work; possible writes still need reconciliation."""


def audit(state, detail=''):
    path = PAUSE_FILE.parent / 'moyi_control_events.jsonl'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps({'at': time.time(), 'state': state, 'detail': detail}, ensure_ascii=False) + '\n')
    from core.error_notifications import report
    try:
        if state != 'worker_started': report(state)  # worker reports actual startup
    except OSError:
        with path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps({'at': time.time(), 'state': 'report_queue_failed',
                                     'detail': '상황 보고 저장 실패'}, ensure_ascii=False) + '\n')


def worker_processes():
    import psutil
    for process in psutil.process_iter(['cmdline', 'cwd']):
        try:
            args = process.info['cmdline'] or []
            if ('moyi-worker' in args and any(Path(a).name == 'main.py' for a in args)
                    and Path(process.info['cwd'] or '').resolve() == ROOT.resolve()):
                yield process
        except (psutil.Error, OSError):
            continue


def emergency_stop(source='button'):
    """Persist pause before terminating this installation's worker only.

    Existing journals are preserved; interrupted writes must not be replayed.
    """
    set_paused(True)
    import psutil
    failed = []
    for process in worker_processes():
        try:
            process.terminate()
            process.wait(timeout=3)
        except psutil.NoSuchProcess:
            pass
        except psutil.Error:
            failed.append(process.pid)
    audit('stop_failed' if failed else 'emergency_stopped',
          f'{source}; failed_pids={failed}; 중단된 전송은 결과 확인 필요')
    if failed:
        raise RuntimeError('워커 종료 실패: ' + str(failed))


def is_paused() -> bool:
    return PAUSE_FILE.exists()


def set_paused(paused: bool) -> None:
    PAUSE_FILE.parent.mkdir(parents=True, exist_ok=True)
    if paused:
        temporary = PAUSE_FILE.with_suffix(".tmp")
        temporary.write_text("paused\n", encoding="utf-8")
        temporary.replace(PAUSE_FILE)
    else:
        PAUSE_FILE.unlink(missing_ok=True)
    audit('pause_requested' if paused else 'resume_requested')

