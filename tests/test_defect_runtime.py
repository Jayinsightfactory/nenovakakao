import time
from unittest.mock import Mock
from core import defect_runtime as r
from core import defect_approval as d
from core.atomic_json import save
from tests.test_defect_approval import EVENT, waiting, reply


def setup(monkeypatch, tmp_path):
    monkeypatch.setattr(d, 'ROOT', tmp_path)
    monkeypatch.setattr(r, 'is_paused', lambda: False)
    monkeypatch.setattr(r, 'config', lambda: {'enabled': True, 'write_enabled': True})
    monkeypatch.setattr(r, 'forward_config', lambda: {'start_at': '2026-09-15T10:06:45+09:00'})
    monkeypatch.setattr(r, 'operator_name', lambda: '강현우')
    monkeypatch.setattr(d, 'recipient_for', lambda event: '강현우')
    monkeypatch.setattr(r, '_next_collection', time.monotonic()+100)
    monkeypatch.setattr(r, 'start_master', Mock())
    monkeypatch.setattr(r, 'MASTER', tmp_path / 'cache' / 'master.json')


def test_collection_matches_and_requests_approval_in_same_poll(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    monkeypatch.setattr(r, '_next_collection', 0)
    save(r.MASTER, {})
    rematch = Mock(side_effect=lambda row, master: {**row, 'status': 'ready_question'})
    question = Mock()
    monkeypatch.setattr(d, 'rematch', rematch)
    monkeypatch.setattr(d, 'send_question', question)
    export, send = Mock(return_value=[EVENT]), Mock()
    r.poll(export, send)
    rematch.assert_called_once()
    question.assert_called_once()
    assert d.load(question.call_args.args[0])['status'] == 'ready_question'


def test_collection_captures_text_without_send(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    monkeypatch.setattr(r, '_next_collection', 0)
    export = Mock(return_value=[EVENT, {**EVENT,'event_id':'photo','content':'사진'}])
    send = Mock()
    r.poll(export, send)
    export.assert_called_once_with('수입불량방')
    assert len(list(tmp_path.glob('*.json'))) == 1
    send.assert_not_called()


def test_reply_saved_before_next_turn_write(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    path, row = waiting(tmp_path)
    export = Mock(return_value=[{'event_id':'question-1'},reply(row,'맞아')])
    adapter = Mock(); adapter.profile = '강현우'
    monkeypatch.setattr(r, '_adapter', adapter)
    r.poll(export, Mock())
    assert d.load(path)['status'] == 'approved'
    adapter.insert.assert_not_called()


def test_unknown_write_keeps_hold_and_no_second_insert(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    path, row = waiting(tmp_path)
    save(path, d.apply_reply(row, reply(row,'맞아'), {'answer-1'}))
    adapter = Mock(); adapter.profile = '강현우'
    adapter.lookup.return_value = None
    adapter.insert.side_effect = TimeoutError()
    monkeypatch.setattr(r, '_adapter', adapter)
    r.poll(Mock(), Mock())
    assert d.load(path)['status'] == 'write_unknown'
    r.poll(Mock(), Mock())
    adapter.insert.assert_called_once()


def test_old_cutoff_and_changed_recipient_never_dispatched(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    path, row = waiting(tmp_path)
    row['recipient'] = '다른사람'; save(path,row)
    export,send = Mock(),Mock()
    r.poll(export,send)
    export.assert_not_called(); send.assert_not_called()


def test_single_author_export_processes_other_pending_questions(monkeypatch,tmp_path):
    from copy import deepcopy
    setup(monkeypatch,tmp_path)
    path,row=waiting(tmp_path)
    row['reply_number']=1;save(path,row)
    other=deepcopy(row);other.update(id='other',reply_number=3,question_event_id='q3')
    other_path=tmp_path/'other.json';save(other_path,other)
    export=Mock(return_value=[{'event_id':'question-1'}, {'event_id':'q3'},
        {'event_id':'answer-3','sender_name':'강현우','content':'3 맞아'}])
    r.poll(export,Mock(),phase='responses')
    export.assert_called_once_with('강현우')
    assert d.load(path)['status']=='waiting'
    assert d.load(other_path)['status']=='approved'
