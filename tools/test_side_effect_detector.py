"""
side_effect_detector / safe_actions 정적 자가 테스트.

실제 마우스/키보드를 절대 건드리지 않음. StateSnapshot 을 직접 만들어서
diagnose() 의 룰 매칭과 학습 동작을 검증.

실행:
    python tools/test_side_effect_detector.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.side_effect_detector import (  # noqa: E402
    StateSnapshot,
    diagnose,
    is_coord_forbidden,
    is_sequence_forbidden,
    load_rules,
)


def _snap(fg: str, titles: list[str]) -> StateSnapshot:
    return StateSnapshot(fg_title=fg, visible_titles=frozenset(titles), timestamp=0.0)


def assert_eq(label: str, got, want):
    ok = got == want
    mark = "✅" if ok else "❌"
    print(f"  {mark} {label}: got={got!r} want={want!r}")
    return ok


def test_friend_add_critical():
    print("[TEST] 친구 추가 다이얼로그 감지 (critical, halt=true)")
    before = _snap("카카오톡", ["카카오톡", "주광 담당", "Program Manager"])
    after = _snap("친구 추가", ["카카오톡", "주광 담당", "Program Manager", "친구 추가"])
    d = diagnose(before, after)
    ok = True
    ok &= assert_eq("has_side_effect", d.has_side_effect, True)
    ok &= assert_eq("severity", d.severity, "critical")
    ok &= assert_eq("should_halt", d.should_halt, True)
    ok &= assert_eq("recovery_keys", d.recovery_keys, ["escape", "escape"])
    return ok


def test_unified_search_high():
    print("\n[TEST] 통합검색 다이얼로그 감지 (high, halt=false)")
    before = _snap("카카오톡", ["카카오톡", "Program Manager"])
    after = _snap("통합검색", ["카카오톡", "Program Manager", "통합검색"])
    d = diagnose(before, after)
    ok = True
    ok &= assert_eq("has_side_effect", d.has_side_effect, True)
    ok &= assert_eq("severity", d.severity, "high")
    ok &= assert_eq("should_halt", d.should_halt, False)
    ok &= assert_eq("recovery_keys", d.recovery_keys, ["escape"])
    return ok


def test_unknown_window():
    print("\n[TEST] 알려지지 않은 새 창 (high)")
    before = _snap("카카오톡", ["카카오톡"])
    after = _snap("카카오톡", ["카카오톡", "이상한 팝업"])
    d = diagnose(before, after)
    ok = True
    ok &= assert_eq("has_side_effect", d.has_side_effect, True)
    ok &= assert_eq("kind", d.side_effect_kind, "unknown_window")
    ok &= assert_eq("severity", d.severity, "high")
    ok &= assert_eq("detected_title", d.detected_title, "이상한 팝업")
    return ok


def test_protected_window_ignored():
    print("\n[TEST] 보호 대상 창은 부작용 아님 (Chrome 새 창)")
    before = _snap("카카오톡", ["카카오톡"])
    after = _snap("카카오톡", ["카카오톡", "Google - Chrome"])
    d = diagnose(before, after)
    ok = assert_eq("has_side_effect", d.has_side_effect, False)
    return ok


def test_program_manager_ignored():
    print("\n[TEST] Program Manager 는 ignore 룰로 무시")
    before = _snap("카카오톡", ["카카오톡"])
    after = _snap("Program Manager", ["카카오톡", "Program Manager"])
    d = diagnose(before, after)
    ok = assert_eq("has_side_effect", d.has_side_effect, False)
    return ok


def test_no_change():
    print("\n[TEST] 변화 없으면 부작용 아님")
    s = _snap("카카오톡", ["카카오톡", "주광 담당"])
    d = diagnose(s, s)
    ok = assert_eq("has_side_effect", d.has_side_effect, False)
    return ok


def test_forbidden_coord():
    print("\n[TEST] forbidden_coords — 카톡 검색바 영역 차단")
    # 카톡 메인창 (50, 50) 기준 상대좌표 (150, 105) → 절대 (200, 155)
    rule = is_coord_forbidden(200, 155, kakaotalk_origin=(50, 50))
    ok = True
    ok &= assert_eq("forbidden 매칭", bool(rule), True)
    if rule:
        ok &= assert_eq("name", rule.get("name"), "kakaotalk_unified_search_zone")

    # 안전 영역 (채팅 리스트 행) 은 통과
    rule2 = is_coord_forbidden(190, 300, kakaotalk_origin=(50, 50))  # rel (140, 250)
    ok &= assert_eq("안전 영역 통과", rule2, None)
    return ok


def test_forbidden_sequence():
    print("\n[TEST] forbidden_sequences — Ctrl+F 차단")
    rule = is_sequence_forbidden(("ctrl", "f"))
    ok = True
    ok &= assert_eq("Ctrl+F 매칭", bool(rule), True)
    if rule:
        ok &= assert_eq("name", rule.get("name"), "ctrl_f_for_room_lookup")

    # Ctrl+S 는 안전
    rule2 = is_sequence_forbidden(("ctrl", "s"))
    ok &= assert_eq("Ctrl+S 통과", rule2, None)
    return ok


def test_rules_loaded():
    print("\n[TEST] 룰북 로드 확인")
    r = load_rules()
    ok = True
    ok &= assert_eq("forbidden_coords 존재", len(r.get("forbidden_coords") or []) > 0, True)
    ok &= assert_eq("forbidden_sequences 존재", len(r.get("forbidden_sequences") or []) > 0, True)
    ok &= assert_eq("known_dialogs 존재", len(r.get("known_dialogs") or []) > 0, True)
    ok &= assert_eq("protected_windows 존재",
                   bool((r.get("protected_windows") or {}).get("exact")), True)
    return ok


def main() -> int:
    print("=" * 60)
    print("side_effect_detector 정적 테스트 (마우스/키보드 미사용)")
    print("=" * 60)
    print()
    tests = [
        test_rules_loaded,
        test_friend_add_critical,
        test_unified_search_high,
        test_unknown_window,
        test_protected_window_ignored,
        test_program_manager_ignored,
        test_no_change,
        test_forbidden_coord,
        test_forbidden_sequence,
    ]
    results = []
    for t in tests:
        try:
            results.append(t())
        except Exception as e:
            print(f"  💥 {t.__name__} 예외: {type(e).__name__}: {e}")
            results.append(False)

    print()
    print("=" * 60)
    passed = sum(results)
    total = len(results)
    print(f"  결과: {passed}/{total} 통과")
    print("=" * 60)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
