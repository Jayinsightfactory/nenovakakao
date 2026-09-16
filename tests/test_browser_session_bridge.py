from copy import deepcopy
import time
import pytest
from core import browser_session_bridge as b, defect_approval as d
from core import moyi_control
from core.atomic_json import save
from core.defect_services import DefectAdapter
from tests.test_defect_approval import waiting,reply


def fixture(monkeypatch,tmp_path):
    path,row=waiting(tmp_path)
    row['recipient']='박성수'
    row=d.apply_reply(row,reply(row,'맞아',sender='박성수'),{'answer-1'})
    save(path,row)
    monkeypatch.setattr(d,'ROOT',tmp_path)
    monkeypatch.setattr(moyi_control,'is_paused',lambda:False)
    job={'request_id':row['id'],'expires_at':time.time()+20,'method':'GET',
         'params':{'year':2026,'week':38},'body':None}
    return path,row,job


def test_only_approved_scope_is_read(monkeypatch,tmp_path):
    path,row,job=fixture(monkeypatch,tmp_path)
    b.validate_job(job)
    job['params']['week']=37
    with pytest.raises(ValueError):b.validate_job(job)
    row['status']='waiting';save(path,row)
    with pytest.raises(ValueError):b.validate_job(job)


def test_write_matches_exact_approved_payload_and_journal(monkeypatch,tmp_path):
    path,row,job=fixture(monkeypatch,tmp_path)
    payload={**DefectAdapter().payload(row),'managerId':'sales-a','managerName':'박성수'}
    job.update(method='POST',params=None,body=payload)
    with pytest.raises(ValueError,match='write_ahead'):b.validate_job(job)
    row['status']='write_unknown';save(path,row)
    b.validate_job(job)
    wrong=deepcopy(job);wrong['body']['rows'][0]['quantity']=999
    with pytest.raises(ValueError,match='changed'):b.validate_job(wrong)
    job['body']['action']='register'
    with pytest.raises(ValueError,match='not_allowed'):b.validate_job(job)


def test_expiry_pause_recipient_and_traversal_rejected(monkeypatch,tmp_path):
    path,row,job=fixture(monkeypatch,tmp_path)
    with pytest.raises(ValueError):b.validate_job({**job,'expires_at':0})
    with pytest.raises(ValueError):b.validate_job({**job,'request_id':'../other'})
    row['recipient']='강현우';save(path,row)
    with pytest.raises(ValueError):b.validate_job(job)
    monkeypatch.setattr(moyi_control,'is_paused',lambda:True)
    with pytest.raises(ValueError):b.validate_job(job)


def test_no_live_connection_keeps_approved_without_credential_login(monkeypatch,tmp_path):
    path,row,_=fixture(monkeypatch,tmp_path)
    monkeypatch.setattr(b,'QUEUE',tmp_path/'queue')
    with pytest.raises(RuntimeError,match='브라우저 연결'):d.submit(path,b.BrowserDefectAdapter(),lambda:False)
    assert d.load(path)['status']=='approved'
