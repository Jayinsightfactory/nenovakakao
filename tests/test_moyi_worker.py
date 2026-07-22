import requests
from pathlib import Path
from unittest.mock import Mock, patch

import core.moyi_worker as worker
from core.moyi_worker import _download_attachment, _retryable_request_error, _safe_attachment_name, _safe_request_error


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

