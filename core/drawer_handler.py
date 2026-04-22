"""
카카오톡 서랍 사진 다운로드 + 카카오워크 업로드 자동화.

실측 검증 완료 (2026-04-16):
  1. ≡ 클릭 → 팝업 메뉴
  2. "채팅방 서랍" hover (팝업 y+82) → 서브메뉴 출현
  3. "사진/동영상" 클릭 (서브메뉴 첫 항목)
  4. 서랍 창 열림 (좌측: 방 리스트, 우측: 사진 그리드)
  5. 좌측에서 방 선택 → 우측 사진 그리드 갱신
  6. 사진 더블클릭 → 뷰어 (제목: "발신자 날짜")
  7. ↓ 버튼 (하단 바 우측에서 ~70px) → 드롭다운
  8. "묶음사진 전체저장" → "다른 이름으로 저장" → Enter → 파일 저장

핵심 전략: 서랍 1회 열기 → 좌측 리스트에서 방 순회 → 전부 다운로드.
방 이름 매칭: Claude Vision OCR → 드로어 breadcrumb 검증.
"""
from __future__ import annotations

import base64
import ctypes
import os
import re
import time
from pathlib import Path

import pyautogui
import win32gui
import win32con

from core.traced_actions import mark

pyautogui.FAILSAFE = False
ctypes.windll.user32.AllowSetForegroundWindow(-1)

KAKAO_DOWNLOAD_DIR = Path("C:/Users/USER/Documents/카카오톡 받은 파일")
CAPTURES_DIR = Path(__file__).parent.parent / "captures"


# ═══════════════════════════════════════════════════════
# 방 이름 매칭 (OCR 기반)
# ═══════════════════════════════════════════════════════

def _normalize_room_name(name: str) -> str:
    """방 이름 정규화: 공백/특수문자 제거, 소문자화."""
    if not name:
        return ""
    # 공백·점·이음표·따옴표 모두 제거, & 와 + 를 동등 취급
    n = re.sub(r"[\s\.\-_\"'()]+", "", name)
    n = n.replace("&", "+").lower()
    return n


def _rooms_match(a: str, b: str, min_prefix: int = 4) -> bool:
    """두 방 이름이 같은지 (정규화 + 접두사 매칭)."""
    na, nb = _normalize_room_name(a), _normalize_room_name(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    # 한쪽이 잘린 경우 (예: "네노바 + 다..." vs "네노바 + 다원")
    short, long_ = (na, nb) if len(na) <= len(nb) else (nb, na)
    if len(short) >= min_prefix and long_.startswith(short):
        return True
    return False


def _vision_ocr(image_path: Path, prompt: str, max_tokens: int = 200) -> str | None:
    """Claude Vision OCR. 실패 시 None."""
    try:
        import anthropic  # type: ignore
    except ImportError:
        return None
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    try:
        client = anthropic.Anthropic(api_key=api_key)
        with open(image_path, "rb") as f:
            data = base64.standard_b64encode(f.read()).decode()
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=max_tokens,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": data}},
                    {"type": "text", "text": prompt},
                ],
            }],
        )
        return msg.content[0].text.strip()
    except Exception as e:
        print(f"    [OCR] 실패: {e}", flush=True)
        return None


def _ocr_drawer_header(drawer_hwnd: int) -> str | None:
    """드로어 상단 breadcrumb OCR → 현재 선택된 방 이름."""
    try:
        from PIL import ImageGrab
    except ImportError:
        return None
    dr = win32gui.GetWindowRect(drawer_hwnd)
    dw = dr[2] - dr[0]
    # 좌측 패널은 약 195px 폭. breadcrumb는 우측 패널 상단 (약 y+90~135)
    panel_left = dr[0] + 210
    header_top = dr[1] + 85
    header_right = dr[0] + min(dw - 30, panel_left + 300)
    header_bottom = dr[1] + 135
    try:
        CAPTURES_DIR.mkdir(parents=True, exist_ok=True)
        img = ImageGrab.grab(bbox=(panel_left, header_top, header_right, header_bottom))
        crop_path = CAPTURES_DIR / "drawer_header_ocr.png"
        img.save(crop_path)
    except Exception as e:
        print(f"    [OCR] breadcrumb 캡처 실패: {e}", flush=True)
        return None
    return _vision_ocr(
        crop_path,
        "이 이미지에 표시된 채팅방 이름 텍스트만 그대로 한 줄로 반환. 설명 없이 이름만. 없으면 빈 문자열.",
        max_tokens=80,
    )


def verify_drawer_room(drawer_hwnd: int, expected_room: str) -> bool:
    """드로어의 현재 선택된 방이 expected_room과 일치하는지."""
    current = _ocr_drawer_header(drawer_hwnd)
    if not current:
        print(f"    [서랍] breadcrumb OCR 실패 — 검증 불가", flush=True)
        return False
    match = _rooms_match(current, expected_room)
    tag = "일치" if match else "불일치"
    print(f"    [서랍] breadcrumb={current!r} vs 기대={expected_room!r} → {tag}", flush=True)
    return match


def _ocr_drawer_left_list(drawer_hwnd: int) -> list[dict]:
    """드로어 좌측 리스트 OCR → [{'name': ..., 'rel_y': ...}, ...]"""
    try:
        from PIL import ImageGrab
    except ImportError:
        return []
    dr = win32gui.GetWindowRect(drawer_hwnd)
    # 좌측 패널 영역 (아이콘 포함)
    panel_left = dr[0] + 30
    panel_right = dr[0] + 200
    panel_top = dr[1] + 55
    panel_bottom = dr[3] - 20
    try:
        CAPTURES_DIR.mkdir(parents=True, exist_ok=True)
        img = ImageGrab.grab(bbox=(panel_left, panel_top, panel_right, panel_bottom))
        crop_path = CAPTURES_DIR / "drawer_left_list_ocr.png"
        img.save(crop_path)
        list_height = panel_bottom - panel_top
    except Exception as e:
        print(f"    [OCR] 좌측 리스트 캡처 실패: {e}", flush=True)
        return []
    raw = _vision_ocr(
        crop_path,
        (
            "이 이미지는 채팅방 서랍의 좌측 방 리스트입니다. "
            "각 항목은 아이콘+이름+사진수로 구성됩니다. "
            "위에서부터 순서대로 각 방의 이름만 한 줄씩 나열하세요. "
            "숫자·사진수·아이콘 설명은 빼고 방 이름 텍스트만. "
            "다른 설명 없이 이름만 줄바꿈으로 구분."
        ),
        max_tokens=500,
    )
    if not raw:
        return []
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    if not lines:
        return []
    # 각 항목의 rel_y는 대략 균등 분할 (정확하진 않지만 클릭엔 충분)
    step = list_height / max(len(lines), 1)
    return [
        {"name": ln, "rel_y": int(step * i + step / 2)}
        for i, ln in enumerate(lines)
    ]


def _click_in_drawer_left(drawer_hwnd: int, rel_y: int):
    dr = win32gui.GetWindowRect(drawer_hwnd)
    list_x = dr[0] + 115
    list_top = dr[1] + 55
    pyautogui.click(list_x, list_top + rel_y)
    time.sleep(1.2)


def select_room_in_drawer_by_name(
    drawer_hwnd: int, target_room: str, max_scroll: int = 6
) -> bool:
    """드로어 좌측 리스트에서 target_room 찾아서 클릭 → 검증까지."""
    # 이미 맞으면 스킵
    if verify_drawer_room(drawer_hwnd, target_room):
        return True

    dr = win32gui.GetWindowRect(drawer_hwnd)
    # 먼저 맨 위로 스크롤
    pyautogui.moveTo(dr[0] + 100, dr[1] + 200)
    for _ in range(10):
        pyautogui.scroll(3)
        time.sleep(0.1)
    time.sleep(0.5)

    for scroll_attempt in range(max_scroll):
        rooms = _ocr_drawer_left_list(drawer_hwnd)
        print(
            f"    [서랍] 좌측 {len(rooms)}개 감지: "
            f"{[r['name'] for r in rooms[:5]]}...",
            flush=True,
        )
        for r in rooms:
            if _rooms_match(r["name"], target_room):
                print(f"    [서랍] 매칭: {r['name']!r} → 클릭", flush=True)
                _click_in_drawer_left(drawer_hwnd, r["rel_y"])
                # 검증
                if verify_drawer_room(drawer_hwnd, target_room):
                    return True
                # 한번 더 OCR로 재확인
                time.sleep(0.5)
                if verify_drawer_room(drawer_hwnd, target_room):
                    return True
                # 매칭 OCR이지만 검증 실패 — 계속 시도

        # 현재 화면에 없음 → 아래로 스크롤
        pyautogui.moveTo(dr[0] + 100, dr[1] + 300)
        pyautogui.scroll(-3)
        time.sleep(0.5)

    print(f"    [서랍] '{target_room}' 좌측 리스트에서 못 찾음", flush=True)
    return False


