"""Separate source clarification contacts from completion/report recipients."""
from core import defect_approval as d


def completion_recipient(row, policy='source_author'):
    if policy == 'customer_manager':
        staff = str((row.get('customer') or {}).get('staff') or '').strip()
        target = d.RECIPIENTS.get(staff)
        if not target:
            raise ValueError('불량 완료 알림 보류: 업체 담당자 카카오톡 매핑 확인 필요')
        return target
    if policy != 'source_author':
        raise ValueError('불량 알림 수신 기준 확인 필요')
    target = d.recipient_for(row.get('event', {}))
    stored = row.get('recipient')
    if target and stored != target:
        raise ValueError('불량 알림 보류: 원문 작성자와 기존 수신자 불일치')
    if not target:
        target = stored if stored in set(d.RECIPIENTS.values()) else None
    if not target:
        raise ValueError('불량 알림 수신자 확인 필요')
    return target
