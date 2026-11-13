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


def test_screenshot_requires_exact_saved_receipt(monkeypatch,tmp_path):
    path,row,job=fixture(monkeypatch,tmp_path)
    row.update(status='completed',receipt=[{
        'customerName':'친구플라워','matchedProductName':'Marguerite CHINA / 마가렛 400g',
        'quantity':8,'sourceUnit':'단','orderYear':2026,'orderWeek':39,
        'managerName':'정재훈','note':f"kakao-defect:{row['id']}:1"}])
    save(path,row)
    job.update(method='SCREENSHOT',params=b.screenshot_scope(row))
    b.validate_job(job)
    for field,value in [('quantity',18),('orderYear',2025),('note','other'),('managerName','박성수')]:
        wrong=deepcopy(job)
        wrong['params'][0][field]=value
        with pytest.raises(ValueError,match='invalid_screenshot_scope'):
            b.validate_job(wrong)
    row['status']='approved';save(path,row)
    with pytest.raises(ValueError,match='verified_completion_required'):
        b.validate_job(job)


def test_waiting_clarification_can_read_only_its_registration_scope(monkeypatch,tmp_path):
    path,row,job=fixture(monkeypatch,tmp_path)
    row['status']='clarification_pending'
    row.pop('approved_revision',None)
    save(path,row)
    b.validate_job(job)
    with pytest.raises(ValueError,match='approval_required'):
        b.validate_job({**job,'method':'POST','body':{'action':'save'}})


def test_blocked_bridge_job_reports_reason_without_timeout(monkeypatch,tmp_path):
    monkeypatch.setattr(b,'QUEUE',tmp_path)
    monkeypatch.setattr(b,'validate_job',lambda job:None)
    def reject(path,job):
        save(path,{**job,'state':'blocked','error':'recipient_mismatch'})
    monkeypatch.setattr(b,'save',reject)
    with pytest.raises(RuntimeError,match='recipient_mismatch'):
        b.request('불량-000000000000','GET',timeout=20)


def test_support_read_allows_only_current_scope_and_all_managers(monkeypatch,tmp_path):
    _,_,job=fixture(monkeypatch,tmp_path)
    job['params']={**job['params'],'view':'support','manager':''}
    b.validate_job(job)
    for field,value in [('week',37),('view','lookups'),('manager','another')]:
        wrong=deepcopy(job)
        wrong['params'][field]=value
        with pytest.raises(ValueError,match='invalid_scope'):
            b.validate_job(wrong)


def test_browser_master_is_read_only_and_entity_bounded(monkeypatch,tmp_path):
    monkeypatch.setattr(d,'ROOT',tmp_path/'requests')
    monkeypatch.setattr(moyi_control,'is_paused',lambda:False)
    save(tmp_path/'defect_config.json',{'enabled':True,'transport':'browser-session'})
    job={'request_id':'defect-master','method':'MASTER','expires_at':time.time()+30,
         'params':{'entity':'products'},'body':None}
    b.validate_job(job)
    b.validate_job({**job,'params':{'entity':'customers'}})
    for wrong in ({**job,'params':{'entity':'users'}},{**job,'body':{'action':'save'}},
                  {**job,'method':'POST'}):
        with pytest.raises(ValueError):b.validate_job(wrong)


def test_transient_response_read_lock_is_retried_without_resending(tmp_path,monkeypatch):
    from pathlib import Path
    from core.atomic_json import read
    path=tmp_path/'response.json'
    save(path,{'saved':1})
    original=Path.read_text
    attempts=[]
    def temporarily_locked(self,*args,**kwargs):
        attempts.append(1)
        if len(attempts)<3:raise PermissionError('sharing lock')
        return original(self,*args,**kwargs)
    monkeypatch.setattr(Path,'read_text',temporarily_locked)
    assert read(path)=={'saved':1}
    assert len(attempts)==3
