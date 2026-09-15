from copy import deepcopy
from unittest.mock import Mock
import pytest
from core import defect_approval as d
from core.atomic_json import save


EVENT = {'event_id': 'new-text', 'sender_name': '박성수',
         'timestamp': '2026년 9월 15일 오전 10:07',
         'content': '37-1 카네이션 SP믹스 15단\n검증꽃집'}
MASTER = {'products': [{'name': 'SP믹스', 'category': '카네이션', 'nenova_key': 1},
                       {'name': '수정품목', 'category': '카네이션', 'nenova_key': 2}],
          'customers': [{'name': '검증꽃집', 'nenova_key': 3}]}


def waiting(tmp_path):
    path = d.capture(EVENT, '2026-09-15T10:06:45+09:00', '강현우', tmp_path)
    row = d.rematch(d.load(path), MASTER)
    row.update(status='waiting', question_event_id='question-1')
    save(path, row)
    return path, row


def reply(row, text, eid='answer-1', sender='강현우'):
    return {'event_id': eid, 'sender_name': sender, 'content': d.label(row) + ' ' + text}


def test_cutoff_text_sender_and_idempotent_capture(tmp_path):
    for event in [{**EVENT, 'timestamp': '2026년 9월 15일 오전 10:06'},
                  {**EVENT, 'content': '사진'}, {**EVENT, 'sender_name': '다른사람'}]:
        assert d.capture(event, '2026-09-15T10:06:45+09:00', '강현우', tmp_path) is None
    path, row = waiting(tmp_path)
    d.capture(EVENT, '2026-09-15T10:06:45+09:00', '강현우', tmp_path)
    assert d.load(path) == row


@pytest.mark.parametrize('sender,later', [('다른사람', {'answer-1'}), ('강현우', set())])
def test_untrusted_or_early_reply_does_not_approve(tmp_path, sender, later):
    _, row = waiting(tmp_path)
    assert d.apply_reply(row, reply(row, '맞아', sender=sender), later) == row


def test_correction_requires_new_verified_question_and_new_approval(tmp_path):
    path, row = waiting(tmp_path)
    row = d.apply_reply(row, reply(row, '틀려'), {'answer-1'})
    assert row['status'] == 'awaiting_product'
    assert d.apply_reply(row, reply(row, '맞아', 'a2'), {'a2'})['status'] == 'awaiting_product'
    row = d.apply_reply(row, reply(row, '품목 1=수정품목', 'a3'), {'a3'})
    row = d.rematch(row, MASTER)
    assert row['revision'] == 2 and row['status'] == 'ready_question'
    assert row['items'][0]['product']['nenova_key'] == 2
    save(path, row)
    adapter = Mock()
    d.submit(path, adapter, lambda: False)
    adapter.insert.assert_not_called()
    old = {**reply(row, '맞아', 'a4'), 'content': row['id'] + ' v1 맞아'}
    row.update(status='waiting', question_event_id='question-2')
    assert d.apply_reply(row, old, {'a4'}) == row
    row = d.apply_reply(row, reply(row, '맞아', 'a5'), {'a5'})
    assert row['status'] == 'approved' and row['approved_revision'] == 2


def test_ambiguous_product_cannot_be_approved(tmp_path):
    _, row = waiting(tmp_path)
    master = deepcopy(MASTER)
    master['products'].append({'name': 'SP믹스', 'category': '카네이션', 'nenova_key': 9})
    row = d.rematch(row, master)
    row.update(status='waiting', question_event_id='q')
    assert not d.ready(row)
    assert d.apply_reply(row, reply(row, '맞아'), {'answer-1'}) == row


def test_successful_write_verified_once(tmp_path):
    path, row = waiting(tmp_path)
    row = d.apply_reply(row, reply(row, '맞아'), {'answer-1'})
    save(path, row)
    adapter = Mock()
    adapter.lookup.side_effect = [None, {'registration_id': 1}]
    adapter.matches.return_value = True
    d.submit(path, adapter, lambda: False)
    d.submit(path, adapter, lambda: False)
    assert d.load(path)['status'] == 'completed'
    adapter.insert.assert_called_once()


def test_unknown_write_never_replayed(tmp_path):
    path, row = waiting(tmp_path)
    save(path, d.apply_reply(row, reply(row, '맞아'), {'answer-1'}))
    adapter = Mock()
    adapter.lookup.return_value = None
    adapter.insert.side_effect = TimeoutError()
    with pytest.raises(TimeoutError): d.submit(path, adapter, lambda: False)
    assert d.load(path)['status'] == 'write_unknown'
    d.submit(path, adapter, lambda: False)
    adapter.insert.assert_called_once()


def test_uncertain_question_never_repeated(tmp_path):
    path, row = waiting(tmp_path)
    row['status'] = 'ready_question'
    save(path, row)
    send = Mock(side_effect=TimeoutError())
    with pytest.raises(TimeoutError): d.send_question(path, lambda _: [], send, lambda: False)
    assert d.load(path)['status'] == 'question_unknown'
    d.send_question(path, lambda _: [], send, lambda: False)
    send.assert_called_once()


def test_question_requires_positive_readback(tmp_path):
    path, row = waiting(tmp_path)
    row['status'] = 'ready_question'
    save(path, row)
    export = Mock(side_effect=[[], [{'event_id':'q2', 'content':d.question(row)}]])
    d.send_question(path, export, Mock(), lambda: False)
    assert d.load(path)['status'] == 'waiting'
    assert d.load(path)['question_event_id'] == 'q2'


def test_blank_lines_removed_by_real_export_still_verify(tmp_path):
    path, row = waiting(tmp_path)
    row['status'] = 'ready_question'; save(path, row)
    payload = d.question(row)
    exported = '\n'.join(line for line in payload.splitlines() if line.strip())
    export = Mock(side_effect=[[], [{'event_id': 'real-q', 'content': exported}]])
    d.send_question(path, export, Mock(), lambda: False)
    assert d.load(path)['status'] == 'waiting'


def test_unknown_question_recovery_does_not_accept_old_or_duplicate(tmp_path):
    path, row = waiting(tmp_path)
    row.update(status='question_unknown', question_payload=d.question(row), question_before_ids=['old'])
    save(path,row)
    assert not d.reconcile_question(path,[{'event_id':'old','content':row['question_payload']}])
    events=[{'event_id':'a','content':row['question_payload']}, {'event_id':'b','content':row['question_payload']}]
    assert not d.reconcile_question(path,events)
    assert d.reconcile_question(path,events[:1])
    assert d.load(path)['question_event_id'] == 'a'


def test_verification_does_not_accept_request_id_only_or_changed_quantity():
    assert d.verified_message('request v1\n15단',[],[{'event_id':'a','content':'request v1\n16단'}]) is None
    assert d.verified_message('request v1\n15단',[],[{'event_id':'a','content':'request v1'}]) is None
