"""Fail-closed KakaoTalk-to-MOYI inbound polling for approved rooms."""
from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import time
from pathlib import Path

import pyautogui
import pygetwindow as gw
import requests
import win32api
import win32con
import win32gui
import win32process

from core.kakao_search import clear_room_search, replace_room_search
from core.moyi_outbound import open_room_by_name
from core.safe_worker_room import close_room, open_unique_exact_room

PHOTO_MARKER_RE = re.compile(
    r"^(?:사진(?:\s*\d+장)?|\[사진(?:\s*\d+장)?\]|Photo(?:s)?|\[Photo(?:s)?\])$",
    re.IGNORECASE,
)
FILE_MARKER_RE = re.compile(r"^(?:파일|File)\s*:\s*(?P<name>.+)$", re.IGNORECASE)
MAX_ATTACHMENT_BYTES = 50 * 1024 * 1024
MAX_AUTO_INBOUND_EVENTS = 50

ROOT = Path(__file__).resolve().parent.parent
STATE_FILE = ROOT / "data" / "moyi_inbound_state.json"
OUTBOUND_JOURNAL = ROOT / "data" / "moyi_outbound_journal.jsonl"
MESSAGE_RE = re.compile(r"^\[(?P<sender>.+?)\] \[(?P<ampm>오전|오후) (?P<time>\d{1,2}:\d{2})\] (?P<content>.*)$")
DATE_RE = re.compile(r"^-+ (?P<date>\d{4}년 \d{1,2}월 \d{1,2}일).*-+$")


def parse_export(text: str, binding_id: str) -> list[dict]:
    """Parse KakaoTalk text exports into stable, idempotent message events."""
    events: list[dict] = []
    date = ""
    current: dict | None = None
    for raw_line in text.replace("\r\n", "\n").split("\n"):
        date_match = DATE_RE.match(raw_line.strip())
        if date_match:
            date = date_match.group("date")
            continue
        message_match = MESSAGE_RE.match(raw_line)
        if message_match:
            if current:
                events.append(current)
            current = {
                "sender_name": message_match.group("sender").strip(),
                "timestamp": f"{date} {message_match.group('ampm')} {message_match.group('time')}",
                "content": message_match.group("content"),
            }
        elif current and raw_line:
            current["content"] += "\n" + raw_line
    if current:
        events.append(current)
    for event in events:
        identity = "\x1f".join(
            (binding_id, event["timestamp"], event["sender_name"], event["content"])
        )
        event["event_id"] = "kakao_" + hashlib.sha256(identity.encode()).hexdigest()
    return events


def _load_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(STATE_FILE)


def _events_after_checkpoint(events: list[dict], known_ids: list[str]) -> list[dict]:
    """Return only events after the newest retained checkpoint.

    The state intentionally retains a bounded tail. Set subtraction would make
    older history outside that tail look new again in long-running rooms.
    """
    known = set(known_ids)
    newest_known = -1
    for index, event in enumerate(events):
        if event["event_id"] in known:
            newest_known = index
    return events[newest_known + 1:] if newest_known >= 0 else events


def _recent_outbound_hashes(max_age_sec: int = 3600) -> set[str]:
    if not OUTBOUND_JOURNAL.exists():
        return set()
    cutoff = time.time() - max_age_sec
    hashes: set[str] = set()
    for line in OUTBOUND_JOURNAL.read_text(encoding="utf-8", errors="ignore").splitlines():
        try:
            row = json.loads(line)
            if float(row.get("at") or 0) >= cutoff and row.get("content_hash"):
                hashes.add(str(row["content_hash"]))
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
    return hashes


def _candidate_export_roots() -> tuple[Path, ...]:
    configured = Path(os.getenv("KAKAO_SAVE_DIR", str(Path.home() / "Downloads")))
    return tuple(dict.fromkeys((configured, Path.home() / "Downloads")))


