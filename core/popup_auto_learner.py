"""
자동 팝업 학습기.

세션 종료 시 failed_frame_analyzer가 추출한 [[KW:...]] 마커에서 키워드를 모아
`data/auto_popup_keywords.json`에 영구 저장. 다음 세션의 cleanup_popups가
이 학습 키워드도 사용해 자동으로 닫음.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data"
LEARNED_FILE = DATA_DIR / "auto_popup_keywords.json"

KW_PATTERN = re.compile(r"\[\[KW:\s*([^\]]+?)\s*\]\]")
# 학습 제외 키워드 (메인 창/허용 다이얼로그)
EXCLUDE = {"카카오톡", "KakaoTalk", "카카오워크", "Kakao Work", ""}


def load_learned_keywords() -> list[str]:
    try:
        if LEARNED_FILE.exists():
            data = json.loads(LEARNED_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return [k for k in data if isinstance(k, str)]
    except Exception:
        pass
    return []


def save_learned_keywords(kws: list) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    uniq = sorted({k.strip() for k in kws if isinstance(k, str) and k.strip() and k not in EXCLUDE})
    LEARNED_FILE.write_text(
        json.dumps(uniq, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _selected_room_names() -> list[str]:
    """selected_rooms.json의 모든 방 이름 (학습 키워드 충돌 방지용)."""
    try:
        sel_path = ROOT / "data" / "selected_rooms.json"
        if not sel_path.exists():
            return []
        data = json.loads(sel_path.read_text(encoding="utf-8"))
        out: list[str] = []
        for item in data:
            if isinstance(item, dict) and item.get("name"):
                out.append(item["name"])
            elif isinstance(item, str):
                out.append(item)
        return out
    except Exception:
        return []


def _is_safe_keyword(kw: str, room_names: list[str]) -> bool:
    """키워드가 채팅방 이름의 일부와 매칭되면 위험(채팅창 닫힘) → 학습 거부."""
    if not kw or len(kw) < 2:
        return False
    if kw in EXCLUDE:
        return False
    # 채팅방 이름과 부분 매칭 시 거부
    for rn in room_names:
        if kw in rn or rn in kw:
            return False
    return True


def learn_from_report(report: dict) -> list[str]:
    """report = {step: [{summary, ...}, ...]} → [[KW:...]] 마커 추출 → 영구 저장.
    채팅방 이름과 충돌하는 키워드는 학습 거부.
    Returns: 새로 학습된 키워드 리스트.
    """
    learned = set(load_learned_keywords())
    rooms = _selected_room_names()
    new_kws: list[str] = []
    rejected: list[str] = []
    for step, rows in (report or {}).items():
        for r in rows:
            summary = r.get("summary", "") or ""
            for m in KW_PATTERN.finditer(summary):
                kw = m.group(1).strip()
                if not kw or kw in learned:
                    continue
                if _is_safe_keyword(kw, rooms):
                    learned.add(kw)
                    new_kws.append(kw)
                else:
                    rejected.append(kw)
    if new_kws:
        save_learned_keywords(list(learned))
        print(f"[AUTO-LEARN] 새 팝업 키워드 {len(new_kws)}개 학습: {new_kws}", flush=True)
    if rejected:
        print(f"[AUTO-LEARN] 채팅방 이름과 충돌해 거부된 키워드: {rejected}", flush=True)
    return new_kws
