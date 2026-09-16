from unittest.mock import Mock
import pytest
from core import defect_runtime as r, defect_approval as d, workflow_settings as w
from core.atomic_json import save
from core.agent_runtime import AgentCoordinator
from tests.test_defect_runtime import setup
from tests.test_defect_approval import waiting, reply, MASTER


def test_only_two_workflows_and_responses_before_collection(monkeypatch, tmp_path):
    monkeypatch.setattr(w, 'config', lambda: {'order_processing_enabled': False})
    calls = []
    c = AgentCoordinator(tmp_path / 'log')
    w.register_agents(c, lambda: calls.append('sales'), lambda: calls.append('approval'),
                      lambda: calls.append('order'))
    c.add('defect_responses', 5, 5, lambda: calls.append('defect_responses'))
    c.add('defect', 20, 15, lambda: calls.append('defect'))
    c.run_due()
    assert calls == ['approval', 'defect_responses', 'sales', 'defect']


def test_due_collection_never_preempts_response_phase(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    path, row = waiting(tmp_path)
    monkeypatch.setattr(r, '_next_collection', 0)
    other = dict(row, id='other', status='needs_match', last_polled_at=0)
    save(tmp_path / 'other.json', other)
    export = Mock(return_value=[{'event_id':'question-1'}, reply(row, '맞아')])
    r.poll(export, Mock(), phase='responses')
    export.assert_called_once_with(row['recipient'])
    assert d.load(path)['status'] == 'approved'


def test_unknown_save_recovers_without_second_insert(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    path, row = waiting(tmp_path)
    row.update(status='write_unknown', approved_revision=row['revision'])
    save(path, row)
    adapter = Mock(); adapter.profile = '강현우'
    adapter.lookup.return_value = {'verified': True}
    adapter.matches.return_value = True
    monkeypatch.setattr(r, '_adapter', adapter)
    r.poll(Mock(), Mock(), phase='responses')
    assert d.load(path)['status'] == 'completed'
    adapter.insert.assert_not_called()


@pytest.mark.parametrize('command,field,expected', [
    ('거래처=새꽃집', 'customer', '새꽃집'), ('차수=38-2', 'sequence', '38-2'),
    ('수량 1=3박스', 'quantity_raw', '3')])
def test_each_edit_invalidates_approval_and_requires_new_question(tmp_path, command, field, expected):
    path, row = waiting(tmp_path)
    updated = d.apply_reply(row, reply(row, command), {'answer-1'})
    assert updated['revision'] == 2 and updated['status'] == 'needs_match'
    assert not updated.get('question_event_id')
    source = updated['extracted']
    assert (source['items'][0] if field == 'quantity_raw' else source)[field] == expected
    assert d.apply_reply(updated, reply(updated, '맞아', 'new'), {'new'}) == updated


@pytest.mark.parametrize('command', ['수량 1=0단', '수량 9=3단', '수량 1=-3단', '차수=99-1'])
def test_invalid_edits_preserve_original(tmp_path, command):
    _, row = waiting(tmp_path)
    assert d.apply_reply(row, reply(row, command), {'answer-1'}) == row
