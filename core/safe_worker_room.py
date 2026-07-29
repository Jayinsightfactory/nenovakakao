"""Exact-title room opening; fuzzy matching is unsafe for delivery."""
from __future__ import annotations
import time
import pyautogui, pygetwindow as gw, win32con, win32gui
from core.window_detector import activate_kakaotalk, switch_to_chat_tab

def _foreground_belongs_to(hwnd: int) -> bool:
    """Kakao may foreground a titleless input window owned by the room."""
    foreground = win32gui.GetForegroundWindow()
    return foreground == hwnd or (
        foreground
        and win32gui.GetAncestor(foreground, win32con.GA_ROOTOWNER) == hwnd
    )

def _activate_verified(window) -> None:
    """Bring a Kakao room to the foreground and verify the actual HWND."""
    try:
        window.activate()
    except Exception:
        pass
    time.sleep(0.3)
    if _foreground_belongs_to(window._hWnd):
        return

    # A background worker can be denied foreground activation even when
    # pygetwindow.activate() returns without an exception. An Alt transition
    # grants the normal Windows foreground handoff without minimizing Kakao.
    pyautogui.press("alt")
    win32gui.ShowWindow(window._hWnd, win32con.SW_RESTORE)
    win32gui.SetForegroundWindow(window._hWnd)
    time.sleep(0.3)

def open_unique_exact_room(title: str) -> int:
    main = activate_kakaotalk()
    switch_to_chat_tab(main)
    candidates = [w for w in gw.getAllWindows() if w.visible and w.title == title and w.width > 300 and w.height > 300]
    if len(candidates) != 1:
        raise RuntimeError(f"exact room verification failed: {len(candidates)} matches")
    window = candidates[0]
    _activate_verified(window)
    hwnd = window._hWnd
    if not _foreground_belongs_to(hwnd) or win32gui.GetWindowText(hwnd) != title:
        raise RuntimeError("room title/focus verification failed")
    return hwnd

def close_room(hwnd: int) -> None:
    if _foreground_belongs_to(hwnd):
        pyautogui.press("escape")
