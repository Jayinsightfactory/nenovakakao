from pathlib import Path

import core.moyi_control as control
from unittest.mock import Mock
import json
import psutil
import pytest


def test_pause_marker_round_trip(tmp_path: Path, monkeypatch):
    marker = tmp_path / "worker.pause"
    monkeypatch.setattr(control, "PAUSE_FILE", marker)

    assert control.is_paused() is False
    control.set_paused(True)
    assert control.is_paused() is True
    assert marker.read_text(encoding="utf-8") == "paused\n"
    control.set_paused(False)
    assert control.is_paused() is False


def test_emergency_stop_pauses_before_terminating_and_logs(tmp_path, monkeypatch):
    monkeypatch.setattr(control, 'PAUSE_FILE', tmp_path / 'worker.pause')
    worker = Mock()
    def check_paused():
        assert control.is_paused()
    worker.terminate.side_effect = check_paused
    monkeypatch.setattr(control, 'worker_processes', lambda: [worker])
    control.emergency_stop('test')
    assert control.is_paused()
    worker.terminate.assert_called_once()
    worker.wait.assert_called_once_with(timeout=3)
    rows = [json.loads(line) for line in (tmp_path / 'moyi_control_events.jsonl').read_text(encoding='utf-8').splitlines()]
    assert rows[-1]['state'] == 'emergency_stopped'


def test_stop_failure_keeps_pause_and_logs(tmp_path, monkeypatch):
    monkeypatch.setattr(control, 'PAUSE_FILE', tmp_path / 'worker.pause')
    worker = Mock(pid=999)
    worker.terminate.side_effect = psutil.AccessDenied(999)
    monkeypatch.setattr(control, 'worker_processes', lambda: [worker])
    with pytest.raises(RuntimeError, match='종료 실패'):
        control.emergency_stop()
    assert control.is_paused()
    assert 'stop_failed' in (tmp_path / 'moyi_control_events.jsonl').read_text(encoding='utf-8')


def test_worker_selection_excludes_other_installations(monkeypatch, tmp_path):
    ours = Mock(info={'cmdline': ['python', 'main.py', 'moyi-worker'], 'cwd': str(control.ROOT)})
    other = Mock(info={'cmdline': ['python', 'main.py', 'moyi-worker'], 'cwd': str(tmp_path)})
    console = Mock(info={'cmdline': ['python', 'main.py', 'moyi-console'], 'cwd': str(control.ROOT)})
    monkeypatch.setattr(psutil, 'process_iter', lambda fields: [ours, other, console])
    assert list(control.worker_processes()) == [ours]

