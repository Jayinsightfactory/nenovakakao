import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch
from core import keyword_forward as k
from core import keyword_approval as a


class KeywordTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for name in ('CONFIG', 'STATE', 'LOG'):
            p = patch.object(k, name, Path(self.tmp.name) / (name + '.json'))
            p.start()
            self.addCleanup(p.stop)
        k.save_json(k.CONFIG, dict(enabled=True, source='영업방', target='현장 추가취소방', keywords=['추가', '취소', '변경'], start_at='2026-08-26T09:05:25+09:00'))
        self.send = Mock()
        p = patch.object(a, 'REQUESTS', Path(self.tmp.name) / 'approvals.json')
        p.start()
        self.addCleanup(p.stop)
        self.history = '현장 추가취소방 님과 카카오톡 대화\n--------------- 2026년 8월 26일 수요일 ---------------\n[직원] [오전 9:00] 기존 대화'

    def event(self, content='장미 2박스 추가', minute='06', eid='one'):
        return dict(event_id=eid, sender_name='직원', timestamp=f'2026년 8월 26일 오전 9:{minute}', content=content)

    def run_route(self, events, export=None, paused=lambda: False):
        k.process_source('영업방', events, export or Mock(return_value=self.history), self.send, paused)

    def test_cutoff_and_keywords(self):
        self.run_route([self.event(minute='04'), self.event(minute='05', eid='two'), self.event('안녕하세요', eid='three')])
        self.send.assert_not_called()

    def test_kakao_hint_is_not_a_draft(self):
        self.assertFalse(k.has_draft('메시지 입력'))
        self.assertFalse(k.has_draft(''))
        self.assertTrue(k.has_draft('작성 중인 내용'))
        self.assertTrue(k.has_draft('메시지 입력 중'))

    def test_success_verified_and_replay_suppressed(self):
        after = self.history + '\n[나] [오전 9:07] 직원 - 장미 2박스 추가'
        event = self.event()
        self.run_route([event])
        a.route_status(event, '승인됨', 'test approval')
        self.run_route([event], Mock(side_effect=[self.history, after]))
        self.assertEqual(k.read_json(k.STATE, {})['one']['status'], '전송 성공')
        self.run_route([self.event()])
        self.send.assert_called_once()

    def test_target_duplicate_raw_or_prefixed(self):
        for content in ('장미 2박스 추가', '다른직원 - 장미 2박스 추가'):
            self.run_route([self.event(eid=content)], Mock(return_value=self.history + '\n[기존] [오전 9:01] ' + content))
        self.send.assert_not_called()

    def test_uncertain_send_never_retried(self):
        self.send.side_effect = RuntimeError('uncertain')
        event = self.event()
        self.run_route([event])
        a.route_status(event, '승인됨', 'test approval')
        self.run_route([event])
        self.run_route([self.event(), self.event(eid='same-body')])
        self.send.assert_called_once()
        self.assertEqual(k.read_json(k.STATE, {})['one']['status'], '결과 불명')

    def test_paused_and_wrong_history_block(self):
        self.run_route([self.event()], paused=lambda: True)
        self.send.assert_not_called()
        self.run_route([self.event()], Mock(return_value='잘못된 방'))
        self.send.assert_not_called()
        self.assertEqual(k.read_json(k.STATE, {})['one']['status'], '확인 필요')

    def test_whitespace_but_not_quantities(self):
        self.assertTrue(k.duplicate('장미 2박스\n추가', [{'content': '직원 - 장미 2박스 추가'}]))
        self.assertFalse(k.duplicate('장미 3박스 추가', [{'content': '장미 2박스 추가'}]))

    def test_unconfirmed_history_is_not_success(self):
        event = self.event()
        self.run_route([event])
        a.route_status(event, '승인됨', 'test approval')
        self.run_route([event])
        self.assertEqual(k.read_json(k.STATE, {})['one']['status'], '결과 불명')

    def test_plain_keyword_message_requires_approval(self):
        self.run_route([self.event('장미 2박스 추가')])
        self.send.assert_not_called()
        self.assertEqual(k.read_json(k.STATE, {})['one']['status'], '승인대기')

    def test_shipping_date_change_requires_approval(self):
        self.run_route([self.event('35-1 중국 출고일 변경사항\n목요일 출고로 변경')])
        self.send.assert_not_called()
        self.assertEqual(k.read_json(k.STATE, {})['one']['status'], '승인대기')

    def test_complete_message_requires_approval(self):
        self.run_route([self.event('장미 추가 완료')])
        self.send.assert_not_called()
        self.assertEqual(k.read_json(k.STATE, {})['one']['status'], '승인대기')
        self.assertEqual(len(k.read_json(a.REQUESTS, {})), 1)

    def test_quote_message_requires_approval(self):
        self.run_route([self.event('장미 추가 견적 요청')])
        self.send.assert_not_called()
        self.assertEqual(k.read_json(k.STATE, {})['one']['status'], '승인대기')
        self.assertEqual(len(k.read_json(a.REQUESTS, {})), 1)

    def test_both_approval_words_create_one_request(self):
        self.run_route([self.event('장미 추가 견적 완료')])
        self.send.assert_not_called()
        self.assertEqual(len(k.read_json(a.REQUESTS, {})), 1)

    def test_reply_identity_boundary_and_number(self):
        row = dict(id='ABC123', baseline=['old'], request_event_id='request')
        def reply(eid, sender, body):
            return dict(event_id=eid, sender_name=sender, content=body)
        before = [reply('old', a.APPROVER, '보내 ABC123'), reply('request', '나', '요청')]
        self.assertIsNone(a.decision(before, row))
        self.assertIsNone(a.decision(before + [reply('new', '다른사람', '보내 ABC123')], row))
        self.assertIsNone(a.decision(before + [reply('new', a.APPROVER, '보내 ABC124')], row))
        self.assertIsNone(a.decision(before + [reply('new', a.APPROVER, '보내')], row))
        self.assertEqual(a.decision(before + [reply('new', a.APPROVER, '보내 ABC123')], row), '보내')
        self.assertEqual(a.decision(before + [reply('new', a.APPROVER, '보내지마 ABC123')], row), '보내지마')

    def test_approval_request_and_accept_flow(self):
        event = self.event('장미 추가 완료')
        self.run_route([event])
        rid = next(iter(k.read_json(a.REQUESTS, {})))
        histories = {a.APPROVER: a.APPROVER + ' 님과 카카오톡 대화\n--------------- 2026년 8월 26일 수요일 ---------------\n[직원] [오전 9:00] 안녕', '현장 추가취소방': self.history}
        sends = []
        def send(title, body):
            sends.append(title)
            histories[title] += '\n[나] [오전 9:10] ' + body
        mark = Mock()
        a.poll(histories.__getitem__, send, lambda: False, mark)
        self.assertEqual(sends, [a.APPROVER])
        self.assertEqual(k.read_json(a.REQUESTS, {})[rid]['status'], 'waiting')
        histories[a.APPROVER] += f'\n[{a.APPROVER}] [오전 9:11] 보내 {rid}'
        a.poll(histories.__getitem__, send, lambda: False, mark)
        self.assertEqual(sends, [a.APPROVER, '현장 추가취소방'])
        self.assertEqual(k.read_json(k.STATE, {})['one']['status'], '전송 성공')
        a.poll(histories.__getitem__, send, lambda: False, mark)
        self.assertEqual(len(sends), 2)

    def test_request_failure_is_not_resent(self):
        self.run_route([self.event('장미 추가 완료')])
        export = Mock(return_value=a.APPROVER + ' 님과 카카오톡 대화')
        send = Mock(side_effect=RuntimeError('unknown'))
        a.poll(export, send, lambda: False, Mock())
        a.poll(export, send, lambda: False, Mock())
        send.assert_called_once()

    def test_scan_groups_approvals_but_not_regular_messages(self):
        events = [self.event(f'장미 {i} 추가 견적', eid=str(i)) for i in range(5)]
        self.run_route(events)
        rows = k.read_json(a.REQUESTS, {})
        self.assertEqual(len(rows), 1)
        row = next(iter(rows.values()))
        self.assertEqual(len(row['events']), 5)
        self.assertIn('6. 모두 보낸다', a.request_message(row))
        self.assertIn('7. 모두 안 보낸다', a.request_message(row))
        self.run_route([self.event('다른 추가 완료', eid='next-scan')])
        self.assertEqual(len(k.read_json(a.REQUESTS, {})), 2)

    def test_single_approval_prompt_is_compact_and_removes_deleted_marker(self):
        row = {'id': 'ABC123', 'event': {
            'sender_name': '정재훈',
            'content': '35-1 출고일 변경사항\n광주천사\n화이트 1박스\n메시지가 삭제되었습니다.'}}
        message = a.request_message(row)
        self.assertIn('보내 ABC123', message)
        self.assertNotIn('메시지가 삭제되었습니다', message)
        self.assertLess(len(message), 400)

    def test_deleted_kakao_message_is_never_queued_for_approval(self):
        event = self.event('35-1 출고일 변경사항\n메시지가 삭제되었습니다.')
        self.run_route([event])
        self.assertEqual(k.read_json(k.STATE, {})[event['event_id']]['status'], '삭제 메시지 생략')
        self.assertEqual(k.read_json(a.REQUESTS, {}), {})

    def test_one_event_batch_uses_compact_single_prompt(self):
        event = {'sender_name': '정재훈', 'content': '35-1 출고일 변경사항'}
        row = {'id': 'ABC123', 'event': event, 'events': [event]}
        message = a.request_message(row)
        self.assertIn('보내 ABC123 / 보내지마 ABC123', message)
        self.assertNotIn('몇 번을 빼고', message)

    def test_batch_choices_validate_request_and_ranges(self):
        row = dict(id='ABC', events=[{}] * 5)
        self.assertEqual(a.batch_selection('ABC 2,4', row), [0, 2, 4])
        self.assertEqual(a.batch_selection('ABC 6', row), [0, 1, 2, 3, 4])
        self.assertEqual(a.batch_selection('ABC 7', row), [])
        for invalid in ('6', 'OTHER 6', 'ABC 0', 'ABC 8', 'ABC 6,2', 'ABC 2,2', 'ABC 1,', 'ABC 1 2'):
            self.assertIsNone(a.batch_selection(invalid, row), invalid)

    def test_batch_reply_requires_sender_and_request_boundary(self):
        row = dict(id='ABC', events=[{}] * 5, baseline=['old'], request_event_id='request')
        def reply(eid, sender, content):
            return dict(event_id=eid, sender_name=sender, content=content)
        old = reply('old', a.APPROVER, 'ABC 6')
        boundary = reply('request', '나', 'approval')
        self.assertIsNone(a.decision([old], row))
        self.assertIsNone(a.decision([old, boundary, reply('x', '다른사람', 'ABC 6')], row))
        self.assertEqual(a.decision([old, boundary, reply('x', a.APPROVER, 'ABC 7')], row), [])

    def test_batch_recovery_rechecks_duplicate_and_preserves_other_work(self):
        event = self.event('장미 추가 견적')
        self.run_route([event])
        rows = k.read_json(a.REQUESTS, {})
        rid = next(iter(rows))
        rows[rid].update(status='approved', selected=[0])
        k.save_json(a.REQUESTS, rows)
        export = Mock(return_value=self.history + '\n[직원] [오전 9:09] 장미 추가 견적')
        a.poll(export, self.send, lambda: True, Mock())
        self.assertEqual(k.read_json(k.STATE, {})['one']['status'], '승인대기')
        a.poll(export, self.send, lambda: False, Mock())
        self.send.assert_not_called()
        self.assertEqual(k.read_json(k.STATE, {})['one']['status'], '중복 생략')
        # A pending approval must not block regular forwarding.
        self.run_route([self.event('다른 추가 완료', eid='pending')])
        normal = self.event('장미 7박스 추가', eid='normal')
        after = self.history + '\n[나] [오전 9:12] 직원 - 장미 7박스 추가'
        self.run_route([normal])
        a.route_status(normal, '승인됨', 'test approval')
        self.run_route([normal], Mock(side_effect=[self.history, after]))
        self.assertEqual(k.read_json(k.STATE, {})['normal']['status'], '전송 성공')

    def test_batch_exclusion_delivery_and_replay(self):
        events = [self.event(f'장미 {i} 추가 완료', eid=str(i)) for i in range(3)]
        self.run_route(events)
        rid = next(iter(k.read_json(a.REQUESTS, {})))
        histories = {a.APPROVER: a.APPROVER + ' 님과 카카오톡 대화\n--------------- 2026년 8월 26일 수요일 ---------------\n[직원] [오전 9:00] 안녕', '현장 추가취소방': self.history}
        sends = []
        def send(title, body):
            sends.append((title, body))
            histories[title] += '\n[나] [오전 9:10] ' + body
        a.poll(histories.__getitem__, send, lambda: False, Mock())
        histories[a.APPROVER] += f'\n[{a.APPROVER}] [오전 9:11] {rid} 2'
        a.poll(histories.__getitem__, send, lambda: False, Mock())
        state = k.read_json(k.STATE, {})
        self.assertEqual([state[str(i)]['status'] for i in range(3)], ['전송 성공', '승인거절', '전송 성공'])
        self.assertEqual(len(sends), 3)
        a.poll(histories.__getitem__, send, lambda: False, Mock())
        self.assertEqual(len(sends), 3)

    def test_approved_duplicate_is_rechecked(self):
        event = self.event('장미 추가 완료')
        self.run_route([event])
        a.route_status(event, '승인됨', 'test approval')
        self.run_route([event], Mock(return_value=self.history + '\n[직원] [오전 9:09] 장미 추가 완료'))
        self.send.assert_not_called()
        self.assertEqual(k.read_json(k.STATE, {})['one']['status'], '중복 생략')


if __name__ == '__main__':
    unittest.main()
