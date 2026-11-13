from unittest.mock import Mock
import pytest
from core.defect_notifications import completion_recipient
from core import defect_runtime, defect_approval, daily_staff_reports
from core.atomic_json import save


def claim():
    return {'id':'불량-TEST','status':'completed','recipient':'조현욱',
            'event':{'sender_name':'조현욱'},'customer':{'staff':'박성수'}}


def test_company_manager_does_not_rewrite_source_contact():
    row=claim()
    assert completion_recipient(row,'customer_manager')=='박성수'
    assert row['recipient']=='조현욱'
    assert completion_recipient(row,'source_author')=='조현욱'


def test_unknown_company_manager_never_falls_back_to_author():
    row=claim();row['customer']['staff']=''
    with pytest.raises(ValueError,match='업체 담당자'):
        completion_recipient(row,'customer_manager')


def test_staff_alias_maps_to_the_exact_room():
    row=claim();row['customer']['staff']='김원영'
    assert completion_recipient(row,'customer_manager')=='김원영차장'


def test_legacy_operator_recipient_cannot_silently_receive_source_notice():
    row=claim();row['recipient']='강현우'
    with pytest.raises(ValueError,match='불일치'):
        completion_recipient(row,'source_author')


def test_notice_freezes_actual_destination_before_send(tmp_path,monkeypatch):
    row=claim();path=tmp_path/'claim.json';save(path,row)
    monkeypatch.setattr(defect_runtime,'config',lambda:{'completion_recipient_policy':'customer_manager'})
    monkeypatch.setattr(defect_runtime,'is_paused',lambda:False)
    monkeypatch.setattr(defect_approval,'verified_message',lambda *a:{'event_id':'sent1'})
    export=Mock(return_value=[]);send=Mock()
    defect_runtime._notice(path,row,'notice',export,send,'receipt_event_id')
    send.assert_called_once_with('박성수','notice')
    assert [c.args[0] for c in export.call_args_list]==['박성수','박성수']
    assert row['notice_recipient']=='박성수' and row['recipient']=='조현욱'


def test_daily_report_uses_same_company_recipient_policy(tmp_path,monkeypatch):
    monkeypatch.setattr(daily_staff_reports,'ROOT',tmp_path)
    save(tmp_path/'defect_config.json',{'completion_recipient_policy':'customer_manager'})
    row=claim();row.update(completed_at=10,receipt=[{'deductionKey':1239,'customerName':'레바논',
        'matchedProductName':'연핑크','quantity':30,'sourceUnit':'스팀(대)','orderYear':2026,'orderWeek':44}])
    save(tmp_path/'defect_requests'/'claim.json',row)
    report=daily_staff_reports.collect(1,20)
    assert set(report)=={'박성수'} and report['박성수'][0]['key']=='defect:1239'
