"""
카카오톡 PC win32 API 직접 자동화 (좌표·OCR·Computer Use 전부 불필요).

채택 출처: https://github.com/kronenz/kakaotalk-mcp (MIT License, (c) 2025 kronenz)
원본 controller.py + config.py + parser.py 핵심 함수를 우리 프로젝트 구조에 맞춰 통합.

핵심 발견 (2026-05-18):
  카톡 PC 의 child window 가 win32 으로 직접 enum/조작 가능. EVA UI 라 보이지만
  실제로는 표준 윈도우 클래스 (EVA_Window_Dblclk, RICHEDIT50W, EVA_VH_ListControl_Dblclk).

검증된 워크플로우:
  1. search_and_open_room(name) — 채팅탭 검색 Edit 에 WM_CHAR 로 글자별 → Enter
     → 분리창 띄움. 통합검색 다이얼로그 아님, 친구추가 부작용 없음.
  2. read_chat_messages(name) — 분리창의 ListControl 포커스 → Ctrl+A → Ctrl+C
     → 클립보드 텍스트. 저장 다이얼로그 안 씀.
  3. find_chat_window(name) — title 로 직접 hwnd 찾음. 더블클릭/캡쳐 무관.
  4. send_message_to_room — 분리창 RICHEDIT50W 에 클립보드 paste + Enter
     (봇 API 와 별개; Fallback).
  5. download_recent_images — 카톡 cache 디렉토리에서 이미지 직접 복사.
     서랍 자동화 완전 우회.
"""
from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
import time
from typing import Optional, List, Dict

import win32api
import win32clipboard
import win32con
import win32gui
import win32process


# ─────────────────────────────────────────────
# 카톡 윈도우 클래스명 (kakao-mcp 의 config.py 에서 추출)
# ─────────────────────────────────────────────
KAKAO_MAIN_WINDOW_CLASS = "EVA_Window_Dblclk"
KAKAO_MAIN_WINDOW_TITLE = "카카오톡"
KAKAO_CHAT_WINDOW_CLASS = "EVA_Window_Dblclk"
KAKAO_LIST_CONTROL_CLASS = "EVA_VH_ListControl_Dblclk"
KAKAO_EDIT_CLASS = "RICHEDIT50W"

# win32 상수
WM_SETTEXT = 0x000C
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_CHAR = 0x0102
EM_SETSEL = 0x00B1
WM_CLEAR = 0x0303
VK_RETURN = 0x0D
VK_CONTROL = 0x11
VK_ESCAPE = 0x1B
VK_A = 0x41
VK_C = 0x43
VK_F = 0x46
VK_V = 0x56
VK_MENU = 0x12
KEYEVENTF_KEYUP = 0x0002

# 타이밍
WINDOW_ACTIVATE_WAIT_SEC = 0.15
EDIT_CLICK_WAIT_SEC = 0.08
SEARCH_ACTIVATE_WAIT_SEC = 0.3
SEARCH_CHAR_INTERVAL_SEC = 0.02
SEARCH_RESULTS_WAIT_SEC = 0.8
SEARCH_OPEN_WAIT_SEC = 0.5
KEY_COMBO_WAIT_SEC = 0.15
CLIPBOARD_MAX_RETRIES = 5
CLIPBOARD_RETRY_INTERVAL_SEC = 0.1

# 카톡 데이터 경로 (사진 캐시)
KAKAO_LOCAL_DATA = os.path.join(
    os.environ.get("LOCALAPPDATA", ""), "Kakao", "KakaoTalk"
)
KAKAO_USERS_DIR = os.path.join(KAKAO_LOCAL_DATA, "users")
IMAGE_CACHE_SUBDIRS = [
    "chat_data/cli_http_v2",
    "chat_data/cli/thumbnail",
    "chat_data/oci_v2",
    "chat_data/mci_v2",
]

_user32 = ctypes.windll.user32


def _log(msg: str) -> None:
    print(f"[kakao_win32] {msg}", file=sys.stderr, flush=True)


