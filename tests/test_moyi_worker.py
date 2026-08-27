import requests
from pathlib import Path
from unittest.mock import Mock, patch

import core.moyi_worker as worker
from core.moyi_worker import _download_attachment, _is_suppressed_system_item, _retryable_request_error, _safe_attachment_name, _safe_request_error


def test_sales_alternates_without_starving_other_rooms():
    rooms = [{'exact_title': title} for title in ('현장방', '영업방', '견적방', '스케줄방')]
    schedule = worker._inbound_schedule(rooms)
    assert [r['exact_title'] for r in schedule] == [
        '영업방', '현장방', '영업방', '견적방', '영업방', '스케줄방']
    assert rooms[0]['exact_title'] == '현장방'


def test_sales_schedule_empty_single_missing_and_ambiguous():
    for titles in ([], ['영업방'], ['현장방', '견적방'], ['영업방', '영업방', '현장방']):
        rooms = [{'exact_title': title} for title in titles]
        assert worker._inbound_schedule(rooms) == rooms


def test_sales_schedule_after_room_list_changes():
    rooms = [{'exact_title': title} for title in ('영업방', '현장방', '견적방')]
    index = 3
    schedule = worker._inbound_schedule(rooms[:2])
    assert schedule[index % len(schedule)]['exact_title'] == '현장방'


def _http_error(status: int) -> requests.HTTPError:
    response = requests.Response()
    response.status_code = status
    return requests.HTTPError(response=response)


def test_transient_poll_failures_are_retryable_without_exposing_request_details():
    error = _http_error(502)
    assert _retryable_request_error(error) is True
    assert _safe_request_error(error) == "HTTP 502"


def test_authentication_failures_remain_fail_closed():
    assert _retryable_request_error(_http_error(401)) is False
    assert _retryable_request_error(_http_error(403)) is False


def test_attachment_name_cannot_escape_cache_directory():
    assert _safe_attachment_name("../../secret.txt") == "secret.txt"


def test_thread_head_settings_notice_is_suppressed():
    item = {"parts": [{"type": "text", "text": "말머리 설정 내역이 변경되었습니다."}]}
    assert _is_suppressed_system_item(item) is True


def test_normal_text_and_attachments_are_not_suppressed():
    item = {"parts": [
        {"type": "text", "text": "일반 업무 메시지"},
        {"type": "file", "name": "report.xlsx"},
    ]}
    assert _is_suppressed_system_item(item) is False


def test_attachment_download_rejects_untrusted_host():
    with patch.object(worker, "ROOT", Path("unused")):
        try:
            _download_attachment("https://api.nowlink.kr", {"url": "https://evil.test/file"})
        except RuntimeError as exc:
            assert "outside" in str(exc)
        else:
            raise AssertionError("untrusted attachment URL was accepted")


def test_attachment_download_enforces_size_limit(tmp_path: Path):
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.raise_for_status.return_value = None
    response.iter_content.return_value = [b"x" * 6]
    with patch.object(worker, "ROOT", tmp_path), \
         patch.object(worker, "MAX_ATTACHMENT_BYTES", 5), \
         patch.object(worker.requests, "get", return_value=response):
        try:
            _download_attachment(
                "https://api.nowlink.kr",
                {"url": "https://api.nowlink.kr/files/1/raw", "name": "a.bin", "delivery_key": "key"},
            )
        except RuntimeError as exc:
            assert "50MB" in str(exc)
        else:
            raise AssertionError("oversized attachment was accepted")

