"""Durable daytime photo deferral; only collect after 18:00 KST.

The drawer exposes newest thumbnails only. Never attach today's newest image
to an older event unless the complete pending tail can be accounted for.
"""
from datetime import datetime, timezone, timedelta
from pathlib import Path
import hashlib
import json
import time
from core.atomic_json import save

QUEUE = Path(__file__).resolve().parents[1] / 'data' / 'deferred_photos'
KST = timezone(timedelta(hours=9))


def allowed(now=None):
    now = now or datetime.now(KST)
    return now.astimezone(KST).hour >= 18


def enqueue(binding, title, event):
    path = QUEUE / (hashlib.sha256(event['event_id'].encode()).hexdigest()+'.json')
    if not path.exists():
        save(path, {'binding':binding, 'title':title, 'event':event, 'status':'queued'})


def drain_one(server, secret):
    from core.moyi_control import is_paused
    if not allowed() or is_paused(): return
    from core import moyi_inbound as inbound
    from core.inbound_archive_queue import enqueue as archive
    jobs = [(p,json.loads(p.read_text(encoding='utf-8'))) for p in QUEUE.glob('*.json')]
    pending = [(p,r) for p,r in jobs if r['status']=='queued' and r.get('retry_at',0)<=time.time()]
    if not pending: return
    title = pending[0][1]['title']
    group = [(p,r) for p,r in jobs if r['title']==title and r['status']=='queued']
    for p,r in group:
        r['retry_at']=time.time()+1800; save(p,r)
    binding = group[0][1]['binding']
    events = inbound.parse_export(inbound.export_exact_room(title),binding)
    photos = [e for e in events if inbound.PHOTO_MARKER_RE.search(e['content'])]
    tail = photos[-len(group):]
    expected = {r['event']['event_id'] for _,r in group}
    # Album download cardinality cannot reliably identify per-message images.
    safe = (len(group)<=12 and len(tail)==len(group)
            and {e['event_id'] for e in tail}==expected
            and all(e['content'].strip() in ('사진','[사진]','Photo','[Photo]') for e in tail))
    if not safe:
        for p,r in group:
            r['reason']='서랍 최신 사진과 원문 대응 확인 필요'; save(p,r)
        return
    if is_paused(): return
    files = inbound._collect_photo_files(title,len(group))
    if len(files)!=len(group):
        for p,r in group:
            r['reason']='사진 파일 수와 원문 수 불일치'; save(p,r)
        return
    by_id = {r['event']['event_id']:(p,r) for p,r in group}
    for event,path in zip(reversed(tail),files):
        if not allowed() or is_paused(): return
        p,row = by_id[event['event_id']]
        attachment = inbound._upload_attachment(server,{'X-Company-Secret':secret},path)
        archive({**event,'room_binding_id':binding,'external_room_id':title,
                 'origin':'kakao','attachments':[attachment]})
        row['status']='archived'; save(p,row)
