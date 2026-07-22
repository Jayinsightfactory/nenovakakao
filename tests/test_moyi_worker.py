import requests

from core.moyi_worker import _retryable_request_error, _safe_request_error


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

