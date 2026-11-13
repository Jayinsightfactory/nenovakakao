"""자동 최신화: 실제 임시 git 저장소로 '안전할 때만 앞으로 간다'를 확인한다."""
import json
import subprocess
from unittest.mock import Mock

import pytest

from core import self_update as up


def git(cwd, *args):
    done = subprocess.run(['git', *args], cwd=str(cwd), capture_output=True, text=True, encoding='utf-8')
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


def commit(repo, name, text, message):
    target = repo / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding='utf-8')
    git(repo, 'add', '-A')
    git(repo, '-c', 'user.name=t', '-c', 'user.email=t@example.invalid', 'commit', '-q', '-m', message)
    return git(repo, 'rev-parse', 'HEAD')


@pytest.fixture
def repos(tmp_path, monkeypatch):
    """origin(배포 저장소)과 pc(운영 PC 사본). pc 는 release 의 첫 저장 기록에 있다."""
    origin = tmp_path / 'Jayinsightfactory' / 'nenovakakao'
    origin.mkdir(parents=True)
    git(origin, 'init', '-q', '-b', 'release')
    git(origin, 'config', 'core.autocrlf', 'false')
    commit(origin, 'core/__init__.py', '', 'base')
    commit(origin, 'core/moyi_worker.py', 'VALUE = 1\n', 'worker')
    commit(origin, 'core/self_update.py', '', 'updater')
    commit(origin, 'main.py', '', 'main')
    commit(origin, 'moyi_console.py', '', 'console')
    commit(origin, 'scripts/x.py', '', 'scripts')
    pc = tmp_path / 'pc'
    git(tmp_path, 'clone', '-q', str(origin), str(pc))
    git(pc, 'config', 'core.autocrlf', 'false')
    monkeypatch.setattr(up, 'ROOT', pc)
    return origin, pc


def enable(**extra):
    up.CONFIG.write_text(json.dumps({'enabled': True, **extra}), encoding='utf-8')


def test_disabled_by_default_never_touches_git(monkeypatch):
    monkeypatch.setattr(up, '_git', Mock(side_effect=AssertionError('꺼져 있으면 git 호출 금지')))
    assert up.poll(restart=Mock()) is False


def test_new_release_is_verified_applied_and_worker_restarted(repos):
    origin, pc = repos
    new = commit(origin, 'core/moyi_worker.py', 'VALUE = 2\n', 'fix')
    enable()
    restart = Mock()
    assert up.poll(restart=restart) is True
    assert git(pc, 'rev-parse', 'HEAD') == new
    assert (pc / 'core' / 'moyi_worker.py').read_text(encoding='utf-8') == 'VALUE = 2\n'
    restart.assert_called_once()
    states = [json.loads(line)['state'] for line in up.LOG.read_text(encoding='utf-8').splitlines()]
    assert states == ['updated']
    assert git(pc, 'worktree', 'list').count('\n') == 0  # 임시 폴더 정리됨


def test_nothing_new_means_no_restart(repos):
    enable()
    restart = Mock()
    assert up.poll(restart=restart) is False
    restart.assert_not_called()


def test_checks_are_throttled(repos, monkeypatch):
    enable(interval_sec=600)
    assert up.poll(restart=Mock()) is False
    monkeypatch.setattr(up, 'check', Mock(side_effect=AssertionError('주기 전 재확인 금지')))
    assert up.poll(restart=Mock()) is False


def test_broken_release_is_rejected_and_code_stays(repos):
    origin, pc = repos
    before = git(pc, 'rev-parse', 'HEAD')
    commit(origin, 'core/moyi_worker.py', 'def broken(:\n', 'syntax error')
    enable()
    restart = Mock()
    assert up.poll(restart=restart) is False
    assert git(pc, 'rev-parse', 'HEAD') == before
    restart.assert_not_called()
    assert json.loads(up.LOG.read_text(encoding='utf-8').splitlines()[-1])['state'] == 'failed'


def test_local_edits_on_the_pc_are_never_overwritten(repos):
    origin, pc = repos
    before = git(pc, 'rev-parse', 'HEAD')
    commit(origin, 'core/moyi_worker.py', 'VALUE = 2\n', 'fix')
    (pc / 'main.py').write_text('# 운영 PC 에서 직접 고친 줄\n', encoding='utf-8')
    enable()
    assert up.poll(restart=Mock()) is False
    assert git(pc, 'rev-parse', 'HEAD') == before
    assert (pc / 'main.py').read_text(encoding='utf-8') == '# 운영 PC 에서 직접 고친 줄\n'
    assert json.loads(up.LOG.read_text(encoding='utf-8').splitlines()[-1])['state'] == 'blocked_local_changes'


def test_diverged_history_is_not_merged(repos):
    origin, pc = repos
    commit(origin, 'core/moyi_worker.py', 'VALUE = 2\n', 'fix')
    local = commit(pc, 'core/local_only.py', 'X = 1\n', 'pc only commit')
    enable()
    assert up.poll(restart=Mock()) is False
    assert git(pc, 'rev-parse', 'HEAD') == local
    assert json.loads(up.LOG.read_text(encoding='utf-8').splitlines()[-1])['state'] == 'blocked_diverged'


def test_pc_ahead_of_release_is_left_alone(repos):
    origin, pc = repos
    local = commit(pc, 'core/local_only.py', 'X = 1\n', 'pc ahead')
    enable()
    assert up.poll(restart=Mock()) is False
    assert git(pc, 'rev-parse', 'HEAD') == local
    assert not up.LOG.exists()


def test_other_remote_is_refused(repos, tmp_path):
    origin, pc = repos
    other = tmp_path / 'someone' / 'else'
    other.mkdir(parents=True)
    git(pc, 'remote', 'set-url', 'origin', str(other))
    enable()
    assert up.poll(restart=Mock()) is False
    assert json.loads(up.LOG.read_text(encoding='utf-8').splitlines()[-1])['state'] == 'blocked_remote'


def test_restarted_worker_waits_for_previous_worker(monkeypatch):
    alive = iter([True, True, False])
    import psutil
    monkeypatch.setattr(psutil, 'pid_exists', lambda pid: next(alive))
    monkeypatch.setattr(up.time, 'sleep', Mock())
    monkeypatch.setattr(up, 'current_version', lambda root=None: 'abc1234')
    monkeypatch.setenv(up.PARENT_ENV, '4242')
    up.wait_for_parent()
    assert up.time.sleep.call_count == 2
    assert up.PARENT_ENV not in up.os.environ


def test_normal_start_does_not_wait(monkeypatch):
    monkeypatch.delenv(up.PARENT_ENV, raising=False)
    monkeypatch.setattr(up.time, 'sleep', Mock(side_effect=AssertionError('대기 금지')))
    up.wait_for_parent()
