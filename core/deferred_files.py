"""Keep attachment evidence durable without blocking the text business loop."""
import hashlib
import time
from pathlib import Path
from core.atomic_json import save

QUEUE = Path(__file__).resolve().parents[1] / 'data' / 'deferred_files'


def enqueue(binding, title, event, filename):
    identity = str(binding) + '\x1f' + event['event_id']
    path = QUEUE / (hashlib.sha256(identity.encode()).hexdigest() + '.json')
    if not path.exists():
        save(path, {'binding': binding, 'room': title, 'event': event,
                    'filename': filename, 'status': 'pending_attachment_review',
                    'queued_at': time.time()})
    return path
