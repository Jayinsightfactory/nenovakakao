"""
자동화 액션 직후 부작용 자동 감지 + 회복 + 학습.

핵심 흐름:
    before = capture_state()
    do_action()                # click / paste / hotkey
    after  = capture_state()
    diag   = diagnose(before, after)
    if diag.has_side_effect:
        recover(diag)          # ESC 등 회복 액션
        learn(intent, coord, diag)   # automation_rules.yaml 자동 진화
        if diag.should_halt:
            raise SideEffectDetected

룰북: `data/automation_rules.yaml`
사고 로그: `data/side_effect_log.jsonl`

이 모듈은 read/write 둘 다 함:
  - read: 매 호출마다 yaml 재로드 (mtime cache) → 룰북 핫리로드
  - write: 새 부작용 발견 시 룰북에 항목 자동 추가 + 영구 기록
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
RULES_PATH = ROOT / "data" / "automation_rules.yaml"
LOG_PATH = ROOT / "data" / "side_effect_log.jsonl"


class SideEffectDetected(Exception):
    """halt=true 인 부작용이 감지됐을 때 호출자가 즉시 중단하도록."""


@dataclass
class StateSnapshot:
    """순간 화면 상태."""
    fg_title: str
    visible_titles: frozenset[str]
    timestamp: float


@dataclass
class Diagnosis:
    has_side_effect: bool
    side_effect_kind: str  # "friend_add" | "unified_search" | "unknown_window" | "none"
    severity: str          # "critical" | "high" | "low" | "ignore" | "none"
    detected_title: str
    recovery_keys: list[str] = field(default_factory=list)
    should_halt: bool = False
    new_window_titles: list[str] = field(default_factory=list)
    matched_rule: str = ""  # 어느 룰에 매칭됐는지


# ─────────────────────────────────────────────
# 룰북 로딩 (mtime cache)
# ─────────────────────────────────────────────
_rules_cache: dict[str, Any] = {}
_rules_mtime: float = 0.0
_rules_lock = RLock()


def load_rules() -> dict[str, Any]:
    """yaml 룰북 로드. mtime 바뀌었을 때만 재읽기."""
    global _rules_cache, _rules_mtime
    with _rules_lock:
        if not RULES_PATH.exists():
            _rules_cache = {
                "forbidden_coords": [],
                "forbidden_sequences": [],
                "known_dialogs": [],
                "protected_windows": {"exact": [], "contains": []},
            }
            return _rules_cache
        try:
            mt = RULES_PATH.stat().st_mtime
        except FileNotFoundError:
            return _rules_cache or {}
        if mt == _rules_mtime and _rules_cache:
            return _rules_cache
        try:
            with open(RULES_PATH, encoding="utf-8") as f:
                _rules_cache = yaml.safe_load(f) or {}
            _rules_mtime = mt
        except Exception as e:
            print(f"  [RULES] yaml 로드 실패: {e}", flush=True)
            return _rules_cache or {}
        return _rules_cache


def _save_rules(rules: dict[str, Any]) -> None:
    """yaml 룰북 저장. 다음 load_rules 가 자동으로 mtime 재인식."""
    global _rules_mtime
    with _rules_lock:
        try:
            RULES_PATH.write_text(
                yaml.safe_dump(rules, allow_unicode=True, sort_keys=False, indent=2),
                encoding="utf-8",
            )
            _rules_mtime = RULES_PATH.stat().st_mtime
        except Exception as e:
            print(f"  [RULES] yaml 저장 실패: {e}", flush=True)


# ─────────────────────────────────────────────
# 상태 캡쳐
# ─────────────────────────────────────────────
def capture_state() -> StateSnapshot:
    """현재 포그라운드 title + 모든 visible 창 title 스냅샷."""
    try:
        import win32gui
    except ImportError:
        return StateSnapshot(fg_title="", visible_titles=frozenset(), timestamp=time.time())

    fg = win32gui.GetForegroundWindow()
    fg_title = ""
    if fg:
        try:
            fg_title = win32gui.GetWindowText(fg) or ""
        except Exception:
            fg_title = ""

    titles: set[str] = set()

    def _cb(h, _):
        try:
            if not win32gui.IsWindowVisible(h):
                return
            t = win32gui.GetWindowText(h) or ""
            if not t:
                return
            r = win32gui.GetWindowRect(h)
            w, hh = r[2] - r[0], r[3] - r[1]
            # 너무 작은 창 (1x1 placeholder 등) 제외
            if w < 50 or hh < 50:
                return
            titles.add(t)
        except Exception:
            pass

    try:
        win32gui.EnumWindows(_cb, None)
    except Exception:
        pass

    return StateSnapshot(
        fg_title=fg_title,
        visible_titles=frozenset(titles),
        timestamp=time.time(),
    )


# ─────────────────────────────────────────────
# 진단
# ─────────────────────────────────────────────
def _is_protected(title: str, protected: dict[str, list[str]]) -> bool:
    if not title:
        return True
    if title in (protected.get("exact") or []):
        return True
    for needle in protected.get("contains") or []:
        if needle and needle in title:
            return True
    return False


def diagnose(before: StateSnapshot, after: StateSnapshot) -> Diagnosis:
    """before → after 변화에서 부작용 감지."""
    rules = load_rules()
    known: list[dict] = rules.get("known_dialogs") or []
    protected = rules.get("protected_windows") or {"exact": [], "contains": []}

    new_titles = list(after.visible_titles - before.visible_titles)

    # 1) 알려진 다이얼로그 매칭 (after 의 fg + 새 창 둘 다 검사)
    candidates = set()
    if after.fg_title:
        candidates.add(after.fg_title)
    candidates.update(new_titles)

    for dlg in known:
        exact = dlg.get("title_exact")
        contains = dlg.get("title_contains")
        severity = dlg.get("severity", "high")
        if severity == "ignore":
            continue
        matched_title = None
        for t in candidates:
            if exact and t == exact:
                matched_title = t
                break
            if contains and contains in t:
                matched_title = t
                break
        if matched_title:
            return Diagnosis(
                has_side_effect=True,
                side_effect_kind=(contains or exact or "dialog").strip().lower().replace(" ", "_"),
                severity=severity,
                detected_title=matched_title,
                recovery_keys=list(dlg.get("recovery") or ["escape"]),
                should_halt=bool(dlg.get("halt", False)),
                new_window_titles=new_titles,
                matched_rule=f"known_dialogs:{contains or exact}",
            )

    # 2) 알려지지 않은 새 창 (보호 대상 외)
    unknown_new = [t for t in new_titles if not _is_protected(t, protected)]
    if unknown_new:
        return Diagnosis(
            has_side_effect=True,
            side_effect_kind="unknown_window",
            severity="high",
            detected_title=unknown_new[0],
            recovery_keys=["escape"],
            should_halt=False,
            new_window_titles=new_titles,
            matched_rule="unknown_window",
        )

    return Diagnosis(
        has_side_effect=False,
        side_effect_kind="none",
        severity="none",
        detected_title="",
        recovery_keys=[],
        should_halt=False,
        new_window_titles=new_titles,
        matched_rule="",
    )


# ─────────────────────────────────────────────
# 회복
# ─────────────────────────────────────────────
def recover(diag: Diagnosis, *, max_wait: float = 0.5) -> None:
    """진단된 부작용에 대한 회복 키 입력."""
    try:
        import pyautogui
    except ImportError:
        return
    for key in diag.recovery_keys or ["escape"]:
        try:
            pyautogui.press(key)
        except Exception as e:
            print(f"  [RECOVER] press({key}) 실패: {e}", flush=True)
        time.sleep(max_wait)


# ─────────────────────────────────────────────
# 학습 — 부작용 발생 시 룰북/로그 진화
# ─────────────────────────────────────────────
def learn_side_effect(
    intent: str,
    coord: tuple[int, int] | None,
    diag: Diagnosis,
    *,
    auto_forbid_coord: bool = True,
    forbid_radius: int = 40,
    kakaotalk_origin: tuple[int, int] | None = None,
) -> None:
    """
    부작용을 영구 기록 + (옵션) 좌표 기반 차단 영역 자동 추가.

    Args:
        intent: 액션 의도 (예: "채팅 리스트 검색바 클릭")
        coord: 절대 좌표 (x, y). None 이면 키 입력 등으로 좌표 없음.
        diag: 진단 결과
        auto_forbid_coord: True 면 critical/high 부작용 시 좌표를 forbidden_coords 에 자동 추가
        forbid_radius: 좌표 기반 forbidden 영역의 반지름 (px)
        kakaotalk_origin: (left, top) — coord 를 상대좌표로 변환할 때 사용
    """
    # 1) 항상 jsonl 로그
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": round(time.time(), 2),
        "intent": intent,
        "coord_abs": list(coord) if coord else None,
        "kind": diag.side_effect_kind,
        "severity": diag.severity,
        "detected_title": diag.detected_title,
        "matched_rule": diag.matched_rule,
        "new_window_titles": diag.new_window_titles,
    }
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"  [LEARN] jsonl 저장 실패: {e}", flush=True)

    print(
        f"  [LEARN] 부작용 기록: kind={diag.side_effect_kind!r} "
        f"severity={diag.severity!r} title={diag.detected_title!r}",
        flush=True,
    )

    # 2) 좌표 기반 차단 영역 자동 추가 (critical/high 만)
    if not auto_forbid_coord or coord is None or kakaotalk_origin is None:
        return
    if diag.severity not in ("critical", "high"):
        return

    rules = load_rules()
    forbidden = list(rules.get("forbidden_coords") or [])
    abs_x, abs_y = coord
    origin_x, origin_y = kakaotalk_origin
    rel_x = abs_x - origin_x
    rel_y = abs_y - origin_y

    # 이미 같은 영역에 forbidden 있으면 추가 안 함 (중복 방지)
    for f in forbidden:
        if f.get("window") != "카카오톡":
            continue
        if (
            f.get("x_rel_min", 0) <= rel_x <= f.get("x_rel_max", 0)
            and f.get("y_rel_min", 0) <= rel_y <= f.get("y_rel_max", 0)
        ):
            return  # 이미 차단된 영역

    new_rule = {
        "name": f"auto_{int(time.time())}_{diag.side_effect_kind}",
        "window": "카카오톡",
        "x_rel_min": max(0, rel_x - forbid_radius),
        "x_rel_max": rel_x + forbid_radius,
        "y_rel_min": max(0, rel_y - forbid_radius),
        "y_rel_max": rel_y + forbid_radius,
        "reason": (
            f"자동 추가: intent={intent!r} 좌표에서 {diag.side_effect_kind} "
            f"({diag.detected_title!r}) 발생"
        ),
        "added_by": "auto",
        "added_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    forbidden.append(new_rule)
    rules["forbidden_coords"] = forbidden
    rules["last_updated"] = time.strftime("%Y-%m-%d")
    _save_rules(rules)
    print(
        f"  [LEARN] forbidden_coords 추가: rel({rel_x},{rel_y}) ±{forbid_radius}px",
        flush=True,
    )


# ─────────────────────────────────────────────
# 차단 체크 (액션 실행 전)
# ─────────────────────────────────────────────
def is_coord_forbidden(
    abs_x: int, abs_y: int, kakaotalk_origin: tuple[int, int] | None = None
) -> dict | None:
    """좌표가 forbidden_coords 에 걸리면 해당 룰 dict 반환. 아니면 None."""
    rules = load_rules()
    forbidden = rules.get("forbidden_coords") or []
    for f in forbidden:
        if f.get("window") == "카카오톡" and kakaotalk_origin is not None:
            origin_x, origin_y = kakaotalk_origin
            rel_x = abs_x - origin_x
            rel_y = abs_y - origin_y
            if (
                f.get("x_rel_min", 0) <= rel_x <= f.get("x_rel_max", 0)
                and f.get("y_rel_min", 0) <= rel_y <= f.get("y_rel_max", 0)
            ):
                return f
        elif "x_abs_min" in f:
            if (
                f.get("x_abs_min", 0) <= abs_x <= f.get("x_abs_max", 0)
                and f.get("y_abs_min", 0) <= abs_y <= f.get("y_abs_max", 0)
            ):
                return f
    return None


def is_sequence_forbidden(keys: tuple[str, ...]) -> dict | None:
    """단축키 시퀀스가 forbidden_sequences 에 걸리면 해당 룰 반환."""
    rules = load_rules()
    seqs = rules.get("forbidden_sequences") or []
    norm = tuple(k.lower() for k in keys)
    for s in seqs:
        sk = tuple((k or "").lower() for k in (s.get("keys") or []))
        if sk == norm:
            return s
    return None
