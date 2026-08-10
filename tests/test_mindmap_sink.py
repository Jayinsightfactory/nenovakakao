import json
from pathlib import Path
from unittest.mock import Mock, patch

import core.mindmap_sink as sink


def test_timestamp_to_iso_preserves_kakao_absolute_time():
    value, approximate = sink.timestamp_to_iso("2026년 8월 10일 오후 3:07")
    assert value == "2026-08-10T15:07:00+09:00"
    assert approximate is False


def test_enqueue_is_idempotent_and_preserves_korean(tmp_path: Path):
    outbox = tmp_path / "outbox.json"
    event = {"event_id": "kakao-1", "sender_name": "홍길동", "timestamp": "2026년 8월 10일 오전 9:30", "content": "한글 원문"}
    with patch.object(sink, "OUTBOX_FILE", outbox), patch.dict(sink.os.environ, {"MINDMAP_BASE": "https://example.test", "MINDMAP_IMPORT_TOKEN": "secret"}):
        assert sink.enqueue_events("stable-room-id", "수입방", [event]) == 1
        assert sink.enqueue_events("stable-room-id", "수입방", [event]) == 0
    rows = json.loads(outbox.read_text(encoding="utf-8"))
    assert len(rows) == 1
    assert rows[0]["chat_id"] == "stable-room-id"
    assert rows[0]["chatroom"] == "수입방"
    assert rows[0]["message"] == "한글 원문"


def test_failed_flush_keeps_outbox_and_success_removes_it(tmp_path: Path):
    outbox = tmp_path / "outbox.json"
    outbox.write_text(json.dumps([{"external_message_id": "kakao-1", "message": "원문"}], ensure_ascii=False), encoding="utf-8")
    failed = Mock()
    failed.raise_for_status.side_effect = RuntimeError("offline")
    ok = Mock()
    ok.raise_for_status.return_value = None
    ok.json.return_value = {"imported": 1, "skipped": 0, "total": 1}
    env = {"MINDMAP_BASE": "https://example.test", "MINDMAP_IMPORT_TOKEN": "secret"}
    with patch.object(sink, "OUTBOX_FILE", outbox), patch.dict(sink.os.environ, env), patch.object(sink.requests, "post", return_value=failed):
        try:
            sink.flush_pending()
        except RuntimeError:
            pass
    assert len(json.loads(outbox.read_text(encoding="utf-8"))) == 1
    with patch.object(sink, "OUTBOX_FILE", outbox), patch.dict(sink.os.environ, env), patch.object(sink.requests, "post", return_value=ok):
        assert sink.flush_pending() == 1
    assert json.loads(outbox.read_text(encoding="utf-8")) == []


def test_export_room_title_uses_exact_kakao_header(tmp_path: Path):
    export = tmp_path / "room.txt"
    export.write_text("수입방 님과 카카오톡 대화\n저장한 날짜 : 2026-08-10", encoding="utf-8")
    assert sink._export_room_title(export) == "수입방"
