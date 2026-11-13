from unittest.mock import Mock
import pytest
from core.defect_services import DefectAdapter


def test_expired_read_reauthenticates_once():
    session = Mock()
    session.request.side_effect = [Mock(status_code=401),
                                   Mock(status_code=200, json=lambda: {'success': True})]
    adapter = DefectAdapter(session=session)
    adapter.login = Mock()
    assert adapter.request('GET')['success']
    assert session.request.call_count == 2


def test_expired_write_is_never_replayed():
    session = Mock()
    session.request.return_value = Mock(status_code=401)
    adapter = DefectAdapter(session=session)
    with pytest.raises(RuntimeError, match='401'):
        adapter.request('POST', json={'action': 'save'})
    assert session.request.call_count == 1
    assert adapter.authenticated is False


def test_repeated_expired_read_stops():
    session = Mock()
    session.request.return_value = Mock(status_code=401)
    adapter = DefectAdapter(session=session)
    adapter.login = Mock()
    with pytest.raises(RuntimeError, match='401'):
        adapter.request('GET')
    assert session.request.call_count == 2
    assert adapter.authenticated is False
