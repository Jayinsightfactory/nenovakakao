from copy import deepcopy
from unittest.mock import Mock
import pytest
from core.defect_services import DefectAdapter
from core import defect_approval as d
from tests.test_defect_approval import waiting


def receipt(adapter, row):
    return [{**item, 'deductionKey': n, 'sourceFileName': adapter.marker(row),
             'orderYear': 2026, 'orderWeek': '38', 'managerName': '박성수'}
            for n, item in enumerate(adapter.payload(row)['rows'], 1)]


def test_sales_save_scope_fields_and_no_final_registration(tmp_path):
    _, row = waiting(tmp_path)
    adapter = DefectAdapter(session=Mock())
    adapter.owner = {'managerId': 'sales-a', 'managerName': '박성수'}
    adapter.request = Mock(return_value={'saved': 1})
    adapter.insert(row)
    body = adapter.request.call_args.kwargs['json']
    assert body['action'] == 'save' and body['week'] == 38 and body['year'] == 2026
    assert body['managerName'] == '박성수'
    assert '37-1' in body['rows'][0]['note']
    assert body['rows'][0]['quantity'] == 15 and body['rows'][0]['sourceUnit'] == '단'
    assert 'deductionKey' not in body['rows'][0]


def test_readback_checks_scope_owner_and_quantities(tmp_path):
    _, row = waiting(tmp_path)
    adapter = DefectAdapter(session=Mock())
    actual = receipt(adapter, row)
    assert adapter.matches(row, actual)
    for key, value in [('orderYear',2025), ('managerName','다른사람'), ('quantity',16),
                       ('sourceFileName','other'), ('prodKey',999), ('sourceUnit','박스')]:
        wrong = deepcopy(actual); wrong[0][key] = value
        assert not adapter.matches(row, wrong)


def test_manual_identical_record_is_held(tmp_path):
    _, row = waiting(tmp_path)
    adapter = DefectAdapter(session=Mock())
    actual = receipt(adapter, row); actual[0]['sourceFileName'] = 'manual.xlsx'
    adapter.request = Mock(return_value={'rows': actual})
    with pytest.raises(ValueError, match='중복'): adapter.lookup(row)


def test_unsupported_unit_never_defaults_to_bunch(tmp_path):
    _, row = waiting(tmp_path)
    row['items'][0]['unit_raw'] = '묶음'
    adapter = DefectAdapter(session=Mock())
    assert not d.ready(row)
    with pytest.raises(ValueError, match='단위'): adapter.payload(row)


def test_flower_stem_unit_preserves_quantity_and_approval_revision(tmp_path):
    _, row = waiting(tmp_path)
    from tests.test_defect_approval import reply
    edited = d.apply_reply(row, reply(row,'수량 1=6송이'), {'answer-1'})
    assert edited['status']=='needs_match' and edited['revision']==row['revision']+1
    assert not edited.get('approved_revision')
    row['items'][0].update(unit_raw='송이', quantity_raw='6')
    adapter = DefectAdapter(session=Mock())
    assert d.ready(row)
    item=adapter.payload(row)['rows'][0]
    assert item['quantity']==6 and item['sourceUnit']=='스팀(대)'


def test_missing_credentials_does_not_call_api(monkeypatch):
    from core import defect_services as service
    monkeypatch.setattr(service, 'load', lambda _: None)
    adapter = DefectAdapter(); adapter.session = Mock()
    with pytest.raises(RuntimeError, match='미설정'): adapter.request('GET')
    adapter.session.request.assert_not_called()


def test_remote_validation_requires_exact_keys(tmp_path):
    _, row = waiting(tmp_path)
    adapter = DefectAdapter(session=Mock())
    body = adapter.payload(row)
    adapter.request = Mock(side_effect=[{'managerOptions': [{'managerId':'a','managerName':'박성수'}]},
                                       {'rows': [{**body['rows'][0], 'prodKey': 999}]}])
    with pytest.raises(ValueError, match='재검증'): adapter.validate(row)
