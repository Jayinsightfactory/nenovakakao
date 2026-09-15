"""One bounded defect phase per turn of the existing single UI executor."""
import json
import subprocess
import sys
import time
from pathlib import Path
from core import defect_approval as d
from core.atomic_json import save
from core.keyword_forward import config as forward_config, timestamp
from core.operator_settings import operator_name
from core.moyi_control import is_paused
from core.defect_services import DefectAdapter

CONFIG = d.ROOT.parent / 'defect_config.json'
MASTER = d.ROOT.parent / 'defect_master.json'
_next_collection = 0
_master_process = None
_adapter = None


def config():
    return d.load(CONFIG) if CONFIG.exists() else {'enabled': False}


def start_master():
    global _master_process
    if _master_process is not None and _master_process.poll() is None:
        return
    if MASTER.exists() and time.time() - MASTER.stat().st_mtime < 1800:
        return
    with (d.ROOT.parent / 'defect_master.log').open('a', encoding='utf-8') as log:
        _master_process = subprocess.Popen([sys.executable, '-X', 'utf8', '-m', 'core.defect_runtime', 'master'],
            cwd=d.ROOT.parents[1], stdout=log, stderr=log,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def _notice(path, row, payload, export, send, field):
    before = export(row['recipient'])
    if is_paused():
        return
    previous = row['status']
    row.update(status='notice_unknown', notice_payload=payload, notice_previous_status=previous,
               notice_before_ids=[e['event_id'] for e in before], notice_receipt_field=field)
    save(path, row)
    send(row['recipient'], payload)
    found = d.verified_message(payload, row['notice_before_ids'], export(row['recipient']))
    if found:
        row.update(status=previous)
        row[field] = found['event_id']
        save(path, row)


def poll(export, send):
    global _next_collection, _adapter
    cfg = config()
    if not cfg.get('enabled') or is_paused():
        return
    cutoff = forward_config()['start_at']
    if time.monotonic() >= _next_collection:
        _next_collection = time.monotonic() + cfg.get('collection_interval_sec', 60)
        # No attachment drawer/download/upload; the export includes text only.
        for event in export('수입불량방'):
            d.capture(event, cutoff, d.recipient_for(event))
        start_master()
        return
    active = []
    for path in d.ROOT.glob('*.json'):
        row = d.load(path)
        stamp = timestamp(row['event'].get('timestamp', ''))
        from datetime import datetime
        if stamp is None or stamp <= datetime.fromisoformat(cutoff) or row['recipient'] != d.recipient_for(row['event']):
            continue
        if row.get('retry_at', 0) > time.time():
            continue
        if row['status'] in ('needs_match', 'ready_question', 'waiting', 'awaiting_product', 'approved',
                             'question_unknown', 'notice_unknown') or (
                row['status'] in ('completed', 'declined') and not row.get('receipt_event_id')):
            active.append((path, row))
    if not active:
        return
    # Round-robin means an unanswered request cannot starve new questions.
    path, row = min(active, key=lambda pair: pair[1].get('last_polled_at', 0))
    row['last_polled_at'] = time.time()
    save(path, row)
    try:
        status = row['status']
        if status == 'question_unknown':
            d.reconcile_question(path, export(row['recipient']))
        elif status == 'notice_unknown':
            if 'notice_before_ids' not in row:
                return
            found = d.verified_message(row['notice_payload'], row['notice_before_ids'], export(row['recipient']))
            if found:
                row['status'] = row['notice_previous_status']
                row[row['notice_receipt_field']] = found['event_id']
                save(path, row)
        elif status == 'needs_match':
            start_master()
            if MASTER.exists() and time.time() - MASTER.stat().st_mtime < 1800:
                save(path, d.rematch(row, d.load(MASTER)))
        elif status == 'ready_question':
            d.send_question(path, export, send, is_paused)
        elif status in ('waiting', 'awaiting_product'):
            history = export(row['recipient'])
            ids = [e['event_id'] for e in history]
            boundary = row.get('question_event_id')
            if boundary not in ids:
                return
            later = history[ids.index(boundary)+1:]
            for event in later:
                updated = d.apply_reply(row, event, {e['event_id'] for e in later})
                if updated != row:
                    row = updated
                    save(path, row)
                    break
            if row['status'] == 'awaiting_product' and not row.get('correction_notice_id'):
                _notice(path, row, row['correction_message'], export, send, 'correction_notice_id')
        elif status == 'approved':
            if not cfg.get('write_enabled'):
                return
            if _adapter is None or _adapter.profile != cfg.get('credential_profile', '강현우'):
                _adapter = DefectAdapter(cfg.get('credential_profile', '강현우'))
            d.submit(path, _adapter, is_paused)
        elif status in ('completed', 'declined'):
            payload = (f"{d.label(row)} 처리했습니다.\n영업수입불량차감 > 영업 입력 저장 완료."
                       if status == 'completed' else f"{d.label(row)} 안함 처리했습니다. 입력에서 제외했습니다.")
            _notice(path, row, payload, export, send, 'receipt_event_id')
    except Exception as exc:
        from core.moyi_control import OperationPaused
        import pyautogui
        if isinstance(exc, (OperationPaused, pyautogui.FailSafeException)):
            raise
        # Preserve the latest write-ahead status, never overwrite unknown with approved.
        latest = d.load(path)
        latest.update(last_error=str(exc)[:250], retry_at=time.time()+60)
        save(path, latest)


if __name__ == '__main__' and sys.argv[-1] == 'master':
    from dotenv import load_dotenv
    from core.order_services import master
    load_dotenv(d.ROOT.parents[1] / '.env')
    data = master()
    save(MASTER, {'products': data['products'], 'customers': data['customers']})
