from unittest.mock import Mock
from core.order_review import HEADERS, plan_updates, review_rows
from core.agent_runtime import AgentCoordinator
from core.workflow_settings import register_agents


def test_newest_original_has_priority_over_later_queue_insertion():
    from core.keyword_approval import queued_priority_at
    older = {'created_at': 900, 'event': {'timestamp': '2026년 9월 14일 오전 9:00'}}
    newer = {'created_at': 100, 'event': {'timestamp': '2026년 9월 14일 오전 10:00'}}
    assert queued_priority_at(newer) > queued_priority_at(older)


def test_sales_and_approval_alternate_before_less_frequent_orders(tmp_path):
    now = [100.0]; calls = []
    coordinator = AgentCoordinator(tmp_path / 'runtime.jsonl', clock=lambda: now[0])
    register_agents(coordinator, lambda: calls.append('sales'),
                    lambda: calls.append('approval'), lambda: calls.append('order'))
    coordinator.run_due()
    now[0] += 5
    coordinator.run_due()
    now[0] += 60
    coordinator.run_due()
    assert calls == ['approval', 'sales', 'order', 'approval', 'sales', 'approval', 'sales', 'order']


def test_updates_follow_user_sort_and_never_overwrite_review_columns():
    one = ['ORD-A', '1'] + ['old'] * 16
    two = ['ORD-B', '1'] + ['unchanged'] * 16
    existing = [HEADERS, two + ['검토완료', '고객수정', '품목수정', '메모'],
                one + ['확인중', '', '', '유지해야 함']]
    changed = ['ORD-A', '1'] + ['new'] * 16
    updates, last = plan_updates(existing, [changed, two])
    assert updates == [{'range': 'A3:R3', 'values': [changed]}]
    assert last == 3


def test_uncertain_previous_write_is_found_without_duplicate_append():
    row = ['ORD-A', '1'] + [''] * 16
    assert plan_updates([HEADERS, row], [row]) == ([], 2)


def test_analysis_failures_and_original_dates_are_visible():
    row = {'id': 'ORD-A', 'status': 'analysis_error', 'items': [],
           'error': 'matching failed', 'event': {'timestamp': '2026년 9월 14일 오전 10:00',
                                               'content': '원문', 'sender_name': '담당자'}}
    result = review_rows({'event': row})[0]
    assert result[2] == row['event']['timestamp']
    assert result[15] == 'analysis_error' and result[16] == 'matching failed'
    assert result[17] == '원문' and len(result) == 18


def test_review_mode_preserves_failed_analysis_even_for_unlisted_sender(tmp_path, monkeypatch):
    from core import import_order, workflow_settings
    from core.atomic_json import save
    monkeypatch.setattr(import_order, 'STATE', tmp_path / 'orders.json')
    monkeypatch.setattr(import_order, 'config', lambda: {'enabled': True, 'allowed_senders': ['다른직원']})
    save(workflow_settings.CONFIG, {'order_review_only': True})
    event = {'event_id': 'test', 'sender_name': '직원', 'content': '원문'}
    rid = import_order.capture(event, Mock(side_effect=RuntimeError('analysis failed')), Mock())
    stored = import_order._read(import_order.STATE, {})['test']
    assert rid == stored['id'] and stored['status'] == 'analysis_error'
    assert stored['event'] == event
