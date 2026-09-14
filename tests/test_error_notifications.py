from unittest.mock import Mock
from core import error_notifications as notices


def test_unanswered_group_is_throttled_and_receipts_keep_priority(monkeypatch):
    from core import keyword_approval as a, keyword_forward as k
    monkeypatch.setattr('core.moyi_control.audit', Mock())
    monkeypatch.setattr(k, 'config', lambda: {'approval_active_hours': [0, 24]})
    now = [10000]
    monkeypatch.setattr(notices.time, 'time', lambda: now[0])
    sent = []
    monkeypatch.setattr('core.moyi_inbound.parse_export', lambda *args: [
        {'event_id': str(i), 'content': text} for i, text in enumerate(sent)])
    requests = {rid: {'id': rid, 'status': 'awaiting_late_reply', 'event': {'event_id': rid}}
                for rid in ('AAA111', 'BBB222', 'CCC333')}
    requests['DDD444'] = {'status': 'historical_review'}
    k.save_json(a.REQUESTS, requests)
    for rid in ('AAA111', 'BBB222', 'DDD444'):
        notices.enqueue('approval_unanswered', rid)
    export = Mock(return_value='임재용대리 님과 카카오톡 대화\n')
    send = lambda room, payload: sent.append(payload)
    notices.poll(export, send, lambda: False)
    assert len(sent) == 1 and '대기 모음' in sent[0]
    assert 'AAA111' in sent[0] and 'BBB222' in sent[0] and 'DDD444' not in sent[0]
    notices.enqueue('approval_unanswered', 'CCC333')
    now[0] += 10
    notices.poll(export, send, lambda: False)
    assert len(sent) == 1
    notices.approval_result('AAA111', 'item-a', '가', '승인거절')
    notices.poll(export, send, lambda: False)
    assert len(sent) == 2 and '처리했습니다' in sent[-1]
    now[0] += 3600
    notices.poll(export, send, lambda: False)
    assert len(sent) == 3 and 'CCC333' in sent[-1]
    notices.poll(export, send, lambda: False)
    assert len(sent) == 3


def test_unknown_group_is_not_replayed_and_throttles_followups(monkeypatch):
    from core import keyword_approval as a, keyword_forward as k
    monkeypatch.setattr('core.moyi_control.audit', Mock())
    monkeypatch.setattr(k, 'config', lambda: {'approval_active_hours': [0, 24]})
    monkeypatch.setattr('core.moyi_inbound.parse_export', lambda *args: [])
    k.save_json(a.REQUESTS, {rid: {'status': 'awaiting_late_reply', 'event': {}}
                            for rid in ('AAA111', 'BBB222')})
    notices.enqueue('approval_unanswered', 'AAA111')
    send = Mock(side_effect=TimeoutError())
    export = Mock(return_value='임재용대리 님과 카카오톡 대화\n')
    notices.poll(export, send, lambda: False)
    notices.enqueue('approval_unanswered', 'BBB222')
    notices.poll(export, send, lambda: False)
    assert send.call_count == 1


def test_item_receipts_send_during_approval_wait_and_do_not_repeat(monkeypatch):
    monkeypatch.setattr('core.moyi_control.audit', Mock())
    monkeypatch.setattr('core.keyword_approval.has_pending_question', lambda: True)
    notices.report('approval_waiting', 'ABC123')
    notices.approval_result('ABC123', 'event-a', '가', '승인거절')
    sent = []
    monkeypatch.setattr('core.moyi_inbound.parse_export', lambda *args: [
        {'event_id': str(i), 'content': text} for i, text in enumerate(sent)])
    export = Mock(return_value='임재용대리 님과 카카오톡 대화\n')
    send = lambda room, text: sent.append(text)
    notices.poll(export, send, lambda: False)
    assert len(sent) == 1
    assert '가: 안보내 — 전달하지 않도록 처리했습니다.' in sent[0]
    notices.approval_result('ABC123', 'event-a', '가', '승인거절')
    notices.poll(export, send, lambda: False)
    assert len(sent) == 1
    notices.approval_result('ABC123', 'event-b', '나', '전송 성공')
    notices.poll(export, send, lambda: False)
    assert len(sent) == 2 and '나: 보내 — 전달 완료했습니다.' in sent[1]


def test_item_receipt_unknown_result_never_replays(monkeypatch):
    monkeypatch.setattr('core.moyi_control.audit', Mock())
    monkeypatch.setattr('core.moyi_inbound.parse_export', lambda *args: [])
    notices.approval_result('ABC123', 'event', '가', '승인거절')
    export = Mock(return_value='임재용대리 님과 카카오톡 대화\n')
    send = Mock(side_effect=RuntimeError('uncertain'))
    notices.poll(export, send, lambda: False)
    notices.approval_result('ABC123', 'event', '가', '승인거절')
    notices.poll(export, send, lambda: False)
    assert send.call_count == 1
    assert next(iter(notices._rows().values()))['status'] == 'unknown'


