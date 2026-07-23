"""Safe KakaoTalk room-search field interaction."""
from __future__ import annotations

import time

import pyautogui
import pyperclip

SEARCH_X_RATIO = 0.55
SEARCH_Y_RATIO = 0.11


def replace_room_search(window, title: str) -> None:
    """Focus the visible search box, clear it, and paste one exact title."""
    # Current KakaoTalk reliably focuses room search with Ctrl+F. A coordinate
    # click can land on the list underneath even though the search box is shown.
    pyautogui.hotkey("ctrl", "f")
    time.sleep(0.2)
    # Clicking may place the caret in the middle of the previous query. Move it
    # to the end before clearing; otherwise the suffix remains and exact search
    # silently turns into a different title.
    pyautogui.press("end")
    # KakaoTalk reserves Ctrl+A for "add friend" even while the search field
    # appears focused. Repeated Backspace is slower but cannot open that dialog.
    pyautogui.press("backspace", presses=255)
    pyperclip.copy(title)
    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.8)


def clear_room_search(window) -> None:
    """Clear search without Esc, which can hide KakaoTalk to the tray."""
    replace_room_search(window, "")

