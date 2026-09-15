import json
from unittest.mock import Mock

from core.agent_runtime import AgentCoordinator


class Clock:
    def __init__(self): self.value = 100.0
    def __call__(self): return self.value


def test_agents_run_by_priority_and_respect_intervals(tmp_path):
    clock = Clock(); calls = []
    runtime = AgentCoordinator(tmp_path / 'agents.jsonl', clock=clock, wall_clock=clock)
    runtime.add('order', 30, 10, lambda: calls.append('order'))
    runtime.add('sales', 0, 3, lambda: calls.append('sales'))
    runtime.add('approval', 10, 5, lambda: calls.append('approval'))
    runtime.run_due()
    assert calls == ['sales', 'approval', 'order']
    clock.value += 4
    runtime.run_due()
    assert calls[-1] == 'sales'
    rows = [json.loads(line) for line in (tmp_path / 'agents.jsonl').read_text().splitlines()]
    completed = [row for row in rows if row['outcome'] != 'started']
    assert [row['agent'] for row in completed[:3]] == ['sales', 'approval', 'order']


def test_agent_failure_isolated_and_lower_agent_continues(tmp_path):
    errors = []; lower = Mock()
    runtime = AgentCoordinator(tmp_path / 'agents.jsonl', on_error=lambda agent, exc: errors.append(agent.name))
    runtime.add('sales', 0, 5, Mock(side_effect=RuntimeError('private content')))
    runtime.add('order', 30, 5, lower)
    assert [outcome[:2] for outcome in runtime.run_due()] == [('sales', 'error'), ('order', 'ok')]
    assert errors == ['sales'] and lower.call_count == 1
    text = (tmp_path / 'agents.jsonl').read_text()
    assert 'RuntimeError' in text and 'private content' not in text


def test_duplicate_agent_names_are_rejected(tmp_path):
    runtime = AgentCoordinator(tmp_path / 'agents.jsonl')
    runtime.add('sales', 0, 5, Mock())
    try:
        runtime.add('sales', 1, 5, Mock())
    except ValueError:
        pass
    else:
        raise AssertionError('duplicate agent accepted')


def test_user_pause_is_logged_without_failure_or_error_callback(tmp_path):
    from core.moyi_control import OperationPaused
    errors = Mock()
    runtime = AgentCoordinator(tmp_path / 'agents.jsonl', on_error=errors)
    agent = runtime.add('approval', 0, 5, Mock(side_effect=OperationPaused('일시정지')))
    assert runtime.run_due() == [('approval', 'paused', None)]
    assert agent.failures == 0
    errors.assert_not_called()


def test_pause_stops_remaining_due_agents(tmp_path):
    from core.moyi_control import OperationPaused
    lower = Mock()
    runtime = AgentCoordinator(tmp_path / 'agents.jsonl')
    runtime.add('approval', 0, 5, Mock(side_effect=OperationPaused('paused')))
    runtime.add('order', 30, 5, lower)
    assert runtime.run_due() == [('approval', 'paused', None)]
    lower.assert_not_called()
