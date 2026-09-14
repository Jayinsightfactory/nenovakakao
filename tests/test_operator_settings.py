from unittest.mock import Mock
import pytest
from core import operator_settings as settings, keyword_approval as approval
from core import keyword_forward as k, error_notifications as notices, import_order


def test_name_persists_and_configuration_pauses():
    from core.moyi_control import is_paused
    assert settings.configure(' 현우 ') == '현우'
    assert settings.operator_name() == '현우'
    assert is_paused()


@pytest.mark.parametrize('name', ['', '   ', '현\n우', 'x' * 81])
def test_invalid_names_do_not_replace_saved_recipient(name):
    settings.configure('현우')
    with pytest.raises(ValueError):
        settings.configure(name)
    assert settings.operator_name() == '현우'


def test_new_operator_receives_receipts_and_unknown_notices_do_not_replay(monkeypatch):
    settings.configure('현우')
    monkeypatch.setattr('core.moyi_control.audit', Mock())
    sent = []
    monkeypatch.setattr('core.moyi_inbound.parse_export', lambda *args: [
        {'event_id': str(i), 'content': text} for i, (_, text) in enumerate(sent)])
    export = Mock(return_value='현우 님과 카카오톡 대화\n')
    notices.approval_result('ABC123', 'item', '가', '승인거절')
    notices.poll(export, lambda room, text: sent.append((room, text)), lambda: False)
    assert sent[0][0] == '현우' and '처리했습니다' in sent[0][1]
    assert next(iter(notices._rows().values()))['recipient'] == '현우'
    export.assert_called_with('현우')
    settings.configure('다음담당자')
    notices.poll(export, Mock(), lambda: False)
    assert len(sent) == 1


def test_legacy_questions_are_not_reinterpreted_as_new_operator_answers():
    rows = {'old': {'status': 'waiting', 'request_event_id': 'old-prompt'},
            'new': {'status': 'queued'}}
    assert approval.hold_previous_operator_requests(rows, '현우')
    assert rows['old']['status'] == 'operator_changed_review'
    assert rows['old']['approver_name'] == '임재용대리'
    assert rows['new']['status'] == 'queued'


def test_new_question_and_answer_use_configured_operator(monkeypatch):
    settings.configure('현우')
    monkeypatch.setattr(k, 'config', lambda: {'enabled': True, 'target': '현장방'})
    monkeypatch.setattr(approval, 'route_status', Mock())
    events = []
    monkeypatch.setattr('core.moyi_inbound.parse_export', lambda *args: list(events))
    export = lambda room: room + ' 님과 카카오톡 대화\n'
    send = Mock(side_effect=lambda room, text: events.append(
        {'event_id': 'prompt', 'sender_name': '네노바', 'content': text}))
    row = {'id': 'ABC123', 'status': 'queued', 'created_at': 1,
           'event': {'event_id': 'item', 'sender_name': '직원', 'content': '장미 추가'}}
    k.save_json(approval.REQUESTS, {'ABC123': row})
    approval.poll(export, send, lambda: False, Mock())
    send.assert_called_once()
    assert send.call_args.args[0] == '현우'
    saved = k.read_json(approval.REQUESTS, {})['ABC123']
    assert saved['approver_name'] == '현우'
    replies = events + [{'event_id': 'reply', 'sender_name': '현우', 'content': '가 안보내'}]
    assert approval.decision(replies, saved, allow_short=True) == {0: 'reject'}
    replies[-1]['sender_name'] = '임재용대리'
    assert approval.decision(replies, saved, allow_short=True) is None


def test_order_gate_uses_operator_without_changing_staff(tmp_path, monkeypatch):
    monkeypatch.setattr(import_order, 'CONFIG', tmp_path / 'orders.json')
    import_order._save(import_order.CONFIG, {'review_gate_room': '임재용대리',
                                            'staff_rooms': {'직원': '직원'}})
    settings.configure('현우')
    assert import_order.config()['review_gate_room'] == '현우'
    assert import_order.config()['staff_rooms'] == {'직원': '직원'}


def test_console_name_entry_saves_operator(monkeypatch):
    import tkinter as tk
    from moyi_console import Console
    root = Console.__new__(Console)
    tk.Tk.__init__(root)
    root.withdraw()
    root.status = tk.StringVar(value='대기')
    root.pause_text = tk.StringVar(value='재개')
    monkeypatch.setattr(root, '_update_pause_display', Mock())
    monkeypatch.setattr('moyi_console.audit', Mock())
    try:
        root._build()
        root.operator_name.set('현우')
        root.save_operator()
        assert settings.operator_name() == '현우'
        assert '현우' in root.operator_status.get()
        root.update_idletasks()
    finally:
        root.destroy()
