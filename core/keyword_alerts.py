"""Durable, request-scoped notification receipts (not approval decisions)."""
import re
import time
from . import keyword_forward as k

RECEIPTS = k.ROOT / 'data' / 'keyword_approval_alerts.json'


def alert_key(row):
    match = re.search(r'요청\s+([A-Fa-f0-9]{12})(?![A-Za-z0-9])', row.get('detail', ''))
    return 'request:' + match[1].upper() if match else 'event:' + row['event_id']


def claim_alert(row):
    if row.get('status') != '승인대기':
        return False
    receipts = k.read_json(RECEIPTS, {})
    key = alert_key(row)
    if key in receipts:
        return False
    # Persist before showing a popup. On a crash the dashboard still shows
    # the pending request, without producing repeated popups on restart.
    receipts[key] = {'shown_at': time.time()}
    k.save_json(RECEIPTS, receipts)
    return True
