"""
측정된 레이아웃(`data/drawer_layout.json`) 기반 서랍 자동화.

기존 `drawer_handler.py`의 개별 사진 더블클릭 방식 대신
**일괄 체크 → 한 번에 다운로드**를 수행한다.

전제조건:
  - `measure_drawer_layout.py` 실행 후 `data/drawer_layout.json` 생성됨
  - 서랍이 측정 시점과 동일한 위치/크기로 락 가능 (lock_drawer_to_layout)
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pyautogui
import pygetwindow as gw
import win32con
import win32gui

from core.traced_actions import mark

ROOT = Path(__file__).parent.parent
LAYOUT_FILE = ROOT / "data" / "drawer_layout.json"
KAKAO_DOWNLOAD_DIR = Path("C:/Users/USER/Documents/카카오톡 받은 파일")


def _status(msg: str) -> None:
    """오버레이 상태 업데이트 (실패 무시)."""
    try:
        from core.status_overlay import get_overlay
        get_overlay().set_status(msg)
    except Exception:
        pass


# ═══════════════════════════════════════════════════════
# 레이아웃 로드
# ═══════════════════════════════════════════════════════

_cached_layout: dict | None = None


def load_layout(force_reload: bool = False) -> dict:
    """drawer_layout.json 로드 (캐시)."""
    global _cached_layout
    if _cached_layout is None or force_reload:
        if not LAYOUT_FILE.exists():
            raise FileNotFoundError(
                f"{LAYOUT_FILE} 없음 — `measure_drawer_layout.py` 먼저 실행"
            )
        with open(LAYOUT_FILE, encoding="utf-8") as f:
            _cached_layout = json.load(f)
    return _cached_layout


# ═══════════════════════════════════════════════════════
# 서랍 창 감지/락
# ═══════════════════════════════════════════════════════

def find_drawer_hwnd() -> int | None:
    """서랍 창 hwnd 찾기 (제목 '채팅방 서랍' 또는 큰 무제목 창)."""
    results: list[int] = []

    def _by_title(hwnd, lst):
        if win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd) == "채팅방 서랍":
            lst.append(hwnd)

    win32gui.EnumWindows(_by_title, results)
    if results:
        return results[0]

    # fallback: 큰 창 중 카톡 메인이 아닌 것
    candidates: list[tuple[int, int]] = []

    def _by_size(hwnd, lst):
        if not win32gui.IsWindowVisible(hwnd):
            return
        t = win32gui.GetWindowText(hwnd)
        if t == "카카오톡":
            return
        r = win32gui.GetWindowRect(hwnd)
        w, h = r[2] - r[0], r[3] - r[1]
        if w >= 600 and h >= 500 and w >= h:
            lst.append((hwnd, w * h))

    win32gui.EnumWindows(_by_size, candidates)
    if not candidates:
        return None
    return max(candidates, key=lambda x: x[1])[0]


def lock_drawer_to_layout() -> bool:
    """서랍 창을 layout 위치/크기로 강제 락 + TOPMOST."""
    hwnd = find_drawer_hwnd()
    if not hwnd:
        mark("drawer.lock", "fail", {"reason": "hwnd not found"})
        return False

    layout = load_layout()
    d = layout["drawer"]

    try:
        placement = win32gui.GetWindowPlacement(hwnd)
        if placement[1] == win32con.SW_SHOWMINIMIZED:
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            time.sleep(0.2)
        win32gui.MoveWindow(hwnd, d["left"], d["top"], d["width"], d["height"], True)
        time.sleep(0.3)
        # TOPMOST로 다른 창이 위로 못 오게
        SWP = 0x0002 | 0x0001 | 0x0040  # NOMOVE | NOSIZE | SHOWWINDOW
        try:
            win32gui.SetWindowPos(hwnd, -1, 0, 0, 0, 0, SWP)
        except Exception as e:
            print(f"  [DRAWER-LOCK] SetWindowPos 실패 (무시): {e}", flush=True)
        time.sleep(0.1)
        # SetForegroundWindow는 실패할 수 있음 (Windows 규칙) — 무시
        try:
            win32gui.SetForegroundWindow(hwnd)
        except Exception as e:
            print(f"  [DRAWER-LOCK] SetForegroundWindow 실패 (무시, TOPMOST로 충분): {e}", flush=True)
        time.sleep(0.2)
        mark("drawer.lock", "after", {"rect": [d["left"], d["top"], d["width"], d["height"]]})
        return True
    except Exception as e:
        mark("drawer.lock", "fail", {"error": str(e)})
        print(f"  [DRAWER-LOCK] 실패: {e}", flush=True)
        return False


# ═══════════════════════════════════════════════════════
# 탭 클릭
# ═══════════════════════════════════════════════════════

def click_tab(tab: str) -> None:
    """탭 클릭: 'photo' | 'file' | 'link'."""
    layout = load_layout()
    t = layout["tabs"].get(tab)
    if not t:
        raise ValueError(f"unknown tab: {tab}")
    mark("drawer.click_tab", "before", {"tab": tab, "xy": [t["x"], t["y"]]})
    pyautogui.click(t["x"], t["y"])
    time.sleep(1.0)  # 탭 전환 로딩
    mark("drawer.click_tab", "after", {"tab": tab})


# ═══════════════════════════════════════════════════════
# 체크박스 좌표 계산 + 클릭
# ═══════════════════════════════════════════════════════

def checkbox_positions(kind: str, n: int) -> list[tuple[int, int]]:
    """kind: 'photo'|'file', n개 체크박스 좌표 리스트.

    그리드 최대치 초과 시 잘라냄.
    """
    layout = load_layout()
    grid = layout[f"{kind}_grid"]
    cols = grid["cols"]
    rows = grid["rows"]
    col_xs = grid["col_xs"]
    y0 = grid["row_y_start"]
    step = grid["row_step"]
    max_n = cols * rows

    if n > max_n:
        print(f"  [WARN] {kind} 요청 {n} > 최대 {max_n}, 스크롤 필요 — 일단 {max_n}만 처리")
        n = max_n

    positions: list[tuple[int, int]] = []
    for i in range(n):
        row = i // cols
        col = i % cols
        x = col_xs[col]
        y = y0 + row * step
        positions.append((x, y))
    return positions


def check_items(kind: str, n: int) -> int:
    """N개 체크박스 순차 클릭. 매 클릭 전 서랍 foreground 재확인."""
    positions = checkbox_positions(kind, n)
    mark(f"drawer.check_{kind}", "before", {"requested": n, "planned": len(positions)})

    hwnd = find_drawer_hwnd()
    for i, (x, y) in enumerate(positions, 1):
        # 매 클릭 전 서랍 foreground + TOPMOST 재확인 (Chrome 등 간섭 방지)
        if hwnd:
            try:
                SWP = 0x0002 | 0x0001 | 0x0040
                win32gui.SetWindowPos(hwnd, -1, 0, 0, 0, 0, SWP)
                win32gui.SetForegroundWindow(hwnd)
            except Exception:
                pass
        pyautogui.click(x, y)
        time.sleep(0.25)
    mark(f"drawer.check_{kind}", "after", {"clicked": len(positions)})
    return len(positions)


# ═══════════════════════════════════════════════════════
# 다운로드
# ═══════════════════════════════════════════════════════

def _snapshot_downloads() -> set[str]:
    """다운로드 폴더 파일 스냅샷."""
    if not KAKAO_DOWNLOAD_DIR.exists():
        return set()
    return {str(p) for p in KAKAO_DOWNLOAD_DIR.iterdir() if p.is_file()}


def click_download_and_confirm(max_wait: int = 15) -> list[Path]:
    """다운로드 버튼 클릭 → 저장 다이얼로그 Enter → 새 파일 수집.

    Args:
        max_wait: 다운로드 완료 대기 최대 초

    Returns:
        새로 저장된 파일 리스트 (mtime 순)
    """
    layout = load_layout()
    dl = layout["download"]

    before = _snapshot_downloads()

    # 다운로드 클릭 직전 서랍 foreground 재확인
    try:
        hwnd = find_drawer_hwnd()
        if hwnd:
            SWP = 0x0002 | 0x0001 | 0x0040
            win32gui.SetWindowPos(hwnd, -1, 0, 0, 0, 0, SWP)
            win32gui.SetForegroundWindow(hwnd)
            time.sleep(0.2)
    except Exception:
        pass

    mark("drawer.download_click", "before", {"xy": [dl["x"], dl["y"]]})
    print(f"  [DRAWER] ↓ 다운로드 버튼 클릭: ({dl['x']}, {dl['y']})", flush=True)
    pyautogui.click(dl["x"], dl["y"])
    time.sleep(2.0)
    mark("drawer.download_click", "after")

    # 저장 다이얼로그 대응
    fg = win32gui.GetForegroundWindow()
    ft = win32gui.GetWindowText(fg)
    print(f"  [DRAWER] 다이얼로그 체크: foreground='{ft}'", flush=True)
    if any(k in ft for k in ["저장", "Save", "다른 이름"]):
        mark("drawer.save_dialog", "after", {"title": ft})
        pyautogui.press("enter")
        time.sleep(1.5)

        # 덮어쓰기 팝업 대응 (Y 눌러서 덮어쓰기)
        for sec in range(1, 6):
            fg2 = win32gui.GetForegroundWindow()
            ft2 = win32gui.GetWindowText(fg2)
            if any(k in ft2 for k in ["바꾸", "교체", "있습니다", "Replace"]):
                pyautogui.press("y")
                time.sleep(0.5)
                break
            time.sleep(1.0)

    # 새 파일 수집 (max_wait초 대기)
    new_files: list[Path] = []
    for _ in range(max_wait):
        after = _snapshot_downloads()
        new_paths = [Path(p) for p in (after - before)]
        if new_paths:
            # 다운로드 진행 중 파일은 크기가 변하므로 1초 더 기다려 안정화
            time.sleep(1.0)
            after = _snapshot_downloads()
            new_paths = [Path(p) for p in (after - before)]
            new_files = sorted(new_paths, key=lambda p: p.stat().st_mtime)
            break
        time.sleep(1.0)

    mark("drawer.download_done", "after", {"count": len(new_files)})
    return new_files


# ═══════════════════════════════════════════════════════
# 상위 API: 사진/파일 N개 다운로드 (서랍이 이미 열려있어야 함)
# ═══════════════════════════════════════════════════════

def download_n_from_drawer(kind: str, n: int) -> list[Path]:
    """서랍이 열려있는 상태에서 kind(photo/file) N개 체크 → 다운로드."""
    if n <= 0:
        return []

    # 1. 서랍 락
    _status(f"서랍 창 락 ({kind})")
    hwnd = find_drawer_hwnd()
    if hwnd:
        r_before = win32gui.GetWindowRect(hwnd)
        print(f"  [DRAWER] 락 전 서랍 rect={r_before}", flush=True)
    if not lock_drawer_to_layout():
        print(f"  [DRAWER] 서랍 창 락 실패 → 다운로드 중단", flush=True)
        return []
    if hwnd:
        r_after = win32gui.GetWindowRect(hwnd)
        layout = load_layout()
        d = layout["drawer"]
        expected = (d["left"], d["top"], d["left"]+d["width"], d["top"]+d["height"])
        print(f"  [DRAWER] 락 후 서랍 rect={r_after} / 기대={expected}", flush=True)

    # 2. 탭 클릭
    _status(f"{kind} 탭 클릭")
    click_tab(kind)

    # 3. N개 체크
    _status(f"체크박스 {n}개 클릭 중")
    clicked = check_items(kind, n)
    print(f"  [DRAWER] 체크박스 {clicked}개 클릭 완료", flush=True)
    if clicked == 0:
        print(f"  [DRAWER] 체크 실패", flush=True)
        return []

    # 4. 다운로드
    _status(f"↓ 다운로드 ({clicked}개)")
    files = click_download_and_confirm()
    print(f"  [DRAWER] {kind} {clicked}개 체크 → {len(files)}개 다운로드", flush=True)
    _status(f"다운로드 완료: {len(files)}개")
    return files


# ═══════════════════════════════════════════════════════
# 레이아웃 검증 (디버그용)
# ═══════════════════════════════════════════════════════

def verify_layout_matches() -> bool:
    """현재 서랍 창 위치/크기가 layout과 일치하는지 확인."""
    hwnd = find_drawer_hwnd()
    if not hwnd:
        return False
    r = win32gui.GetWindowRect(hwnd)
    w, h = r[2] - r[0], r[3] - r[1]
    d = load_layout()["drawer"]
    return (r[0], r[1], w, h) == (d["left"], d["top"], d["width"], d["height"])


# ═══════════════════════════════════════════════════════
# 통합 API: 기존 extract_photos_from_room 드롭인 대체
# ═══════════════════════════════════════════════════════

def extract_photos_from_chat_via_layout(
    chat_hwnd: int,
    photo_count: int = 0,
    room_name: str = "",
) -> list[Path]:
    """기존 `drawer_handler.extract_photos_from_room`의 드롭인 대체.

    Flow:
      1. chat_hwnd 에서 ≡ → 채팅방 서랍 → 사진/동영상 (기존 open_drawer)
      2. 서랍 창을 layout에 맞게 락
      3. N개 체크박스 클릭
      4. 다운로드 → 파일 수집
      5. ESC로 서랍 닫기
    """
    if photo_count <= 0:
        return []

    # ── 1. 서랍 열기: UIA → 픽셀(Vision) 폴백 체인 ──
    _status("서랍 열기 (UIA 시도)")
    drawer_hwnd = None
    try:
        from core.drawer_uia import open_drawer_uia
        drawer_hwnd = open_drawer_uia(chat_hwnd)
        if drawer_hwnd:
            print(f"  [DRAWER-V2] UIA 경로 성공: hwnd={drawer_hwnd}", flush=True)
    except Exception as e:
        print(f"  [DRAWER-V2] UIA 경로 예외: {e}", flush=True)

    if not drawer_hwnd:
        _status("서랍 메뉴 열기 (≡ 픽셀/Vision 폴백)")
        try:
            from core.drawer_handler import open_drawer
        except Exception as e:
            print(f"  [DRAWER-V2] open_drawer import 실패: {e}", flush=True)
            return []

        drawer_hwnd = open_drawer(chat_hwnd)

    if not drawer_hwnd:
        print(f"  [DRAWER-V2] 서랍 열기 실패 (UIA + 픽셀 모두)", flush=True)
        _status("서랍 열기 실패")
        return []

    time.sleep(0.5)

    try:
        # drawer_hwnd 유효성 체크 (하위 로직들이 이 hwnd를 써야 함)
        if not win32gui.IsWindow(drawer_hwnd):
            print(f"  [DRAWER-V2] drawer_hwnd 무효 → 재탐색", flush=True)
            drawer_hwnd = find_drawer_hwnd()
            if not drawer_hwnd:
                print(f"  [DRAWER-V2] 서랍 창 찾기 실패", flush=True)
                return []

        # 사진 다운로드: 더블클릭 묶음저장 방식이 안정적으로 검증됨.
        # layout 체크박스 방식은 Z-order/포커스 이슈로 불안정 — 사용 안 함.
        _status("더블클릭 묶음저장 방식")
        print(f"  [DRAWER-V2] 더블클릭 묶음저장 (drawer_hwnd={drawer_hwnd})", flush=True)
        try:
            # 서랍이 layout 위치에 있어야 더블클릭 좌표가 맞음
            lock_drawer_to_layout()
            time.sleep(0.5)

            from core.drawer_handler import download_photos_from_drawer
            files = download_photos_from_drawer(
                drawer_hwnd,
                room_key=room_name,
                verify_room=False,  # 이미 올바른 방 선택됨
                max_bundles=max(photo_count, 3),
            )
            print(f"  [DRAWER-V2] 더블클릭 결과: {len(files)}장", flush=True)
            return files
        except Exception as e:
            print(f"  [DRAWER-V2] 더블클릭 예외: {e}", flush=True)
            return []
    finally:
        # 5. 서랍 닫기 (ESC 2회: 서랍 → 카톡 본창)
        try:
            pyautogui.press("escape")
            time.sleep(0.3)
        except Exception:
            pass


if __name__ == "__main__":
    # 스탠드얼론 테스트: 서랍 열린 상태에서 좌표 검증만 수행 (클릭 안 함)
    import sys
    if not LAYOUT_FILE.exists():
        print(f"[ERROR] {LAYOUT_FILE} 없음 — measure_drawer_layout.py 먼저 실행")
        sys.exit(1)

    print(f"[TEST] layout 로드 중...")
    layout = load_layout()
    print(f"  drawer: ({layout['drawer']['left']},{layout['drawer']['top']}) "
          f"{layout['drawer']['width']}x{layout['drawer']['height']}")
    print(f"  tabs: {layout['tabs']}")
    print(f"  photo_grid: {layout['photo_grid']['cols']} x {layout['photo_grid']['rows']}")
    print(f"  file_grid:  {layout['file_grid']['cols']} x {layout['file_grid']['rows']}")
    print(f"  download: ({layout['download']['x']},{layout['download']['y']})")

    hwnd = find_drawer_hwnd()
    if hwnd:
        r = win32gui.GetWindowRect(hwnd)
        w, h = r[2] - r[0], r[3] - r[1]
        print(f"\n[TEST] 현재 서랍 창: hwnd={hwnd} ({r[0]},{r[1]}) {w}x{h}")
        print(f"       layout 일치: {verify_layout_matches()}")
    else:
        print("\n[TEST] 서랍 창 미감지 — 서랍을 연 상태에서 실행하세요.")

    print("\n[TEST] 사진 1~9 체크박스 예상 좌표:")
    for i, (x, y) in enumerate(checkbox_positions("photo", 9), 1):
        print(f"  P{i}: ({x}, {y})")

    print("\n[TEST] 파일 1~6 체크박스 예상 좌표:")
    for i, (x, y) in enumerate(checkbox_positions("file", 6), 1):
        print(f"  F{i}: ({x}, {y})")
