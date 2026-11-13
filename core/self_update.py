"""운영 PC 자동 최신화: 지정한 배포 가지(release)를 주기적으로 확인해 안전할 때만 받아 온다.

원칙
- 기본은 꺼짐. `data/update_config.json` 에 {"enabled": true} 가 있어야 동작한다(운영 PC 로컬 설정, Git 제외).
- 워커가 한 바퀴 일을 마친 사이(또는 일시정지 중)에만 실행한다. 카톡·전산 작업 도중에는 실행하지 않는다.
- 앞으로만 간다(fast-forward). 로컬에서 고친 추적 파일이 있거나 갈래가 갈라졌으면 아무것도 바꾸지 않는다.
- 새 코드를 임시 폴더에 먼저 풀어 문법·불러오기 검사를 통과한 뒤에만 실제 폴더를 바꾼다.
- 일시정지 표식은 건드리지 않는다. 정지 중이면 코드만 바뀌고 계속 정지 상태다.
- 카톡 메시지를 보내지 않는다. 기록은 `data/update_events.jsonl` 에만 남긴다.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / 'data' / 'update_config.json'
LOG = ROOT / 'data' / 'update_events.jsonl'
EXPECTED_REMOTE = 'jayinsightfactory/nenovakakao'
PARENT_ENV = 'MOYI_UPDATE_PARENT_PID'
_next_at = 0.0
_rejected: set = set()


def config() -> dict:
    value = {'enabled': False, 'ref': 'release', 'interval_sec': 600, 'run_tests': False}
    try:
        value.update(json.loads(CONFIG.read_text(encoding='utf-8')))
    except (OSError, ValueError):
        pass
    return value


def _event(state: str, **detail) -> None:
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps({'at': time.time(), 'state': state, **detail}, ensure_ascii=False) + '\n')
    except OSError:
        pass
    print(f'[UPDATE] {state} {detail or ""}'.rstrip())


def _run(args, cwd, timeout=120) -> str:
    done = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True, encoding='utf-8',
                          errors='replace', timeout=timeout,
                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if done.returncode != 0:
        raise RuntimeError(f"{' '.join(map(str, args[:3]))} 실패: {(done.stderr or done.stdout).strip()[:300]}")
    return done.stdout.strip()


def _git(*args, cwd=None, timeout=120) -> str:
    return _run(['git', *args], cwd or ROOT, timeout)


def current_version(root=None) -> str:
    try:
        return _git('rev-parse', '--short', 'HEAD', cwd=root or ROOT, timeout=15)
    except Exception:
        return 'unknown'


def check(root=None, ref=None):
    """받아 올 새 버전이 있고 안전하면 계획(dict)을, 아니면 None 을 돌려준다. 아무것도 바꾸지 않는다."""
    root = root or ROOT
    ref = ref or config()['ref']
    remote = _git('remote', 'get-url', 'origin', cwd=root).lower().replace('\\', '/')
    if EXPECTED_REMOTE not in remote:
        _event('blocked_remote', detail='origin 이 네노바 카카오 저장소가 아님')
        return None
    _git('fetch', '--quiet', 'origin', ref, cwd=root, timeout=180)
    head = _git('rev-parse', 'HEAD', cwd=root)
    target = _git('rev-parse', 'FETCH_HEAD', cwd=root)
    if head == target or target in _rejected:
        return None
    if _is_ancestor(target, head, root):
        return None  # 이 PC 가 이미 더 새 코드다
    if not _is_ancestor(head, target, root):
        _rejected.add(target)
        _event('blocked_diverged', head=head[:7], target=target[:7],
               detail='이 PC 의 코드가 배포 가지와 갈라져 있어 자동 최신화하지 않음')
        return None
    dirty = _git('status', '--porcelain', '--untracked-files=no', cwd=root)
    if dirty:
        _rejected.add(target)
        _event('blocked_local_changes', target=target[:7], files=dirty.splitlines()[:10],
               detail='이 PC 에서 고친 파일이 있어 덮어쓰지 않음')
        return None
    changed = _git('diff', '--name-only', head, target, cwd=root).splitlines()
    return {'from': head, 'to': target, 'ref': ref, 'files': len(changed),
            'requirements_changed': 'requirements.txt' in changed}


def _is_ancestor(older, newer, root) -> bool:
    try:
        _git('merge-base', '--is-ancestor', older, newer, cwd=root)
        return True
    except RuntimeError:
        return False


def verify(plan: dict, root=None) -> None:
    """새 코드를 임시 폴더에 풀어 문법·불러오기(선택: 테스트)를 확인한다. 실패하면 예외."""
    root = root or ROOT
    temp = Path(tempfile.mkdtemp(prefix='nenovakakao-update-'))
    tree = temp / 'tree'
    try:
        _git('worktree', 'add', '--detach', str(tree), plan['to'], cwd=root, timeout=180)
        if plan.get('requirements_changed'):
            _run([sys.executable, '-m', 'pip', 'install', '--quiet', '-r', 'requirements.txt'], tree, timeout=900)
        _run([sys.executable, '-m', 'compileall', '-q', 'core', 'scripts', 'main.py', 'moyi_console.py'], tree, timeout=300)
        _run([sys.executable, '-c', 'import core.moyi_worker, core.self_update'], tree, timeout=120)
        if config().get('run_tests'):
            _run([sys.executable, '-m', 'pytest', 'tests', '-q', '-p', 'no:cacheprovider'], tree, timeout=900)
    finally:
        try:
            _git('worktree', 'remove', '--force', str(tree), cwd=root, timeout=60)
        except Exception:
            pass
        shutil.rmtree(temp, ignore_errors=True)
        try:
            _git('worktree', 'prune', cwd=root, timeout=30)
        except Exception:
            pass


def apply(plan: dict, root=None) -> None:
    root = root or ROOT
    if _git('rev-parse', 'HEAD', cwd=root) != plan['from']:
        raise RuntimeError('검사 뒤 코드가 바뀜: 다음 주기에 다시 확인')
    _git('merge', '--ff-only', plan['to'], cwd=root, timeout=180)


def restart_worker() -> None:
    """새 코드로 워커를 다시 띄우고 지금 프로세스는 끝낸다. 새 워커는 이 프로세스가 끝난 뒤 일을 시작한다."""
    data = ROOT / 'data'
    data.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env[PARENT_ENV] = str(os.getpid())
    with (data / 'moyi_worker_stdout.log').open('a', encoding='utf-8') as out, \
            (data / 'moyi_worker_stderr.log').open('a', encoding='utf-8') as err:
        subprocess.Popen([sys.executable, '-u', 'main.py', 'moyi-worker'], cwd=ROOT, env=env,
                         stdout=out, stderr=err,
                         creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    raise SystemExit(0)


def wait_for_parent(timeout=60.0) -> None:
    """자동 최신화로 다시 뜬 워커는 이전 워커가 완전히 끝날 때까지 기다린다(카톡 동시 조작 방지)."""
    pid = os.environ.pop(PARENT_ENV, '')
    if not pid.isdigit():
        return
    import psutil
    deadline = time.monotonic() + timeout
    while psutil.pid_exists(int(pid)):
        if time.monotonic() >= deadline:
            raise RuntimeError('이전 워커가 끝나지 않아 시작하지 않음')
        time.sleep(0.5)
    _event('restarted', version=current_version())


def poll(restart=None) -> bool:
    """워커 반복문의 '쉬는 순간'에 부른다. 최신화했으면 워커를 다시 띄운다."""
    global _next_at
    cfg = config()
    if not cfg.get('enabled') or time.monotonic() < _next_at:
        return False
    _next_at = time.monotonic() + max(60, float(cfg.get('interval_sec', 600)))
    plan = None
    try:
        plan = check(ref=cfg['ref'])
        if not plan:
            return False
        verify(plan)
        apply(plan)
    except Exception as exc:  # 최신화 실패가 매크로 본업을 멈추면 안 된다
        if plan:
            _rejected.add(plan['to'])
        _event('failed', target=(plan or {}).get('to', '')[:7], detail=str(exc)[:300])
        return False
    _event('updated', **{'from': plan['from'][:7], 'to': plan['to'][:7], 'files': plan['files']})
    (restart or restart_worker)()
    return True
