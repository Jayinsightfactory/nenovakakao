import unittest

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


if __name__ == "__main__":
    unittest.main()