# ─────────────────────────────────────────────
# 윈도우 탐색
# ─────────────────────────────────────────────
def is_kakaotalk_running() -> Dict:
    """카톡 메인창 존재 확인. {'running': bool, 'hwnd': int|None, 'pid': int|None}"""
    hwnd = win32gui.FindWindow(KAKAO_MAIN_WINDOW_CLASS, KAKAO_MAIN_WINDOW_TITLE)
    if hwnd == 0:
        return {"running": False, "hwnd": None, "pid": None}
    _, pid = win32process.GetWindowThreadProcessId(hwnd)
    return {"running": True, "hwnd": hwnd, "pid": pid}


def _norm_room_title(t: str) -> str:
    """그룹방 제목 비교용 정규화.

    멤버목록 그룹방은 창 제목 끝에 ', ...'(잘림 표시)가 붙어
    매핑 이름과 exact 매칭이 안 된다 (예: '김다혜, 전정식, ..., 이진수, ...').
    트레일링 '...'/'…'/', '/공백을 제거해 비교한다.
    일반 방('네노바 영업' 등)은 변화 없음 → 오매칭 위험 없음.
    """
    t = (t or "").strip()
    prev = None
    while t != prev:
        prev = t
        for suf in ("...", "…"):
            if t.endswith(suf):
                t = t[: -len(suf)].rstrip()
        t = t.rstrip(", ").rstrip()
    return t


def _title_matches_room(title: str, room_name: str) -> bool:
    """창 제목이 방 이름과 일치하는가 (트레일링 ', ...' 허용)."""
    if not title:
        return False
    if title == room_name:
        return True
    return _norm_room_title(title) == _norm_room_title(room_name)


def find_chat_window(room_name: str) -> Optional[int]:
    """방 이름 일치하는 분리창 hwnd. 그룹방 제목의 트레일링 ', ...' 허용. 없으면 None."""
    results: List[int] = []

    def _cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            cls = win32gui.GetClassName(hwnd)
            title = win32gui.GetWindowText(hwnd)
            if cls == KAKAO_CHAT_WINDOW_CLASS and _title_matches_room(title, room_name):
                results.append(hwnd)
        return True

    win32gui.EnumWindows(_cb, None)
    return results[0] if results else None


def list_chat_windows() -> List[Dict]:
    """현재 열린 카톡 채팅 분리창 목록 (메인창 제외).

    Returns: [{'hwnd': int, 'title': str}, ...]
    """
    main_hwnd = win32gui.FindWindow(KAKAO_MAIN_WINDOW_CLASS, KAKAO_MAIN_WINDOW_TITLE)
    windows: List[Dict] = []

    def _cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            cls = win32gui.GetClassName(hwnd)
            title = win32gui.GetWindowText(hwnd)
            if (
                cls == KAKAO_CHAT_WINDOW_CLASS
                and hwnd != main_hwnd
                and title
                and title != KAKAO_MAIN_WINDOW_TITLE
            ):
                windows.append({"hwnd": hwnd, "title": title})
        return True

    win32gui.EnumWindows(_cb, None)
    return windows


def find_child_window_recursive(parent_hwnd: int, class_name: str) -> Optional[int]:
    """parent 의 child window 를 재귀로 class_name 매칭."""
    found: List[int] = []

    def _cb(hwnd, _):
        if win32gui.GetClassName(hwnd) == class_name:
            found.append(hwnd)
            return False
        return True

    try:
        win32gui.EnumChildWindows(parent_hwnd, _cb, None)
    except Exception:
        pass
    return found[0] if found else None


def bring_window_to_front(hwnd: int) -> None:
    """창을 포그라운드로. AttachThreadInput 해킹으로 Windows 제한 우회."""
    VK_MENU_LOCAL = 0x12
    SW_RESTORE = 9
    HWND_TOPMOST = -1
    HWND_NOTOPMOST = -2
    SWP_NOMOVE = 0x0002
    SWP_NOSIZE = 0x0001
    SWP_SHOWWINDOW = 0x0040

    _user32.ShowWindow(hwnd, SW_RESTORE)
    # Alt 키 누름 → SetForegroundWindow 잠금 해제
    _user32.keybd_event(VK_MENU_LOCAL, 0, 0, 0)
    _user32.keybd_event(VK_MENU_LOCAL, 0, KEYEVENTF_KEYUP, 0)
    _user32.SetForegroundWindow(hwnd)
    _user32.SetWindowPos(
        hwnd, HWND_TOPMOST, 0, 0, 0, 0,
        SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW,
    )
    _user32.SetWindowPos(
        hwnd, HWND_NOTOPMOST, 0, 0, 0, 0,
        SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW,
    )


