"""Safe KakaoTalk room-search field interaction."""
from __future__ import annotations

import time

import pyautogui
import pyperclip

SEARCH_X_RATIO = 0.45
SEARCH_Y_OFFSET = 37


def replace_room_search(window, title: str) -> None:
    """Focus the visible search box, clear it, and paste one exact title."""
    pyautogui.hotkey("ctrl", "f")
    time.sleep(0.3)
    pyautogui.click(
        window.left + int(window.width * SEARCH_X_RATIO),
        window.top + SEARCH_Y_OFFSET,
    )
    pyautogui.hotkey("ctrl", "a")
    pyautogui.press("backspace")
    pyperclip.copy(title)
    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.8)