def _snapshot_downloads() -> set[str]:
    """사진 저장 폴더(들) 스냅샷.
    카톡이 '마지막 폴더 기억'으로 우리 강제 텍스트 폴더(KAKAO_SAVE_DIR)에 사진을
    저장할 수 있으므로 두 곳 모두 감시한다.
    """
    result: set[str] = set()
    paths = [KAKAO_DOWNLOAD_DIR]
    try:
        from core.message_extractor import KAKAO_SAVE_DIR
        if KAKAO_SAVE_DIR not in paths:
            paths.append(KAKAO_SAVE_DIR)
    except Exception:
        pass
    for p in paths:
        if p.exists():
            for f in p.rglob("*"):
                if f.is_file():
                    result.add(str(f))
    return result


def _activate(hwnd: int):
    try:
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        time.sleep(0.1)
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        pass
    time.sleep(0.3)


def _find_popup(min_w=180, max_w=350, min_h=200, max_h=550, near_xy=None):
    """제목 없는 팝업 찾기 (≡ 메뉴).

    near_xy=(x, y) 지정 시 그 좌표에 가장 가까운 팝업 선택 (≡ 클릭 위치 기준).
    """
    results = []
    def cb(hwnd, lst):
        if win32gui.IsWindowVisible(hwnd):
            if not win32gui.GetWindowText(hwnd):
                r = win32gui.GetWindowRect(hwnd)
                w, h = r[2] - r[0], r[3] - r[1]
                if min_w <= w <= max_w and min_h <= h <= max_h:
                    # EVA_Menu 클래스 우선 (카톡 메뉴)
                    cls = win32gui.GetClassName(hwnd) or ""
                    lst.append((r, cls))
    win32gui.EnumWindows(cb, results)
    if not results:
        return None

    # EVA_Menu 클래스만 필터링 (실제 카톡 메뉴)
    eva = [r for r, c in results if "EVA_Menu" in c]
    candidates = eva if eva else [r for r, c in results]

    if near_xy and candidates:
        # 클릭 위치에서 가장 가까운 팝업 선택 + 거리 300px 초과면 제외 (오탐 방지)
        cx, cy = near_xy
        def dist(r):
            rx = (r[0] + r[2]) // 2
            ry = (r[1] + r[3]) // 2
            return ((rx - cx) ** 2 + (ry - cy) ** 2) ** 0.5
        best = min(candidates, key=dist)
        d = dist(best)
        if d > 350:
            # 근처에 팝업 없음 = 실제 메뉴가 안 열린 것
            return None
        return best
    return candidates[0]


def _find_submenu():
    """서브메뉴 (작은 팝업) 찾기."""
    results = []
    def cb(hwnd, lst):
        if win32gui.IsWindowVisible(hwnd):
            if not win32gui.GetWindowText(hwnd):
                r = win32gui.GetWindowRect(hwnd)
                w, h = r[2] - r[0], r[3] - r[1]
                if 50 <= w <= 250 and 30 <= h <= 150:
                    lst.append(r)
    win32gui.EnumWindows(cb, results)
    return results[0] if results else None


def _find_drawer_window():
    """'채팅방 서랍' 독립 창 찾기."""
    results = []
    def cb(hwnd, lst):
        if win32gui.IsWindowVisible(hwnd):
            t = win32gui.GetWindowText(hwnd)
            if t == "채팅방 서랍":
                lst.append((hwnd, win32gui.GetWindowRect(hwnd)))
    win32gui.EnumWindows(cb, results)
    return results[0] if results else None


def _find_viewer():
    """사진 뷰어 찾기 (제목에 날짜 포함)."""
    results = []
    def cb(hwnd, lst):
        if win32gui.IsWindowVisible(hwnd):
            t = win32gui.GetWindowText(hwnd)
            r = win32gui.GetWindowRect(hwnd)
            w, h = r[2] - r[0], r[3] - r[1]
            if w > 300 and h > 300 and "2026" in t:
                lst.append((hwnd, t, r))
    win32gui.EnumWindows(cb, results)
    return results[0] if results else None


# ═══════════════════════════════════════════════════════
# UIA 기반 팝업 내부 메뉴 네비 (≡ 이미 클릭된 상태에서 호출)
# ═══════════════════════════════════════════════════════