# ─────────────────────────────────────────────
# 키 입력 헬퍼
# ─────────────────────────────────────────────
def _send_ctrl_key_combo(vk_key: int) -> None:
    """Ctrl+<key> 단축키 전송 (활성 창에 영향)."""
    _user32.keybd_event(VK_CONTROL, 0, 0, 0)
    time.sleep(0.02)
    _user32.keybd_event(vk_key, 0, 0, 0)
    time.sleep(0.02)
    _user32.keybd_event(vk_key, 0, KEYEVENTF_KEYUP, 0)
    time.sleep(0.02)
    _user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)


# ─────────────────────────────────────────────
# 클립보드
# ─────────────────────────────────────────────
def _read_clipboard_text() -> str:
    """클립보드 텍스트 읽기 (재시도 포함)."""
    for _ in range(CLIPBOARD_MAX_RETRIES):
        try:
            win32clipboard.OpenClipboard()
            try:
                data = win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
                return data if data else ""
            finally:
                win32clipboard.CloseClipboard()
        except Exception:
            time.sleep(CLIPBOARD_RETRY_INTERVAL_SEC)
    return ""


def _set_clipboard_text(text: str) -> None:
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardText(text, win32clipboard.CF_UNICODETEXT)
    finally:
        win32clipboard.CloseClipboard()


# ─────────────────────────────────────────────
# 메시지 읽기 ★ (저장 다이얼로그 안 씀)
# ─────────────────────────────────────────────
def read_chat_messages(room_name: str) -> Dict:
    """카톡 분리창에서 메시지를 Ctrl+A + Ctrl+C 로 클립보드 추출.

    저장 다이얼로그 / 파일 저장 흐름 자체를 우회. 분리창이 열려있어야 함.
    분리창 없으면 search_and_open_room 으로 먼저 열어야 함.

    Returns:
        {'success': bool, 'raw_text': str, 'error': str?}
    """
    hwnd = find_chat_window(room_name)
    if hwnd is None:
        return {"success": False, "error": f"분리창 '{room_name}' 없음 (먼저 열기)", "raw_text": ""}

    list_hwnd = find_child_window_recursive(hwnd, KAKAO_LIST_CONTROL_CLASS)
    if list_hwnd is None:
        return {
            "success": False,
            "error": f"ListControl ({KAKAO_LIST_CONTROL_CLASS}) 못 찾음 in '{room_name}'",
            "raw_text": "",
        }

    bring_window_to_front(hwnd)
    time.sleep(WINDOW_ACTIVATE_WAIT_SEC)

    # 리스트 영역 클릭 → 포커스
    try:
        rect = win32gui.GetWindowRect(list_hwnd)
        cx = (rect[0] + rect[2]) // 2
        cy = (rect[1] + rect[3]) // 2
        _user32.SetCursorPos(cx, cy)
        _user32.mouse_event(0x0002, 0, 0, 0, 0)
        _user32.mouse_event(0x0004, 0, 0, 0, 0)
        time.sleep(EDIT_CLICK_WAIT_SEC)
    except Exception:
        pass

    _send_ctrl_key_combo(VK_A)
    time.sleep(KEY_COMBO_WAIT_SEC)
    _send_ctrl_key_combo(VK_C)
    time.sleep(KEY_COMBO_WAIT_SEC)

    raw = _read_clipboard_text()
    return {"success": True, "raw_text": raw}


# ─────────────────────────────────────────────
# 채팅방 검색 + 열기 ★ (Ctrl+F = 채팅탭 검색 Edit, 통합검색 아님)
# ─────────────────────────────────────────────
def _find_chat_list_view(main_hwnd: int) -> Optional[int]:
    """카톡 메인 안의 ChatRoomListView (EVA_Window) 찾기."""
    result = None

    def _cb(hwnd, _):
        nonlocal result
        cls = win32gui.GetClassName(hwnd)
        text = win32gui.GetWindowText(hwnd)
        if cls == "EVA_Window" and "ChatRoomListView" in text:
            result = hwnd
            return False
        return True

    try:
        win32gui.EnumChildWindows(main_hwnd, _cb, None)
    except Exception:
        pass
    return result


