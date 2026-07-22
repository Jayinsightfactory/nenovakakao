from unittest.mock import call, patch

from core.kakao_search import replace_room_search


class Window:
    left = 100
    top = 200
    width = 400
    height = 800


def test_replace_room_search_focuses_field_before_clearing_and_pasting():
    with patch("core.kakao_search.time.sleep"), \
         patch("core.kakao_search.pyperclip.copy") as copy, \
         patch("core.kakao_search.pyautogui") as gui:
        replace_room_search(Window(), "현장방")

    assert gui.method_calls == [
        call.click(320, 288),
        call.press("backspace", presses=255),
        call.hotkey("ctrl", "v"),
    ]
    copy.assert_called_once_with("현장방")

