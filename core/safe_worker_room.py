"""Exact-title room opening; fuzzy matching is unsafe for delivery."""
from __future__ import annotations
import time
import pyautogui, pygetwindow as gw, win32gui
from core.window_detector import activate_kakaotalk, switch_to_chat_tab

def open_unique_exact_room(title: str) -> int:
    main = activate_kakaotalk()
    switch_to_chat_tab(main)
    candidates = [w for w in gw.getAllWindows() if w.visible and w.title == title and w.width > 300 and w.height > 300]
    if len(candidates) != 1:
        raise RuntimeError(f"exact room verification failed: {len(candidates)} matches")
    window = candidates[0]
    window.activate()
    time.sleep(0.3)
    hwnd = win32gui.GetForegroundWindow()
    if hwnd != window._hWnd or win32gui.GetWindowText(hwnd) != title:
        raise RuntimeError("room title/focus verification failed")
    return hwnd

def close_room(hwnd: int) -> None:
    if win32gui.GetForegroundWindow() == hwnd:
        pyautogui.press("escape")
