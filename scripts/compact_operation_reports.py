"""Back up and compact queued normal reports; preserve errors/unknown results."""
from collections import Counter
import json
from pathlib import Path
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.atomic_json import save

STATE = ROOT / 'data' / 'error_notifications.json'
BACKUPS = ROOT / 'data' / 'report_queue_backups'


def main():
    if not STATE.exists():
        print('no state')
        return
    rows = json.loads(STATE.read_text(encoding='utf-8'))
    queued_reports = [r for r in rows.values()
                      if r.get('kind') == 'report' and r.get('status') == 'queued']
    if not queued_reports:
        print('queued_reports=0')
        return
    BACKUPS.mkdir(parents=True, exist_ok=True)
    backup = BACKUPS / f'error_notifications_before_compaction_{int(time.time())}.json'
    shutil.copy2(STATE, backup)
    categories = Counter(r.get('category', '') for r in queued_reports)
    kept = {key: row for key, row in rows.items() if row not in queued_reports}
    created = min(r['created_at'] for r in queued_reports)
    summary_id = 'operationsummary' + str(int(time.time()))
    kept[summary_id] = {
        'id': summary_id, 'category': 'operation_summary', 'kind': 'report_summary',
        'status': 'held_summary', 'created_at': created, 'updated_at': time.time(),
        'count': len(queued_reports), 'categories': dict(categories),
        'detail': '로컬 감사용 요약; 카톡 자동 전송 금지',
    }
    save(STATE, kept)
    print(json.dumps({'backed_up': len(rows), 'compacted': len(queued_reports),
                      'active_rows': len(kept), 'backup': str(backup)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
