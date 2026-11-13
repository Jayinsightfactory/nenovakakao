"""Weekday 13:00/16:00 checks inside the existing single UI worker."""
from datetime import datetime
from pathlib import Path
import time
from core.atomic_json import save
from core import keyword_forward as k

STATE = Path(__file__).resolve().parents[1] / 'data' / 'keyword_missing_schedule.json'


def due_slot(cfg, now):
    schedule=cfg.get('approval_missing_schedule') or {}
    if cfg.get('approval_missing_recheck_mode')!='fixed': return None
    local=datetime.fromtimestamp(now,k.KST)
    if local.weekday() not in schedule.get('weekdays',[0,1,2,3,4]): return None
    hours=[h for h in schedule.get('hours',[13,16]) if 0<=h<=23 and h<=local.hour]
    if not hours: return None
    hour=max(hours)
    return f'{local.date().isoformat()}:{hour:02d}:00'


def poll(export, send, paused):
    cfg=k.config();now=time.time()
    if paused() or not cfg.get('enabled') or not cfg.get('approval_missing_recheck'): return
    key=due_slot(cfg,now)
    if key is None: return
    slots=k.read_json(STATE,{})
    slot=slots.get(key)
    if slot and (slot['status'] in ('completed','completed_with_holds') or slot.get('retry_at',0)>now): return
    from core import keyword_approval as a
    if not a.question_hours_open(cfg): return
    rows=k.read_json(a.REQUESTS,{})
    if slot is None:
        ids=[rid for rid,r in rows.items() if r.get('status') in ('waiting','awaiting_late_reply')
             and any(a.recheck_today(e,now) for e in r.get('events') or [r['event']])]
        # Late start catches up only the most recent same-day slot, once.
        # Earlier unsatisfied slots remain explicitly missed, never completed.
        for previous,entry in slots.items():
            if previous[:10]==key[:10] and previous<key and entry['status']!='completed':
                entry.update(status='superseded_unfinished',superseded_by=key)
        slot={'scheduled_slot':key,'started_at':now,'status':'running','request_ids':ids,'results':{}}
        slots[key]=slot;save(STATE,slots)
    # One full, fresh target read per pass shared by all source requests.
    # A second absence observation requires another pass after the separation.
    target_cache={};approver_cache={}
    def cached_export(room):
        if room not in target_cache: target_cache[room]=export(room)
        return target_cache[room]
    def history(refresh=False):
        approver=a.operator_name()
        if refresh or approver not in approver_cache:
            text=export(approver)
            if (text.splitlines() or [''])[0].strip() not in (
                    approver+' 님과 카카오톡 대화',approver+' 임과 카카오톡 대화'):
                raise RuntimeError('고정 미전달 점검 승인자 방 제목 불일치')
            from core.moyi_inbound import parse_export
            approver_cache[approver]=parse_export(text,'keyword-approval')
        return approver_cache[approver]
    retry={'history_unavailable','awaiting_confirmation','check_failed'}
    for rid in slot['request_ids']:
        if paused(): return
        previous=slot['results'].get(rid,{})
        if previous and previous['status'] not in retry: continue
        if rid not in rows:
            slot['results'][rid]={'status':'request_missing'};save(STATE,slots);continue
        try:
            outcome=a.recheck_missing(rows,cached_export,send,paused,history,
                                      schedule_slot=key,request_id=rid)
            if paused(): return
            slot['results'][rid]=outcome or {'status':'ineligible_existing_state'}
        except Exception as exc:
            slot['results'][rid]={'status':'check_failed','error_type':type(exc).__name__}
        slot['results'][rid]['attempts']=previous.get('attempts',0)+1
        if (slot['results'][rid]['status'] in ('history_unavailable','check_failed')
                and slot['results'][rid]['attempts']>=3):
            slot['results'][rid]['status']='blocked_verification'
        save(STATE,slots)
    pending=[rid for rid in slot['request_ids'] if slot['results'].get(rid,{}).get('status') in retry]
    if pending:
        slot.update(status='waiting_verification',retry_at=now+max(60,float(cfg.get('approval_missing_confirmation_sec',60))))
    else:
        held=any(r['status'] in ('blocked_verification','request_missing','reminder_unknown','ineligible_existing_state')
                 for r in slot['results'].values())
        slot.update(status='completed_with_holds' if held else 'completed',completed_at=time.time())
    slot['last_checked_at']=time.time()
    save(STATE,slots)
