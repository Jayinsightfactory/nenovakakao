import pytest


@pytest.fixture(autouse=True)
def isolated_error_notice_queue(tmp_path, monkeypatch):
    """Tests must never queue notifications for real staff."""
    from core import error_notifications
    monkeypatch.setattr(error_notifications, 'STATE', tmp_path / 'error_notices.json')
    monkeypatch.setattr(error_notifications, 'OPERATOR_LOG', tmp_path / 'operator_alerts.jsonl')
    from core import keyword_approval
    monkeypatch.setattr(keyword_approval, 'REQUESTS', tmp_path / 'approval_requests.json')
    from core import moyi_control
    monkeypatch.setattr(moyi_control, 'PAUSE_FILE', tmp_path / 'worker.pause')
    from core import operator_settings
    monkeypatch.setattr(operator_settings, 'CONFIG', tmp_path / 'operator.json')
