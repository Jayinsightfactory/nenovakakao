from core import keyword_alerts as alerts


def test_receipt_survives_reload_and_detail_change(tmp_path, monkeypatch):
    monkeypatch.setattr(alerts, 'RECEIPTS', tmp_path / 'receipts.json')
    row = dict(event_id='one', status='승인대기', detail='임재용대리 승인 필요 · 요청 ABCDEF123456')
    assert alerts.claim_alert(row)
    # Every call reads from disk, as a restarted console does.
    assert not alerts.claim_alert(dict(row, event_id='two', detail='요청 ABCDEF123456 전송 확인 · 답변 대기'))
    assert alerts.claim_alert(dict(row, detail='요청 ABCDEF123457'))


def test_terminal_status_never_alerts(tmp_path, monkeypatch):
    monkeypatch.setattr(alerts, 'RECEIPTS', tmp_path / 'receipts.json')
    for status in ('승인거절', '전송 성공', '중복 생략', '승인됨'):
        assert not alerts.claim_alert(dict(event_id='one', status=status, detail='요청 ABCDEF123456'))
    assert not alerts.RECEIPTS.exists()
