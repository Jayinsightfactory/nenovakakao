"""Safe KakaoTalk room-search field interaction."""
from __future__ import annotations

import time

import pyautogui
import pyperclip

SEARCH_X_RATIO = 0.55
SEARCH_Y_RATIO = 0.11


def replace_room_search(window, title: str) -> None:
    """Focus the visible search box, clear it, and paste one exact title."""
    # The search field is always visible in current KakaoTalk. Keyboard
    # shortcuts are unsafe here: Ctrl+F toggles state and Esc can hide the
    # entire main window when search is already closed.
    pyautogui.click(
        window.left + int(window.width * SEARCH_X_RATIO),
        window.top + int(window.height * SEARCH_Y_RATIO),
    )
    # KakaoTalk reserves Ctrl+A for "add friend" even while the search field
    # appears focused. Repeated Backspace is slower but cannot open that dialog.
    pyautogui.press("backspace", presses=255)
    pyperclip.copy(title)
    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.8)

