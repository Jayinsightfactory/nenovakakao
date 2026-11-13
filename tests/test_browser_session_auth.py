from unittest.mock import Mock
import pytest
from core import browser_session_auth as a
from core.atomic_json import save


def test_expiry_notifies_once_and_blocks_writes_until_success(monkeypatch,tmp_path):
    monkeypatch.setattr(a,'path',lambda:tmp_path/'auth.json')
    monkeypatch.setattr(a,'automatic_relogin_enabled',lambda:False)
    notify=Mock();resolve=Mock()
    monkeypatch.setattr('core.error_notifications.notify',notify)
    monkeypatch.setattr('core.error_notifications.resolve',resolve)
    for _ in range(3):
        with pytest.raises(a.SessionExpired):a.expired()
    assert notify.call_count==1
    with pytest.raises(a.SessionExpired):a.guard('POST')
    with pytest.raises(a.SessionExpired):a.guard('GET')
    s=a.state();s['next_probe_at']=0;save(a.path(),s)
    a.guard('GET')
    with pytest.raises(a.SessionExpired):a.guard('MASTER')
    with pytest.raises(a.SessionExpired):a.guard('POST')
    a.recovered()
    a.guard('POST')
    assert not a.state()['blocked']
    resolve.assert_called_once_with('browser_login_needed')


def test_login_notice_has_concrete_action_and_no_item_reentry():
    from core.error_notifications import message
    text=message({'category':'browser_login_needed'})
    assert '한 번 로그인' in text
    assert '수집은 계속' in text
    assert '다시 입력하지' in text
