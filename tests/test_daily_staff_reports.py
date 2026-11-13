from datetime import datetime
from unittest.mock import Mock
import pytest
from core import daily_staff_reports as r
from core.atomic_json import read, save


@pytest.fixture
def setup(monkeypatch, tmp_path):
    monkeypatch.setattr(r, 'STATE', tmp_path/'state.json')
    monkeypatch.setattr(r, 'CONFIG', tmp_path/'config.json')
    save(r.CONFIG, {'enabled': True, 'enabled_at': 1})
    entry = {'key':'defect:1','kind':'불량','customer':'아이엠','product':'화이트',
             'quantity':30,'unit':'단','period':'2026년 40차'}
    monkeypatch.setattr(r, 'collect', lambda start,end: {'박성수':[entry]})
    return entry


def test_before_five_sends_nothing(setup):
    send = Mock()
    r.poll(Mock(), send, lambda:False, datetime(2026,10,2,16,59,tzinfo=r.KST))
    send.assert_not_called()


def test_once_verified_and_empty_staff_excluded(setup):
    history = []
    def send(room,text):
        assert room == '박성수'
        history.append({'event_id':'sent','content':text})
    now = datetime(2026,10,2,17,tzinfo=r.KST)
    sender = Mock(side_effect=send)
    r.poll(lambda _:history[:], sender, lambda:False, now)
    r.poll(lambda _:history[:], sender, lambda:False, now)
    sender.assert_called_once()
    assert list(read(r.STATE).values())[0]['status'] == 'sent'


def test_uncertain_send_never_repeated(setup):
    send = Mock(side_effect=RuntimeError('uncertain'))
    now = datetime(2026,10,2,17,tzinfo=r.KST)
    with pytest.raises(RuntimeError):
        r.poll(lambda _:[], send, lambda:False, now)
    r.poll(lambda _:[], send, lambda:False, now)
    send.assert_called_once()


def test_report_has_only_present_sections(setup):
    text = r.message('2026-10-02',[setup])
    assert '불량 1건' in text and '30단' in text and '등록 2026년 40차' in text
    assert '주문' not in text


def test_collection_excludes_duplicates_and_unverified_dates(tmp_path, monkeypatch):
    monkeypatch.setattr(r, 'ROOT', tmp_path)
    folder = tmp_path/'defect_requests'
    folder.mkdir()
    receipt = {'deductionKey':1,'customerName':'아이엠','matchedProductName':'화이트',
               'quantity':30,'sourceUnit':'단','orderYear':2026,'orderWeek':40}
    for name,status,stamp in [('a','completed',10),('b','completed',10),
                             ('c','duplicate_skipped',10),('d','completed',0)]:
        save(folder/(name+'.json'),{'status':status,'completed_at':stamp,'recipient':'박성수','receipt':[receipt]})
    assert len(r.collect(1,20)['박성수']) == 1
