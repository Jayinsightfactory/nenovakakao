import json
import pytest
from core import subscription_ai as ai

@pytest.fixture
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(ai,'CACHE',tmp_path)
    monkeypatch.setattr(ai,'executable',lambda:'codex.exe')
    return tmp_path

def test_secret_environment_filtered(monkeypatch):
    for key in ['ANTHROPIC_API_KEY','OPENAI_API_KEY','TYPESAFE_API_KEY','ORBIT_TOKEN','ANTHROPIC_BASE_URL']:
        monkeypatch.setenv(key,'secret')
        assert key not in ai.environment()

def test_subscription_only_and_no_fallback(isolated,monkeypatch):
    calls=[]
    def run(args,*rest):
        calls.append(args)
        return 'Logged in using API key'
    monkeypatch.setattr(ai,'_run',run)
    with pytest.raises(ai.Unavailable): ai.ask('json',{'text':'test'})
    assert len(calls)==1
    with pytest.raises(ai.Unavailable): ai.ask('json',{'text':'test'})
    assert len(calls)==1

def test_isolation_and_cache(isolated,monkeypatch):
    calls=[]
    def run(args,env,cwd,input=None):
        calls.append(args)
        if 'login' in args: return 'Logged in using ChatGPT'
        assert args[args.index('--sandbox')+1]=='read-only'
        assert '--ignore-user-config' in args and '--ephemeral' in args
        assert 'shell_tool' in args and 'unified_exec' in args
        assert 'forced_login_method="chatgpt"' in args
        return json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'{"quantity":10,"unit":"단"}'}})+'\n'+json.dumps({'type':'turn.completed'})
    monkeypatch.setattr(ai,'_run',run)
    assert ai.ask('json',{'text':'test'})['quantity']==10
    assert ai.ask('json',{'text':'test'})['quantity']==10
    assert len(calls)==2

@pytest.mark.parametrize('output',['garbage','{"type":"result","subtype":"error","is_error":true}',
    '{"type":"result","subtype":"success","result":"true"}'])
def test_invalid_results_never_applied(isolated,monkeypatch,output):
    def run(args,*rest):
        if 'login' in args: return 'Logged in using ChatGPT'
        return output
    monkeypatch.setattr(ai,'_run',run)
    with pytest.raises(Exception): ai.ask('json',{})
    assert (isolated/'retry_after.json').exists()

def test_image_failure_is_not_mock_success(monkeypatch):
    from core.defect_analyzer import analyze_defect_image
    def fail(*a,**k): raise ai.Unavailable('unavailable')
    monkeypatch.setattr(ai,'ask',fail)
    result=analyze_defect_image('missing.png')
    assert result.confidence==0 and result.severity==0
