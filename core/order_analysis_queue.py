"""Durable, single-consumer order analysis without any Kakao UI operations."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
from core.atomic_json import save

ROOT = Path(__file__).resolve().parents[1]
QUEUE = ROOT / 'data' / 'order_analysis_queue'
_process = None


def enqueue(event):
    key = hashlib.sha256(event['event_id'].encode()).hexdigest()
    path = QUEUE / (key + '.json')
    if not path.exists():
        save(path, event)


def drain(capture, paused):
    for path in sorted(QUEUE.glob('*.json'), key=lambda p: p.stat().st_mtime_ns):
        if paused():
            break
        event = json.loads(path.read_text(encoding='utf-8'))
        capture(event)  # capture persists by event_id before acknowledgement
        path.unlink()


def start():
    global _process
    if _process is not None and _process.poll() is None:
        return
    if not any(QUEUE.glob('*.json')):
        return
    with (ROOT / 'data' / 'order_analysis.log').open('a', encoding='utf-8') as log:
        _process = subprocess.Popen(
            [sys.executable, '-X', 'utf8', '-m', 'core.order_analysis_queue'],
            cwd=ROOT, stdout=log, stderr=log,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def main():
    import msvcrt
    from core import import_order, order_llm, order_services, order_review
    from core.moyi_control import is_paused
    from core.workflow_settings import config
    QUEUE.mkdir(parents=True, exist_ok=True)
    with (QUEUE / 'consumer.lock').open('a+b') as lock:
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return
        if not config()['order_review_only'] or not config()['order_processing_enabled']:
            return
        started = time.time()
        drain(lambda event: import_order.capture(event, order_llm.parse, order_services.master),
              lambda: is_paused() or not config()['order_review_only'] or not config()['order_processing_enabled']
                      or not import_order.config().get('enabled'))
        print(json.dumps({'at': time.time(), 'duration_sec': time.time()-started,
                          'pending': len(list(QUEUE.glob('*.json')))}), flush=True)
        if not is_paused():
            order_review.start_sync()


if __name__ == '__main__':
    main()
