"""core/mirror_dispatch.py — 미러 대상 스위치.

카톡 델타를 어느 대상에 미러할지 MIRROR_TARGET env 로 라우팅.
send_delta_interleaved 시그니처/반환은 kakaowork_router 와 동일 → main.py 는 이 모듈만 호출.

MIRROR_TARGET:
  kakaowork (기본)  카카오워크 봇/앱 미러 (기존)
  talkhub | moyi    MOYI(talkhub) 브릿지 미러 (신규)
  both              둘 다 (병렬 운영/컷오버)
  none | off        미러 안 함 (시트 전용 등)

컷오버 절차(메모리 talkhub_migration): kakaowork → both(병렬 검증) → talkhub. 문제 시 즉시 롤백.
"""
from __future__ import annotations

import os

_ZERO = {
    "total_messages": 0, "text_sent": 0, "text_failed": 0, "text_skipped": 0,
    "photos_uploaded": 0, "photos_missing": 0, "trailing_uploaded": 0,
}


def target() -> str:
    return (os.environ.get("MIRROR_TARGET") or "kakaowork").strip().lower()


def _merge(results: list[dict]) -> dict:
    merged = dict(_ZERO)
    for r in results:
        for k, v in (r or {}).items():
            if isinstance(v, (int, float)):
                merged[k] = merged.get(k, 0) + v
    return merged


def send_delta_interleaved(kakaotalk_name: str, delta: str, photo_files: list | None = None,
                           *, delay: float = 0.3) -> dict:
    tgt = target()
    if tgt in ("none", "off", ""):
        return dict(_ZERO)

    results: list[dict] = []
    if tgt in ("kakaowork", "kw", "both"):
        try:
            from core.kakaowork_router import send_delta_interleaved as _kw
            results.append(_kw(kakaotalk_name, delta, photo_files, delay=delay))
        except Exception as e:
            print(f"  [dispatch] kakaowork 미러 예외: {type(e).__name__}: {e}", flush=True)
    if tgt in ("talkhub", "moyi", "both"):
        try:
            from core.talkhub_router import send_delta_interleaved as _th
            results.append(_th(kakaotalk_name, delta, photo_files, delay=delay))
        except Exception as e:
            print(f"  [dispatch] talkhub 미러 예외: {type(e).__name__}: {e}", flush=True)

    if not results:
        # 알 수 없는 타겟 → 안전하게 기존(kakaowork)로 폴백
        print(f"  [dispatch] 알 수 없는 MIRROR_TARGET='{tgt}' → kakaowork 폴백", flush=True)
        from core.kakaowork_router import send_delta_interleaved as _kw
        results.append(_kw(kakaotalk_name, delta, photo_files, delay=delay))

    return _merge(results)
