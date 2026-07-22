import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import core.moyi_inbound as inbound
from core.moyi_inbound import parse_export


class MoyiInboundParserTests(unittest.TestCase):
    def test_parse_export_creates_stable_events_and_multiline_content(self):
        text = """room 님과 카카오톡 대화
--------------- 2026년 7월 22일 수요일 ---------------
[Alice] [오전 10:04] first line
second line
[Bob] [오후 1:15] reply
"""
        first = parse_export(text, "binding")
        second = parse_export(text, "binding")
        self.assertEqual(len(first), 2)
        self.assertEqual(first[0]["content"], "first line\nsecond line")
        self.assertEqual(first[0]["event_id"], second[0]["event_id"])
        self.assertNotEqual(first[0]["event_id"], first[1]["event_id"])

    def test_room_without_unread_badge_is_not_opened(self):
        with TemporaryDirectory() as tmp, patch.object(inbound, "STATE_FILE", Path(tmp) / "state.json"):
            inbound._save_state({"binding": ["known"]})
            rooms = Mock()
            rooms.json.return_value = {"items": [{"room_binding_id": "binding", "exact_title": "room"}]}
            rooms.raise_for_status.return_value = None
            with patch.object(inbound.requests, "get", return_value=rooms), \
                 patch.object(inbound, "has_unread_exact_room", return_value=False), \
                 patch.object(inbound, "export_exact_room") as export:
                result = inbound.poll_once("https://example.test", "secret")
            self.assertEqual(result, {"sent": 0, "initialized": 0})
            export.assert_not_called()

    def test_failed_unread_delivery_keeps_rescan_marker(self):
        with TemporaryDirectory() as tmp, patch.object(inbound, "STATE_FILE", Path(tmp) / "state.json"), \
             patch.object(inbound, "OUTBOUND_JOURNAL", Path(tmp) / "journal.jsonl"):
            inbound._save_state({"binding": []})
            rooms = Mock()
            rooms.json.return_value = {"items": [{"room_binding_id": "binding", "exact_title": "room"}]}
            rooms.raise_for_status.return_value = None
            verify = Mock()
            verify.raise_for_status.return_value = None
            failed = Mock()
            failed.raise_for_status.side_effect = RuntimeError("server unavailable")
            with patch.object(inbound.requests, "get", return_value=rooms), \
                 patch.object(inbound.requests, "post", side_effect=[verify, failed]), \
                 patch.object(inbound, "has_unread_exact_room", return_value=True), \
                 patch.object(inbound, "export_exact_room", return_value="export"), \
                 patch.object(inbound, "parse_export", return_value=[{
                     "event_id": "event-1", "sender_name": "Alice",
                     "timestamp": "2026-07-22 10:04", "content": "hello",
                 }]):
                with self.assertRaises(RuntimeError):
                    inbound.poll_once("https://example.test", "secret")
            self.assertEqual(inbound._load_state()["_needs_rescan"], ["binding"])


if __name__ == "__main__":
    unittest.main()
