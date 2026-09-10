import json
from unittest.mock import Mock
import pytest
from scripts import incremental_sync as sync


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(sync, 'PENDING_FILE', tmp_path / 'pending.json')
    monkeypatch.setattr(sync, 'SYNC_STATE_FILE', tmp_path / 'state.json')
    monkeypatch.setattr(sync, 'BATCH_SIZE', 2)
    monkeypatch.setattr(sync, 'BATCH_DELAY', 0)
    monkeypatch.setattr(sync.logger, 'handlers', [])


def job():
    return {'status': 'pending', 'tabs': {'test': [['a'], ['b'], ['c']]},
            'offsets': {}, 'next_state': {'rooms': {'room': {'total_synced': 3}}}, 'total_new': 3}


def test_capacity_failure_is_durable_and_never_claims_completion(monkeypatch, caplog):
    sheet = Mock()
    sheet.worksheet.return_value.append_rows.side_effect = RuntimeError('increase cells above 10000000')
    get = Mock(return_value=sheet)
    monkeypatch.setattr(sync, '_get_gspread_client', get)
    assert sync.deliver_pending(job()) == 2
    saved = json.loads(sync.PENDING_FILE.read_text())
    assert saved['status'] == 'capacity_blocked' and saved['tabs'] == job()['tabs']
    assert not sync.SYNC_STATE_FILE.exists()
    assert sync.run_sync() == 2
    assert get.call_count == 1
    assert '저장 확인 완료' not in caplog.text


def test_partial_write_checkpoints_success_and_does_not_replay_unknown(monkeypatch):
    sheet = Mock()
    sheet.worksheet.return_value.append_rows.side_effect = [None, TimeoutError()]
    monkeypatch.setattr(sync, '_get_gspread_client', Mock(return_value=sheet))
    assert sync.deliver_pending(job()) == 3
    saved = json.loads(sync.PENDING_FILE.read_text())
    assert saved['offsets'] == {'test': 2} and saved['status'] == 'unknown'
    assert sync.run_sync() == 3
    assert sheet.worksheet.return_value.append_rows.call_count == 2
    assert not sync.SYNC_STATE_FILE.exists()


def test_confirmed_offsets_resume_without_duplicate_rows(monkeypatch):
    sheet = Mock()
    monkeypatch.setattr(sync, '_get_gspread_client', Mock(return_value=sheet))
    work = job(); work['offsets']['test'] = 2
    assert sync.deliver_pending(work) == 0
    sheet.worksheet.return_value.append_rows.assert_called_once_with([['c']], value_input_option='RAW')
    assert sync.SYNC_STATE_FILE.exists() and not sync.PENDING_FILE.exists()


def test_missing_anchor_does_not_replay_history(tmp_path):
    exported = tmp_path / 'new.txt'
    exported.write_text('room\nsaved\nnew text\n', encoding='utf-8')
    with pytest.raises(RuntimeError, match='기준점'):
        sync.extract_delta('room', [exported], {'last_message_md5': 'missing'})