def _txt_files() -> dict[Path, int]:
    files: dict[Path, int] = {}
    for root in _candidate_export_roots():
        if root.exists():
            for path in root.rglob("*.txt"):
                try:
                    files[path] = path.stat().st_mtime_ns
                except OSError:
                    continue
    return files


def _visible_dialogs_for_process(process_id: int) -> set[int]:
    dialogs: set[int] = set()

    def collect(hwnd: int, _extra: object) -> None:
        _, hwnd_process_id = win32process.GetWindowThreadProcessId(hwnd)
        if (
            hwnd_process_id == process_id
            and win32gui.IsWindowVisible(hwnd)
            and win32gui.GetClassName(hwnd) == "#32770"
        ):
            dialogs.add(hwnd)

    win32gui.EnumWindows(collect, None)
    return dialogs


def _save_export_dialog(chat_hwnd: int, dialogs_before: set[int], timeout: float = 8.0) -> None:
    """Confirm Kakao's Save As dialog and verify it closed before continuing."""
    _, process_id = win32process.GetWindowThreadProcessId(chat_hwnd)
    deadline = time.monotonic() + timeout
    save_dialog = 0
    while time.monotonic() < deadline:
        candidates = _visible_dialogs_for_process(process_id) - dialogs_before
        if len(candidates) == 1:
            save_dialog = next(iter(candidates))
            break
        if len(candidates) > 1:
            raise RuntimeError(f"Kakao export dialog verification failed: {len(candidates)} dialogs")
        time.sleep(0.2)
    if not save_dialog:
        raise RuntimeError("Kakao Save As dialog was not opened")

    win32gui.ShowWindow(save_dialog, win32con.SW_RESTORE)
    win32gui.SetForegroundWindow(save_dialog)
    save_button = win32gui.GetDlgItem(save_dialog, win32con.IDOK)
    if not save_button:
        raise RuntimeError("Kakao Save As button was not found")
    win32api.SendMessage(save_button, win32con.BM_CLICK, 0, 0)

    while time.monotonic() < deadline:
        if not win32gui.IsWindow(save_dialog) or not win32gui.IsWindowVisible(save_dialog):
            return
        time.sleep(0.2)
    raise RuntimeError("Kakao Save As dialog did not close")


def has_unread_exact_room(title: str) -> bool:
    """Check the exact-title search result for an unread badge without opening it."""
    from core.badge_monitor import detect_badge_positions
    from core.window_detector import activate_kakaotalk, capture_room_list, switch_to_chat_tab

    window = activate_kakaotalk()
    switch_to_chat_tab(window)
    replace_room_search(window, title)
    image_name = hashlib.sha256(title.encode()).hexdigest()[:16] + ".png"
    image_path = capture_room_list(window, ROOT / "captures" / "inbound_unread" / image_name)
    badges = detect_badge_positions(image_path)
    clear_room_search(window)
    switch_to_chat_tab(window)
    if len(badges) > 1:
        raise RuntimeError(f"unread badge conflict for exact room: {len(badges)} matches")
    return len(badges) == 1


def export_exact_room(title: str) -> str:
    """Open one exact room, export its text, and return the UTF-8 content."""
    open_room_by_name(title)
    hwnd = open_unique_exact_room(title)
    before = _txt_files()
    _, kakao_process_id = win32process.GetWindowThreadProcessId(hwnd)
    dialogs_before = _visible_dialogs_for_process(kakao_process_id)
    started = time.time_ns()
    try:
        pyautogui.hotkey("ctrl", "s")
        _save_export_dialog(hwnd, dialogs_before)
        time.sleep(1)
        after = _txt_files()
        candidates = [
            path
            for path, modified in after.items()
            if modified >= started or before.get(path) != modified
        ]
        if not candidates:
            raise RuntimeError("KakaoTalk export file was not created")
        latest = max(candidates, key=lambda path: after[path])
        return latest.read_text(encoding="utf-8")
    finally:
        close_room(hwnd)


