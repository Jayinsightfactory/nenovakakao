"""
앵커 검증기 — 현재 화면에 '학습된 성공 앵커'가 있는지 템플릿 매칭으로 확인.

철학: 넓게 시작(전체 프레임) → 학습 반복으로 ROI 좁혀가기.

사용:
    av = AnchorVerifier()
    if av.wait_for("save_dialog_open", timeout=5):
        pyautogui.press("enter")
    else:
        # 실패 로깅 → 재시도 or 이슈 보고
        ...
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import cv2
import mss
import numpy as np

ROOT = Path(__file__).parent.parent
ANCHOR_DIR = ROOT / "data" / "anchors"  # 관리자 확정 앵커
META_PATH = ROOT / "data" / "anchors_meta.json"  # 각 앵커의 ROI/threshold


class AnchorVerifier:
    def __init__(self):
        ANCHOR_DIR.mkdir(parents=True, exist_ok=True)
        self.meta = self._load_meta()
        self.anchors = self._load_anchors()

    # ─── 공개 API ───
    def verify(self, step_name: str) -> tuple[bool, float, tuple[int, int] | None]:
        """현재 화면에 앵커가 있는지 즉시 확인."""
        anchor = self.anchors.get(step_name)
        if anchor is None:
            # 앵커 미학습 → 검증 스킵 (통과 처리)
            return True, 1.0, None

        meta = self.meta.get(step_name, {})
        threshold = meta.get("threshold", 0.85)
        roi = meta.get("roi")  # [x, y, w, h] or None

        screen = self._grab(roi)
        # 템플릿이 ROI보다 크면 매칭 불가
        if anchor.shape[0] > screen.shape[0] or anchor.shape[1] > screen.shape[1]:
            return False, 0.0, None

        result = cv2.matchTemplate(screen, anchor, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)
        return max_val >= threshold, float(max_val), max_loc

    def wait_for(self, step_name: str, timeout: float = 5.0, interval: float = 0.15) -> bool:
        """앵커가 나타날 때까지 폴링."""
        t0 = time.time()
        last_conf = 0.0
        while time.time() - t0 < timeout:
            ok, conf, _ = self.verify(step_name)
            last_conf = max(last_conf, conf)
            if ok:
                return True
            time.sleep(interval)
        print(f"[AnchorVerifier] 타임아웃: {step_name} (최고신뢰도 {last_conf:.2f})", flush=True)
        return False

    def has(self, step_name: str) -> bool:
        return step_name in self.anchors

    # ─── 내부 ───
    def _load_anchors(self) -> dict[str, np.ndarray]:
        anchors: dict[str, np.ndarray] = {}
        for png in ANCHOR_DIR.glob("*.png"):
            img = cv2.imread(str(png))
            if img is not None:
                anchors[png.stem] = img
        return anchors

    def _load_meta(self) -> dict:
        if META_PATH.exists():
            try:
                return json.loads(META_PATH.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}

    def _grab(self, roi: list[int] | None) -> np.ndarray:
        with mss.mss() as sct:
            if roi:
                x, y, w, h = roi
                bbox = {"left": x, "top": y, "width": w, "height": h}
            else:
                bbox = sct.monitors[0]
            img = np.array(sct.grab(bbox))
        return cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)


# 싱글톤 (선택)
_singleton: AnchorVerifier | None = None


def get() -> AnchorVerifier:
    global _singleton
    if _singleton is None:
        _singleton = AnchorVerifier()
    return _singleton


def reload() -> AnchorVerifier:
    """앵커 파일 변경 후 재로드."""
    global _singleton
    _singleton = AnchorVerifier()
    return _singleton
