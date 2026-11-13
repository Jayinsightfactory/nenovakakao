from unittest.mock import Mock
from core import defect_approval as d
from core.atomic_json import save
from core.defect_services import DuplicateDefect
from core.defect_runtime import completion_message
from tests.test_defect_approval import waiting


def setup_policy(tmp_path, monkeypatch):
    root = tmp_path / 'requests'
    root.mkdir()
    monkeypatch.setattr(d, 'ROOT', root)
    save(tmp_path / 'defect_config.json', {'approval_mode': 'automatic',
                                         'automatic_policy_id': 'user-policy'})
    path, row = waiting(root)
    d.prepare_automatic(path)
    return path, d.load(path)


def test_policy_authorizes_without_fabricating_staff_reply(tmp_path, monkeypatch):
    path, row = setup_policy(tmp_path, monkeypatch)
    assert row['status'] == 'approved'
    assert d.auto_authorized(row)
    assert not row.get('approval_event_id')
    row['revision'] += 1
    assert not d.auto_authorized(row)


def test_similar_entry_is_held_and_does_not_insert(tmp_path, monkeypatch):
    path, row = setup_policy(tmp_path, monkeypatch)
    adapter = Mock()
    adapter.lookup.side_effect = DuplicateDefect([{'deductionKey': 10}])
    d.submit(path, adapter, lambda: False)
    adapter.insert.assert_not_called()
    result = d.load(path)
    assert result['status'] == 'duplicate_source_hold'
    assert '추가 등록하지 않았습니다' in completion_message(result)
    d.submit(path, adapter, lambda: False)
    assert adapter.lookup.call_count == 1


def test_ambiguous_match_is_held_without_question(tmp_path, monkeypatch):
    path, row = setup_policy(tmp_path, monkeypatch)
    row['items'][0]['product'] = None
    save(path, row)
    d.prepare_automatic(path)
    assert d.load(path)['status'] == 'clarification_pending'


def test_uncertain_write_is_not_treated_as_duplicate(tmp_path, monkeypatch):
    path, row = setup_policy(tmp_path, monkeypatch)
    row['status'] = 'write_unknown'
    save(path, row)
    adapter = Mock()
    d.submit(path, adapter, lambda: False)
    adapter.lookup.assert_not_called()


def test_missing_scope_question_and_corrected_reply(tmp_path, monkeypatch):
    path, row = setup_policy(tmp_path, monkeypatch)
    row['extracted']['sequence'] = ''
    row['extracted']['issues'] = ['차수 없음']
    save(path, row)
    d.prepare_automatic(path)
    row = d.load(path)
    assert '원래 몇 차수 물건인가요?' in d.question(row)
    assert '마감 여부' in d.question(row)
    assert '맞으면' not in d.question(row)
    row.update(status='waiting', question_event_id='q')
    event = {'event_id': 'a', 'sender_name': row['recipient'],
             'content': d.label(row) + ' 39차'}
    updated = d.apply_reply(row, event, {'a'})
    assert updated['status'] == 'needs_match'
    assert updated['extracted']['sequence'] == '39'
    assert '차수 없음' not in updated['extracted']['issues']


def test_short_general_variant_reply_uses_unique_candidate(tmp_path, monkeypatch):
    path, row = setup_policy(tmp_path, monkeypatch)
    row.update(status='waiting', information_only=True, question_event_id='q')
    row['items'][0].update(product_raw='주밀리아', product=None, candidates=[
        {'name': '주밀리아', 'nenova_key': 2101}, {'name': '주밀리아 DY', 'nenova_key': 2466}])
    answer = {'event_id': 'a', 'sender_name': row['recipient'], 'content': d.label(row)+' 일반'}
    updated = d.apply_reply(row, answer, {'a'})
    assert updated['status'] == 'needs_match'
    assert updated['extracted']['items'][0]['product_raw'] == '2101'

def test_complete_source_does_not_ask_staff_for_server_period(tmp_path, monkeypatch):
    import core.defect_scope as scope
    monkeypatch.setattr(scope, 'upload_scope', Mock(side_effect=ValueError('영업지원 등록 이력 기준 차수 검증 필요')))
    path, row = setup_policy(tmp_path, monkeypatch)
    assert row['status'] == 'approved'
    assert row['information_only'] is False
    scope.upload_scope.assert_not_called()
