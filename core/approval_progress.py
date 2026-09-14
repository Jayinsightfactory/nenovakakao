"""Per-item view joining durable approval, delivery and receipt evidence."""
import hashlib
from core import keyword_forward as k, keyword_approval as a, error_notifications as notices


def snapshot():
    requests = k.read_json(a.REQUESTS, {})
    routes = k.read_json(k.STATE, {})
    receipts = k.read_json(notices.STATE, {})
    result = []
    for row in requests.values():
        for i, event in enumerate(row.get('events') or [row['event']]):
            delivery = routes.get(event['event_id'], {})
            status = delivery.get('status', '')
            answer = row.get('item_answers', {}).get(str(i))
            key = hashlib.sha256(f"approval-result:{row['id']}:{event['event_id']}".encode()).hexdigest()
            receipt = receipts.get(key, {}).get('status', '')
            if row['status'] in ('operator_changed_review', 'historical_review', 'stale_review') or (
                    row['status'] != 'queued' and row.get('approver_name', a.LEGACY_NAME) != a.operator_name()):
                stage = '별도 검토'
            elif row['status'] == 'resolved_existing':
                stage = '질문 전 중복 제외'
            elif status in a.HELD_ITEMS or row['status'] in ('request_unknown', 'delivery_held', 'hold'):
                stage = '결과 확인 필요'
            elif status in a.COMPLETED_ITEMS:
                stage = ('완료' if receipt == 'sent' else '완료 알림 확인' if receipt == 'unknown'
                         else '완료 알림 대기' if receipt == 'queued' else '처리 완료·알림 기록 확인')
            elif answer == 'approve' or row['status'] == 'approved':
                stage = '승인 후 전달 대기'
            elif row.get('request_event_id') and row['status'] in ('waiting', 'awaiting_late_reply'):
                stage = '답변 대기'
            elif row['status'] == 'queued':
                stage = '대조·질문 대기'
            else:
                stage = {'expired': '과거 종료', 'expired_deleted_message': '삭제 원문 제외',
                         'manually_resolved': '수동 완료', 'filtered_non_actionable': '전달 대상 아님',
                         'resolved': '처리 기록 확인', 'rejected': '미전송 처리'}.get(row['status'], '기록 확인 필요')
            result.append({'request_id': row['id'], 'event_id': event['event_id'],
                'label': a.item_label(row, i), 'source_time': event.get('timestamp', ''),
                'sender': event.get('sender_name', ''), 'stage': stage,
                'answer': answer, 'delivery': status, 'receipt': receipt,
                'detail': delivery.get('detail', ''), 'created_at': row.get('created_at', 0),
                'preview': event.get('content', '')[:160]})
    return result
