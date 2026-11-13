"""Shared login-expiry circuit breaker; probes are read-only, writes never replay."""
import time
from core.atomic_json import read, save


class SessionExpired(RuntimeError):
    pass


def path():
    from core.browser_session_bridge import QUEUE
    return QUEUE / 'authentication.json'


def state():
    target = path()
    return read(target) if target.exists() else {}


def guard(method):
    current = state()
    if not current.get('blocked'):
        return
    if method not in ('GET', 'MASTER') or time.time() < current.get('next_probe_at', 0):
        raise SessionExpired('네노바 로그인 만료: 등록 대기 중; 중복 재전송하지 않음')
    current['next_probe_at'] = time.time() + 60
    save(path(), current)


def automatic_relogin_enabled():
    from core import defect_approval as d
    try:
        return bool(read(d.ROOT.parent / 'defect_config.json').get('automatic_relogin'))
    except (OSError, ValueError):
        return False


def expired():
    current = state()
    first = not current.get('blocked')
    save(path(), {**current, 'blocked':True, 'since':current.get('since',time.time()),
                  'next_probe_at':time.time()+60})
    if first and not automatic_relogin_enabled():
        from core.error_notifications import notify
        notify('browser_login_needed', context={'cause':'네노바 웹 요청 HTTP 401'})
    raise SessionExpired('네노바 로그인 만료: 등록 대기 중; 중복 재전송하지 않음')


def recovered():
    if state().get('blocked'):
        save(path(), {'blocked':False,'recovered_at':time.time()})
        from core.error_notifications import resolve
        resolve('browser_login_needed')
