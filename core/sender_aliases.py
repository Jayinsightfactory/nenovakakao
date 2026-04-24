"""발신자명 정규화 — 같은 사람의 여러 표기를 canonical 이름으로 통합.

실데이터에서 한 사람이 3~4개 이름으로 분산 기록됨. 예:
  정재훈 + 네노바 정재훈님 + 정재훈대리 = 동일 인물 3,390건
  Grb + 가브리엘 = 동일 인물 5,703건

data/user_mapping.json 의 'aliases' 섹션을 정규화 소스로 사용.
관리자가 aliases 만 수정하면 즉시 반영 (mtime 감지).
"""
from __future__ import annotations

import json
from pathlib import Path

_PATH = Path(__file__).parent.parent / "data" / "user_mapping.json"
_aliases: dict[str, str] = {}
_mtime: float = 0.0


def _reload() -> None:
    global _aliases, _mtime
    if not _PATH.exists():
        _aliases = {}
        return
    mtime = _PATH.stat().st_mtime
    if mtime == _mtime and _aliases:
        return
    try:
        data = json.loads(_PATH.read_text(encoding="utf-8"))
        _aliases = data.get("aliases", {}) or {}
        _mtime = mtime
    except Exception:
        _aliases = {}


def normalize_sender(name: str | None) -> str:
    """발신자 이름 → canonical 이름. 등록 안 된 이름은 원본 반환."""
    if not name:
        return ""
    _reload()
    stripped = name.strip()
    return _aliases.get(stripped, stripped)
