"""Windows Credential Manager storage for per-staff nenova accounts."""
from __future__ import annotations
import re

PREFIX = 'nenovakakao/nenova/'


def _target(staff):
    staff = str(staff).strip()
    if not staff or len(staff) > 80 or re.search(r'[\\/\x00-\x1f]', staff):
        raise ValueError('담당자 이름 형식 오류')
    return PREFIX + staff


def save(staff, username, password):
    import win32cred
    username, password = str(username).strip(), str(password)
    if not username or not password: raise ValueError('아이디와 비밀번호가 필요합니다')
    win32cred.CredWrite({
        'Type': win32cred.CRED_TYPE_GENERIC,
        'TargetName': _target(staff),
        'UserName': username,
        'CredentialBlob': password,
        'Persist': win32cred.CRED_PERSIST_LOCAL_MACHINE,
        'Comment': 'nenovakakao 담당자별 네노바웹 로그인',
    }, 0)


def load(staff):
    import win32cred
    try:
        row = win32cred.CredRead(_target(staff), win32cred.CRED_TYPE_GENERIC, 0)
    except Exception:
        return None
    blob = row.get('CredentialBlob', b'')
    if isinstance(blob, bytes):
        try: password = blob.decode('utf-16-le')
        except UnicodeDecodeError: password = blob.decode('utf-8')
    else: password = str(blob)
    return {'username': row.get('UserName', ''), 'password': password}


def configured(staff):
    value = load(staff)
    return bool(value and value['username'] and value['password'])
