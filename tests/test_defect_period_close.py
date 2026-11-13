import pytest
from core import defect_approval as d
from core.atomic_json import save
from core.defect_services import DefectAdapter


def test_registered_entry_does_not_close_new_draft_period(tmp_path, monkeypatch):
    monkeypatch.setattr(d, 'ROOT', tmp_path / 'requests')
    save(tmp_path / 'defect_config.json', {'registration_scope_policy': 'open_period'})
    adapter = DefectAdapter()
    monkeypatch.setattr(adapter, 'scope', lambda row: {'year': 2026, 'week': 40})
    row = {'status': 'approved'}
    adapter.ensure_period_open(row, {'rows': [{'orderYear': 2026, 'orderWeek': 38,
        'appliedOrderYear': 2026, 'appliedOrderWeek': 38, 'estimateKey': 10}]})
    adapter.ensure_period_open(row, {'rows': [{'orderYear': 2026, 'orderWeek': 38,
            'appliedOrderYear': 2026, 'appliedOrderWeek': 40, 'estimateKey': 10}]})


def test_unavailable_listing_is_not_open(tmp_path, monkeypatch):
    monkeypatch.setattr(d, 'ROOT', tmp_path / 'requests')
    save(tmp_path / 'defect_config.json', {'registration_scope_policy': 'open_period'})
    adapter = DefectAdapter()
    monkeypatch.setattr(adapter, 'scope', lambda row: {'year': 2026, 'week': 40})
    with pytest.raises(ValueError):
        adapter.ensure_period_open({'status': 'approved'}, {})


def test_completed_without_original_scope_must_not_move_to_current_week(tmp_path, monkeypatch):
    from core.defect_scope import upload_scope
    monkeypatch.setattr(d, 'ROOT', tmp_path / 'requests')
    save(tmp_path / 'defect_config.json', {'registration_scope_policy': 'open_period',
        'current_registration_period': {'year': 2026, 'week': 40}})
    with pytest.raises(ValueError, match='원래 등록 차수'):
        upload_scope({'status': 'completed'})


def test_existing_write_preserves_original_period(tmp_path, monkeypatch):
    from core.defect_scope import upload_scope
    monkeypatch.setattr(d, 'ROOT', tmp_path / 'requests')
    save(tmp_path / 'defect_config.json', {'registration_scope_policy': 'open_period'})
    for status in ('completed', 'write_unknown'):
        assert upload_scope({'status': status, 'registration_scope': {'year': 2026, 'week': 38}}) == {
            'year': 2026, 'week': 38}


def test_period_guard_reads_support_including_carryover(tmp_path, monkeypatch):
    monkeypatch.setattr(d, 'ROOT', tmp_path / 'requests')
    save(tmp_path / 'defect_config.json', {'registration_scope_policy': 'open_period'})
    adapter = DefectAdapter()
    monkeypatch.setattr(adapter, 'scope', lambda row: {'year': 2026, 'week': 40})
    calls=[]
    def request(method, **kwargs):
        calls.append((method,kwargs))
        return {'rows':[{'orderYear':2026,'orderWeek':38,'appliedOrderYear':2026,
                         'appliedOrderWeek':40,'estimateKey':10}]}
    monkeypatch.setattr(adapter,'request',request)
    listing=adapter.registration_listing({'status':'approved'},{'rows':[]})
    adapter.ensure_period_open({'status':'approved'},listing)
    assert calls == [('GET',{'params':{'year':2026,'week':40,'view':'support','manager':''}})]


def test_explicit_server_close_blocks(tmp_path, monkeypatch):
    monkeypatch.setattr(d, 'ROOT', tmp_path / 'requests')
    save(tmp_path / 'defect_config.json', {'registration_scope_policy': 'open_period'})
    adapter = DefectAdapter()
    monkeypatch.setattr(adapter, 'scope', lambda row: {'year': 2026, 'week': 40})
    with pytest.raises(ValueError, match='40차 마감'):
        adapter.ensure_period_open({'status': 'approved'}, {'rows': [], 'periodClosed': True})
