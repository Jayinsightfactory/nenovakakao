"""
화면 정체 자동 감지 + 캡쳐 + 원인 파악 + 자동 회복.

설계:
  StallTracker 가 매 액션 결과 (no_change / changed) 를 받음.
  N 회 연속 no_change → stall 로 판단:
    1) 전체 화면 + 카톡 메인 캡쳐 (captures/stall/<ts>_*.png)
    2) 포그라운드 창 / 새 창 분석 → blocking dialog 식별
    3) automation_rules.yaml 의 known_dialogs 매칭 시 recovery_keys 자동 실행
    4) 알려지지 않은 정체 → 캡쳐 path + 컨텍스트 반환 (caller 가 stop 또는 LLM 분석)

호출자는 stall info 받으면:
  - recovery_applied=True 면 한 번 더 재시도
  - False 면 캡쳐 보고 + 정지

캡쳐 정보는 data/stall_log.jsonl 에 누적 — 다음 세션의 학습 자료.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STALL_LOG = ROOT / "data" / "stall_log.jsonl"
STALL_CAP_DIR = ROOT / "captures" / "stall"

# 알려진 blocking 다이얼로그 (카톡 PC 에서 흔히 뜨는 거)
BLOCKING_TITLE_CONTAINS = (
    "열기", "다른 이름으로 저장", "저장", "다운로드",
    "친구 추가", "통합검색",
    "확인", "경고", "오류",
)


@dataclass
class StallInfo:
    is_stall: bool
    reason: str = ""
    foreground_title: str = ""
    new_windows: list[str] = field(default_factory=list)
    capture_path: str = ""
    recovery_keys: list[str] = field(default_factory=list)
    recovery_applied: bool = False


class StallTracker:
    """N 회 연속 no_change 누적 → stall 감지."""

    def __init__(self, threshold: int = 3, label: str = "stall"):
        self.threshold = threshold
        self.label = label
        self.consecutive = 0
        self.initial_titles: set[str] | None = None

    def reset(self) -> None:
        self.consecutive = 0

    def init_baseline(self, titles: set[str]) -> None:
        """시작 시 떠있는 창 = 보호 대상 baseline."""
        self.initial_titles = set(titles)

    def record_change(self) -> None:
        """변화 있는 액션 — 카운터 리셋."""
        self.consecutive = 0

    def record_no_change(self) -> StallInfo | None:
        """변화 없는 액션 — 카운터 증가. 임계 도달 시 StallInfo 반환."""
        self.consecutive += 1
        if self.consecutive < self.threshold:
            return None
        info = analyze_stall(self.label, self.initial_titles or set())
        if info.is_stall:
            self.consecutive = 0  # 분석/회복 시도 후 리셋
        return info


def _list_visible_windows() -> list[tuple[int, str]]:
    """visible + 크기 있는 모든 창."""
    import win32gui
    out: list[tuple[int, str]] = []

    def _cb(h, _):
        try:
            if not win32gui.IsWindowVisible(h):
                return
            t = win32gui.GetWindowText(h) or ""
            if not t:
                return
            r = win32gui.GetWindowRect(h)
            if (r[2] - r[0]) < 50 or (r[3] - r[1]) < 50:
                return
            out.append((h, t))
        except Exception:
            pass

    try:
        win32gui.EnumWindows(_cb, None)
    except Exception:
        pass
    return out


def _capture_screen(label: str) -> str:
    """전체 화면 캡쳐 → captures/stall/<ts>_<label>.png. path 반환."""
    try:
        from PIL import ImageGrab
    except ImportError:
        return ""
    STALL_CAP_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    path = STALL_CAP_DIR / f"{ts}_{label}.png"
    try:
        img = ImageGrab.grab()
        img.save(path, optimize=True)
        return str(path)
    except Exception as e:
        print(f"  [STALL-CAP] 캡쳐 실패: {e}", flush=True)
        return ""


def analyze_stall(label: str, baseline_titles: set[str]) -> StallInfo:
    """현재 화면 상태 분석 + 캡쳐 + 알려진 다이얼로그면 회복.

    Args:
        label: 캡쳐 파일명 식별자
        baseline_titles: 시작 시 떠있던 창 — 새 창 차분에서 제외
    """
    import win32gui

    cap_path = _capture_screen(label)

    fg_title = ""
    try:
        fg = win32gui.GetForegroundWindow()
        fg_title = win32gui.GetWindowText(fg) or ""
    except Exception:
        pass

    visible = _list_visible_windows()
    visible_titles = {t for _, t in visible}
    new_windows = sorted(visible_titles - baseline_titles)

    # blocking dialog 감지
    blocker = ""
    for kw in BLOCKING_TITLE_CONTAINS:
        if fg_title and kw in fg_title:
            blocker = fg_title
            break
        for t in new_windows:
            if kw in t:
                blocker = t
                break
        if blocker:
            break

    info = StallInfo(
        is_stall=True,
        reason="blocking_dialog" if blocker else "no_response",
        foreground_title=fg_title,
        new_windows=new_windows,
        capture_path=cap_path,
    )
    if blocker:
        # 자동 회복: ESC 시도
        info.recovery_keys = ["escape"]
        try:
            import pyautogui
            pyautogui.press("escape")
            time.sleep(0.4)
            info.recovery_applied = True
        except Exception as e:
            print(f"  [STALL-RECOVER] ESC 실패: {e}", flush=True)
        # 한 번 더 캡쳐 — 회복 후 상태
        info.capture_path = cap_path  # 회복 전 캡쳐 보존

    # 로그
    try:
        STALL_LOG.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": round(time.time(), 2),
            "label": label,
            "reason": info.reason,
            "fg_title": fg_title,
            "new_windows": new_windows[:10],
            "blocker": blocker,
            "capture": cap_path,
            "recovery_applied": info.recovery_applied,
        }
        with open(STALL_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass

    print(
        f"  🚨 [STALL] reason={info.reason} fg={fg_title!r} "
        f"new_windows={new_windows[:3]} recovery={info.recovery_applied}",
        flush=True,
    )
    if cap_path:
        print(f"  📸 캡쳐: {cap_path}", flush=True)

    return info