def _upload_attachment(server: str, headers: dict[str, str], path: Path) -> dict:
    """Upload one locally downloaded Kakao attachment without exposing secrets."""
    if path.stat().st_size > MAX_ATTACHMENT_BYTES:
        raise RuntimeError(f"Kakao attachment exceeds 50MB: {path.name}")
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    with path.open("rb") as stream:
        response = requests.post(
            f"{server}/kakao/agent/files",
            headers=headers,
            files={"file": (path.name, stream, mime)},
            timeout=90,
        )
    response.raise_for_status()
    return response.json()


def _collect_photo_files(title: str, count: int) -> list[Path]:
    """Download the newest photo thumbnails from one exact Kakao room."""
    from core.drawer_handler import extract_photos_from_room

    open_room_by_name(title)
    hwnd = open_unique_exact_room(title)
    try:
        if count == 1:
            roots = _candidate_export_roots()
            before = {
                path.resolve(): path.stat().st_mtime_ns
                for root in roots if root.exists()
                for path in root.rglob("*") if path.is_file()
            }
            existing_hwnds = {window._hWnd for window in gw.getAllWindows()}
            chat = next(window for window in gw.getAllWindows() if window._hWnd == hwnd)
            # A new unread photo is the bottom-most media bubble. Kakao opens
            # it in a separate preview window on double-click.
            pyautogui.doubleClick(
                chat.left + int(chat.width * 0.63),
                chat.top + int(chat.height * 0.58),
                interval=0.15,
            )
            time.sleep(2)
            previews = [
                window for window in gw.getAllWindows()
                if window.visible and window._hWnd not in existing_hwnds
                and window.width > 500 and window.height > 400
            ]
            if len(previews) != 1:
                raise RuntimeError(f"Kakao photo preview verification failed: {len(previews)} matches")
            preview = previews[0]
            preview.activate()
            pyautogui.click(preview.left + preview.width - 62, preview.top + preview.height - 25)
            time.sleep(1)
            save_windows = [window for window in gw.getAllWindows() if window.title == "다른 이름으로 저장"]
            if len(save_windows) != 1:
                raise RuntimeError("Kakao photo save dialog was not opened")
            save_windows[0].activate()
            pyautogui.press("enter")
            time.sleep(3)
            pyautogui.press("left")
            pyautogui.press("enter")
            time.sleep(1)
            after = {
                path.resolve(): path.stat().st_mtime_ns
                for root in roots if root.exists()
                for path in root.rglob("*") if path.is_file()
            }
            downloaded = sorted(
                (path for path, modified in after.items() if before.get(path) != modified),
                key=lambda path: after[path],
            )
            pyautogui.press("escape")
            if len(downloaded) != 1:
                raise RuntimeError(f"Kakao photo save verification failed: {len(downloaded)} files")
            return downloaded
        return extract_photos_from_room(hwnd, photo_count=count)
    finally:
        close_room(hwnd)


def _find_local_kakao_file(name: str) -> Path:
    """Resolve an exact filename fail-closed from user-approved local roots."""
    safe_name = Path(name).name.strip()
    if not safe_name or safe_name != name.strip():
        raise RuntimeError("Unsafe Kakao attachment filename")
    configured = Path(os.getenv(
        "KAKAO_DOWNLOAD_DIR", str(Path.home() / "Documents" / "카카오톡 받은 파일")
    ))
    roots = tuple(dict.fromkeys((configured, Path.home() / "Downloads", Path.home() / "Desktop")))
    matches: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        try:
            matches.extend(path for path in root.rglob(safe_name) if path.is_file())
        except OSError:
            continue
    unique = list(dict.fromkeys(path.resolve() for path in matches))
    if len(unique) != 1:
        raise RuntimeError(f"Kakao file resolution requires exactly one local match: {safe_name}")
    return unique[0]