def _activate_search_and_get_edit(main_hwnd: int) -> Optional[int]:
    """Ctrl+F → 채팅탭 검색 Edit 활성화 후 hwnd 반환.

    카톡 PC 의 Ctrl+F 는 채팅탭 검색바 활성. (통합검색이 아님!)
    """
    chat_list_view = _find_chat_list_view(main_hwnd)
    _log(f"ChatRoomListView hwnd: {chat_list_view}")
    if chat_list_view is None:
        return None

    _send_ctrl_key_combo(VK_F)
    time.sleep(SEARCH_ACTIVATE_WAIT_SEC)

    edit_hwnd = find_child_window_recursive(chat_list_view, "Edit")
    if edit_hwnd:
        vis = win32gui.IsWindowVisible(edit_hwnd)
        _log(f"Edit hwnd after Ctrl+F: {edit_hwnd}, visible: {vis}")
    return edit_hwnd


def clear_chat_search() -> bool:
    """채팅탭 검색 Edit 의 텍스트를 비운다 (목록 필터 해제).

    검색창에 이전 방이름이 남아 목록이 필터된 채로 있으면:
      - monitor 의 좌표 클릭이 엉뚱한 방을 누르고
      - 다음 검색(워크→카톡)도 막힌다.
    텍스트만 비우면 검색 필터가 풀려 원래 목록이 복원된다(탭은 유지).
    """
    main_hwnd = win32gui.FindWindow(KAKAO_MAIN_WINDOW_CLASS, KAKAO_MAIN_WINDOW_TITLE)
    if main_hwnd == 0:
        return False
    clv = _find_chat_list_view(main_hwnd)
    if clv is None:
        return False
    edit_hwnd = find_child_window_recursive(clv, "Edit")
    if edit_hwnd is None:
        return False
    try:
        win32api.SendMessage(edit_hwnd, EM_SETSEL, 0, -1)
        win32api.SendMessage(edit_hwnd, WM_CLEAR, 0, 0)
        win32api.SendMessage(edit_hwnd, WM_SETTEXT, 0, "")
        _log("검색창 비움")
        return True
    except Exception as e:
        _log(f"검색창 비우기 실패: {e}")
        return False


def _ensure_foreground(hwnd: int) -> bool:
    bring_window_to_front(hwnd)
    time.sleep(WINDOW_ACTIVATE_WAIT_SEC)
    return _user32.GetForegroundWindow() == hwnd


