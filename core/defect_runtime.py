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
_master_retry_at = 0


def config():
    return d.load(CONFIG) if CONFIG.exists() else {'enabled': False}


def start_master():
    global _master_process, _master_retry_at
    if _master_process is not None and _master_process.poll() is None:
        return
    if MASTER.exists() and time.time() - MASTER.stat().st_mtime < 1800:
        return
    if time.monotonic() < _master_retry_at:
        return
    _master_retry_at = time.monotonic() + 300
    with (d.ROOT.parent / 'defect_master.log').open('a', encoding='utf-8') as log:
        _master_process = subprocess.Popen([sys.executable, '-X', 'utf8', '-m', 'core.defect_runtime', 'master'],
            cwd=d.ROOT.parents[1], stdout=log, stderr=log,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def _notice(path, row, payload, export, send, field):
    from core.defect_notifications import completion_recipient
    target = (completion_recipient(row, config().get('completion_recipient_policy', 'source_author'))
              if row['status'] == 'completed' and field == 'receipt_event_id' else row['recipient'])
    before = export(target)
    if is_paused():
        return
    previous = row['status']
    row.update(status='notice_unknown', notice_payload=payload, notice_previous_status=previous,
               notice_recipient=target,
               notice_before_ids=[e['event_id'] for e in before], notice_receipt_field=field)
    save(path, row)
    send(target, payload)
    after = export(target)
    found = d.verified_message(payload, row['notice_before_ids'], after)
    if found:
        row.update(status=previous)
        row[field] = found['event_id']
        save(path, row)
        return after


def completion_message(row):
    if row['status'] == 'duplicate_source_hold':
        return '동일 업체·품목·수량 원장이 있어 원문 대조 중입니다. 추가 등록하지 않았습니다. 등록 완료로 확정하지 않았습니다.'
    if row['status'] in ('duplicate_skipped', 'match_held'):
        source = row['extracted']
        title = ('중복 내역이 있어 추가 등록하지 않았습니다.'
                 if row['status'] == 'duplicate_skipped' else '품목 또는 거래처 매칭이 불확실해 등록을 보류했습니다.')
        if row.get('duplicate_partial'):
            title += ' 일부 항목만 중복되어 나머지 항목도 보류했습니다.'
        return '\n'.join([f"{d.label(row)} {title}",
                          f"{source.get('sequence', '')} · {source.get('customer', '')}",
                          *[f"{i['product_raw']} {i['quantity_raw']}{i['unit_raw']}"
                            for i in source['items']]])
    lines = [f"✅ {d.label(row)} 처리했습니다.", "[불량 처리 확인서 · 등록 완료]"]
    for receipt in row.get('receipt') or []:
        lines.append(f"{receipt.get('orderYear', '')}년 {receipt.get('orderWeek', '')}차 · {receipt.get('customerName', '')}")
        lines.append(f"▶ {receipt.get('matchedProductName') or receipt.get('productName', '')} · {receipt.get('quantity', '')}{receipt.get('sourceUnit', '')}")
        lines.append(f"저장번호: {receipt.get('deductionKey', '')}")
    lines.append('영업수입불량차감 > 영업 입력에서 확인해주세요.')
    return '\n'.join(lines)


def poll(export, send, phase='all'):
    global _next_collection, _adapter
    cfg = config()
    if not cfg.get('enabled') or is_paused():
        return
    cutoff = forward_config()['start_at']
    processing_start = cfg.get('processing_start_at')
    if processing_start:
        from datetime import datetime
        cutoff = max((cutoff, processing_start), key=datetime.fromisoformat)
    if phase != 'responses' and time.monotonic() >= _next_collection:
        _next_collection = time.monotonic() + cfg.get('collection_interval_sec', 60)
        # No attachment drawer/download/upload; the export includes text only.
        for event in export('수입불량방'):
            d.capture(event, cutoff, d.recipient_for(event))
        start_master()
    active = []
    for path in d.ROOT.glob('*.json'):
        row = d.load(path)
        stamp = timestamp(row['event'].get('timestamp', ''))
        from datetime import datetime
        # Explicit restart scope also applies to previously asked/approved work.
        # Preserve old requests and receipts; do not replay the historical backlog.
        if processing_start and (stamp is None or stamp <= datetime.fromisoformat(processing_start)):
            continue
        if row.get('maintenance_hold'):
            # A read-only audit may find unresolved ledger ownership. Restart
            # must not turn that hold back into approval or another question.
            continue
        # The intake cutoff must not orphan a question already sent and verified.
        sent_question = bool(row.get('question_event_id'))
        if ((not sent_question and (stamp is None or stamp <= datetime.fromisoformat(cutoff)))
                or row['recipient'] != d.recipient_for(row['event'])):
            continue
        if row.get('retry_at', 0) > time.time():
            continue
        if phase == 'responses' and row['status'] in ('needs_match', 'ready_question'):
            continue
        if phase == 'intake' and row['status'] not in ('needs_match', 'ready_question', 'clarification_pending'):
            continue
        if row['status'] in ('needs_match', 'ready_question', 'clarification_pending', 'waiting', 'awaiting_product', 'approved',
                             'question_unknown', 'notice_unknown', 'write_unknown') or (
                row['status'] in ('completed', 'declined', 'duplicate_skipped', 'match_held') and not row.get('receipt_event_id')) or (
                row['status'] == 'completed' and cfg.get('completion_image_mode') != 'text' and row.get('screenshot_enabled')
                and row.get('screenshot_retry_at', 0) <= time.time()
                and row.get('screenshot_state') not in ('sent', 'unknown', 'failed')):
            active.append((path, row))
    if not active:
        return
    # Dedicated response phase cannot be starved by new matches/questions.
    path, row = min(active, key=lambda pair: (
        0 if pair[1]['status'] in ('completed','declined','notice_unknown','duplicate_skipped','match_held') and not pair[1].get('receipt_event_id')
        else 1 if pair[1]['status'] == 'approved' else 2,
        pair[1].get('last_polled_at', 0)))
    row['last_polled_at'] = time.time()
    save(path, row)
    try:
        status = row['status']
        if status == 'question_unknown':
            d.reconcile_question(path, export(row['recipient']))
        elif status == 'notice_unknown':
            if 'notice_before_ids' not in row:
                return
            # Reconcile in the room used before Enter, even if routing policy
            # changed. Never retransmit an uncertain notice into another room.
            found = d.verified_message(row['notice_payload'], row['notice_before_ids'], export(row.get('notice_recipient', row['recipient'])))
            if found:
                row['status'] = row['notice_previous_status']
                row[row['notice_receipt_field']] = found['event_id']
                save(path, row)
        elif status == 'needs_match':
            start_master()
            # Last good catalog may propose matches; validate() still checks
            # authoritative ERP keys before every write. Refresh outage must
            # not silently starve all new defects.
            if MASTER.exists():
                master = d.load(MASTER)
                from core import browser_session_auth
                if (time.time() - MASTER.stat().st_mtime >= 1800
                        and not browser_session_auth.state().get('blocked')):
                    from core.error_notifications import notify
                    notify('defect_master_error', context={'stage': '불량 품목 마스터 갱신',
                        'cause': '마스터 갱신 지연; 마지막 성공 목록 사용',
                        'automatic_action': '등록 전 네노바 서버 재매칭으로 품목 검증'})
                customer, _ = d.match_customer(row['extracted'], d._data(master, 'customers'))
                if customer:
                    from core.defect_distribution import poll as distribution_poll
                    evidence = distribution_poll(row, customer['nenova_key'])
                    if evidence is None:
                        return
                    master['distribution'] = evidence
                matched = d.rematch(row, master)
                if cfg.get('llm_review_enabled') and not d.ready(matched):
                    from core.defect_llm import poll as llm_poll
                    matched = llm_poll(matched)
                    if matched is None:
                        return
                save(path, matched)
                if cfg.get('approval_mode') == 'automatic':
                    d.prepare_automatic(path)
                elif not is_paused():
                    d.send_question(path, export, send, is_paused)
        elif status == 'ready_question':
            if cfg.get('approval_mode') == 'automatic':
                d.prepare_automatic(path)
            else:
                d.send_question(path, export, send, is_paused)
        elif status == 'clarification_pending':
            if cfg.get('approval_mode') == 'automatic' and d.ready(row):
                d.prepare_automatic(path)
                return
            row.update(status='ready_question', information_only=True)
            save(path, row)
            d.send_question(path, export, send, is_paused)
        elif status in ('waiting', 'awaiting_product'):
            if cfg.get('approval_mode') == 'automatic' and (not row.get('information_only') or d.ready(row)):
                if status == 'awaiting_product':
                    row['status'] = 'match_held'
                    save(path, row)
                else:
                    d.prepare_automatic(path)
                return
            history = export(row['recipient'])
            ids = [e['event_id'] for e in history]
            # One export contains replies to every pending question for this
            # author. Consume each against its own verified question boundary.
            for other_path, other in active:
                if other['recipient'] != row['recipient'] or other['status'] not in ('waiting','awaiting_product'):
                    continue
                # This export checked every waiting request for the recipient,
                # even if no new answer or question boundary was found.
                other['last_polled_at'] = row['last_polled_at']
                save(other_path, other)
                boundary = other.get('question_event_id')
                if boundary not in ids:
                    continue
                later = history[ids.index(boundary)+1:]
                for event in later:
                    updated = d.apply_reply(other,event,{e['event_id'] for e in later})
                    if updated != other:
                        save(other_path,updated)
                        if other_path == path:
                            row = updated
                        break
            if row['status'] == 'awaiting_product' and not row.get('correction_notice_id'):
                _notice(path, row, row['correction_message'], export, send, 'correction_notice_id')
        elif status in ('approved', 'write_unknown'):
            if not cfg.get('write_enabled'):
                return
            profile = 'browser-session' if cfg.get('transport') == 'browser-session' else cfg.get('credential_profile', '강현우')
            if _adapter is None or _adapter.profile != profile:
                if profile == 'browser-session':
                    from core.browser_session_bridge import BrowserDefectAdapter
                    _adapter = BrowserDefectAdapter()
                else:
                    _adapter = DefectAdapter(profile)
            if status == 'approved':
                if cfg.get('approval_mode') == 'automatic' and not d.auto_authorized(row):
                    d.prepare_automatic(path)
                d.submit(path, _adapter, is_paused)
                completed = d.load(path)
                if completed.get('status') == 'completed':
                    completed['screenshot_enabled'] = cfg.get('completion_image_mode') != 'text'
                    save(path, completed)
            else:
                # Reconcile a possibly successful write; never insert again.
                _adapter.validate(row)
                receipt = _adapter.lookup(row)
                if receipt is not None and _adapter.matches(row, receipt):
                    row.update(status='completed', receipt=receipt, completed_at=row.get('completed_at') or time.time(), screenshot_enabled=cfg.get('completion_image_mode') != 'text')
                else:
                    row['retry_at'] = time.time() + 300
                save(path, row)
        elif status in ('completed', 'declined', 'duplicate_skipped', 'match_held'):
            receipt_events = None
            payload = (completion_message(row)
                       if status != 'declined' else f"{d.label(row)} 안함 처리했습니다. 입력에서 제외했습니다.")
            if not row.get('receipt_event_id'):
                receipt_events = _notice(path, row, payload, export, send, 'receipt_event_id')
            if (row['status'] == 'completed' and cfg.get('completion_image_mode') != 'text' and row.get('screenshot_enabled')
                    and row.get('screenshot_state') not in ('sent', 'unknown', 'failed')):
                from core.defect_screenshot import deliver
                if row.get('screenshot_retry_at', 0) <= time.time():
                    try:
                        deliver(path, row, export, before_events=receipt_events)
                    except Exception as exc:
                        from core.moyi_control import OperationPaused
                        from core.browser_session_auth import SessionExpired
                        import pyautogui
                        if isinstance(exc, (OperationPaused, SessionExpired, pyautogui.FailSafeException)):
                            raise
                        latest = d.load(path)
                        # Once attachment Enter was pressed, never resend an
                        # uncertain image. Registration stays completed.
                        if latest.get('screenshot_state') == 'unknown':
                            latest['screenshot_error'] = str(exc)[:250]
                            save(path, latest)
                            return
                        attempts = latest.get('completion_image_failures', 0) + 1
                        latest.update(completion_image_failures=attempts,
                                      screenshot_state='failed' if attempts >= 2 else 'retry_pending',
                                      screenshot_retry_at=time.time()+60,
                                      screenshot_error=str(exc)[:250])
                        save(path, latest)
                        if attempts >= 2:
                            from core.error_notifications import notify
                            notify('defect_error', latest['id'].removeprefix('불량-'), {
                                'stage':'completed', 'sender':latest.get('recipient'),
                                'preview':latest['event'].get('content',''),
                                'cause':'처리 확인 이미지 전달 보류: '+str(exc)[:180]})
    except Exception as exc:
        from core.moyi_control import OperationPaused
        import pyautogui
        if isinstance(exc, (OperationPaused, pyautogui.FailSafeException)):
            raise
        # Preserve the latest write-ahead status, never overwrite unknown with approved.
        latest = d.load(path)
        latest.update(last_error=str(exc)[:250], retry_at=time.time()+60)
        save(path, latest)
        from core.browser_session_auth import SessionExpired
        if isinstance(exc, SessionExpired):
            return  # One shared, actionable login notice covers every waiting row.
        from core.error_notifications import notify
        notify('defect_error', latest['id'].removeprefix('불량-'), {
            'source_room': '수입불량방', 'sender': latest.get('recipient'),
            'preview': latest['event'].get('content', ''), 'stage': latest['status'],
            'cause': str(exc), 'automatic_action': '등록 결과 확인 후 재처리; 중복 등록 금지'})


if __name__ == '__main__' and sys.argv[-1] == 'master':
    from dotenv import load_dotenv
    from core.order_services import master
    load_dotenv(d.ROOT.parents[1] / '.env')
    if config().get('transport') == 'browser-session':
        from core.browser_session_bridge import request
        from core.order_services import _product_row, _customer_row
        products = request('defect-master', 'MASTER', params={'entity':'products'}, timeout=45)['items']
        customers = request('defect-master', 'MASTER', params={'entity':'customers'}, timeout=45)['items']
        if len(products) < 1000 or len(customers) < 500:
            raise RuntimeError('웹 세션 마스터 응답 불완전; 기존 기준표 유지')
        data = {'products': {'data': [_product_row(row) for row in products]},
                'customers': {'data': [_customer_row(row) for row in customers]}}
    else:
        data = master()
    save(MASTER, {'products': data['products'], 'customers': data['customers']})
    from core.error_notifications import resolve
    resolve('defect_master_error')
