"""Non-UI MOYI archival; uncertain submissions are held, never replayed."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
from core.atomic_json import save

ROOT = Path(__file__).resolve().parents[1]
QUEUE = ROOT / 'data' / 'inbound_archive_queue'
_process = None
_next_at = 0


def enqueue(payload):
    key = hashlib.sha256(payload['event_id'].encode()).hexdigest()
    path = QUEUE / (key + '.json')
    if not path.exists():
        save(path, {'status': 'queued', 'payload': payload})


def drain(post, paused):
    processed = 0
    for path in sorted(QUEUE.glob('*.json'), key=lambda p: p.stat().st_mtime_ns):
        if paused() or processed >= 50:
            break
        row = json.loads(path.read_text(encoding='utf-8'))
        if row['status'] != 'queued':
            continue
        processed += 1
        row.update(status='attempting', attempted_at=time.time())
        save(path, row)
        try:
            post(row['payload'])
        except Exception as exc:
            row.update(status='unknown', error_type=type(exc).__name__)
            save(path, row)
            continue
        row.update(status='sent', completed_at=time.time())
        save(path, row)
        path.unlink()


def start():
    global _process, _next_at
    if time.monotonic() < _next_at or (_process is not None and _process.poll() is None):
        return
    _next_at = time.monotonic() + 60
    if not any(QUEUE.glob('*.json')):
        return
    with (ROOT / 'data' / 'inbound_archive.log').open('a', encoding='utf-8') as log:
        _process = subprocess.Popen([sys.executable, '-X', 'utf8', '-m', 'core.inbound_archive_queue'],
            cwd=ROOT, stdout=log, stderr=log,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def main():
    import msvcrt
    import requests
    from core.moyi_worker import _config
    from core.moyi_control import is_paused
    server, secret = _config()
    QUEUE.mkdir(parents=True, exist_ok=True)
    with (QUEUE / 'consumer.lock').open('a+b') as lock:
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return
        def post(payload):
            response = requests.post(server + '/kakao/agent/inbound',
                headers={'X-Company-Secret': secret}, json=payload, timeout=20)
            response.raise_for_status()
        started = time.time()
        drain(post, is_paused)
        print(json.dumps({'at': time.time(), 'duration_sec': time.time()-started,
                          'remaining': len(list(QUEUE.glob('*.json')))}), flush=True)


if __name__ == '__main__':
    main()
