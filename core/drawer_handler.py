"""KakaoTalk 2026 room-drawer attachment downloads."""
from __future__ import annotations

import os
import time
from pathlib import Path

import pyautogui
import pygetwindow as gw
import win32con
import win32gui

DRAWER_TITLE = "채팅방 서랍"
MAX_ATTACHMENTS = 12
DOWNLOAD_X_FROM_RIGHT = 40
DOWNLOAD_Y_FROM_BOTTOM = 30


def _download_roots() -> tuple[Path, ...]:
    configured = Path(os.getenv("KAKAO_DOWNLOAD_DIR", str(Path.home() / "Downloads")))
    return tuple(dict.fromkeys((configured, Path.home() / "Downloads")))


def _snapshot_downloads() -> dict[Path, int]:
    files: dict[Path, int] = {}
    for root in _download_roots():
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            try:
                files[path.resolve()] = path.stat().st_mtime_ns
            except OSError:
                continue
    return files


def _activate(hwnd: int) -> None:
    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        pyautogui.press("alt")
        win32gui.SetForegroundWindow(hwnd)
    time.sleep(0.3)


def find_drawer_window() -> object:
    candidates = [
        window for window in gw.getWindowsWithTitle(DRAWER_TITLE)
        if window.visible and window.width > 500 and window.height > 700
    ]
    if len(candidates) != 1:
        raise RuntimeError(
            f"Kakao room drawer verification failed: {len(candidates)} matches"
        )
    return candidates[0]


def open_drawer(chat_hwnd: int) -> object:
    """Open the room drawer on its file tab using the current Ctrl+J shortcut."""
    _activate(chat_hwnd)
    pyautogui.hotkey("ctrl", "j")
    time.sleep(2)
    drawer = find_drawer_window()
    _activate(drawer._hWnd)
    return drawer


def _download_selection(drawer: object, count: int, kind: str) -> list[Path]:
    before = _snapshot_downloads()
    limit = min(max(0, count), MAX_ATTACHMENTS)
    if not limit:
        return []

    if kind == "photo":
        # Switch from the default file tab to Photos/Videos.
        pyautogui.click(drawer.left + 315, drawer.top + 196)
        time.sleep(1)
        first_x, first_y = drawer.left + 345, drawer.top + 329
        columns, x_spacing, y_spacing = 3, 133, 132
    elif kind == "file":
        first_x, first_y = drawer.left + 365, drawer.top + 359
        columns, x_spacing, y_spacing = 2, 176, 160
    else:
        raise ValueError(f"Unsupported drawer attachment kind: {kind}")

    # Kakao can preserve a prior selection while the drawer window is reused.
    # Clear it first so Ctrl+click always adds the intended cards.
    pyautogui.click(drawer.left + 275, drawer.top + drawer.height - 30)
    time.sleep(0.2)

    selected = 0
    pyautogui.keyDown("ctrl")
    try:
        for index in range(limit):
            row, col = divmod(index, columns)
            x = first_x + col * x_spacing
            y = first_y + row * y_spacing
            if y > drawer.top + drawer.height - 80:
                break
            pyautogui.click(x, y)
            selected += 1
            time.sleep(0.2)
    finally:
        pyautogui.keyUp("ctrl")
    if selected != limit:
        raise RuntimeError(
            f"Kakao {kind} selection incomplete: expected {limit}, selected {selected}"
        )

    pyautogui.click(
        drawer.left + drawer.width - DOWNLOAD_X_FROM_RIGHT,
        drawer.top + drawer.height - DOWNLOAD_Y_FROM_BOTTOM,
    )
    # Files from a non-contact can show a Kakao safety confirmation as a
    # titleless owned window. Confirm only the uniquely verified owned modal.
    time.sleep(0.7)
    owned_modals: list[int] = []
    win32gui.EnumWindows(
        lambda hwnd, found: found.append(hwnd)
        if (
            win32gui.IsWindowVisible(hwnd)
            and win32gui.GetParent(hwnd) == drawer._hWnd
            and win32gui.GetClassName(hwnd) == "EVA_Window_Dblclk"
        )
        else None,
        owned_modals,
    )
    if len(owned_modals) > 1:
        raise RuntimeError(
            f"Kakao attachment confirmation conflict: {len(owned_modals)} dialogs"
        )
    if owned_modals:
        left, top, right, bottom = win32gui.GetWindowRect(owned_modals[0])
        pyautogui.click(left + int((right - left) * 0.25), bottom - 24)
    time.sleep(max(3.0, selected * 0.5))
    after = _snapshot_downloads()
    downloaded = sorted(
        (path for path, modified in after.items() if before.get(path) != modified),
        key=lambda path: after[path],
    )
    if kind == "photo":
        image_suffixes = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
        downloaded = [
            path for path in downloaded if path.suffix.lower() in image_suffixes
        ]
    if len(downloaded) != selected:
        raise RuntimeError(
            f"Kakao {kind} download incomplete: expected {selected}, got {len(downloaded)}"
        )
    return downloaded


def _close_drawer(drawer: object | None) -> None:
    if drawer is not None and win32gui.IsWindow(drawer._hWnd):
        win32gui.PostMessage(drawer._hWnd, win32con.WM_CLOSE, 0, 0)
        time.sleep(0.5)


def extract_photos_from_room(chat_hwnd: int, photo_count: int = 0) -> list[Path]:
    drawer = None
    try:
        drawer = open_drawer(chat_hwnd)
        return _download_selection(drawer, photo_count, "photo")
    finally:
        _close_drawer(drawer)


def extract_files_from_room(chat_hwnd: int, file_count: int) -> list[Path]:
    drawer = None
    try:
        drawer = open_drawer(chat_hwnd)
        return _download_selection(drawer, file_count, "file")
    finally:
        _close_drawer(drawer)
