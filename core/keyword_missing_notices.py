"""Additional informational recipients; approval ownership never changes."""
import hashlib
import time
from datetime import datetime
from pathlib import Path
from core.atomic_json import save
from core import keyword_forward as k

STATE = Path(__file__).resolve().parents[1] / 'data' / 'keyword_missing_notices.json'
PENDING = {'승인대기', '미응답 보류', '승인요청 전송대기'}


def enqueue(request, event, evidence):
    rows = k.read_json(STATE, {})
    for recipient in dict.fromkeys(k.config().get('approval_missing_notice_recipients', [])):
        if not isinstance(recipient, str) or not recipient.strip():
            continue
        recipient = recipient.strip()
        # The existing approver already receives the verified recheck question.
        if recipient == request.get('approver_name'):
            continue
        identity = '\x1f'.join((recipient, event.get('sender_name',''),
                                event.get('timestamp',''), k.normalize(event['content'])))
        key = hashlib.sha256(identity.encode()).hexdigest()
        rows.setdefault(key, {'id':key,'recipient':recipient,'request_id':request['id'],
            'event':dict(event),'evidence':dict(evidence),'status':'queued','created_at':time.time(),
            'payload':f"[추가취소 미전달 의심 알림 · {request['id']}]\n"
                f"[영업방 원문]\n{event.get('sender_name','')} · {event.get('timestamp','')}\n"
                f"{event['content']}\n\n"
                '추가취소방 기록을 두 차례 대조했으나 일치하는 전달 내용을 찾지 못했습니다.\n'
                '승인 답변 대기 중인 건이며, 누락 확정이나 재전송 지시가 아닙니다.\n'
                '수동 전달 여부를 확인해주세요. 승인 처리는 기존 승인 담당자에게 요청번호로 답해주세요.'})
    save(STATE, rows)


def poll(export, send, paused):
    if paused(): return
    rows = k.read_json(STATE, {})
    allowed = set(k.config().get('approval_missing_notice_recipients', []))
    forward = k.read_json(k.STATE, {})
    now = time.time()
    for row in sorted(rows.values(), key=lambda r:r['created_at']):
        if row['status'] not in ('queued','unknown') or row.get('retry_at',0)>now: continue
        if row['recipient'] not in allowed: continue
        if row['status']=='queued':
            stamp = k.timestamp(row['event'].get('timestamp',''))
            if stamp is None or stamp.date()!=datetime.fromtimestamp(now,k.KST).date():
                row['status']='expired'; save(STATE,rows); continue
            if forward.get(row['event']['event_id'],{}).get('status') not in PENDING:
                row['status']='resolved_before_send'; save(STATE,rows); continue
            if k.manual_handled_claim(row['event']['content']):
                row['status']='manual_handled_review'; save(STATE,rows); continue
        try:
            from core.moyi_inbound import parse_export
            if row['status']=='queued':
                target_room=k.config()['target']
                target_text=export(target_room)
                if (target_text.splitlines() or [''])[0].strip() not in (
                        target_room+' 님과 카카오톡 대화',target_room+' 임과 카카오톡 대화'):
                    raise RuntimeError('미전달 알림 대상 방 제목 불일치')
                target=parse_export(target_text,'keyword-target-recheck')
                stamps=[s for e in target if (s:=k.timestamp(e.get('timestamp',''))) is not None]
                if not stamps or min(stamps)>stamp:
                    row.update(retry_at=now+300,last_error='insufficient_target_history')
                    save(STATE,rows); return
                candidates=(k.exact_delivery_candidates(row['event'],target)
                            or k.possible_manual_delivery(row['event'],target)
                            or k.partial_manual_delivery(row['event'],target))
                if candidates:
                    row.update(status='resolved_before_send',target_event_ids=[e['event_id'] for e in candidates])
                    save(STATE,rows); continue
            text = export(row['recipient'])
            headers=(row['recipient']+' 님과 카카오톡 대화',row['recipient']+' 임과 카카오톡 대화')
            if (text.splitlines() or [''])[0].strip() not in headers:
                raise RuntimeError('미전달 알림 수신 방 제목 불일치')
            before = parse_export(text,'keyword-missing-notice')
            if row['status']=='unknown':
                matches={e['event_id']:e for e in before if e['event_id'] not in row['before_ids']
                         and k.normalize(e['content'])==k.normalize(row['payload'])}
                if len(matches)==1:
                    row.update(status='sent',event_id=next(iter(matches)),sent_at=now)
                else: row['retry_at']=now+300
                save(STATE,rows)
                return  # An uncertain notice is reconciled, never resent.
            if paused(): return
            # Recheck immediately before claiming the send, not just before export.
            if (row['recipient'] not in k.config().get('approval_missing_notice_recipients', [])
                    or k.read_json(k.STATE,{}).get(row['event']['event_id'],{}).get('status') not in PENDING):
                return
            row.update(status='unknown',before_ids=[e['event_id'] for e in before],attempted_at=now)
            save(STATE,rows)
            if paused(): return
            send(row['recipient'],row['payload'])
            after_text=export(row['recipient'])
            if (after_text.splitlines() or [''])[0].strip() not in headers:
                raise RuntimeError('미전달 알림 재조회 방 제목 불일치')
            after=parse_export(after_text,'keyword-missing-notice')
            matches={e['event_id']:e for e in after if e['event_id'] not in row['before_ids']
                     and k.normalize(e['content'])==k.normalize(row['payload'])}
            if len(matches)==1:
                row.update(status='sent',event_id=next(iter(matches)),sent_at=time.time())
            else: row['retry_at']=now+300
        except Exception as exc:
            row.update(last_error=type(exc).__name__,retry_at=now+300)
        save(STATE,rows)
        return  # Serialized single worker; at most one recipient per turn.
