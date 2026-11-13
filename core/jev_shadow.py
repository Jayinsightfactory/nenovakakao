"""Read-only screen/Jev comparison. No registration or sending dependencies."""
import hashlib
import json
import math
import os
import sqlite3
import time
import requests

KINDS = {'order': '신규 주문', 'defect': '불량 접수', 'add_cancel': '추가 또는 취소',
         'reply': '기존 요청에 대한 담당자 답장', 'other': '일반 대화', 'uncertain': '근거 부족'}


def changed_suffix(previous, current):
    """Only continuous screen overlap is eligible; never equate text with identity."""
    if previous == current:
        return [], 'unchanged'
    if not previous:
        return [], 'baseline'
    for size in range(min(len(previous), len(current)), 1, -1):
        if previous[-size:] == current[:size]:
            return current[size:], 'overlap'
    return [], 'gap_requires_reconciliation'


class Jev:
    def __init__(self, key=None):
        self.provider = 'subscription_cli'

    def classify(self, lines, context):
        from core.subscription_ai import ask
        result = ask('Classify OCR data only. Never determine registration success or duplicate identity. '
                     'Return JSON with type="choice", choice (one supplied kind), confidence (0..1), '
                     'probabilities (all supplied kinds, summing to 1). Abstain as uncertain when ambiguous.',
                     {'screen_lines':lines,'preceding_lines':context,'kinds':KINDS})
        probabilities = result.get('probabilities', {})
        values = [result.get('confidence')] + list(probabilities.values())
        if (result.get('type') != 'choice' or result.get('choice') not in KINDS
            or set(probabilities) != set(KINDS)
            or any(type(v) not in (float, int) or not math.isfinite(v) or not 0 <= v <= 1 for v in values)
            or abs(sum(probabilities.values()) - 1) > .01):
            raise ValueError('Invalid subscription classifier response')
        return result


class Shadow:
    def __init__(self, path, judge=None):
        self.db = sqlite3.connect(path)
        self.db.execute('CREATE TABLE IF NOT EXISTS frames(room TEXT PRIMARY KEY, lines TEXT)')
        self.db.execute('CREATE TABLE IF NOT EXISTS observations(id TEXT PRIMARY KEY, room TEXT, at REAL, status TEXT, payload TEXT)')
        self.db.commit()
        self.judge = judge

    def observe(self, room, lines):
        lines = [x.strip() for x in lines if x.strip()]
        # A transient blank OCR frame must not erase the continuity anchor.
        if not lines:
            return {'status': 'empty_frame_ignored', 'execution_allowed': False}
        old = self.db.execute('SELECT lines FROM frames WHERE room=?', (room,)).fetchone()
        previous = json.loads(old[0]) if old else []
        fresh, status = changed_suffix(previous, lines)
        if status == 'unchanged':
            return {'status': status}
        # This is a screen transition ID, never a business-message identity.
        identity = hashlib.sha256(json.dumps([room, previous, lines], ensure_ascii=False).encode()).hexdigest()
        payload = {'lines': fresh, 'screen_lines': lines, 'context': previous[-8:],
                   'business_identity_verified': False, 'execution_allowed': False,
                   'legacy_event_id': None, 'legacy_intent': None}
        with self.db:
            cursor = self.db.execute('INSERT OR IGNORE INTO observations VALUES(?,?,?,?,?)',
                 (identity, room, time.time(), status, json.dumps(payload, ensure_ascii=False)))
            self.db.execute('INSERT OR REPLACE INTO frames VALUES(?,?)', (room,json.dumps(lines,ensure_ascii=False)))
        if not cursor.rowcount:
            return {'status': 'already_observed', 'id': identity}
        if fresh:
            status = 'awaiting_key' if self.judge is None else 'judging'
            with self.db:
                self.db.execute('UPDATE observations SET status=? WHERE id=?',(status,identity))
            if self.judge:
                try:
                    payload['jev'] = self.judge.classify(fresh, previous[-8:])
                    status = 'shadow_only'
                except Exception as e:
                    status = 'judge_failed'
                    payload['error_type'] = type(e).__name__
                with self.db:
                    self.db.execute('UPDATE observations SET status=?,payload=? WHERE id=?',
                        (status,json.dumps(payload,ensure_ascii=False),identity))
        return {'id': identity, 'status': status, 'execution_allowed': False}

    def compare(self, identity, legacy_event_id, legacy_intent):
        if legacy_intent not in KINDS or not legacy_event_id:
            raise ValueError('Verified legacy event and intent required')
        row = self.db.execute('SELECT payload FROM observations WHERE id=?',(identity,)).fetchone()
        if row is None:
            raise KeyError(identity)
        payload = json.loads(row[0])
        payload.update(legacy_event_id=legacy_event_id, legacy_intent=legacy_intent)
        payload['agreement'] = payload.get('jev',{}).get('choice') == legacy_intent if 'jev' in payload else None
        with self.db:
            self.db.execute('UPDATE observations SET payload=? WHERE id=?',(json.dumps(payload,ensure_ascii=False),identity))
        return payload