def test_unfinished_forward_does_not_report_completion():
    for status in ('승인대기', '승인됨', '결과 불명', '확인 필요'):
        notices.approval_result('ABC123', 'event', '가', status)
    assert not notices._rows()


def test_report_collection_preserves_preview_artifacts():
    notices.report('worker_started')
    preview = notices.STATE.parent / 'operation_reports' / 'approval_preview.json'
    preview.write_text('{"candidates": [], "sent": false}', encoding='utf-8')
    notices.collect_reports()
    assert preview.exists()
    assert len(notices._rows()) == 1


def test_delayed_error_is_not_presented_as_a_new_occurrence(monkeypatch):
    monkeypatch.setattr(notices.time, 'time', lambda: 1000)
    row = {'id': 'test', 'category': 'forward_error', 'request_id': '', 'created_at': 100}
    assert '지연 알림' in notices.message(row)
    assert '재발·복구 여부는 별도 확인' in notices.message(row)
    row['created_at'] = 999
    assert '지연 알림' not in notices.message(row)


def test_approval_error_includes_request_and_safe_reason(monkeypatch):
    from core import keyword_approval, keyword_forward
    monkeypatch.setattr(keyword_forward, 'read_json', lambda *args: {})
    keyword_approval.route_status({'event_id': 'test'}, '확인 필요',
        '요청 68BA67B5BFCB 결과 불명: 입력란 원문 검증 실패; Enter 전송 차단')
    row = next(iter(notices._rows().values()))
    text = notices.message(row)
    assert '68BA67B5BFCB' in text
    assert 'Enter 전 전송 차단' in text
    assert '승인 질문과 추가취소 내용 모두 전송하지 않았습니다' in text


def test_notice_deduplicates_and_drops_untrusted_identifiers():
    key = notices.enqueue('order_error', 'password=secret')
    assert notices.enqueue('order_error', 'password=secret') == key
    rows = notices._rows()
    assert len(rows) == 1
    assert 'secret' not in notices.message(rows[key])
    assert rows[key]['request_id'] == ''
    assert '프로그램 로그 확인' not in notices.message(rows[key])


def test_notice_preserves_bounded_operator_context_and_redacts_secrets():
    key = notices.enqueue('approval_error', 'ABC123', {
        'source_room': '영업방', 'target_room': '현장 추가취소방', 'sender': '홍길동',
        'preview': '36-1 카네이션 2박스 추가',
        'stage': '승인 직전 중복 확인',
        'cause': '대화 저장 완료창 잔류 password=hidden https://example.test',
        'automatic_action': '전송하지 않고 보류',
    })
    text = notices.message(notices._rows()[key])
    for expected in ('원본 방: 영업방', '처리 대상 방: 현장 추가취소방', '원문 작성자: 홍길동',
                     '처리할 원문: 36-1 카네이션 2박스 추가', '실패 단계: 승인 직전 중복 확인',
                     '정확한 실패 사유:', '자동 조치: 전송하지 않고 보류'):
        assert expected in text
    assert 'hidden' not in text and 'example.test' not in text


def test_local_operator_log_survives_without_kakao_delivery(tmp_path, monkeypatch):
    monkeypatch.setattr(notices, 'OPERATOR_LOG', tmp_path / 'operator.jsonl')
    key = notices.enqueue('approval_error', 'ABC123', {
        'source_room': '영업방', 'cause': '중복 확인 실패 password=hidden'})
    lines = notices.OPERATOR_LOG.read_text(encoding='utf-8').splitlines()
    row = __import__('json').loads(lines[-1])
    assert row['event'] == 'incident_opened' and row['id'] == key
    assert row['context']['source_room'] == '영업방'
    assert 'hidden' not in lines[-1]


def test_unanswered_is_labeled_as_waiting_not_technical_error():
    key = notices.enqueue('approval_unanswered', 'ABC123')
    text = notices.message(notices._rows()[key])
    assert text.startswith('[담당자 답변 대기')


def test_resolve_all_closes_recovered_queued_incidents():
    first = notices.enqueue('inbound_room_failed')
    second = notices.enqueue('approval_error', 'ABC123')
    assert notices.resolve_all('inbound_room_failed') is True
    rows = notices._rows()
    assert rows[first]['status'] == 'resolved'
    assert rows[second]['status'] == 'queued'


