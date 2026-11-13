import pytest
from core.defect_scope import upload_scope


@pytest.mark.parametrize('year,sequence,target',[
    (2026,'37-1',{'year':2026,'week':38}),
    (2026,'37-2',{'year':2026,'week':38}),
    (2026,'53-1',{'year':2027,'week':1}),
    (2025,'52-1',{'year':2026,'week':1}),
])
def test_post_in_next_week(year,sequence,target,tmp_path,monkeypatch):
    from core import defect_approval as d
    monkeypatch.setattr(d, "ROOT", tmp_path / "requests")
    row={'event':{'timestamp':f'{year}년 9월 16일'},'extracted':{'sequence':sequence}}
    assert upload_scope(row)==target
    assert row['extracted']['sequence']==sequence


def test_explicit_defect_period_can_differ_from_iso_week(tmp_path, monkeypatch):
    from core import defect_approval as d
    from datetime import date
    monkeypatch.setattr(d, 'ROOT', tmp_path / 'requests')
    today = date.today()
    week = 42 if today.isocalendar().week != 42 else 41
    cfg = {'registration_scope_policy': 'open_period', 'current_registration_period':
           {'year': today.year, 'week': week, 'verified_date': today.isoformat(),
            'source': 'user_confirmed_defect_period'}}
    d.save(tmp_path / 'defect_config.json', cfg)
    row = {'event': {'timestamp': f'{today.year}년 {today.month}월 {today.day}일'}}
    assert upload_scope(row) == {'year': today.year, 'week': week}
    cfg['current_registration_period']['source'] = 'ERP dashboard verified'
    d.save(tmp_path / 'defect_config.json', cfg)
    with pytest.raises(ValueError):
        upload_scope(row)


def test_iso_week_alone_does_not_authorize_period(tmp_path, monkeypatch):
    from core import defect_approval as d
    from datetime import date
    monkeypatch.setattr(d, 'ROOT', tmp_path / 'requests')
    today = date.today()
    iso = today.isocalendar()
    d.save(tmp_path / 'defect_config.json', {
        'registration_scope_policy': 'open_period',
        'current_registration_period': {'year': iso.year, 'week': iso.week}})
    with pytest.raises(ValueError, match='명시 확인'):
        upload_scope({'event': {'timestamp': f'{today.year}년 {today.month}월 {today.day}일'}})


def test_source_next_uses_source_sequence_and_preserves_saved_scope(tmp_path, monkeypatch):
    from core import defect_approval as d
    monkeypatch.setattr(d, 'ROOT', tmp_path / 'requests')
    d.save(tmp_path / 'defect_config.json', {'registration_scope_policy':'source_next'})
    row = {'status':'approved', 'event':{'timestamp':'2026년 11월 2일'},
           'extracted':{'sequence':'43-1'}}
    assert upload_scope(row) == {'year':2026,'week':44}
    row['extracted']['sequence'] = '43'
    assert upload_scope(row) == {'year':2026,'week':44}
    row['extracted']['sequence'] = '44-2'
    assert upload_scope(row) == {'year':2026,'week':45}
    for state in ('completed','write_unknown','resolved_existing'):
        row.update(status=state,registration_scope={'year':2026,'week':40})
        assert upload_scope(row) == {'year':2026,'week':40}
    row.update(status='approved');row['extracted']['sequence']='52-1'
    with pytest.raises(ValueError,match='연말'):
        upload_scope(row)
