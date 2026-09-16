from datetime import datetime
from unittest.mock import Mock
from core import deferred_photos as p


def test_kst_eighteen_boundary():
    assert not p.allowed(datetime(2026,9,16,17,59,tzinfo=p.KST))
    assert p.allowed(datetime(2026,9,16,18,0,tzinfo=p.KST))
    assert not p.allowed(datetime(2026,9,17,0,0,tzinfo=p.KST))


def test_daytime_does_no_export_or_upload(monkeypatch):
    from core import moyi_inbound as inbound
    monkeypatch.setattr(p,'allowed',lambda:False)
    export=Mock();monkeypatch.setattr(inbound,'export_exact_room',export)
    p.drain_one('server','secret')
    export.assert_not_called()


def test_queue_is_durable_and_deduplicated(monkeypatch,tmp_path):
    monkeypatch.setattr(p,'QUEUE',tmp_path)
    event={'event_id':'photo1','content':'사진'}
    p.enqueue('binding','방',event);p.enqueue('binding','방',event)
    import json
    files=list(tmp_path.glob('*.json'))
    assert len(files)==1
    assert json.loads(files[0].read_text(encoding='utf-8'))['event']==event
