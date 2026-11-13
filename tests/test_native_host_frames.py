"""Exercise the real native messaging framing without sending a business job."""
import base64
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys


def test_large_response_frame_does_not_silently_kill_host():
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / 'browser-extension/manifest.json').read_text(encoding='utf-8'))
    digest = hashlib.sha256(base64.b64decode(manifest['key'])).hexdigest()[:32]
    extension = ''.join(chr(ord('a') + int(c, 16)) for c in digest)
    payload = json.dumps({'type': 'invalid-test-message', 'padding': 'x' * 900000}).encode()
    result = subprocess.run([sys.executable, str(root / 'scripts/nenova_session_host.py'),
        'chrome-extension://' + extension + '/'], input=struct.pack('<I', len(payload)) + payload,
        capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr.decode(errors='replace')
    assert len(result.stdout) >= 4, 'Host silently dropped a normal-sized ledger response'
    size = struct.unpack('<I', result.stdout[:4])[0]
    assert len(result.stdout[4:]) == size
    assert json.loads(result.stdout[4:])['type'] == 'error'