def _try_uia_inner_nav(popup_rect: tuple) -> bool:
    """팝업이 이미 떠 있는 상태에서 "채팅방 서랍" → "사진/동영상" 을 UIA 로 클릭.

    popup_rect: `_find_popup` 이 반환한 (l, t, r, b) 튜플.

    Returns:
        True — 성공 (서랍 창이 뜰 시간까지 sleep 포함)
        False — 실패 (호출자가 픽셀 폴백을 진행해야 함)
    """
    import os as _os
    print(f"    [UIA-NAV] 진입: popup={popup_rect}", flush=True)

    if _os.getenv("NENOVA_DRAWER_FORCE_PIXEL") == "1":
        print(f"    [UIA-NAV] FORCE_PIXEL — 스킵", flush=True)
        return False

    try:
        from core.drawer_uia import (
            PYWINAUTO_AVAILABLE, _find_by_name_substr, _invoke_safely,
            MENU_NAMES_DRAWER, MENU_NAMES_PHOTO_TAB,
        )
    except Exception as e:
        print(f"    [UIA-NAV] 모듈 import 실패: {e}", flush=True)
        return False

    if not PYWINAUTO_AVAILABLE:
        print(f"    [UIA-NAV] pywinauto 사용 불가", flush=True)
        return False

    # ── Step 1: 팝업 rect 매칭되는 hwnd 찾기 (fuzzy) ──
    popup_cx = (popup_rect[0] + popup_rect[2]) // 2
    popup_cy = (popup_rect[1] + popup_rect[3]) // 2

    # EnumWindows 는 파이썬 콜백을 호출. 콜백에서 예외 나면 C 레벨에서 중단.
    # 단순 수집기 패턴으로 안전하게.
    all_small_popups: list = []

    def _collect(hwnd, _):
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return
            if win32gui.GetWindowText(hwnd):
                return
            r = win32gui.GetWindowRect(hwnd)
            w, h = r[2] - r[0], r[3] - r[1]
            if 100 <= w <= 500 and 100 <= h <= 700:
                cls = win32gui.GetClassName(hwnd) or ""
                all_small_popups.append((hwnd, r, cls))
        except Exception:
            pass

    try:
        win32gui.EnumWindows(_collect, None)
    except Exception as e:
        print(f"    [UIA-NAV] EnumWindows 실패: {e}", flush=True)
        return False

    print(f"    [UIA-NAV] 팝업 후보 {len(all_small_popups)}개", flush=True)

    # 중심점 거리로 정렬, EVA_Menu 우선
    def _dist(cand):
        r = cand[1]
        cx = (r[0] + r[2]) // 2
        cy = (r[1] + r[3]) // 2
        return abs(cx - popup_cx) + abs(cy - popup_cy)

    eva = [c for c in all_small_popups if "EVA_Menu" in c[2]]
    pool = eva if eva else all_small_popups
    if not pool:
        print(f"    [UIA-NAV] 후보 0개 — 실패", flush=True)
        return False

    pool.sort(key=_dist)
    popup_hwnd, popup_rect_live, popup_cls = pool[0]
    d = _dist(pool[0])
    if d > 80:
        print(f"    [UIA-NAV] 가장 가까운 팝업도 거리 {d}px — 매칭 실패", flush=True)
        for hwnd, r, c in pool[:3]:
            print(f"       hwnd={hwnd} cls={c} rect={r} dist={_dist((hwnd, r, c))}", flush=True)
        return False

    print(f"    [UIA-NAV] 매칭: hwnd={popup_hwnd} cls={popup_cls} rect={popup_rect_live} dist={d}", flush=True)

    # ── Step 2: UIA 로 팝업 연결 → "채팅방 서랍" MenuItem invoke ──
    try:
        from pywinauto import Application
        app = Application(backend="uia").connect(handle=popup_hwnd, timeout=1)
        popup_win = app.window(handle=popup_hwnd)
    except Exception as e:
        print(f"    [UIA-NAV] 팝업 UIA connect 실패: {e}", flush=True)
        return False

    drawer_item = _find_by_name_substr(
        popup_win, MENU_NAMES_DRAWER,
        control_types=["MenuItem", "ListItem", "Button", "Text"],
    )

    drawer_clicked_via = None  # "uia" | "hardcoded" | "vision"
    drawer_x = drawer_y = None

    if drawer_item is not None:
        print(f"    [UIA-NAV] '채팅방 서랍' UIA 발견: {drawer_item.element_info.name!r} → invoke", flush=True)
        if _invoke_safely(drawer_item, "open_drawer.uia_drawer_item"):
            drawer_clicked_via = "uia"

    if drawer_clicked_via is None:
        # 팝업이 표준 크기(225x324)에 EVA_Menu 클래스면 하드코딩 좌표 사용.
        # E2E 검증: "채팅방 서랍" 은 팝업 top+95, center x 근처.
        pw = popup_rect_live[2] - popup_rect_live[0]
        ph = popup_rect_live[3] - popup_rect_live[1]
        size_ok = 200 <= pw <= 260 and 300 <= ph <= 360
        if size_ok:
            drawer_x = (popup_rect_live[0] + popup_rect_live[2]) // 2
            drawer_y = popup_rect_live[1] + 95
            print(f"    [UIA-NAV] 하드코딩 '채팅방 서랍': ({drawer_x},{drawer_y}) [팝업 {pw}x{ph} 표준]", flush=True)
            import pyautogui as _pag
            _pag.moveTo(drawer_x, drawer_y, duration=0.2)
            drawer_clicked_via = "hardcoded_hover"
        else:
            # 비표준 크기 → Vision OCR
            try:
                items = popup_win.descendants(control_type="MenuItem")
                names = [(it.element_info.name or "")[:30] for it in items]
                print(f"    [UIA-NAV] MenuItems 비어있음 ({names[:10]}) + 비표준 팝업 → Vision 폴백", flush=True)
            except Exception:
                print(f"    [UIA-NAV] MenuItems 조회 실패 → Vision 폴백", flush=True)

            try:
                from core.vision_clicker import find_and_click
                v_result = find_and_click(
                    popup_rect_live,
                    "카카오톡 채팅방 메뉴 팝업에서 '채팅방 서랍' 또는 '서랍' 텍스트가 있는 메뉴 항목. "
                    "서랍 아이콘 옆에 표시됨. '대화 내용'이나 '톡캘린더'가 아님.",
                    tag="drawer.vision_find_drawer_item",
                    min_confidence=0.55,
                    dry_run=True,
                )
                if not v_result.found:
                    print(f"    [UIA-NAV] Vision '채팅방 서랍' 못 찾음 — 실패", flush=True)
                    return False

                drawer_x, drawer_y = v_result.x, v_result.y
                print(f"    [UIA-NAV] Vision '채팅방 서랍' 좌표: ({drawer_x},{drawer_y}) conf={v_result.confidence:.2f}", flush=True)

                import pyautogui as _pag
                _pag.moveTo(drawer_x, drawer_y, duration=0.2)
                drawer_clicked_via = "vision_hover"
            except Exception as e:
                print(f"    [UIA-NAV] Vision 호출 에러: {e}", flush=True)
                return False

    # ── Step 3: 서브메뉴 찾기. 2단계 전략:
    #   (a) 1.5초 hover 대기 — hover 에 반응하는 경우 (일반적)
    #   (b) 못 찾으면 click — 일부 빌드는 click 해야 서브메뉴 출현
    # ────────────────────────────────────────────────────────
    def _scan_submenu_once() -> tuple | None:
        new_popups: list = []

        def _collect_sub(hwnd, _):
            try:
                if not win32gui.IsWindowVisible(hwnd):
                    return
                if win32gui.GetWindowText(hwnd):
                    return
                if hwnd == popup_hwnd:
                    return
                r = win32gui.GetWindowRect(hwnd)
                w, h = r[2] - r[0], r[3] - r[1]
                if 80 <= w <= 500 and 30 <= h <= 500:
                    cls = win32gui.GetClassName(hwnd) or ""
                    new_popups.append((hwnd, r, cls))
            except Exception:
                pass

        try:
            win32gui.EnumWindows(_collect_sub, None)
        except Exception as e:
            print(f"    [UIA-NAV] 서브메뉴 enum 실패: {e}", flush=True)
            return None

        if not new_popups:
            return None

        pop_h = popup_rect_live[3] - popup_rect_live[1]
        smaller = [p for p in new_popups if (p[1][3] - p[1][1]) < pop_h]
        candidates = smaller if smaller else new_popups
        pop_right = popup_rect_live[2]
        def _score(p):
            r = p[1]
            cx = (r[0] + r[2]) // 2
            return abs(cx - (pop_right + 80))
        return min(candidates, key=_score)

    submenu_rect = None
    # (a) hover 만 해두고 대기
    t0 = time.time()
    while time.time() - t0 < 2.0:
        best = _scan_submenu_once()
        if best:
            submenu_rect = best[1]
            print(f"    [UIA-NAV] hover 로 서브메뉴 감지: hwnd={best[0]} cls={best[2]} rect={submenu_rect}", flush=True)
            break
        time.sleep(0.2)

    # (b) hover 실패 → 그 위치를 CLICK (일부 빌드는 클릭 필요)
    if submenu_rect is None and drawer_clicked_via in ("vision_hover", "hardcoded_hover") and drawer_x is not None:
        print(f"    [UIA-NAV] hover 로 서브메뉴 안 뜸 → click 재시도", flush=True)
        import pyautogui as _pag
        _pag.click(drawer_x, drawer_y)
        t0 = time.time()
        while time.time() - t0 < 2.5:
            best = _scan_submenu_once()
            if best:
                submenu_rect = best[1]
                print(f"    [UIA-NAV] click 후 서브메뉴: hwnd={best[0]} cls={best[2]} rect={submenu_rect}", flush=True)
                break
            time.sleep(0.2)

    if submenu_rect is None:
        print(f"    [UIA-NAV] 서브메뉴 창 없음 (hover + click 모두 실패)", flush=True)
        return False

    # ── Step 4: 서브메뉴에서 "사진/동영상" 클릭 ──
    # E2E 검증: 서브메뉴는 160x86 크기, "사진/동영상" 은 첫 번째 (y_offset ≈ 18)
    sm_w = submenu_rect[2] - submenu_rect[0]
    sm_h = submenu_rect[3] - submenu_rect[1]
    if 140 <= sm_w <= 200 and 70 <= sm_h <= 140:
        # 하드코딩: 서브메뉴 center x, top+18 (첫 번째 항목)
        photo_x = (submenu_rect[0] + submenu_rect[2]) // 2
        photo_y = submenu_rect[1] + 18
        print(f"    [UIA-NAV] 하드코딩 '사진/동영상': ({photo_x},{photo_y}) [서브메뉴 {sm_w}x{sm_h} 표준]", flush=True)
        try:
            import pyautogui as _pag
            _pag.click(photo_x, photo_y)
            mark("open_drawer.submenu_detected", "after", {"method": "hardcoded"})
            mark("open_drawer.photo_tab_clicked", "after", {"method": "hardcoded"})
            time.sleep(2.5)
            return True
        except Exception as e:
            print(f"    [UIA-NAV] 하드코딩 클릭 예외: {e}", flush=True)
            return False

    # 비표준 크기 → Vision
    try:
        from core.vision_clicker import find_and_click
        v_result = find_and_click(
            submenu_rect,
            "카카오톡 서랍 서브메뉴에서 '사진/동영상' 또는 '사진' 텍스트 메뉴 항목. "
            "보통 서브메뉴의 첫 번째 항목.",
            tag="drawer.vision_find_photo_tab",
            min_confidence=0.55,
        )
        if v_result.found:
            print(f"    [UIA-NAV] '사진/동영상' Vision 클릭 성공: ({v_result.x},{v_result.y})", flush=True)
            mark("open_drawer.submenu_detected", "after", {"method": "vision"})
            mark("open_drawer.photo_tab_clicked", "after", {"method": "vision"})
            time.sleep(2.5)
            return True
        else:
            print(f"    [UIA-NAV] '사진/동영상' Vision 못 찾음", flush=True)
            return False
    except Exception as e:
        print(f"    [UIA-NAV] 서브메뉴 Vision 에러: {e}", flush=True)
        return False


