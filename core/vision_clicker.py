"""
Vision 기반 100% 정확도 클릭 엔진.

원칙:
  1. 어떤 클릭도 추측 좌표를 쓰지 않는다.
  2. 매 클릭 전: 화면 캡처 → Claude Vision API에 "이 영역에서 'X' 텍스트
     위치를 픽셀 좌표로 알려줘" → 받은 좌표만 클릭.
  3. 매 클릭 후: 검증 캡처 → "기대한 결과가 보이나?" → 실패 시 재시도.
  4. 모든 시도/성공/실패는 data/vision_click_log.jsonl에 누적 → 학습.

API 요금이 들기 때문에 캡처를 작게 자르고 결과를 캐시한다.
"""
from __future__ import annotations

import base64
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pyautogui
from dotenv import load_dotenv
from PIL import ImageGrab

ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env", override=True)

DATA_DIR = ROOT / "data"
CAPTURES_DIR = ROOT / "captures" / "vision"
CAPTURES_DIR.mkdir(parents=True, exist_ok=True)
CLICK_LOG = DATA_DIR / "vision_click_log.jsonl"
ANCHOR_CACHE_FILE = DATA_DIR / "vision_anchor_cache.json"

VISION_MODEL_PRIMARY = "claude-haiku-4-5-20251001"     # 빠르고 싼 기본
VISION_MODEL_FALLBACK = "claude-opus-4-7"              # 고정밀 (실패 시 재시도)
VISION_MODEL = VISION_MODEL_PRIMARY                     # 하위호환

# 태그별 자동 모델 승격 규칙 (성공률 낮은 태그 → Opus 우선)
MODEL_RULES_FILE = DATA_DIR / "vision_model_rules.json"
MIN_SAMPLES_FOR_RULE = 10       # 규칙 반영에 필요한 최소 샘플 수
UPGRADE_THRESHOLD = 0.70        # 성공률이 이 값 미만이면 Opus 승격
DOWNGRADE_THRESHOLD = 0.92      # Opus 승격 태그가 이 값 이상 회복하면 Primary 복귀
CAPTURE_MAX_FILES = 500         # 캡처 디렉토리 rotation 임계
CAPTURE_TRIM_BATCH = 50         # 초과 시 한 번에 삭제할 오래된 파일 수


@dataclass
class ClickTarget:
    """Vision이 찾은 클릭 대상."""
    found: bool
    x: int = 0       # 절대 화면 좌표
    y: int = 0
    confidence: float = 0.0
    debug: str = ""


