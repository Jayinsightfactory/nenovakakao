import os
import sys
import types
import unittest
from unittest.mock import Mock, patch

from core.moyi_room_sync import _config, _filter_rooms, _parse_allowlist, sync_once


class MoyiRoomSyncSafetyTests(unittest.TestCase):
    def test_parse_allowlist_supports_json_and_deduplicates(self):
        self.assertEqual(
            _parse_allowlist('["test-room", "other-room", "test-room"]'),
            ("test-room", "other-room"),
        )

    def test_filter_rooms_keeps_exact_matches_only(self):
        rooms = [
            {"name": "other-room", "order": 1},
            {"name": "test-room", "order": 2},
        ]
        self.assertEqual(
            _filter_rooms(rooms, ("test-room",)),
            [{"name": "test-room", "order": 2}],
        )

    def test_filter_rooms_fails_when_allowlisted_room_is_missing(self):
        with self.assertRaisesRegex(RuntimeError, "감지하지 못했습니다"):
            _filter_rooms([{"name": "other-room"}], ("test-room",))

    def test_config_requires_workspace_id(self):
        env = {
            "MOYI_SERVER": "https://example.invalid",
            "MOYI_BRIDGE_SECRET": "secret",
            "MOYI_AGENT_ID": "agent",
            "MOYI_ROOM_ALLOWLIST": "test-room",
        }
        with (
            patch.dict(os.environ, env, clear=True),
            patch("core.moyi_room_sync.load_dotenv"),
            self.assertRaisesRegex(RuntimeError, "MOYI_WORKSPACE_ID"),
        ):
            _config()

    def test_config_requires_allowlist(self):
        env = {
            "MOYI_SERVER": "https://example.invalid",
            "MOYI_BRIDGE_SECRET": "secret",
            "MOYI_WORKSPACE_ID": "workspace",
            "MOYI_AGENT_ID": "agent",
        }
        with (
            patch.dict(os.environ, env, clear=True),
            patch("core.moyi_room_sync.load_dotenv"),
            self.assertRaisesRegex(RuntimeError, "MOYI_ROOM_ALLOWLIST"),
        ):
            _config()

    def test_sync_payload_has_workspace_and_only_allowlisted_rooms(self):
        detector = types.ModuleType("core.window_detector")
        detector.activate_kakaotalk = Mock(return_value="window")
        detector.switch_to_chat_tab = Mock()
        response = Mock()
        response.json.return_value = {"items": [{"exact_title": "test-room"}]}

        with (
            patch(
                "core.moyi_room_sync._config",
                return_value=(
                    "https://example.invalid",
                    "secret",
                    "workspace",
                    "agent",
                    ("test-room",),
                ),
            ),
            patch.dict(sys.modules, {"core.window_detector": detector}),
            patch(
                "core.moyi_room_sync._scan_allowlisted_rooms",
                return_value=[{"name": "test-room", "order": 1}],
            ),
            patch("core.moyi_room_sync.requests.post", return_value=response) as post,
        ):
            sync_once()

        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["workspace_id"], "workspace")
        self.assertEqual(payload["agent_id"], "agent")
        self.assertEqual(
            [room["exact_title"] for room in payload["rooms"]], ["test-room"]
        )
        self.assertNotIn("secret", str(payload))


if __name__ == "__main__":
    unittest.main()
