from core.jev_shadow import Shadow, changed_suffix

class Judge:
    def __init__(self): self.calls=0
    def classify(self,*args):
        self.calls+=1
        return {'choice':'defect','confidence':.9}

def test_baseline_gap_and_overlap():
    assert changed_suffix([],['old'])[1]=='baseline'
    assert changed_suffix(['a','b'],['a','b'])==([], 'unchanged')
    assert changed_suffix(['a','b'],['x','y'])[1]=='gap_requires_reconciliation'
    assert changed_suffix(['a','b'],['a','b','new'])==(['new'],'overlap')

def test_no_repeat_call_across_restart(tmp_path):
    judge=Judge(); p=tmp_path/'db'
    s=Shadow(p,judge);s.observe('room',['a','b'])
    r=s.observe('room',['a','b','new'])
    assert r['status']=='shadow_only' and not r['execution_allowed']
    s.db.close();s=Shadow(p,judge)
    assert s.observe('room',['a','b','new'])['status']=='unchanged'
    assert judge.calls==1
    payload=s.compare(r['id'],'verified-event','defect')
    assert payload['agreement'] and not payload['execution_allowed']
    s.db.close()

def test_no_key_and_different_room(tmp_path):
    s=Shadow(tmp_path/'db')
    s.observe('a',['a','b'])
    assert s.observe('a',['a','b','new'])['status']=='awaiting_key'
    assert s.observe('b',['a','b','new'])['status']=='baseline'
    s.db.close()

def test_failure_not_automatically_retried(tmp_path):
    class Failed:
        def classify(self,*args): raise TimeoutError()
    s=Shadow(tmp_path/'db',Failed());s.observe('r',['a','b'])
    assert s.observe('r',['a','b','c'])['status']=='judge_failed'
    assert s.observe('r',['a','b','c'])['status']=='unchanged'
    s.db.close()

def test_invalid_jev_output_rejected(monkeypatch):
    from core.jev_shadow import Jev
    import pytest
    monkeypatch.setattr('core.subscription_ai.ask',lambda *a,**kw:{'type':'choice','choice':'defect','confidence':float('nan'),'probabilities':{}})
    with pytest.raises(ValueError): Jev('fake-test-key').classify(['text'],[])

def test_blank_ocr_preserves_anchor(tmp_path):
    j=Judge();s=Shadow(tmp_path/'db',j)
    s.observe('r',['a','b'])
    assert s.observe('r',[])['status']=='empty_frame_ignored'
    assert s.observe('r',['a','b','new'])['status']=='shadow_only'
    assert j.calls==1
    s.db.close()

def test_ocr_jitter_and_scroll_do_not_trigger_judge(tmp_path):
    j=Judge();s=Shadow(tmp_path/'db',j)
    s.observe('r',['sender','장미 2단'])
    assert s.observe('r',['sender','장미 Z단'])['status']=='gap_requires_reconciliation'
    assert j.calls==0
    s.db.close()