def search_and_open_room(room_name: str) -> Dict:
    """카톡 메인의 채팅탭 검색 → 첫 결과 Enter 로 분리창 열기.

    검색바 Edit 에 WM_CHAR 로 글자별 송신 (Korean IME 회피).
    Returns: {'success': bool, 'message'|'error': str, 'hwnd': int?}
    """
    main_hwnd = win32gui.FindWindow(KAKAO_MAIN_WINDOW_CLASS, KAKAO_MAIN_WINDOW_TITLE)
    if main_hwnd == 0:
        return {"success": False, "error": "카톡 메인창 없음"}

    if not _ensure_foreground(main_hwnd):
        _log("warn: 카톡 포그라운드 실패")

    # 검색 후보 결정:
    #  - 일반 방: 방 이름 그대로 1회 시도 (+ substring/any 폴백)
    #  - 멤버목록(쉼표) 그룹방: 전체 문자열로는 검색이 안 되므로 멤버 이름들을
    #    순서대로 시도해 '정규화 일치' 창이 열릴 때까지 반복 (잘못된 방 송신 방지)
    is_group = ", " in room_name
    if is_group:
        members = [m.strip() for m in room_name.split(",")
                   if m.strip() and m.strip() != "..."]
        # 접두 조합(앞 2~3명)을 먼저 시도 — 방 제목 앞부분과 매칭돼 정확한 방을
        # 열 확률이 높음. 그 다음 개별 멤버(폴백, 다른 방이 열릴 수 있음).
        candidates = []
        if len(members) >= 2:
            candidates.append(", ".join(members[:2]))   # "A, B"
        if len(members) >= 3:
            candidates.append(", ".join(members[:3]))   # "A, B, C"
        candidates += members
        # 중복 제거(순서 유지)
        seen = set()
        candidates = [c for c in candidates if not (c in seen or seen.add(c))]
    else:
        candidates = [room_name]

    def _search_once(q: str) -> list:
        """검색 재활성화 → q 입력 → Enter → 열린 분리창 목록 반환."""
        _ensure_foreground(main_hwnd)
        eh = _activate_search_and_get_edit(main_hwnd)
        if eh is None:
            return []
        win32api.SendMessage(eh, EM_SETSEL, 0, -1)
        win32api.SendMessage(eh, WM_CLEAR, 0, 0)
        win32api.SendMessage(eh, WM_SETTEXT, 0, "")
        time.sleep(EDIT_CLICK_WAIT_SEC)
        for ch in q:
            win32api.SendMessage(eh, WM_CHAR, ord(ch), 0)
            time.sleep(SEARCH_CHAR_INTERVAL_SEC)
        time.sleep(SEARCH_RESULTS_WAIT_SEC)
        _user32.keybd_event(VK_RETURN, 0, 0, 0)
        _user32.keybd_event(VK_RETURN, 0, KEYEVENTF_KEYUP, 0)
        time.sleep(SEARCH_OPEN_WAIT_SEC)
        return list_chat_windows()

    result = None
    for q in candidates:
        all_windows = _search_once(q)
        _log(f"검색 시도 '{q}' (대상 '{room_name}') → 창 {len(all_windows)}개")
        # 정규화 일치 (트레일링 ', ...' 허용)
        for w in all_windows:
            if _title_matches_room(w["title"], room_name):
                result = {"success": True, "message": f"opened '{w['title']}'", "hwnd": w["hwnd"]}
                break
        if result:
            break
        # 일반 방은 substring/any 폴백 1회 (그룹방은 오송신 방지 위해 정규화만)
        if not is_group:
            for w in all_windows:
                if room_name in w["title"]:
                    result = {"success": True,
                              "message": f"opened '{w['title']}' (substr)",
                              "hwnd": w["hwnd"]}
                    break
            if result is None and all_windows:
                result = {"success": True,
                          "message": f"opened (title: '{all_windows[0]['title']}')",
                          "hwnd": all_windows[0]["hwnd"]}
            break  # 일반 방은 1회만

    if result is None:
        result = {"success": False, "error": f"방 '{room_name}' 검색 후 분리창 못 찾음"}

    # 검색창 텍스트 비우기 — 잔류 시 다음 검색/monitor 목록 필터를 방해함
    clear_chat_search()
    return result


# ─────────────────────────────────────────────
# 메시지 송신 (Bot API fallback)
# ─────────────────────────────────────────────
def send_message_to_room(room_name: str, text: str) -> Dict:
    """분리창 RICHEDIT50W 에 클립보드 paste + Enter.

    봇 API 와 별개. 봇이 들어가지 않은 방에 직접 송신 가능.

    ⚠️ 통합 라이브 E2E 에서 '방금 연 창/포커스 경합' 시 분리창이 전면이 아니라
    paste·Enter 가 다른 창으로 빠져 메시지가 조용히 유실되는 게 확인됐다.
    그래서 **GetForegroundWindow 로 분리창이 실제 전면인지 확인**한 뒤 송신한다.
    (RICHEDIT50W 는 WM_GETTEXTLENGTH/WM_SETTEXT 가 크로스프로세스로 신뢰되지 않아
     길이 기반 검증은 쓰지 않는다. Ctrl+A+Del 클리어는 포커스 오류 시 '전송 메시지
     삭제' 사고를 내므로 절대 사용 금지.)
    """
    hwnd = find_chat_window(room_name)
    if hwnd is None:
        return {"success": False, "error": f"분리창 '{room_name}' 없음"}

    edit_hwnd = find_child_window_recursive(hwnd, KAKAO_EDIT_CLASS)
    if edit_hwnd is None:
        return {"success": False, "error": f"RICHEDIT50W 못 찾음 in '{room_name}'"}

    # 분리창을 확실히 전면화 (keystroke 오라우팅 방지). GetForegroundWindow 로 확인.
    fg_ok = False
    for _ in range(6):
        bring_window_to_front(hwnd)
        time.sleep(WINDOW_ACTIVATE_WAIT_SEC + 0.1)
        try:
            if win32gui.GetForegroundWindow() == hwnd:
                fg_ok = True
                break
        except Exception:
            pass
        time.sleep(0.25)
    if not fg_ok:
        # 전면화 실패 → 키입력이 엉뚱한 창으로 갈 위험 → 송신 보류(상위에서 다음 사이클 재시도)
        return {"success": False, "error": f"분리창 '{room_name}' 전면화 실패 — 송신 보류"}

    try:
        rect = win32gui.GetWindowRect(edit_hwnd)
        cx = (rect[0] + rect[2]) // 2
        cy = (rect[1] + rect[3]) // 2
        _user32.SetCursorPos(cx, cy)
        _user32.mouse_event(0x0002, 0, 0, 0, 0)
        _user32.mouse_event(0x0004, 0, 0, 0, 0)
        time.sleep(EDIT_CLICK_WAIT_SEC + 0.1)
    except Exception:
        pass

    _set_clipboard_text(text)
    time.sleep(0.08)
    _send_ctrl_key_combo(VK_V)
    time.sleep(0.12)
    _user32.keybd_event(VK_RETURN, 0, 0, 0)
    _user32.keybd_event(VK_RETURN, 0, KEYEVENTF_KEYUP, 0)
    return {"success": True, "message": f"sent to '{room_name}'"}


