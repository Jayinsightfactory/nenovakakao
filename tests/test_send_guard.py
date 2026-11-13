import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from core import moyi_outbound as outbound


def test_room_daily_limit_blocks_and_persists(monkeypatch):
    monkeypatch.setattr(outbound, 'DAILY_LIMIT_PER_ROOM', 2)
    monkeypatch.setattr(outbound, 'DAILY_LIMIT_TOTAL', 10)
    outbound.check_send_limit('방A')
    outbound.record_send('방A')
    outbound.record_send('방A')
    with pytest.raises(outbound.SendLimitExceeded):
        outbound.check_send_limit('방A')
    outbound.check_send_limit('방B')  # 다른 방은 영향 없음


def test_total_daily_limit_blocks_every_room(monkeypatch):
    monkeypatch.setattr(outbound, 'DAILY_LIMIT_PER_ROOM', 10)
    monkeypatch.setattr(outbound, 'DAILY_LIMIT_TOTAL', 2)
    outbound.record_send('방A')
    outbound.record_send('방B')
    with pytest.raises(outbound.SendLimitExceeded):
        outbound.check_send_limit('방C')


def test_limit_counter_resets_on_a_new_day(monkeypatch):
    import json
    with open(outbound._LIMIT_FILE, 'w', encoding='utf-8') as f:
        json.dump({'date': '2000-01-01', 'total': 999, 'rooms': {'방A': 999}}, f)
    outbound.check_send_limit('방A')


def _fake_win32(monkeypatch, foreground_title):
    gui = SimpleNamespace(GetForegroundWindow=lambda: 7, GetAncestor=lambda h, flag: 7,
                          GetWindowText=lambda h: foreground_title)
    monkeypatch.setitem(sys.modules, 'win32gui', gui)
    monkeypatch.setitem(sys.modules, 'win32con', SimpleNamespace(GA_ROOTOWNER=3))


def test_wrong_foreground_room_stops_before_paste(monkeypatch):
    _fake_win32(monkeypatch, '카카오톡')
    monkeypatch.setattr(outbound.pyperclip, 'copy', Mock())
    monkeypatch.setattr(outbound.pyautogui, 'hotkey', Mock())
    monkeypatch.setattr(outbound.pyautogui, 'press', Mock())
    with pytest.raises(RuntimeError, match='대상 방 불일치'):
        outbound.send_text_to_open_room('보고', expected_title='임재용')
    outbound.pyperclip.copy.assert_not_called()
    outbound.pyautogui.hotkey.assert_not_called()
    outbound.pyautogui.press.assert_not_called()


def test_open_room_verified_waits_for_slow_room(monkeypatch):
    """11/10~13: 방이 1.5초보다 늦게 떠서 메인 창만 보고 중단됐다. 뜰 때까지 기다린다."""
    seen = iter([[], [], [], ['room'], ['room']])
    monkeypatch.setattr(outbound, '_exact_room_windows', lambda title: next(seen))
    monkeypatch.setattr(outbound, 'open_room_by_name', Mock())
    monkeypatch.setattr(outbound.time, 'sleep', Mock())
    verified = Mock(return_value=42)
    monkeypatch.setattr('core.safe_worker_room.open_unique_exact_room', verified)
    assert outbound.open_room_verified('임재용') == 42
    outbound.open_room_by_name.assert_called_once_with('임재용')
    verified.assert_called_once_with('임재용', allow_main_activation=False)


def test_open_room_verified_reuses_open_room_without_searching(monkeypatch):
    monkeypatch.setattr(outbound, '_exact_room_windows', lambda title: ['room'])
    monkeypatch.setattr(outbound, 'open_room_by_name', Mock(side_effect=AssertionError('검색 불필요')))
    monkeypatch.setattr('core.safe_worker_room.open_unique_exact_room', Mock(return_value=5))
    assert outbound.open_room_verified('임재용') == 5


def test_open_room_verified_gives_up_after_bounded_retries(monkeypatch):
    _fake_win32(monkeypatch, '카카오톡')
    monkeypatch.setattr(outbound, '_exact_room_windows', lambda title: [])
    monkeypatch.setattr(outbound, 'open_room_by_name', Mock())
    clock = iter(range(0, 1000, 2))
    monkeypatch.setattr(outbound.time, 'monotonic', lambda: next(clock))
    monkeypatch.setattr(outbound.time, 'sleep', Mock())
    with pytest.raises(RuntimeError, match="열지 못함.*카카오톡"):
        outbound.open_room_verified('임재용', attempts=2, timeout=6.0)
    assert outbound.open_room_by_name.call_count == 2


def test_duplicate_room_windows_stop_delivery(monkeypatch):
    monkeypatch.setattr(outbound, '_exact_room_windows', lambda title: ['a', 'b'])
    monkeypatch.setattr(outbound, 'open_room_by_name', Mock())
    with pytest.raises(RuntimeError, match='2개'):
        outbound.open_room_verified('임재용')


def test_report_does_not_send_or_mark_sent_when_room_never_opens(monkeypatch, tmp_path):
    import send_report
    room_file = tmp_path / 'room.txt'
    room_file.write_text('임재용', encoding='utf-8')
    sent_file = tmp_path / 'sent.txt'
    monkeypatch.setattr(send_report, 'ROOM_FILE', room_file)
    monkeypatch.setattr(send_report, 'SENT_FILE', sent_file)
    monkeypatch.setattr(send_report, 'compose', lambda: '보고 본문')
    monkeypatch.setattr(send_report, 'save_failure_screenshot', Mock())
    monkeypatch.setattr(send_report.time, 'sleep', Mock())
    monkeypatch.setenv('KAKAO_REPORT_OPEN_ROUNDS', '2')
    monkeypatch.setattr(outbound, 'open_room_verified', Mock(side_effect=RuntimeError('열지 못함')))
    send = Mock()
    monkeypatch.setattr(outbound, 'send_text_to_open_room', send)
    clip = SimpleNamespace(paste=lambda: '원래 클립보드', copy=Mock())
    monkeypatch.setitem(sys.modules, 'pyperclip', clip)
    assert send_report.main([]) == 1
    assert outbound.open_room_verified.call_count == 2
    send.assert_not_called()
    assert not sent_file.exists()
    clip.copy.assert_called_with('원래 클립보드')


def test_report_sends_once_then_marks_today(monkeypatch, tmp_path):
    import send_report
    room_file = tmp_path / 'room.txt'
    room_file.write_text('임재용', encoding='utf-8')
    sent_file = tmp_path / 'sent.txt'
    monkeypatch.setattr(send_report, 'ROOM_FILE', room_file)
    monkeypatch.setattr(send_report, 'SENT_FILE', sent_file)
    monkeypatch.setattr(send_report, 'compose', lambda: '보고 본문')
    monkeypatch.setattr(send_report.time, 'sleep', Mock())
    monkeypatch.setattr(outbound, 'open_room_verified', Mock(return_value=1))
    send = Mock()
    monkeypatch.setattr(outbound, 'send_text_to_open_room', send)
    monkeypatch.setitem(sys.modules, 'pyperclip', SimpleNamespace(paste=lambda: '', copy=Mock()))
    assert send_report.main([]) == 0
    send.assert_called_once_with('보고 본문', expected_title='임재용')
    assert sent_file.read_text(encoding='utf-8') == send_report.today()
    assert send_report.main([]) == 0  # 같은 날 두 번째 실행은 보내지 않음
    send.assert_called_once()
