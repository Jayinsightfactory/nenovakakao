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


def test_queue_time_includes_previous_operation(tmp_path):
    clock=Clock()
    runtime=AgentCoordinator(tmp_path/'timings.jsonl',clock=clock,wall_clock=clock)
    def slow(): clock.value+=12
    runtime.add('approval',0,0,slow)
    runtime.add('sales',10,0,lambda:None)
    runtime.run_due()
    rows=[json.loads(s) for s in (tmp_path/'timings.jsonl').read_text().splitlines()]
    assert rows[-1]['queue_ms']==12000


def test_failure_backoff_caps_and_success_restores_normal_interval(tmp_path):
    clock = Clock()
    operation = Mock(side_effect=[RuntimeError('offline')] * 7 + [None, RuntimeError('again')])
    runtime = AgentCoordinator(tmp_path / 'agents.jsonl', clock=clock)
    agent = runtime.add('sales', 0, 5, operation)
    for delay in [30, 60, 120, 240, 300, 300, 300, 5, 30]:
        runtime.run_due()
        assert agent.next_at == clock.value + delay
        clock.value = agent.next_at - 0.01
        assert runtime.run_due() == []
        clock.value = agent.next_at
    assert agent.failures == 8
    assert agent.consecutive_failures == 1


def test_cooldown_does_not_delay_healthy_agent(tmp_path):
    clock = Clock()
    runtime = AgentCoordinator(tmp_path / 'agents.jsonl', clock=clock)
    runtime.add('sales', 0, 5, Mock(side_effect=RuntimeError()))
    healthy = runtime.add('order', 1, 5, Mock())
    runtime.run_due()
    clock.value += 5
    assert [row[:2] for row in runtime.run_due()] == [('order', 'ok')]
    assert healthy.runs == 2


def test_error_report_failure_is_isolated_and_logged_without_content(tmp_path):
    clock = Clock()
    path = tmp_path / 'agents.jsonl'
    runtime = AgentCoordinator(path, clock=clock,
        on_error=Mock(side_effect=ValueError('secret')))
    agent = runtime.add('sales', 0, 5, Mock(side_effect=RuntimeError('private')))
    runtime.add('order', 1, 5, Mock())
    assert [row[:2] for row in runtime.run_due()] == [('sales', 'error'), ('order', 'ok')]
    assert agent.next_at == 130
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert rows[1]['callback_error_type'] == 'ValueError'
    assert 'secret' not in path.read_text() and 'private' not in path.read_text()


def test_pause_preserves_failure_streak_and_long_interval_is_not_shortened(tmp_path):
    from core.moyi_control import OperationPaused
    clock = Clock()
    runtime = AgentCoordinator(tmp_path / 'agents.jsonl', clock=clock)
    agent = runtime.add('sales', 0, 600,
        Mock(side_effect=[RuntimeError(), OperationPaused(), None]))
    runtime.run_due()
    assert agent.next_at == 700
    clock.value = 700
    runtime.run_due()
    assert agent.consecutive_failures == 1 and agent.failures == 1
    clock.value = 1300
    runtime.run_due()
    assert agent.consecutive_failures == 0
