"""Read-only evidence classification; never authorizes a write or adoption."""
import re
from datetime import datetime
from core.defect_services import DefectAdapter
from core.keyword_forward import timestamp


def source_sequence(value):
    match = re.fullmatch(r'(\d{1,2})-(\d{1,2})', str(value).strip())
    return tuple(map(int, match.groups())) if match else None


def classify(row, entries):
    adapter = DefectAdapter()
    desired = {adapter.signature(x) for x in adapter.payload(row)['rows']}
    matches = []
    posted = timestamp(row['event'].get('timestamp', ''))
    sequence = row['extracted'].get('sequence', '')
    for entry in entries:
        if entry.get('isDeleted') or entry.get('deductionType') != '불량차감':
            continue
        if adapter.signature(entry) not in desired:
            continue
        note = str(entry.get('note') or '')
        scopes = re.findall(r'(?<!\d)\d{1,2}-\d{1,2}(?!\d)', note)
        reason = 'same_signature_only'
        if re.search(r'kakao-defect:' + re.escape(row['id']) + r':\d+(?!\d)', note):
            reason = 'request_marker_present'
        elif source_sequence(sequence) and scopes and source_sequence(sequence) not in {
                source_sequence(s) for s in scopes}:
            reason = 'different_source_sequence'
        elif posted and entry.get('estimateDtm'):
            try:
                applied = datetime.fromisoformat(str(entry['estimateDtm']).replace('Z', '+00:00'))
            except ValueError:
                applied = None
            if applied and applied.date() < posted.date():
                # EstimateDtm is a business date and may be backdated. It is
                # not proof of when this source message was registered.
                reason = 'estimate_date_before_message_only'
        matches.append({'deduction_key': entry.get('deductionKey'),
                        'reason': reason, 'order_week': entry.get('orderWeek'),
                        'source_sequences': scopes})
    return matches