# ═══════════════════════════════════════════════════════
# 1단계: 서랍 열기 (아무 채팅방에서 1회)
# ═══════════════════════════════════════════════════════

def open_drawer(chat_hwnd: int) -> int | None:
    """
    채팅방에서 ≡ → 채팅방 서랍 → 사진/동영상 탭 열기.
    Returns: 서랍 창 hwnd or None.

    학습 포인트(mark): open_drawer.{focus,click_menu,popup_detected,
      hover_submenu,submenu_detected,photo_tab_clicked,panel_opened}
    """
    # 안전 가드: 카톡 활성 + 위험 팝업 자동 처치
    try:
        from core.safety_guard import pre_action_guard
        if not pre_action_guard("kakaotalk"):
            mark("open_drawer.focus", "fail", {"reason": "safety guard"})
            return None
    except Exception:
        pass

    mark("open_drawer.focus", "before", {"chat_hwnd": chat_hwnd})

    # 잔여 무명 EVA 팝업 선제 정리 (이전 ≡ 메뉴 미닫힘, 광고 등)
    try:
        import win32con as _wc_pre
        stray_popups_pre = []
        def _find_pre(h, _):
            if not win32gui.IsWindowVisible(h):
                return
            if win32gui.GetWindowText(h):  # 제목 있으면 스킵
                return
            cls = win32gui.GetClassName(h) or ""
            if "EVA_" not in cls:
                return
            r = win32gui.GetWindowRect(h)
            w, hh = r[2]-r[0], r[3]-r[1]
            if 100 <= w <= 500 and 100 <= hh <= 700:
                stray_popups_pre.append((h, cls, r))
        win32gui.EnumWindows(_find_pre, None)
        if stray_popups_pre:
            print(f"    [서랍] ≡ 클릭 전 잔여 EVA 팝업 {len(stray_popups_pre)}개 정리", flush=True)
            for h, c, r in stray_popups_pre:
                try:
                    win32gui.PostMessage(h, _wc_pre.WM_CLOSE, 0, 0)
                except Exception:
                    pass
            time.sleep(0.3)
    except Exception as e:
        print(f"    [서랍] 사전 팝업 정리 실패 (무시): {e}", flush=True)

    # chat_hwnd 유효성 체크 (open_drawer_uia 등에서 무효화됐을 수 있음)
    if not win32gui.IsWindow(chat_hwnd):
        # 재탐색: 같은 방 제목의 유효한 hwnd 찾기
        print(f"    [서랍] chat_hwnd={chat_hwnd} 무효 — 재탐색 시도", flush=True)
        orig_title = ""
        try:
            # 저장해뒀던 이전 제목으로 찾기 — 없으면 그냥 첫 분리창
            candidates = []
            def _find(h, _):
                if not win32gui.IsWindowVisible(h):
                    return
                t = win32gui.GetWindowText(h) or ""
                if not t or t == "카카오톡":
                    return
                cls = win32gui.GetClassName(h) or ""
                if not cls.startswith("EVA_"):
                    return
                r = win32gui.GetWindowRect(h)
                w, hh = r[2]-r[0], r[3]-r[1]
                if 300 <= w <= 900 and 500 <= hh <= 1000:
                    candidates.append((h, t, r))
            win32gui.EnumWindows(_find, None)
            if candidates:
                chat_hwnd = candidates[0][0]
                print(f"    [서랍] 재탐색 성공: hwnd={chat_hwnd} title={candidates[0][1]!r}", flush=True)
            else:
                print(f"    [서랍] 유효한 분리창 없음 → 실패", flush=True)
                return None
        except Exception as e:
            print(f"    [서랍] 재탐색 예외: {e}", flush=True)
            return None

    # 타겟 외 다른 카톡 분리창을 모두 최소화 (클릭 간섭 방지).
    try:
        import win32con as _wc
        others_minimized = []
        def _minimize_other_chats(h, _):
            if h == chat_hwnd:
                return
            if not win32gui.IsWindowVisible(h):
                return
            if win32gui.IsIconic(h):  # 이미 최소화
                return
            t = win32gui.GetWindowText(h) or ""
            if not t or t == "카카오톡":
                return
            cls = win32gui.GetClassName(h) or ""
            if not cls.startswith("EVA_"):
                return
            r = win32gui.GetWindowRect(h)
            w, hh = r[2]-r[0], r[3]-r[1]
            if 300 <= w <= 900 and 500 <= hh <= 1000:
                try:
                    win32gui.ShowWindow(h, _wc.SW_MINIMIZE)
                    others_minimized.append(t[:25])
                except Exception:
                    pass
        win32gui.EnumWindows(_minimize_other_chats, None)
        if others_minimized:
            print(f"    [서랍] 다른 분리창 {len(others_minimized)}개 최소화: {others_minimized}", flush=True)
        time.sleep(0.3)
    except Exception as e:
        print(f"    [서랍] 다른 분리창 정리 실패 (무시): {e}", flush=True)

    # chat_hwnd 재확인 (minimize 후)
    if not win32gui.IsWindow(chat_hwnd):
        print(f"    [서랍] 최소화 후 chat_hwnd 무효 → 실패", flush=True)
        return None

    # 분리창을 E2E 검증된 고정 크기 (910, 50, 600, 800) 로 강제.
    try:
        import win32con as _wc
        # 혹시 최소화 상태면 복원
        if win32gui.IsIconic(chat_hwnd):
            win32gui.ShowWindow(chat_hwnd, _wc.SW_RESTORE)
            time.sleep(0.3)
        win32gui.MoveWindow(chat_hwnd, 100, 50, 600, 800, True)
        time.sleep(0.3)
        SWP = _wc.SWP_NOMOVE | _wc.SWP_NOSIZE | _wc.SWP_SHOWWINDOW
        win32gui.SetWindowPos(chat_hwnd, -1, 0, 0, 0, 0, SWP)
        time.sleep(0.2)
    except Exception as e:
        print(f"    [서랍] 창 위치 고정 실패 (무시): {e}", flush=True)

    _activate(chat_hwnd)
    time.sleep(0.3)

    if not win32gui.IsWindow(chat_hwnd):
        print(f"    [서랍] _activate 후 chat_hwnd 무효 → 실패", flush=True)
        return None

    # 포커스 확실히 확보 (Alt 트릭)
    try:
        import ctypes as _ct
        fg_before = win32gui.GetForegroundWindow()
        fg_before_title = win32gui.GetWindowText(fg_before) if fg_before else ""
        if fg_before != chat_hwnd:
            _ct.windll.user32.keybd_event(0x12, 0, 0, 0)  # Alt down
            time.sleep(0.05)
            _ct.windll.user32.keybd_event(0x12, 0, 0x0002, 0)  # Alt up
            time.sleep(0.1)
            try:
                win32gui.SetForegroundWindow(chat_hwnd)
            except Exception:
                pass
            time.sleep(0.3)
            fg_after = win32gui.GetForegroundWindow()
            fg_after_title = win32gui.GetWindowText(fg_after) if fg_after else ""
            print(f"    [서랍] 포커스 전환: {fg_before_title[:20]!r} → {fg_after_title[:20]!r}", flush=True)
    except Exception as e:
        print(f"    [서랍] Alt 포커스 트릭 실패: {e}", flush=True)

    rect = win32gui.GetWindowRect(chat_hwnd)
    print(f"    [서랍] 분리창 rect={rect} size={rect[2]-rect[0]}x{rect[3]-rect[1]}", flush=True)
    mark("open_drawer.focus", "after", {"rect": rect})

    # rect 가 기대값과 크게 다르면 경고 + 한 번 더 시도
    expected_w = 600
    expected_h = 800
    actual_w = rect[2] - rect[0]
    actual_h = rect[3] - rect[1]
    if abs(actual_w - expected_w) > 20 or abs(actual_h - expected_h) > 20:
        print(f"    [서랍] 크기 불일치 (기대 {expected_w}x{expected_h}) → 재시도", flush=True)
        try:
            import win32con as _wc
            win32gui.MoveWindow(chat_hwnd, 100, 50, 600, 800, True)
            time.sleep(0.5)
            rect = win32gui.GetWindowRect(chat_hwnd)
            print(f"    [서랍] 재시도 후 rect={rect}", flush=True)
        except Exception as e:
            print(f"    [서랍] 재시도 실패: {e}", flush=True)

    # ── ≡ 좌표 결정: 분리창 600x800 고정 → 하드코딩 (1480, 105) 우선 사용.
    # E2E 검증으로 이 위치가 정확함이 확인됨 (rect[2]-20, rect[1]+55).
    # 분리창 크기가 확정되지 않거나 다른 빌드면 Vision 으로 폴백.
    menu_x = menu_y = None

    # 크기가 600x800 에 가까우면 하드코딩 신뢰
    size_ok = abs(actual_w - expected_w) <= 20 and abs(actual_h - expected_h) <= 20
    if size_ok:
        menu_x, menu_y = rect[2] - 20, rect[1] + 55
        print(f"    [서랍] 하드코딩 ≡: ({menu_x}, {menu_y}) [600x800 확정]", flush=True)
    else:
        # 크기가 다르면 Vision 으로 찾기
        try:
            from core.vision_clicker import find_and_click
            v = find_and_click(
                (rect[0], rect[1], rect[2], rect[3]),
                "카카오톡 채팅 분리창에서 방 제목이 표시된 줄의 오른쪽 끝에 있는 "
                "햄버거 메뉴(≡, 가로 세 줄) 아이콘. "
                "Windows 타이틀바의 최소화/최대화/닫기 버튼은 절대 아님. "
                "방 제목 바로 옆, 전화/영상/검색 아이콘들과 같은 높이.",
                tag="drawer.menu_hamburger",
                min_confidence=0.55,
                dry_run=True,
            )
            if v.found:
                # Sanity: y 가 rect top 에서 35~90px 범위여야 함 (너무 위면 타이틀바)
                y_offset = v.y - rect[1]
                if 30 <= y_offset <= 90:
                    menu_x, menu_y = v.x, v.y
                    print(f"    [서랍] Vision ≡ 좌표: ({menu_x}, {menu_y}) y_offset={y_offset}", flush=True)
                else:
                    print(f"    [서랍] Vision ≡ 비정상 y_offset={y_offset} (기대 30~90) → 하드코딩", flush=True)
        except Exception as e:
            print(f"    [서랍] Vision ≡ 예외: {e}", flush=True)

    if menu_x is None:
        menu_x, menu_y = rect[2] - 20, rect[1] + 55
        print(f"    [서랍] 하드코딩 ≡ 사용: ({menu_x}, {menu_y})", flush=True)

    # 최대 3회 시도
    popup = None
    for attempt in range(3):
        print(f"    [서랍] 시도 {attempt+1}/3", flush=True)
        mark("open_drawer.click_menu", "before", {"xy": [menu_x, menu_y], "attempt": attempt})

        # 매 시도 직전 포커스 강제 (Alt 트릭 포함)
        if not win32gui.IsWindow(chat_hwnd):
            print(f"    [서랍] chat_hwnd 무효 — 중단", flush=True)
            break
        try:
            import win32con
            import ctypes as _ct
            # Alt 트릭으로 포커스 권한 확보
            _ct.windll.user32.keybd_event(0x12, 0, 0, 0)
            time.sleep(0.03)
            _ct.windll.user32.keybd_event(0x12, 0, 0x0002, 0)
            time.sleep(0.05)
            # TOPMOST + SetForeground
            SWP = 0x0002 | 0x0001 | 0x0040
            win32gui.SetWindowPos(chat_hwnd, -1, 0, 0, 0, 0, SWP)
            try:
                win32gui.SetForegroundWindow(chat_hwnd)
            except Exception:
                # fallback: BringWindowToTop + SetFocus
                win32gui.BringWindowToTop(chat_hwnd)
        except Exception as e:
            print(f"    [서랍] foreground 실패: {e}", flush=True)
        time.sleep(0.4)

        # 포커스 검증
        try:
            fg = win32gui.GetForegroundWindow()
            if fg != chat_hwnd:
                fg_title = win32gui.GetWindowText(fg) or ""
                print(f"    [서랍] 포커스가 분리창 아님: {fg_title[:20]!r} (hwnd={fg}, expected={chat_hwnd})", flush=True)
        except Exception:
            pass

        # WindowFromPoint 로 클릭 대상 확인 (디버그) — 분리창이 아니면 경고
        click_target_is_chat = True
        try:
            target = win32gui.WindowFromPoint((menu_x, menu_y))
            root = win32gui.GetAncestor(target, 2)  # GA_ROOT
            if root != chat_hwnd:
                t_title = win32gui.GetWindowText(target) or ""
                r_title = win32gui.GetWindowText(root) or ""
                print(f"    [서랍] WindowFromPoint({menu_x},{menu_y})={target}({t_title!r}) root={root}({r_title!r}) expected={chat_hwnd}", flush=True)
                click_target_is_chat = False
        except Exception:
            pass

        if click_target_is_chat:
            # 정상: pyautogui 클릭
            pyautogui.moveTo(menu_x, menu_y, duration=0.15)
            time.sleep(0.2)
            pyautogui.click(menu_x, menu_y)
        else:
            # Z-order 문제로 다른 창이 받음 → PostMessage 로 직접 전송
            print(f"    [서랍] PostMessage 로 ≡ 클릭 시도 (hwnd={chat_hwnd})", flush=True)
            try:
                import win32api as _wapi
                import win32con as _wc
                # 스크린 좌표 → 클라이언트 좌표 변환
                client_x, client_y = win32gui.ScreenToClient(chat_hwnd, (menu_x, menu_y))
                lparam = (client_y << 16) | (client_x & 0xFFFF)
                win32gui.PostMessage(chat_hwnd, _wc.WM_LBUTTONDOWN, _wc.MK_LBUTTON, lparam)
                time.sleep(0.05)
                win32gui.PostMessage(chat_hwnd, _wc.WM_LBUTTONUP, 0, lparam)
            except Exception as e:
                print(f"    [서랍] PostMessage 실패 ({e}) → pyautogui 폴백", flush=True)
                pyautogui.click(menu_x, menu_y)
        time.sleep(1.8)  # 팝업 뜰 시간
        mark("open_drawer.click_menu", "after", {"attempt": attempt})

        # 팝업 감지: 근접 필터 + 디버그 덤프
        popup = _find_popup(near_xy=(menu_x, menu_y))
        if not popup:
            # 실패 시 모든 EVA_Menu 덤프 (디버그용)
            all_popups = []
            def _dump(h, _):
                if not win32gui.IsWindowVisible(h):
                    return
                if win32gui.GetWindowText(h):
                    return
                cls = win32gui.GetClassName(h) or ""
                if "EVA_Menu" in cls or "EVA_Window_Dblclk" in cls:
                    r = win32gui.GetWindowRect(h)
                    w, hh = r[2]-r[0], r[3]-r[1]
                    if 50 <= w <= 800 and 50 <= hh <= 900:
                        all_popups.append((h, r, cls))
            win32gui.EnumWindows(_dump, None)
            if all_popups:
                print(f"    [서랍] popup 근접매칭 실패 — EVA 후보 {len(all_popups)}개:", flush=True)
                for h, r, c in all_popups[:5]:
                    d = ((r[0]+r[2])//2 - menu_x)**2 + ((r[1]+r[3])//2 - menu_y)**2
                    print(f"       hwnd={h} cls={c} rect={r} dist={int(d**0.5)}", flush=True)
                # 거리 제한 완화해서 가장 가까운 EVA_Menu 를 popup 으로
                menu_only = [p for p in all_popups if "EVA_Menu" in p[2]]
                if menu_only:
                    closest = min(menu_only, key=lambda p: ((p[1][0]+p[1][2])//2 - menu_x)**2 + ((p[1][1]+p[1][3])//2 - menu_y)**2)
                    popup = closest[1]
                    print(f"    [서랍] 거리 완화 매칭: {popup}", flush=True)
            else:
                print(f"    [서랍] popup 체크 후: None (EVA 후보 0개)", flush=True)
        else:
            print(f"    [서랍] popup 체크 후: {popup}", flush=True)
        if popup:
            print(f"    [서랍] ≡ 클릭 성공 (시도 {attempt+1}/3)", flush=True)
            break
        if attempt < 2:
            print(f"    [서랍] 시도 {attempt+1}/3 실패, 재시도", flush=True)
    if not popup:
        # Vision 은 이미 위에서 사용 (menu_x, menu_y 는 Vision 결과) → 재시도 의미 없음
        # 여기로 왔다는 건 클릭은 했는데 팝업이 정말 안 뜬 것
        print("    [서랍] 3회 시도 모두 실패. Vision 이 찾은 좌표에서도 팝업 안 뜸.", flush=True)

    if not popup:
        print("    [서랍] ≡ 팝업 미감지 → 사진 스킵", flush=True)
        mark("open_drawer.popup_detected", "fail")
        try:
            pyautogui.press("escape")
        except Exception:
            pass
        return None
    mark("open_drawer.popup_detected", "after", {"popup": popup})

    # ─────────────────────────────────────────────
    # UIA 경로 (권장): 팝업 내 "채팅방 서랍" → "사진/동영상" 을 접근성 이름으로 직접 invoke.
    # 팝업은 표준 EVA_Menu 창이라 UIA 가 MenuItem 을 제대로 노출한다.
    # (≡ 버튼은 DirectUI 라 UIA 불가 — 픽셀로 이미 열린 상태.)
    # 실패 시 아래 기존 y+82 hover/click 로 폴백.
    # ─────────────────────────────────────────────
    uia_ok = False
    try:
        uia_ok = _try_uia_inner_nav(popup)
    except Exception as e:
        import traceback
        print(f"    [UIA] 내부 네비 예외: {e}", flush=True)
        traceback.print_exc()

    if not uia_ok:
        # ───── 기존 픽셀 경로 (폴백) ─────
        # "채팅방 서랍" hover → 서브메뉴 (실측: 팝업 y+82)
        target_x = (popup[0] + popup[2]) // 2
        target_y = popup[1] + 82
        mark("open_drawer.hover_submenu", "before", {"xy": [target_x, target_y]})
        pyautogui.moveTo(target_x, target_y, duration=0.2)
        time.sleep(1.5)
        mark("open_drawer.hover_submenu", "after")

        sub = _find_submenu()
        if not sub:
            # hover 실패 → 직접 클릭 시도
            pyautogui.click(target_x, target_y)
            time.sleep(1.5)
            sub = _find_submenu()

        if not sub:
            print("    [서랍] 서브메뉴 미감지", flush=True)
            mark("open_drawer.submenu_detected", "fail")
            pyautogui.press("escape")
            return None
        mark("open_drawer.submenu_detected", "after", {"sub": sub})

        # "사진/동영상" 클릭 (서브메뉴 첫 항목)
        px = (sub[0] + sub[2]) // 2
        py = sub[1] + 15
        mark("open_drawer.photo_tab_clicked", "before", {"xy": [px, py]})
        pyautogui.click(px, py)
        time.sleep(3.0)
        mark("open_drawer.photo_tab_clicked", "after")

    # 서랍 창 찾기
    drawer = _find_drawer_window()
    if drawer:
        print(f"    [서랍] 열림: hwnd={drawer[0]}", flush=True)
        mark("open_drawer.panel_opened", "after", {"hwnd": drawer[0]})
        return drawer[0]

    # 제목 없는 큰 패널로 열렸을 수 있음
    results = []
    def cb(hwnd, lst):
        if win32gui.IsWindowVisible(hwnd):
            t = win32gui.GetWindowText(hwnd)
            r = win32gui.GetWindowRect(hwnd)
            w, h = r[2] - r[0], r[3] - r[1]
            if not t and 300 <= w <= 500 and 500 <= h <= 800:
                lst.append((hwnd, r))
    win32gui.EnumWindows(cb, results)
    if results:
        print(f"    [서랍] 패널 열림: hwnd={results[0][0]}", flush=True)
        mark("open_drawer.panel_opened", "after", {"hwnd": results[0][0], "fallback": True})
        return results[0][0]

    print("    [서랍] 서랍 창 미감지", flush=True)
    mark("open_drawer.panel_opened", "fail")
    return None


# ═══════════════════════════════════════════════════════
# 2단계: 서랍 내 방 선택 + 사진 다운로드
# ═══════════════════════════════════════════════════════

def _save_one_bundle(v_hwnd: int) -> bool:
    """뷰어가 열린 상태에서 묶음저장 1회 실행. 성공 여부 반환."""
    # 뷰어를 안전 위치로 이동 + TOPMOST (액션 로그/Claude 등 가림 방지)
    try:
        import win32con as _wc
        vr_orig = win32gui.GetWindowRect(v_hwnd)
        vw = vr_orig[2] - vr_orig[0]
        vh = vr_orig[3] - vr_orig[1]
        # 뷰어 중심이 액션 로그 영역 (x>=1620) 안이면 왼쪽으로 이동
        vc_x = (vr_orig[0] + vr_orig[2]) // 2
        if vc_x >= 1500 or vr_orig[2] > 1600:
            # 왼쪽으로 이동 (100, 50) — 화면 좌상단
            new_x = max(50, min(1500 - vw, 100))
            win32gui.MoveWindow(v_hwnd, new_x, 50, vw, vh, True)
            time.sleep(0.3)
        # TOPMOST
        SWP = _wc.SWP_NOMOVE | _wc.SWP_NOSIZE | _wc.SWP_SHOWWINDOW
        win32gui.SetWindowPos(v_hwnd, -1, 0, 0, 0, 0, SWP)
        time.sleep(0.2)
    except Exception as e:
        print(f"    [서랍] 뷰어 위치 조정 실패 (무시): {e}", flush=True)

    _activate(v_hwnd)
    time.sleep(0.5)
    vr = win32gui.GetWindowRect(v_hwnd)
    print(f"    [서랍] 뷰어 rect={vr}", flush=True)

    # Vision 우선으로 ↓ 드롭다운 좌표 찾기 (하드코딩은 폴백).
    # 뷰어 크기는 방마다/화면 해상도마다 달라서 rect[2]-70, rect[3]-22 가
    # 안 맞는 경우가 많음. 뷰어 하단 바를 집중 스캔.
    dl_x = vr[2] - 70  # 기본값 (하드코딩 폴백)
    dl_y = vr[3] - 22
    try:
        from core.vision_clicker import find_and_click
        # 뷰어 하단 바 영역만 캡처 (하단 60px)
        bar_bbox = (vr[0], vr[3] - 60, vr[2], vr[3])
        v = find_and_click(
            bar_bbox,
            "카카오톡 사진 뷰어 하단 바의 오른쪽 끝에 있는 아래 방향 화살표(↓) "
            "드롭다운 버튼. 다운로드/저장 옵션을 여는 아이콘. "
            "X(닫기) 버튼이나 왼쪽 화살표(←/→) 이동 버튼이 아님.",
            tag="viewer.download_dropdown",
            min_confidence=0.55,
            dry_run=True,
        )
        if v.found:
            y_offset = v.y - vr[3]  # vr[3] 기준 (음수여야 정상 — 하단 바 안)
            # 하단 바 범위 내 (상대 -60 ~ -5) 인지 검증
            if -60 <= y_offset <= -5:
                dl_x, dl_y = v.x, v.y
                print(f"    [서랍] Vision ↓ 좌표: ({dl_x}, {dl_y}) conf={v.confidence:.2f}", flush=True)
            else:
                print(f"    [서랍] Vision ↓ y_offset={y_offset} 비정상 → 하드코딩", flush=True)
    except Exception as e:
        print(f"    [서랍] Vision ↓ 예외: {e} → 하드코딩", flush=True)

    mark("download.dropdown_clicked", "before", {"xy": [dl_x, dl_y]})
    pyautogui.click(dl_x, dl_y)
    time.sleep(1.5)
    mark("download.dropdown_clicked", "after")

    # "묶음사진 전체저장" 메뉴도 Vision 으로 찾기 (드롭다운 위에 나타난 메뉴)
    save_x = dl_x - 30  # 하드코딩 폴백
    save_y = dl_y - 55
    try:
        from core.vision_clicker import find_and_click
        # 드롭다운 메뉴 예상 영역: ↓ 위쪽 150x100 정도
        menu_bbox = (dl_x - 160, dl_y - 150, dl_x + 20, dl_y - 10)
        v2 = find_and_click(
            menu_bbox,
            "'묶음사진 전체저장' 또는 '전체 저장' 또는 '모두 저장' 텍스트 메뉴 항목. "
            "한 장만 저장하는 옵션이 아니라 묶음 전체를 저장하는 항목.",
            tag="viewer.batch_save",
            min_confidence=0.55,
            dry_run=True,
        )
        if v2.found:
            save_x, save_y = v2.x, v2.y
            print(f"    [서랍] Vision 묶음저장 좌표: ({save_x}, {save_y}) conf={v2.confidence:.2f}", flush=True)
    except Exception as e:
        print(f"    [서랍] Vision 묶음저장 예외: {e} → 하드코딩", flush=True)

    mark("download.batch_save_clicked", "before", {"xy": [save_x, save_y]})
    pyautogui.click(save_x, save_y)
    time.sleep(2.5)
    mark("download.batch_save_clicked", "after")

    # 다이얼로그 감지: foreground 가 아닌 EnumWindows 로 "다른 이름으로 저장" 창 찾기
    # (액션 로그 창이 포커스 탈취해도 다이얼로그 hwnd 는 별도로 존재)
    def _find_save_dialog():
        """visible + 제목에 '저장/Save/다른 이름' 포함 + 크기 > 300 → 저장 다이얼로그 hwnd."""
        found = []
        def _cb(h, _):
            if not win32gui.IsWindowVisible(h):
                return
            t = win32gui.GetWindowText(h) or ""
            if not any(k in t for k in ("다른 이름으로 저장", "Save As", "저장", "파일 저장")):
                return
            r = win32gui.GetWindowRect(h)
            w, hh = r[2]-r[0], r[3]-r[1]
            if w < 300 or hh < 200:  # 작은 창은 다이얼로그 아님
                return
            # "저장"은 너무 포괄적이라 클래스도 체크 (#32770 = Windows 표준 다이얼로그)
            cls = win32gui.GetClassName(h) or ""
            if cls == "#32770" or "다른 이름" in t or "Save As" in t:
                found.append((h, t, r))
        win32gui.EnumWindows(_cb, None)
        return found[0] if found else None

    # 저장 다이얼로그 대기 (최대 3초)
    dialog_hwnd = None
    dialog_title = ""
    for _ in range(30):
        dlg = _find_save_dialog()
        if dlg:
            dialog_hwnd, dialog_title, _ = dlg
            break
        time.sleep(0.1)

    fg = win32gui.GetForegroundWindow()
    ft = win32gui.GetWindowText(fg)
    dialog_opened = False
    print(f"    [서랍] 묶음저장 후 foreground='{ft[:60]}' / 다이얼로그 hwnd={dialog_hwnd} title={dialog_title!r}", flush=True)

    if dialog_hwnd:
        mark("download.save_dialog_opened", "after", {"title": dialog_title})
        # 다이얼로그에 명시적 포커스 → Enter
        try:
            win32gui.SetForegroundWindow(dialog_hwnd)
            time.sleep(0.2)
        except Exception:
            pass
        pyautogui.press("enter")
        time.sleep(1.0)
        mark("download.save_confirmed", "after")
        dialog_opened = True

        # Enter 후 덮어쓰기 확인 팝업 + 저장 완료 감시 (EnumWindows 기반)
        for sec in range(1, 9):
            # 덮어쓰기 확인 팝업 체크
            overwrite_hwnd = None
            def _cb_ovr(h, _):
                nonlocal overwrite_hwnd
                if overwrite_hwnd or not win32gui.IsWindowVisible(h):
                    return
                t = win32gui.GetWindowText(h) or ""
                if any(k in t for k in ("확인", "바꾸", "교체", "있습니다", "Replace", "Confirm Save")):
                    cls = win32gui.GetClassName(h) or ""
                    if cls == "#32770":
                        overwrite_hwnd = h
            win32gui.EnumWindows(_cb_ovr, None)
            if overwrite_hwnd:
                print(f"    [서랍] Enter+{sec}s 덮어쓰기 팝업 → 'Y' 입력", flush=True)
                try:
                    win32gui.SetForegroundWindow(overwrite_hwnd)
                    time.sleep(0.2)
                except Exception:
                    pass
                pyautogui.press("y")
                time.sleep(0.5)
                continue

            # 원 다이얼로그 아직 살아있는지
            if dialog_hwnd and win32gui.IsWindow(dialog_hwnd) and win32gui.IsWindowVisible(dialog_hwnd):
                if sec >= 2:
                    print(f"    [서랍] Enter+{sec}s 다이얼로그 잔존 → 추가 Enter", flush=True)
                    try:
                        win32gui.SetForegroundWindow(dialog_hwnd)
                        time.sleep(0.2)
                    except Exception:
                        pass
                    pyautogui.press("enter")
                    time.sleep(1.0)
            else:
                # 다이얼로그 닫힘
                break
    else:
        mark("download.save_dialog_opened", "fail", {"title": ft})
        print(f"    [서랍] 저장 다이얼로그 미감지 (EnumWindows 스캔 3초 결과 없음)", flush=True)

    # 호출자가 파일 스냅샷으로 확정 판정
    return dialog_opened


def download_photos_from_drawer(
    drawer_hwnd: int,
    room_key: str = "",
    *,
    verify_room: bool = True,
    max_bundles: int = 9,
) -> list[Path]:
    """
    서랍 사진 그리드에서 **여러 묶음**을 순차적으로 다운로드.

    동작:
      1. 그리드 3x3 = 9개 셀 위치 순회
      2. 각 셀 더블클릭 → 뷰어 열리면 묶음저장 → 뷰어 닫기
      3. 뷰어 안 뜨면 빈 셀로 스킵
      4. 이미 저장된 파일은 스냅샷 비교로 자동 중복 제거

    Args:
        room_key: 파일 리네임 식별자
        verify_room: OCR breadcrumb 검증
        max_bundles: 최대 처리할 묶음 수 (기본 9 = 3x3 그리드)
    """
    # ── 방 이름 검증 ──
    if verify_room and room_key:
        if not verify_drawer_room(drawer_hwnd, room_key):
            print(f"    [서랍] '{room_key}' 불일치 — 좌측에서 찾기 시도", flush=True)
            if not select_room_in_drawer_by_name(drawer_hwnd, room_key):
                print(f"    [서랍] '{room_key}' 선택 실패 → 다운로드 중단", flush=True)
                return []

    before_all = _snapshot_downloads()
    dr = win32gui.GetWindowRect(drawer_hwnd)
    dw, dh = dr[2] - dr[0], dr[3] - dr[1]

    # 그리드 셀 좌표 생성 (3열 x 3행, 서랍 우측 패널)
    # 좌측 패널 ~230px, 탭 헤더 ~200px
    grid_x0 = 235
    grid_y0 = 225
    cell_dx = max((dw - grid_x0 - 30) // 3, 100)
    cell_dy = max((dh - grid_y0 - 30) // 3, 100)
    positions = [
        (dr[0] + grid_x0 + col * cell_dx + cell_dx // 2,
         dr[1] + grid_y0 + row * cell_dy + cell_dy // 2)
        for row in range(3) for col in range(3)
    ]

    all_new: list[Path] = []
    bundles_done = 0
    seen_viewers: set[str] = set()  # 같은 뷰어 제목이면 같은 묶음 → 스킵

    for idx, (px, py) in enumerate(positions[:max_bundles]):
        before = _snapshot_downloads()

        mark("download.photo_dblclick", "before", {"xy": [px, py], "cell": idx, "room": room_key})
        pyautogui.doubleClick(px, py)
        time.sleep(2.5)
        mark("download.photo_dblclick", "after")

        viewer = _find_viewer()
        if not viewer:
            continue  # 빈 셀

        v_hwnd, v_title, _ = viewer

        # 같은 뷰어 제목 = 같은 묶음의 다른 사진 → 이미 저장했으니 스킵
        if v_title in seen_viewers:
            pyautogui.press("escape")
            time.sleep(0.3)
            continue
        seen_viewers.add(v_title)

        mark("download.viewer_detected", "after", {"title": v_title, "cell": idx})
        print(f"    [서랍] [{idx+1}] 뷰어: {v_title}", flush=True)

        # 묶음저장
        dialog_ok = _save_one_bundle(v_hwnd)
        bundles_done += 1

        # 뷰어 닫기
        pyautogui.press("escape")
        time.sleep(0.5)

        # 이번 셀에서 새로 받은 파일 (최대 8초 폴링 — 묶음/대용량 대비)
        cell_new: list[Path] = []
        if dialog_ok:
            for _ in range(8):
                after = _snapshot_downloads()
                cell_new = sorted(
                    [Path(f) for f in (after - before)],
                    key=lambda p: p.stat().st_mtime,
                )
                if cell_new:
                    break
                time.sleep(1.0)
        all_new.extend(cell_new)
        if cell_new:
            print(f"    [서랍] [{idx+1}] +{len(cell_new)}장 (누적 {len(all_new)})", flush=True)
        else:
            reason = "다이얼로그 미감지" if not dialog_ok else "저장 후 새 파일 없음 (폴링 8초)"
            print(f"    [서랍] [{idx+1}] 다운로드 실패 ({reason})", flush=True)

    # ★ 유니크 파일명으로 즉시 rename
    renamed: list[Path] = []
    if all_new and room_key:
        safe_room = re.sub(r"[^\w가-힣-]", "_", room_key)
        ts = int(time.time() * 1000)
        for i, f in enumerate(all_new):
            new_name = f"PHOTO_{safe_room}__{ts}_{i:02d}{f.suffix}"
            try:
                new_path = f.parent / new_name
                f.rename(new_path)
                renamed.append(new_path)
            except Exception as e:
                print(f"    [서랍] rename 실패: {e}", flush=True)
                renamed.append(f)
        all_new = renamed

    print(f"    [서랍] 총 {len(all_new)}장 다운로드 ({bundles_done}묶음)", flush=True)
    return all_new


def select_room_in_drawer(drawer_hwnd: int, room_index: int) -> bool:
    """서랍 좌측 리스트에서 N번째 방 클릭."""
    dr = win32gui.GetWindowRect(drawer_hwnd)
    # 좌측 리스트 영역: x=dr[0]+115, 항목 높이 ~47px, 첫 항목 y=dr[1]+72
    list_x = dr[0] + 115
    item_y = dr[1] + 72 + (room_index * 47)

    # 서랍 영역 밖이면 스크롤 필요
    if item_y > dr[3] - 30:
        pyautogui.moveTo(list_x, dr[1] + 300)
        pyautogui.scroll(-3)
        time.sleep(0.5)
        # 스크롤 후 재계산 (간단히 고정 오프셋)
        item_y -= 140

    pyautogui.click(list_x, item_y)
    time.sleep(1.5)
    return True


# ═══════════════════════════════════════════════════════
# 3단계: 전체 방 순회 다운로드 (서랍 1회 열기)
# ═══════════════════════════════════════════════════════

def download_all_rooms_photos(chat_hwnd: int, room_names: list[str]) -> dict[str, list[Path]]:
    """
    서랍 1회 열기 → 좌측 리스트에서 방 순회 → 사진 다운로드.

    Args:
        chat_hwnd: 아무 채팅방 hwnd (서랍 열기용)
        room_names: 다운로드 대상 방 이름 리스트

    Returns:
        {방이름: [다운로드된 파일]} dict
    """
    drawer = open_drawer(chat_hwnd)
    if not drawer:
        return {}

    results: dict[str, list[Path]] = {}
    total = len(room_names)

    for idx, room_name in enumerate(room_names):
        print(f"  [{idx+1}/{total}] {room_name} 사진...", flush=True)
        try:
            # 좌측 리스트에서 방 선택
            select_room_in_drawer(drawer, idx)
            time.sleep(1.0)

            # 사진 다운로드 (방 키로 리네임)
            files = download_photos_from_drawer(drawer, room_key=room_name)
            results[room_name] = files

        except Exception as e:
            print(f"    [{room_name}] 예외: {e}", flush=True)
            results[room_name] = []

    # 서랍 닫기
    for _ in range(3):
        pyautogui.press("escape")
        time.sleep(0.2)

    return results


# ═══════════════════════════════════════════════════════
# 레거시 호환 (이전 API)
# ═══════════════════════════════════════════════════════

def extract_photos_from_room(
    chat_hwnd: int,
    photo_count: int = 0,
    room_name: str = "",
    *,
    verify_room: bool | None = None,
) -> list[Path]:
    """단일 방 사진 추출. 서랍 방식으로 동작.

    Args:
        chat_hwnd: 채팅방 창 hwnd (서랍 열기 기준)
        photo_count: 기대 사진 수 (현재는 로그용)
        room_name: 방 이름. 제공 시 OCR로 breadcrumb 검증 + 좌측 재선택.
                   미제공이면 검증 없이 첫 사진 다운로드 (하위호환).
        verify_room: None=자동(분리창 제목이 room_name과 일치하면 False, 아니면 True),
                     True/False=명시 강제. 분리창 hwnd로 호출되면 win32 검증이 이미
                     끝났으므로 OCR 재검증 불필요.
    """
    drawer = open_drawer(chat_hwnd)
    if not drawer:
        return []

    # ── verify 자동 결정: 분리창 제목 = room_name이면 검증 스킵 ──
    if verify_room is None:
        try:
            chat_title = win32gui.GetWindowText(chat_hwnd) or ""
            nt = chat_title.replace(" ", "")
            nr = (room_name or "").replace(" ", "")
            if room_name and nt and nt != "카카오톡" and (nt == nr or nr in nt or nt in nr):
                verify_room = False
                print(f"    [서랍] win32 제목 매칭 OK ('{chat_title}') - OCR 검증 스킵", flush=True)
            else:
                verify_room = bool(room_name)
        except Exception:
            verify_room = bool(room_name)

    files = download_photos_from_drawer(
        drawer,
        room_key=room_name,
        verify_room=verify_room,
    )
    # 서랍 닫기
    for _ in range(3):
        pyautogui.press("escape")
        time.sleep(0.2)
    return files


def close_drawer():
    for _ in range(3):
        pyautogui.press("escape")
        time.sleep(0.3)