def test_each_known_error_explains_work_problem_state_and_action():
    for category in notices.ERROR_GUIDANCE:
        text = notices.message({'id': 'ABC123', 'category': category,
                                'request_id': '', 'created_at': notices.time.time()})
        assert '발생 작업:' in text
        assert '문제:' in text
        assert '현재 상태:' in text
        assert '처리 안내:' in text
        assert '프로그램 로그 확인' not in text


def test_pause_blocks_all_notification_io():
    notices.enqueue('order_error')
    export, send = Mock(), Mock()
    notices.poll(export, send, lambda: True)
    export.assert_not_called(); send.assert_not_called()


def test_send_is_verified_and_not_repeated(monkeypatch):
    monkeypatch.setattr('core.moyi_control.audit', Mock())
    key = notices.enqueue('order_error', 'ORD-1234ABCD')
    payload = notices.message(notices._rows()[key])
    monkeypatch.setattr('core.moyi_inbound.parse_export', Mock(side_effect=[[], [
        {'event_id': 'receipt', 'content': payload}]]))
    export = Mock(return_value='임재용대리 님과 카카오톡 대화\n')
    send = Mock()
    notices.poll(export, send, lambda: False)
    notices.poll(export, send, lambda: False)
    send.assert_called_once_with('임재용대리', payload)
    assert notices._rows()[key]['status'] == 'sent'
    notices.enqueue('order_error', 'ORD-1234ABCD')
    assert notices._rows()[key]['status'] == 'sent'


def test_uncertain_send_is_never_retried(monkeypatch):
    monkeypatch.setattr('core.moyi_control.audit', Mock())
    monkeypatch.setattr('core.moyi_inbound.parse_export', lambda *args: [])
    key = notices.enqueue('order_error')
    send = Mock(side_effect=RuntimeError('failed'))
    export = Mock(return_value='임재용대리 님과 카카오톡 대화\n')
    notices.poll(export, send, lambda: False)
    notices.enqueue('order_error')
    notices.poll(export, send, lambda: False)
    assert notices._rows()[key]['status'] == 'unknown'
    assert send.call_count == 1


def test_wrong_room_never_sends_and_waits_before_retry(monkeypatch):
    monkeypatch.setattr('core.moyi_control.audit', Mock())
    key = notices.enqueue('order_error')
    export, send = Mock(return_value='다른방 님과 카카오톡 대화\n'), Mock()
    notices.poll(export, send, lambda: False)
    notices.poll(export, send, lambda: False)
    send.assert_not_called()
    assert export.call_count == 1
    assert notices._rows()[key]['status'] == 'queued'


def test_reports_survive_collection_and_do_not_suppress_distinct_changes():
    notices.report('order_quantity', 'ORD-1234ABCD')
    notices.report('order_quantity', 'ORD-1234ABCD')
    notices.collect_reports()
    notices.collect_reports()
    assert len(notices._rows()) == 2
    assert all(r['kind'] == 'report' for r in notices._rows().values())


def test_noisy_reports_are_aggregated_per_hour(monkeypatch):
    monkeypatch.setattr(notices.time, 'time', lambda: 3601)
    for _ in range(20): notices.report('inbound_processed')
    notices.collect_reports()
    rows = list(notices._rows().values())
    assert len(rows) == 1 and rows[0]['count'] == 20
    assert '20건 집계' in notices.message(rows[0])


def test_resolve_suppresses_only_matching_queued_error():
    key = notices.enqueue('approval_error', 'ABC123')
    other = notices.enqueue('approval_error', 'DEF456')
    assert notices.resolve('approval_error', 'ABC123') is True
    rows = notices._rows()
    assert rows[key]['status'] == 'resolved'
    assert rows[other]['status'] == 'queued'


def test_internal_notice_and_timing_events_never_report_themselves():
    for category in ('error_notice_sent', 'stage_timing', 'report_queue_failed'):
        notices.report(category)
    notices.collect_reports()
    assert notices._rows() == {}


def test_normal_reports_batch_and_error_has_priority(monkeypatch):
    monkeypatch.setattr('core.moyi_control.audit', Mock())
    notices.report('resume_requested')
    notices.report('worker_started')
    key = notices.enqueue('order_error')
    sent = []
    def parse(*args):
        return [{'event_id': str(i), 'content': text} for i, text in enumerate(sent)]
    monkeypatch.setattr('core.moyi_inbound.parse_export', parse)
    export = Mock(return_value='임재용대리 님과 카카오톡 대화\n')
    send = lambda room, text: sent.append(text)
    notices.poll(export, send, lambda: False)
    assert '오류 알림' in sent[0] and notices._rows()[key]['status'] == 'sent'
    notices.poll(export, send, lambda: False)
    assert sent[1].count('[매크로 상황 보고') == 2
    assert all(r['status'] == 'sent' for r in notices._rows().values())
