"""Chrome native messaging host. stdin/stdout contain framed JSON only."""
import json
import os
from pathlib import Path
import re
import struct
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.browser_session_bridge import QUEUE, ROOT, validate_job
from core.atomic_json import save

_validation_stamp = None


def refresh_validation():
    """Refresh code between jobs, never retain yesterday's validation rules."""
    global _validation_stamp, validate_job
    import importlib
    names = ('defect_scope', 'defect_approval', 'defect_services', 'browser_session_bridge', 'order_registration')
    stamp = tuple((ROOT / 'core' / (name + '.py')).stat().st_mtime_ns for name in names)
    if stamp != _validation_stamp:
        for name in names:
            importlib.reload(importlib.import_module('core.' + name))
        from core.browser_session_bridge import validate_job as current_validator
        validate_job = current_validator
        _validation_stamp = stamp


def handle(message):
    refresh_validation()
    QUEUE.mkdir(parents=True, exist_ok=True)
    if message.get('type') == 'poll':
        save(QUEUE / 'connection.json', {'at': time.time(), 'page_ready': bool(message.get('page_ready'))})
        if not message.get('page_ready'):
            # Open the ERP only for a valid pending job, never every idle poll.
            for path in QUEUE.glob('*.json'):
                if not re.fullmatch(r'[a-f0-9]{32}\.json', path.name): continue
                job = json.loads(path.read_text(encoding='utf-8'))
                if job.get('state') != 'queued': continue
                try: validate_job(job)
                except Exception: continue
                return {'type':'open_page'}
            return {'type': 'idle'}
        import msvcrt
        with (QUEUE / 'claim.lock').open('a+b') as lock:
            lock.seek(0)
            try: msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError: return {'type':'idle'}
            try:
                for path in sorted(QUEUE.glob('*.json')):
                    if not re.fullmatch(r'[a-f0-9]{32}\.json', path.name): continue
                    job = json.loads(path.read_text(encoding='utf-8'))
                    if job.get('state') != 'queued': continue
                    try: validate_job(job)
                    except Exception as exc:
                        job.update(state='blocked', error=str(exc)[:180]); save(path, job)
                        save(QUEUE / (job['id'] + '.response.json'), {
                            'status':409, 'data': {'success':False,'error':job['error']}})
                        continue
                    job['state'] = 'dispatched'; save(path, job)
                    return {'type': 'job', 'job': job}
            finally:
                lock.seek(0); msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
        return {'type':'idle'}
    if message.get('type') == 'result':
        key = message.get('id', '')
        if not re.fullmatch(r'[a-f0-9]{32}', key): raise ValueError('invalid_id')
        path = QUEUE / (key + '.json')
        job = json.loads(path.read_text(encoding='utf-8'))
        if job.get('state') != 'dispatched': raise ValueError('not_dispatched')
        save(QUEUE / (key + '.response.json'), {'status': message.get('status'), 'data': message.get('data') or {}})
        job['state'] = 'responded'; save(path, job)
        return {'type':'received'}
    raise ValueError('unsupported_message')


def main():
    manifest = json.loads((ROOT / 'browser-extension' / 'manifest.json').read_text(encoding='utf-8'))
    import hashlib, base64
    digest = hashlib.sha256(base64.b64decode(manifest['key'])).hexdigest()[:32]
    extension_id = ''.join(chr(ord('a')+int(c,16)) for c in digest)
    if len(sys.argv)<2 or sys.argv[1] != 'chrome-extension://' + extension_id + '/': return
    if os.name == 'nt':
        import msvcrt
        msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY); msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
    while True:
        header = sys.stdin.buffer.read(4)
        if len(header) != 4: return
        size = struct.unpack('<I', header)[0]
        # Support view includes carryover rows and can exceed the old 800 KB
        # screenshot-sized cap. Keep a bound, but allow normal ledger responses.
        if size > 16 * 1024 * 1024:
            save(QUEUE / 'host_error.json', {'at':time.time(),
                'error':'native_response_too_large', 'bytes':size})
            return
        raw = sys.stdin.buffer.read(size)
        if len(raw) != size: return
        try: result = handle(json.loads(raw))
        except Exception: result = {'type':'error','error':'bridge_request_rejected'}
        data = json.dumps(result,ensure_ascii=False).encode('utf-8')
        sys.stdout.buffer.write(struct.pack('<I',len(data))+data); sys.stdout.buffer.flush()


if __name__ == '__main__': main()
