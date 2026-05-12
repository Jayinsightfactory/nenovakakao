"""
화면 변경 감지 워치독 — 자동화 stall 자동 감지.

원칙:
- 클릭/키스트로크가 화면 변화 못 일으키면 정지.
- 백그라운드 스레드로 카톡 창 영역만 작게 캡쳐 (가벼움).
- pHash 평균 차이로 비교 — N초 동안 변경 없음 → on_stall 콜백.
- main monitor 가 호출자. stall 시 SIGINT 자기 PID 보내거나 raise.

사용:
    from core.screen_watchdog import ScreenWatchdog
    wd = ScreenWatchdog(stall_seconds=20, on_stall=lambda info: ...)
    wd.start()
    ...
    wd.beat()   # 의미 있는 진행이 있을 때 (예: _process_room_result 완료)
    ...
    wd.stop()
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from PIL import Image, ImageGrab

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class WatchdogConfig:
    poll_interval: float = 1.0          # 캡쳐 주기 (초)
    stall_seconds: float = 20.0          # 이만큼 변화 없으면 stall
    diff_threshold: float = 3.0          # 평균 픽셀 차이 임계 (0~255)
    sample_size: tuple[int, int] = (160, 160)  # 캡쳐 다운샘플 크기 (가벼움)
    capture_bbox: tuple[int, int, int, int] | None = None  # None = 카톡 고정 좌표


class ScreenWatchdog:
    """카톡 창 화면 변화 감지 — N초 정체 시 on_stall 호출."""

    def __init__(
        self,
        *,
        on_stall: Callable[[dict], None],
        stall_seconds: float = 20.0,
        poll_interval: float = 1.0,
        diff_threshold: float = 3.0,
        capture_bbox: tuple[int, int, int, int] | None = None,
    ) -> None:
        self.cfg = WatchdogConfig(
            poll_interval=poll_interval,
            stall_seconds=stall_seconds,
            diff_threshold=diff_threshold,
            capture_bbox=capture_bbox,
        )
        self.on_stall = on_stall
        self._stop_evt = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_change_ts = time.time()
        self._last_sample: Image.Image | None = None
        self._stall_fired = False
        self._beat_evt = threading.Event()  # 호출자가 진행 신호

    # ────────────────────────────────────────────
    # 외부 API
    # ────────────────────────────────────────────
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_evt.clear()
        self._last_change_ts = time.time()
        self._stall_fired = False
        self._thread = threading.Thread(target=self._run, daemon=True, name="ScreenWatchdog")
        self._thread.start()

    def stop(self) -> None:
        self._stop_evt.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def beat(self, label: str = "") -> None:
        """호출자가 명시적으로 '진행 있었음' 신호. last_change_ts 갱신."""
        self._last_change_ts = time.time()
        self._stall_fired = False
        if label:
            try:
                self._log_event({"event": "beat", "label": label})
            except Exception:
                pass

    @property
    def seconds_since_change(self) -> float:
        return time.time() - self._last_change_ts

    # ────────────────────────────────────────────
    # 내부
    # ────────────────────────────────────────────
    def _capture(self) -> Image.Image | None:
        bbox = self.cfg.capture_bbox
        if bbox is None:
            # 카톡 고정 좌표 (window_manager.KAKAOTALK_FIXED_POS)
            try:
                from core.window_manager import KAKAOTALK_FIXED_POS
                x, y, w, h = KAKAOTALK_FIXED_POS
                bbox = (x, y, x + w, y + h)
            except Exception:
                return None
        try:
            img = ImageGrab.grab(bbox=bbox)
            img.thumbnail(self.cfg.sample_size, Image.BILINEAR)
            return img.convert("L")  # 그레이스케일
        except Exception:
            return None

    def _diff(self, a: Image.Image, b: Image.Image) -> float:
        """두 그레이스케일 이미지의 평균 픽셀 차이 (0~255)."""
        if a.size != b.size:
            return 999.0
        try:
            import numpy as np
            arr_a = np.asarray(a, dtype="int16")
            arr_b = np.asarray(b, dtype="int16")
            return float(abs(arr_a - arr_b).mean())
        except ImportError:
            # numpy 없으면 sample
            px_a, px_b = a.load(), b.load()
            n = min(100, a.size[0])
            total = 0
            step = max(1, a.size[0] // 10)
            count = 0
            for x in range(0, a.size[0], step):
                for y in range(0, a.size[1], step):
                    total += abs(px_a[x, y] - px_b[x, y])
                    count += 1
            return total / count if count else 0.0

    def _log_event(self, entry: dict) -> None:
        log_path = ROOT / "data" / "screen_watchdog.jsonl"
        log_path.parent.mkdir(exist_ok=True)
        entry["ts"] = time.time()
        import json
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _run(self) -> None:
        while not self._stop_evt.is_set():
            cur = self._capture()
            if cur is not None:
                if self._last_sample is not None:
                    diff = self._diff(self._last_sample, cur)
                    if diff >= self.cfg.diff_threshold:
                        self._last_change_ts = time.time()
                        self._stall_fired = False
                self._last_sample = cur

                elapsed = time.time() - self._last_change_ts
                if elapsed >= self.cfg.stall_seconds and not self._stall_fired:
                    self._stall_fired = True
                    info = {
                        "event": "stall_detected",
                        "elapsed_seconds": elapsed,
                        "stall_seconds": self.cfg.stall_seconds,
                        "diff_threshold": self.cfg.diff_threshold,
                    }
                    self._log_event(info)
                    try:
                        self.on_stall(info)
                    except Exception as e:
                        print(f"  [WATCHDOG] on_stall 콜백 예외: {e}", flush=True)

            time.sleep(self.cfg.poll_interval)


# 편의: stall 발생 시 자기 PID 에 SIGINT 보내서 main 종료시키는 콜백
def stall_kill_self(info: dict) -> None:
    import signal
    print(
        f"\n🛑 [WATCHDOG] 화면 정체 {info['elapsed_seconds']:.1f}s "
        f"(임계 {info['stall_seconds']:.0f}s) — 자동 정지\n",
        flush=True,
    )
    try:
        os.kill(os.getpid(), signal.SIGINT)
    except Exception:
        os._exit(1)