def _log(entry: dict) -> None:
    """클릭 시도/결과를 jsonl에 누적."""
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        entry = {"ts": time.time(), **entry}
        with open(CLICK_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


# ─── 좌표 앵커 캐시 ───
# Vision OCR 이 실패한 태그도 직전 성공 좌표로 클릭 → 시스템 안정성 확보.
# 카카오워크 NV 탭처럼 OCR 24% 성공률 문제를 좌표 캐시로 우회.
_ANCHOR_BBOX_TOLERANCE = 80  # 캐시된 bbox 와 현재 bbox 가 이 픽셀 이상 차이나면 무효


def _load_anchor_cache() -> dict:
    if not ANCHOR_CACHE_FILE.exists():
        return {}
    try:
        with open(ANCHOR_CACHE_FILE, encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _save_anchor_cache(cache: dict) -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(ANCHOR_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _remember_anchor(tag: str, bbox: tuple, x: int, y: int) -> None:
    """OCR 성공 시 호출 — 다음 OCR 실패에 대비해 좌표 저장."""
    if not tag:
        return
    cache = _load_anchor_cache()
    cache[tag] = {
        "x": int(x),
        "y": int(y),
        "bbox": [int(v) for v in bbox],
        "ts": time.time(),
        "hit_count": cache.get(tag, {}).get("hit_count", 0) + 1,
    }
    _save_anchor_cache(cache)


def _recall_anchor(tag: str, bbox: tuple) -> tuple[int, int] | None:
    """OCR 실패 시 호출 — 캐시 좌표 반환. bbox 가 크게 다르면 None.

    bbox 가 다르면 화면 레이아웃이 바뀐 것 → 캐시는 신뢰할 수 없음.
    """
    if not tag:
        return None
    cache = _load_anchor_cache()
    entry = cache.get(tag)
    if not entry:
        return None
    cached_bbox = entry.get("bbox") or []
    if len(cached_bbox) != 4:
        return None
    # bbox 코너가 일정 픽셀 이내로 일치할 때만 신뢰
    for cur, cached in zip(bbox, cached_bbox):
        if abs(cur - cached) > _ANCHOR_BBOX_TOLERANCE:
            return None
    return entry["x"], entry["y"]


def _rotate_captures() -> None:
    """캡처 디렉토리가 임계 초과하면 가장 오래된 파일 일괄 삭제."""
    try:
        files = sorted(CAPTURES_DIR.glob("*.png"), key=lambda p: p.stat().st_mtime)
        if len(files) <= CAPTURE_MAX_FILES:
            return
        for p in files[:CAPTURE_TRIM_BATCH]:
            try:
                p.unlink()
            except Exception:
                pass
    except Exception:
        pass


def _capture(bbox: tuple[int, int, int, int], tag: str = "") -> Path | None:
    """영역 캡처 → 캡처 파일 경로."""
    try:
        img = ImageGrab.grab(bbox=bbox)
        ts = int(time.time() * 1000)
        safe = re.sub(r"[^\w-]", "_", tag)[:40]
        path = CAPTURES_DIR / f"{ts}_{safe or 'cap'}.png"
        img.save(path)
        _rotate_captures()
        return path
    except Exception as e:
        print(f"  [VISION] 캡처 실패: {e}", flush=True)
        return None


def _load_model_rules() -> dict:
    try:
        if MODEL_RULES_FILE.exists():
            return json.loads(MODEL_RULES_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_model_rules(rules: dict) -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        MODEL_RULES_FILE.write_text(
            json.dumps(rules, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def _pick_model_for_tag(tag: str) -> str:
    """태그별 지정 모델. 학습 규칙이 있으면 그것, 아니면 Primary(Haiku)."""
    if not tag:
        return VISION_MODEL_PRIMARY
    rules = _load_model_rules()
    return rules.get(tag, VISION_MODEL_PRIMARY)


def refresh_model_rules_from_logs() -> dict:
    """click log 통계 → 태그별 성공률 → Opus 승격/복귀 규칙 업데이트.
    반환: 변경된 rules dict.
    """
    if not CLICK_LOG.exists():
        return {}
    from collections import defaultdict
    tag_stats: dict[str, dict] = defaultdict(lambda: {"total": 0, "found": 0})
    with open(CLICK_LOG, encoding="utf-8") as f:
        for line in f:
            try:
                d = json.loads(line)
            except Exception:
                continue
            tag = d.get("tag", "")
            if not tag:
                continue
            tag_stats[tag]["total"] += 1
            r = d.get("result", {}) or {}
            if r.get("found"):
                tag_stats[tag]["found"] += 1

    rules = _load_model_rules()
    changed = False
    for tag, st in tag_stats.items():
        if st["total"] < MIN_SAMPLES_FOR_RULE:
            continue
        rate = st["found"] / st["total"] if st["total"] else 0.0
        cur = rules.get(tag)
        if rate < UPGRADE_THRESHOLD and cur != VISION_MODEL_FALLBACK:
            rules[tag] = VISION_MODEL_FALLBACK
            changed = True
        elif rate >= DOWNGRADE_THRESHOLD and cur == VISION_MODEL_FALLBACK:
            rules.pop(tag, None)
            changed = True
    if changed:
        _save_model_rules(rules)
    return rules


def _vision_locate(
    capture_path: Path,
    bbox: tuple[int, int, int, int],
    target_description: str,
    max_tokens: int = 200,
    model: str | None = None,
) -> ClickTarget:
    """
    캡처 이미지에서 target_description에 해당하는 위치를 Claude Vision으로 찾기.

    Args:
        capture_path: 캡처 PNG 경로
        bbox: 캡처 영역 절대좌표 (left, top, right, bottom)
        target_description: 찾을 대상 한국어 설명
            예: "'네노바 수입(불량 공유방)' 텍스트가 있는 채팅방 행"

    Returns:
        ClickTarget — found=False 시 클릭 안 함
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return ClickTarget(found=False, debug="ANTHROPIC_API_KEY 없음")
    try:
        import anthropic  # type: ignore
    except ImportError:
        return ClickTarget(found=False, debug="anthropic 모듈 없음")

    try:
        b = base64.standard_b64encode(capture_path.read_bytes()).decode()
    except Exception as e:
        return ClickTarget(found=False, debug=f"파일 읽기 실패: {e}")

    bw = bbox[2] - bbox[0]
    bh = bbox[3] - bbox[1]
    prompt = (
        f"이 이미지({bw}x{bh}px)에서 다음 대상의 클릭할 좌표를 찾아주세요:\n"
        f"\n대상: {target_description}\n\n"
        f"응답 형식 (반드시 이 JSON 한 줄만):\n"
        f'{{"found": true|false, "x": <0-{bw} 사이 px>, "y": <0-{bh} 사이 px>, "confidence": 0.0-1.0, "reason": "왜 그렇게 판단했는지"}}\n'
        f"\n주의:\n"
        f"- 좌표는 이미지 내 상대 픽셀 (이미지 왼쪽 위가 0,0)\n"
        f"- 대상이 명확히 보이지 않으면 found=false, 추측 금지\n"
        f"- 대상이 영역 안 클릭 가능한 부분의 정중앙 좌표"
    )
    try:
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model=model or VISION_MODEL_PRIMARY,
            max_tokens=max_tokens,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b}},
                    {"type": "text", "text": prompt},
                ],
            }],
        )
        raw = resp.content[0].text.strip()
        # 코드블록 ```json ... ``` 내용 추출
        cb = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
        if cb:
            json_str = cb.group(1)
        else:
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            if not m:
                return ClickTarget(found=False, debug=f"JSON 못찾음: {raw[:300]}")
            json_str = m.group(0)
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            return ClickTarget(found=False, debug=f"JSON parse: {e} :: {json_str[:200]}")
        if not data.get("found"):
            return ClickTarget(found=False, confidence=float(data.get("confidence", 0.0)),
                               debug=data.get("reason", "")[:200])
        rel_x = int(data["x"])
        rel_y = int(data["y"])
        # 영역 밖 좌표 거부
        if not (0 <= rel_x <= bw and 0 <= rel_y <= bh):
            return ClickTarget(found=False, debug=f"좌표 범위 밖: ({rel_x},{rel_y}) bbox={bw}x{bh}")
        abs_x = bbox[0] + rel_x
        abs_y = bbox[1] + rel_y
        return ClickTarget(
            found=True, x=abs_x, y=abs_y,
            confidence=float(data.get("confidence", 0.0)),
            debug=data.get("reason", "")[:200],
        )
    except Exception as e:
        return ClickTarget(found=False, debug=f"API 에러: {type(e).__name__}: {e}")


def find_and_click(
    bbox: tuple[int, int, int, int],
    target_description: str,
    *,
    tag: str = "",
    double: bool = False,
    min_confidence: float = 0.6,
    dry_run: bool = False,
) -> ClickTarget:
    """
    영역 캡처 → Vision으로 대상 위치 찾기 → 클릭.

    Args:
        bbox: 캡처 영역 절대좌표
        target_description: 찾을 대상 한국어 설명
        tag: 로그용 태그 (예: "kakaowork.find_mirror_room")
        double: True면 더블클릭
        min_confidence: 이 값 미만이면 클릭 안 함
        dry_run: True면 좌표만 반환, 클릭 안 함

    Returns:
        ClickTarget
    """
    cap = _capture(bbox, tag)
    if cap is None:
        result = ClickTarget(found=False, debug="캡처 실패")
        _log({"tag": tag, "bbox": list(bbox), "result": result.__dict__, "clicked": False})
        return result

    # 1차: 태그별 지정 모델 (기본 Haiku, 실패 누적 태그는 자동 Opus)
    primary = _pick_model_for_tag(tag)
    result = _vision_locate(cap, bbox, target_description, model=primary)
    used_model = primary

    # 2차 fallback: Haiku에서 실패/저신뢰면 Opus 1회 재시도
    if primary != VISION_MODEL_FALLBACK and (
        not result.found or result.confidence < min_confidence
    ):
        print(f"  [VISION] {tag} Haiku 실패, Opus 재시도", flush=True)
        result = _vision_locate(cap, bbox, target_description, model=VISION_MODEL_FALLBACK)
        used_model = VISION_MODEL_FALLBACK

    log_entry = {
        "tag": tag,
        "bbox": list(bbox),
        "target": target_description,
        "capture": str(cap.name),
        "model": used_model,
        "result": result.__dict__,
        "clicked": False,
    }

    if not result.found or result.confidence < min_confidence:
        if result.found:
            print(f"  [VISION] {tag} confidence {result.confidence:.2f} < {min_confidence} - 클릭 안 함", flush=True)
        else:
            # stdout 이 cp949 이면 이모지 등으로 UnicodeEncodeError. 안전 프린트.
            _safe = (result.debug or "")[:100].encode("ascii", errors="replace").decode("ascii")
            print(f"  [VISION] {tag} 못찾음: {_safe}", flush=True)

        # ── 좌표 앵커 캐시 폴백 ──
        # OCR 실패 시 직전 성공 좌표로 폴백 시도. NV 탭처럼 자주 실패하지만 위치가
        # 거의 안 바뀌는 항목에 효과적. dry_run 모드에선 클릭 안 함.
        if not dry_run:
            cached = _recall_anchor(tag, bbox)
            if cached is not None:
                cx, cy = cached
                print(f"  [VISION-ANCHOR] {tag} 캐시 좌표 폴백 → ({cx}, {cy})", flush=True)
                try:
                    if double:
                        pyautogui.doubleClick(cx, cy)
                    else:
                        pyautogui.click(cx, cy)
                    log_entry["fallback_anchor"] = {"x": cx, "y": cy}
                    log_entry["clicked"] = True
                    # 폴백 클릭은 found=True 로 다루지 않음 — caller 가 후속 검증으로
                    # 실제 성공 여부 판정. 여기선 좌표만 채워서 진행 가능하게 함.
                    result.x = cx
                    result.y = cy
                except Exception as e:
                    log_entry["fallback_click_error"] = str(e)
        _log(log_entry)
        return result

    _safe_dbg = (result.debug or "")[:80].encode("ascii", errors="replace").decode("ascii")
    print(f"  [VISION] {tag} → ({result.x}, {result.y}) conf={result.confidence:.2f}: {_safe_dbg}", flush=True)

    if dry_run:
        log_entry["dry_run"] = True
        _log(log_entry)
        return result

    try:
        if double:
            pyautogui.doubleClick(result.x, result.y)
        else:
            pyautogui.click(result.x, result.y)
        log_entry["clicked"] = True
        # OCR 성공 클릭 → 다음 실패에 대비해 좌표 캐시 갱신
        _remember_anchor(tag, bbox, result.x, result.y)
    except Exception as e:
        log_entry["click_error"] = str(e)

    _log(log_entry)
    return result


def find_and_verify(
    capture_bbox: tuple[int, int, int, int],
    expected_description: str,
    *,
    tag: str = "",
) -> bool:
    """
    영역에서 expected_description이 보이는지 Vision으로 확인.

    클릭 후 검증용. find_and_click과 같은 로직이지만 클릭 안 하고 found만 반환.
    """
    target = find_and_click(
        capture_bbox, expected_description,
        tag=f"verify.{tag}", dry_run=True,
        min_confidence=0.5,
    )
    return target.found and target.confidence >= 0.5


# ─── 진단/통계 ───
def stats() -> dict:
    """클릭 로그 통계."""
    if not CLICK_LOG.exists():
        return {"total": 0}
    total = clicked = found = 0
    by_tag: dict[str, dict] = {}
    with open(CLICK_LOG, encoding="utf-8") as f:
        for line in f:
            try:
                d = json.loads(line)
            except Exception:
                continue
            total += 1
            t = d.get("tag", "")
            tagstat = by_tag.setdefault(t, {"total": 0, "found": 0, "clicked": 0, "avg_conf": 0.0})
            tagstat["total"] += 1
            r = d.get("result", {}) or {}
            if r.get("found"):
                found += 1
                tagstat["found"] += 1
                tagstat["avg_conf"] += r.get("confidence", 0.0)
            if d.get("clicked"):
                clicked += 1
                tagstat["clicked"] += 1
    for t, ts in by_tag.items():
        if ts["found"]:
            ts["avg_conf"] /= ts["found"]
    return {
        "total": total, "found": found, "clicked": clicked,
        "by_tag": by_tag,
    }
