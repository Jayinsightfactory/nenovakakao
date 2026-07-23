import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import core.moyi_inbound as inbound
from core.moyi_inbound import _events_after_checkpoint, parse_export


def test_bounded_state_does_not_reimport_older_history():
    events = [{"event_id": f"event-{index}"} for index in range(6)]
    assert _events_after_checkpoint(events, ["event-3", "event-4"]) == [{"event_id": "event-5"}]


class MoyiInboundParserTests(unittest.TestCase):
    def test_real_kakao_attachment_markers_are_recognized(self):
        self.assertIsNotNone(inbound.PHOTO_MARKER_RE.match("사진"))
        self.assertEqual(
            inbound.FILE_MARKER_RE.match("파일: report.xlsx").group("name"),
            "report.xlsx",
        )

    def test_local_file_resolution_requires_one_exact_match(self):
        with TemporaryDirectory() as tmp, patch.object(inbound.Path, "home", return_value=Path(tmp)):
            desktop = Path(tmp) / "Desktop"
            desktop.mkdir()
            expected = desktop / "report.xlsx"
            expected.write_bytes(b"xlsx")
            self.assertEqual(inbound._find_local_kakao_file("report.xlsx"), expected.resolve())
            with self.assertRaises(RuntimeError):
                inbound._find_local_kakao_file("../report.xlsx")

    def test_upload_attachment_posts_multipart_and_returns_reference(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.png"
            path.write_bytes(b"png")
            response = Mock()
            response.json.return_value = {"file_id": "file-1", "name": "sample.png"}
            with patch.object(inbound.requests, "post", return_value=response) as post:
                result = inbound._upload_attachment(
                    "https://moyi.example", {"X-Company-Secret": "secret"}, path
                )
            self.assertEqual(result["file_id"], "file-1")
            self.assertIn("files", post.call_args.kwargs)
            self.assertNotIn("secret", str(post.call_args.args))

    def test_upload_attachment_rejects_oversized_file_before_request(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "large.bin"
            path.write_bytes(b"")
            with patch.object(Path, "stat") as stat, patch.object(inbound.requests, "post") as post:
                stat.return_value.st_size = inbound.MAX_ATTACHMENT_BYTES + 1
                with self.assertRaises(RuntimeError):
                    inbound._upload_attachment("https://moyi.example", {}, path)
            post.assert_not_called()

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

    def test_targeted_scan_skips_every_other_room(self):
        with TemporaryDirectory() as tmp, patch.object(inbound, "STATE_FILE", Path(tmp) / "state.json"):
            rooms = Mock()
            rooms.json.return_value = {"items": [
                {"room_binding_id": "other", "exact_title": "other-room"},
                {"room_binding_id": "target", "exact_title": "target-room"},
            ]}
            rooms.raise_for_status.return_value = None
            with patch.object(inbound.requests, "get", return_value=rooms), \
                 patch.object(inbound, "export_exact_room", return_value="") as export, \
                 patch.object(inbound.requests, "post") as post:
                post.return_value.raise_for_status.return_value = None
                result = inbound.poll_once(
                    "https://example.test", "secret", only_title="target-room"
                )
            self.assertEqual(result["initialized"], 1)
            export.assert_called_once_with("target-room")

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