# ─────────────────────────────────────────────
# 사진 cache 직접 추출 (서랍 자동화 우회)
# ─────────────────────────────────────────────
def get_kakao_user_hash_dir() -> Optional[str]:
    """카톡 user 디렉토리 찾기 (SHA1 hash 폴더)."""
    if not os.path.isdir(KAKAO_USERS_DIR):
        return None
    for entry in os.listdir(KAKAO_USERS_DIR):
        full = os.path.join(KAKAO_USERS_DIR, entry)
        if os.path.isdir(full) and len(entry) == 40:
            return full
    return None


def download_recent_images(
    output_dir: str,
    max_images: int = 10,
    min_mtime: float = 0.0,
) -> Dict:
    """카톡 cache 디렉토리에서 최근 이미지를 output_dir 로 복사.

    ⚠ 주의: 카톡 cache 의 이미지는 암호화된 .cng (AES-128-CBC) 라서
    그대로 복사하면 표시 불가. 실제 이미지가 필요하면 서랍 '저장' 경로 사용.
    (이 함수는 포렌식/디버그용으로만 유지)

    Args:
        output_dir: 저장 폴더
        max_images: 복사할 최대 개수
        min_mtime: 이 시간 이후 수정된 파일만 (0 이면 전체)

    Returns: {'message': str, 'images': [{'source', 'destination', 'size'}, ...]}
    """
    user_dir = get_kakao_user_hash_dir()
    if user_dir is None:
        return {"error": "카톡 user 디렉토리 못 찾음"}

    image_files: List[Dict] = []
    for subdir in IMAGE_CACHE_SUBDIRS:
        cache_path = os.path.join(user_dir, subdir)
        if not os.path.isdir(cache_path):
            continue
        for fname in os.listdir(cache_path):
            full_path = os.path.join(cache_path, fname)
            if not os.path.isfile(full_path):
                continue
            try:
                stat = os.stat(full_path)
                if stat.st_size < 1024:
                    continue
                if stat.st_mtime < min_mtime:
                    continue
                image_files.append({
                    "path": full_path,
                    "name": fname,
                    "mtime": stat.st_mtime,
                    "size": stat.st_size,
                })
            except OSError:
                continue

    if not image_files:
        return {"message": "no cached images", "images": []}

    image_files.sort(key=lambda x: x["mtime"], reverse=True)
    image_files = image_files[:max_images]

    os.makedirs(output_dir, exist_ok=True)
    copied: List[Dict] = []
    for img in image_files:
        dest_name = img["name"]
        if not os.path.splitext(dest_name)[1]:
            dest_name += ".jpg"
        dest = os.path.join(output_dir, dest_name)
        try:
            shutil.copy2(img["path"], dest)
            copied.append({
                "source": img["path"],
                "destination": dest,
                "size": img["size"],
            })
        except OSError:
            continue

    return {
        "message": f"downloaded {len(copied)} image(s) to {output_dir}",
        "images": copied,
    }