def poll_once(server: str, secret: str, only_title: str | None = None) -> dict[str, int]:
    """Open only unread/retry rooms and post messages newer than the baseline."""
    headers = {"X-Company-Secret": secret}
    response = requests.get(f"{server}/kakao/agent/rooms", headers=headers, timeout=20)
    response.raise_for_status()
    state = _load_state()
    retry_bindings = set(state.get("_needs_rescan", []))
    outbound_hashes = _recent_outbound_hashes()
    sent = 0
    initialized = 0
    for room in response.json().get("items", []):
        binding = str(room.get("room_binding_id") or "").strip()
        title = str(room.get("exact_title") or "").strip()
        if not binding or not title:
            continue
        if only_title is not None and title != only_title:
            continue
        needs_initialization = binding not in state
        has_unread = (
            has_unread_exact_room(title)
            if not needs_initialization and binding not in retry_bindings
            else False
        )
        if not needs_initialization and binding not in retry_bindings and not has_unread:
            continue
        # Opening a room clears KakaoTalk's unread badge. Persist a retry marker
        # first so a transient export/API failure cannot silently lose messages.
        retry_bindings.add(binding)
        state["_needs_rescan"] = sorted(retry_bindings)
        _save_state(state)
        text = export_exact_room(title)
        verify = requests.post(
            f"{server}/kakao/agent/verify-room",
            headers=headers,
            json={"room_binding_id": binding, "exact_title": title, "match_count": 1},
            timeout=20,
        )
        verify.raise_for_status()
        events = parse_export(text, binding)
        known_ids = state.get(binding, [])
        if not isinstance(known_ids, list):
            known_ids = []
        known = set(known_ids)
        if binding not in state:
            state[binding] = [event["event_id"] for event in events][-2000:]
            initialized += 1
            retry_bindings.discard(binding)
            state["_needs_rescan"] = sorted(retry_bindings)
            _save_state(state)
            continue
        new_events = _events_after_checkpoint(events, known_ids)
        if len(new_events) > MAX_AUTO_INBOUND_EVENTS:
            raise RuntimeError(
                "Kakao inbound backlog held: "
                f"{len(new_events)} events exceeds automatic limit "
                f"{MAX_AUTO_INBOUND_EVENTS}"
            )
        photo_events = [event for event in new_events if PHOTO_MARKER_RE.search(event["content"])]
        if photo_events:
            photo_files = _collect_photo_files(title, len(photo_events))
            if len(photo_files) < len(photo_events):
                raise RuntimeError(
                    f"Kakao photo download incomplete: expected {len(photo_events)}, got {len(photo_files)}"
                )
            uploaded = [_upload_attachment(server, headers, path) for path in photo_files]
            # Kakao's drawer is newest-first, as are the downloaded thumbnails.
            for event, attachment in zip(reversed(photo_events), uploaded):
                event["attachments"] = [attachment]
        for event in new_events:
            file_match = FILE_MARKER_RE.match(event["content"].strip())
            if file_match:
                local_file = _find_local_kakao_file(file_match.group("name").strip())
                event["attachments"] = [_upload_attachment(server, headers, local_file)]
        for event in new_events:
            content_hash = hashlib.sha256(event["content"].strip().encode()).hexdigest()
            if content_hash not in outbound_hashes:
                inbound = requests.post(
                    f"{server}/kakao/agent/inbound",
                    headers=headers,
                    json={
                        **event,
                        "room_binding_id": binding,
                        "external_room_id": title,
                        "origin": "kakao",
                        "attachments": event.get("attachments", []),
                    },
                    timeout=20,
                )
                inbound.raise_for_status()
                sent += 1
            known.add(event["event_id"])
            known_ids.append(event["event_id"])
            state[binding] = known_ids[-2000:]
            _save_state(state)
        retry_bindings.discard(binding)
        state["_needs_rescan"] = sorted(retry_bindings)
        _save_state(state)
    _save_state(state)
    return {"sent": sent, "initialized": initialized}
