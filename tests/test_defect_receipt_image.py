from unittest.mock import Mock
import pytest
from PIL import Image
from core.defect_receipt_image import create
from core.error_notifications import message


def test_verified_receipt_renders_actual_ledger_data(tmp_path):
    adapter = Mock()
    adapter.lookup.return_value = [{'orderYear':2026,'orderWeek':'40','managerName':'조현욱',
        'customerName':'수경원예','matchedProductName':'CARNATION Mariposa',
        'quantity':12,'sourceUnit':'단','deductionKey':621}]
    adapter.matches.return_value = True
    row={'id':'불량-542A369F060A'}
    target=tmp_path/'receipt.png'
    assert create(row,target,adapter)==adapter.lookup.return_value
    adapter.lookup.assert_called_once_with(row)
    adapter.matches.assert_called_once_with(row,adapter.lookup.return_value)
    with Image.open(target) as image:
        assert image.width==1120 and image.height>400


def test_unverified_ledger_does_not_produce_completion_image(tmp_path):
    adapter=Mock()
    adapter.lookup.return_value=[{'deductionKey':621}]
    adapter.matches.return_value=False
    target=tmp_path/'receipt.png'
    with pytest.raises(ValueError,match='불일치'):
        create({'id':'불량-542A369F060A'},target,adapter)
    assert not target.exists()


@pytest.mark.parametrize('cause',['웹 세션 응답 확인 필요: 0 / saved_row_not_unique',
                                '처리 확인 이미지 전달 보류: 웹 세션 연결 대기 시간 초과'])
def test_completed_image_failure_never_says_registration_stopped(cause):
    text=message({'id':'test','request_id':'542A369F060A','category':'defect_error',
                  'context':{'stage':'completed','cause':cause}})
    assert '등록은 완료' in text
    assert '처리가 멈췄습니다' not in text
    assert '로그인하고' not in text


def test_photo_export_without_filename_and_same_minute_duplicates():
    from core.defect_screenshot import verified_attachment
    event={'event_id':'same-minute-photo','sender_name':'네노바','content':'사진'}
    assert verified_attachment([], [event], 'receipt.png') == event
    assert verified_attachment([event], [event,event], 'receipt.png') == event
    assert verified_attachment([event], [event], 'receipt.png') is None
    assert verified_attachment([], [event,event], 'receipt.png') is None
    assert verified_attachment([], [{**event,'sender_name':'조현욱'}], 'receipt.png') is None
