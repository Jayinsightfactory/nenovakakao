import time
from unittest.mock import Mock
from core import defect_runtime as r
from core import defect_approval as d
from core.atomic_json import save
from tests.test_defect_approval import EVENT, waiting, reply


def setup(monkeypatch, tmp_path):
    monkeypatch.setattr('core.error_notifications.notify', Mock())
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


def test_explicit_restart_scope_does_not_replay_old_asked_approval(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    path, row = waiting(tmp_path)
    row['status'] = 'approved'
    save(path, row)
    before = path.read_bytes()
    monkeypatch.setattr(r, 'config', lambda: {'enabled': True, 'write_enabled': True,
        'processing_start_at': '2026-10-23T00:00:00+09:00'})
    export, send = Mock(), Mock()
    r.poll(export, send)
    assert path.read_bytes() == before
    export.assert_not_called()
    send.assert_not_called()


def test_maintenance_ledger_hold_cannot_register_or_send_on_restart(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    path, row = waiting(tmp_path)
    row.update(status='approved',maintenance_hold={'reason':'ledger ownership not verified'})
    save(path,row)
    before=path.read_bytes()
    adapter=Mock();adapter.profile='강현우'
    monkeypatch.setattr(r,'_adapter',adapter)
    export,send=Mock(),Mock()
    r.poll(export,send)
    adapter.insert.assert_not_called();adapter.validate.assert_not_called()
    send.assert_not_called();export.assert_not_called()
    assert path.read_bytes()==before


def test_explicit_restart_scope_captures_today_without_old_backlog(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    monkeypatch.setattr(r, '_next_collection', 0)
    monkeypatch.setattr(r, 'config', lambda: {'enabled': True, 'write_enabled': True,
        'processing_start_at': '2026-10-23T00:00:00+09:00'})
    today = {**EVENT, 'event_id': 'today', 'timestamp': '2026년 10월 23일 오전 9:00'}
    export, send = Mock(return_value=[EVENT, today]), Mock()
    r.poll(export, send)
    rows = [d.load(p) for p in tmp_path.glob('*.json')]
    assert len(rows) == 1 and rows[0]['event']['event_id'] == 'today'
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


def test_one_history_check_rotates_to_next_recipient(monkeypatch,tmp_path):
    from copy import deepcopy
    setup(monkeypatch,tmp_path)
    monkeypatch.setattr(d,'recipient_for',lambda event:event.get('test_recipient','강현우'))
    path,row=waiting(tmp_path)
    other=deepcopy(row);other.update(id='same-author',question_event_id='not-in-export')
    save(tmp_path/'same-author.json',other)
    third=deepcopy(row);third.update(id='next-author',recipient='정재훈',last_polled_at=1)
    third['event']['test_recipient']='정재훈'
    save(tmp_path/'next-author.json',third)
    export=Mock(return_value=[])
    r.poll(export,Mock(),phase='responses')
    assert d.load(path)['last_polled_at']==d.load(tmp_path/'same-author.json')['last_polled_at']
    r.poll(export,Mock(),phase='responses')
    assert [call.args[0] for call in export.call_args_list]==['강현우','정재훈']


def test_verified_sent_question_survives_new_intake_cutoff(monkeypatch,tmp_path):
    setup(monkeypatch,tmp_path)
    path,row=waiting(tmp_path)
    monkeypatch.setattr(r,'forward_config',lambda:{'start_at':'2026-09-28T12:00:00+09:00'})
    r.poll(Mock(return_value=[{'event_id':'question-1'},reply(row,'맞아')]),Mock(),phase='responses')
    assert d.load(path)['status']=='approved'


def test_completion_notice_precedes_another_approved_write(monkeypatch,tmp_path):
    from copy import deepcopy
    setup(monkeypatch,tmp_path)
    path,row=waiting(tmp_path)
    row.update(status='completed',receipt=[]);save(path,row)
    other=deepcopy(row);other.update(id='approved-other',status='approved')
    save(tmp_path/'approved-other.json',other)
    notice=Mock();monkeypatch.setattr(r,'_notice',notice)
    submit=Mock();monkeypatch.setattr(d,'submit',submit)
    r.poll(Mock(),Mock(),phase='responses')
    notice.assert_called_once()
    submit.assert_not_called()


def test_missing_scope_is_clarified_instead_of_blocking_question(monkeypatch,tmp_path):
    setup(monkeypatch,tmp_path)
    path,row=waiting(tmp_path)
    row['status']='ready_question';row['extracted']['sequence']='';row['information_only']=True;save(path,row)
    export,send=Mock(return_value=[]),Mock()
    d.send_question(path,export,send,lambda:False)
    assert export.call_count == 2
    send.assert_called_once()
    saved=d.load(path)
    assert saved['status']=='question_unknown'
    assert '원래 몇 차수 물건인가요?' in saved['question_payload']

def test_complete_information_question_is_recovered_without_resending(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    path, row = waiting(tmp_path)
    row['information_only'] = True
    save(path, row)
    monkeypatch.setattr(r, 'config', lambda: {'enabled': True, 'write_enabled': True, 'approval_mode': 'automatic'})
    prepare = Mock()
    monkeypatch.setattr(d, 'prepare_automatic', prepare)
    export, send = Mock(), Mock()
    r.poll(export, send)
    prepare.assert_called_once_with(path)
    export.assert_not_called()
    send.assert_not_called()
