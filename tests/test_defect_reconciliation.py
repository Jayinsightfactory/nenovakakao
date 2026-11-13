from core.defect_reconciliation import classify
from core.defect_services import DefectAdapter


def test_same_quantity_does_not_prove_same_request(monkeypatch):
    monkeypatch.setattr(DefectAdapter, 'payload', lambda self,row: {'rows':[
        {'custKey':1,'prodKey':2,'quantity':7,'sourceUnit':'단'}]})
    row={'id':'REQ','event':{'timestamp':'2026년 10월 1일 오전 9:00'},
         'extracted':{'sequence':'40-1'}}
    entry={'custKey':1,'prodKey':2,'quantity':7,'sourceUnit':'단',
           'deductionType':'불량차감','deductionKey':5,'orderWeek':'38'}
    assert classify(row,[entry])[0]['reason']=='same_signature_only'
    assert classify(row,[{**entry,'note':'38-1'}])[0]['reason']=='different_source_sequence'
    assert classify(row,[{**entry,'estimateDtm':'2026-09-18T00:00:00Z'}])[0]['reason']=='estimate_date_before_message_only'
    assert classify(row,[{**entry,'note':'kakao-defect:REQ:1'}])[0]['reason']=='request_marker_present'
    assert classify(row,[{**entry,'isDeleted':True}])==[]
    assert classify(row,[{**entry,'note':'40-01'}])[0]['reason']=='same_signature_only'
    assert classify(row,[{**entry,'note':'kakao-defect:REQ-other:1'}])[0]['reason']=='same_signature_only'
    assert classify(row,[{**entry,'estimateDtm':'invalid'}])[0]['reason']=='same_signature_only'
