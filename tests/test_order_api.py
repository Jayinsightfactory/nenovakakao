from unittest.mock import Mock
import pytest
from core.order_api import OrderAPI


def test_get_reauthenticates_once():
    api=OrderAPI('test');api.auth=Mock()
    api.auth.session.request.side_effect=[Mock(status_code=401),Mock(status_code=200,json=lambda:{'success':True})]
    assert api.read('/api/orders/my-customers',{})['success']
    assert api.auth.session.request.call_count==2


def test_post_never_replayed():
    api=OrderAPI('test');api.auth=Mock()
    api.auth.session.request.return_value=Mock(status_code=401)
    with pytest.raises(RuntimeError,match='401'):api._call('POST','/api/orders',json={})
    assert api.auth.session.request.call_count==1


def test_paused_request_does_not_authenticate(monkeypatch):
    monkeypatch.setattr('core.moyi_control.is_paused',lambda:True)
    api=OrderAPI('test');api.auth=Mock()
    with pytest.raises(RuntimeError,match='일시정지'):api.request('ORD-12345678','POST',body={})
    api.auth.login.assert_not_called()


def test_unapproved_request_cannot_reach_network(monkeypatch):
    monkeypatch.setattr('core.moyi_control.is_paused',lambda:False)
    monkeypatch.setattr('core.order_registration.validate_job',Mock(side_effect=ValueError('order_approval_required')))
    api=OrderAPI('test');api.auth=Mock()
    with pytest.raises(ValueError,match='approval'):api.request('ORD-12345678','POST',body={})
    api.auth.session.request.assert_not_called()


def test_direct_api_uses_existing_approval_validation(monkeypatch):
    from core import import_order
    monkeypatch.setattr(import_order,'config',lambda:{'transport':'direct-api','browser_order_registration':False})
    assert import_order.registration_enabled()


def test_redirect_is_not_followed():
    api=OrderAPI('test');api.auth=Mock()
    api.auth.session.request.return_value=Mock(status_code=302)
    with pytest.raises(RuntimeError,match='302'):api.read('/api/master',{'entity':'products'})
    assert api.auth.session.request.call_args.kwargs['allow_redirects'] is False


def test_created_response_is_a_successful_order_write():
    """네노바웹은 주문 저장 성공을 201로 답한다. 실패로 오인하면 저장된 주문의 영수증을 잃는다."""
    api=OrderAPI('test');api.auth=Mock()
    receipt={'success':True,'verified':True,'orderMasterKey':30,'verifiedCount':1,'results':[]}
    api.auth.session.request.return_value=Mock(status_code=201,json=lambda:receipt)
    assert api._call('POST','/api/orders',json={})==receipt
    assert api.auth.session.request.call_count==1


def test_created_status_is_not_accepted_for_reads():
    api=OrderAPI('test');api.auth=Mock()
    api.auth.session.request.return_value=Mock(status_code=201,json=lambda:{'success':True})
    with pytest.raises(RuntimeError,match='201'):api.read('/api/master',{'entity':'products'})
