import json
from core import error_notifications as notices
from scripts import compact_operation_reports as compact


def test_compaction_preserves_errors_unknown_and_backup(tmp_path, monkeypatch):
    state = tmp_path / 'error_notifications.json'
    backups = tmp_path / 'backups'
    monkeypatch.setattr(compact, 'STATE', state)
    monkeypatch.setattr(compact, 'BACKUPS', backups)
    rows = {
        'report1': {'id':'report1','kind':'report','status':'queued','category':'inbound_processed','created_at':1},
        'report2': {'id':'report2','kind':'report','status':'queued','category':'archive_completed','created_at':2},
        'error': {'id':'error','status':'queued','category':'order_error','created_at':3},
        'unknown': {'id':'unknown','kind':'report','status':'unknown','category':'worker_started','created_at':4},
    }
    state.write_text(json.dumps(rows), encoding='utf-8')
    compact.main()
    saved = json.loads(state.read_text(encoding='utf-8'))
    assert saved['error']['status'] == 'queued'
    assert saved['unknown']['status'] == 'unknown'
    summary = next(r for r in saved.values() if r.get('kind') == 'report_summary')
    assert summary['status'] == 'held_summary' and summary['count'] == 2
    assert len(list(backups.glob('*.json'))) == 1
