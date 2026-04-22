"""
앵커 후보 자동 승인기 — 여러 학습 세션의 후보를 클러스터링해서
N회 이상 동일/유사한 프레임이 반복 나타난 스텝은 `data/anchors/`로 자동 확정.

관리자가 `python main.py anchors`로 일일이 승인하지 않아도,
충분한 샘플이 쌓이면 신뢰할 수 있는 앵커가 자동 등록됨.

동작:
  1. `data/anchor_candidates/<session>/*.png`  — 각 세션의 스텝별 캡처
  2. 스텝별로 후보 이미지를 모아 perceptual hash (pHash) 비교
  3. 같은 cluster가 N회 이상 등장 → 대표 이미지 → `data/anchors/<step>.png`
  4. anchors_meta.json에 threshold/ROI(전체 화면) 기록

실행:
  python -m core.anchor_auto_approver              # dry-run (리포트만)
  python -m core.anchor_auto_approver --commit     # 실제 승인
  python -m core.anchor_auto_approver --min-count 3 --commit
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).parent.parent
CAND_DIR = ROOT / "data" / "anchor_candidates"
ANCHOR_DIR = ROOT / "data" / "anchors"
META_PATH = ROOT / "data" / "anchors_meta.json"

# pHash bit 차이 임계값 — 8x8 DCT 64비트, 보통 <=6이면 거의 동일
PHASH_MATCH_THRESHOLD = 6
DEFAULT_MIN_COUNT = 3


def _phash(img: np.ndarray, size: int = 8) -> int:
    """perceptual hash: 32x32 grayscale DCT 저주파 8x8 부호 기준 64bit."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    small = cv2.resize(gray, (32, 32), interpolation=cv2.INTER_AREA).astype(np.float32)
    dct = cv2.dct(small)
    low = dct[:size, :size]
    med = np.median(low)
    bits = (low > med).flatten()
    h = 0
    for b in bits:
        h = (h << 1) | int(b)
    return h


def _hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def _load_meta() -> dict:
    if META_PATH.exists():
        try:
            return json.loads(META_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_meta(m: dict) -> None:
    META_PATH.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")


def collect_candidates() -> dict[str, list[Path]]:
    """
    모든 세션의 후보를 스텝별로 그룹핑.

    Returns:
        {step_name: [path, path, ...]}
    """
    by_step: dict[str, list[Path]] = defaultdict(list)
    if not CAND_DIR.exists():
        return by_step
    for session_dir in CAND_DIR.iterdir():
        if not session_dir.is_dir():
            continue
        # failed/는 별도 취급 — 학습 제외
        if session_dir.name == "failed":
            continue
        for png in session_dir.glob("*.png"):
            # 파일명: <step>__<meta>.png
            stem = png.stem
            if "__" in stem:
                step = stem.split("__", 1)[0]
            else:
                step = stem
            by_step[step].append(png)
    return by_step


def cluster_candidates(paths: list[Path]) -> list[dict]:
    """
    후보 이미지를 pHash로 그룹핑.

    Returns:
        [{"hash": int, "paths": [...], "count": int, "repr": Path}, ...]
        count 내림차순 정렬.
    """
    clusters: list[dict] = []
    for p in paths:
        img = cv2.imread(str(p))
        if img is None:
            continue
        h = _phash(img)
        # 기존 클러스터와 비교
        matched = False
        for c in clusters:
            if _hamming(c["hash"], h) <= PHASH_MATCH_THRESHOLD:
                c["paths"].append(p)
                c["count"] += 1
                matched = True
                break
        if not matched:
            clusters.append({"hash": h, "paths": [p], "count": 1, "repr": p})
    # 대표 이미지 = 최신 파일 (가장 최근에 캡처된 것)
    for c in clusters:
        c["repr"] = max(c["paths"], key=lambda p: p.stat().st_mtime)
    clusters.sort(key=lambda c: c["count"], reverse=True)
    return clusters


def auto_approve(
    *,
    min_count: int = DEFAULT_MIN_COUNT,
    commit: bool = False,
    overwrite: bool = False,
) -> dict:
    """
    조건을 만족하는 스텝은 대표 이미지를 앵커로 자동 승인.

    Args:
        min_count: 한 클러스터가 N회 이상 나타나야 승인
        commit: True면 실제 저장, False면 리포트만
        overwrite: True면 기존 승인 앵커도 덮어씀 (기본 False — 기존 유지)

    Returns:
        {'approved': [(step, count, path)], 'rejected': [...], 'skipped': [...]}
    """
    ANCHOR_DIR.mkdir(parents=True, exist_ok=True)
    meta = _load_meta()

    by_step = collect_candidates()
    approved: list[tuple[str, int, str]] = []
    rejected: list[tuple[str, int, str]] = []
    skipped: list[tuple[str, str]] = []  # (step, reason)

    for step, paths in by_step.items():
        target = ANCHOR_DIR / f"{step}.png"
        if target.exists() and not overwrite:
            skipped.append((step, f"이미 승인됨 ({target.name})"))
            continue

        clusters = cluster_candidates(paths)
        if not clusters:
            skipped.append((step, "후보 없음"))
            continue

        top = clusters[0]
        if top["count"] < min_count:
            rejected.append((step, top["count"], str(top["repr"])))
            continue

        # 승인
        if commit:
            img = cv2.imread(str(top["repr"]))
            if img is None:
                rejected.append((step, top["count"], "이미지 읽기 실패"))
                continue
            cv2.imwrite(str(target), img)
            meta[step] = {
                "threshold": 0.85,
                "roi": None,  # 전체 화면 (관리자가 이후 picker_gui로 좁힐 수 있음)
                "auto_approved": True,
                "cluster_count": top["count"],
                "sources": len(paths),
            }
        approved.append((step, top["count"], str(top["repr"])))

    if commit:
        _save_meta(meta)

    return {
        "approved": approved,
        "rejected": rejected,
        "skipped": skipped,
    }


def main() -> int:
    commit = "--commit" in sys.argv
    overwrite = "--overwrite" in sys.argv
    min_count = DEFAULT_MIN_COUNT
    for i, a in enumerate(sys.argv):
        if a == "--min-count" and i + 1 < len(sys.argv):
            try:
                min_count = int(sys.argv[i + 1])
            except ValueError:
                pass

    r = auto_approve(min_count=min_count, commit=commit, overwrite=overwrite)
    print(f"\n=== 앵커 자동 승인 결과 (min_count={min_count}, commit={commit}) ===")
    print(f"승인: {len(r['approved'])}개")
    for step, cnt, path in r["approved"]:
        print(f"  + {step}  (cluster={cnt}, src={Path(path).name})")
    print(f"\n기각 (샘플 부족): {len(r['rejected'])}개")
    for step, cnt, path in r["rejected"]:
        print(f"  - {step}  (cluster={cnt} < {min_count})")
    print(f"\n스킵: {len(r['skipped'])}개")
    for step, reason in r["skipped"]:
        print(f"  . {step}  ({reason})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
