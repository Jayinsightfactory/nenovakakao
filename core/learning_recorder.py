"""
학습 모드 녹화기 — **디스크 스트리밍** 방식으로 OOM 방지.

기존 문제:
  - self.frames에 모든 프레임을 ndarray로 보관 → 5분 이상 실행 시 RAM 부족
  - 1080p 30분 = 1080*1920*4*10*60*30 ≈ 14GB

새 방식:
  - 캡처 루프는 **마지막 1프레임만** 메모리에 보관 (self._latest_frame)
  - mark(step, phase) 호출 시 그 프레임을 즉시 PNG로 디스크 저장
    → data/anchor_candidates/<session>/<step>__<phase>__<seq>.png
  - 영상 저장은 옵션 (save_video=False가 기본) — 필요시 cv2.VideoWriter로 스트리밍
  - 이벤트 로그는 항상 events.json에 저장

사용:
    rec = LearningRecorder("session_xxx", fps=5)
    set_recorder(rec)
    rec.start()
    mark("step1", "before")
    pyautogui.click(...)
    mark("step1", "after")  ← 이 순간의 프레임이 디스크에 저장됨
    rec.stop_and_save()
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import cv2
import mss
import numpy as np

ROOT = Path(__file__).parent.parent
LEARN_ROOT = ROOT / "captures" / "learn"
ANCHOR_CANDIDATES = ROOT / "data" / "anchor_candidates"

# 후보 저장 페이즈 — 'after' / 'fail' 만 저장 (성공 상태 + 실패 상태 모두 학습 가치)
SAVE_PHASES = {"after", "fail"}


class LearningRecorder:
    """캡처 루프는 최신 프레임만 보관, mark 호출 시점에 디스크 저장."""

    def __init__(self, session_name: str, fps: int = 5, save_video: bool = False):
        self.session = session_name
        self.fps = fps
        self.save_video = save_video
        self.dir = LEARN_ROOT / session_name
        self.dir.mkdir(parents=True, exist_ok=True)
        self.cand_dir = ANCHOR_CANDIDATES / session_name
        self.cand_dir.mkdir(parents=True, exist_ok=True)

        self.events: list[dict] = []
        self._latest_frame: np.ndarray | None = None
        self._latest_lock = threading.Lock()
        self._active = False
        self._thread: threading.Thread | None = None
        self._t0: float = 0.0
        self._step_seq: dict[str, int] = {}

        # video writer (옵션) — 스트리밍 방식
        self._video_writer = None

    def start(self) -> None:
        if self._active:
            return
        self._active = True
        self._t0 = time.time()
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()
        print(f"[LearningRecorder] 녹화 시작 (스트리밍) → {self.cand_dir}", flush=True)
        # 캡처 쓰레드가 첫 프레임을 잡을 때까지 최대 2초 대기
        for _ in range(20):
            with self._latest_lock:
                if self._latest_frame is not None:
                    print(f"[LearningRecorder] 첫 프레임 확인 OK shape={self._latest_frame.shape}", flush=True)
                    return
            time.sleep(0.1)
        print("[LearningRecorder] WARN: 첫 프레임 2초 내 미확인 - 캡처 쓰레드 점검 필요", flush=True)

    def stop_and_save(self) -> Path:
        self._active = False
        if self._thread:
            self._thread.join(timeout=3)

        if self._video_writer is not None:
            try:
                self._video_writer.release()
            except Exception:
                pass
            self._video_writer = None

        # 이벤트 로그 + 인덱스 저장
        events_path = self.dir / "events.json"
        events_path.write_text(
            json.dumps(self.events, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        index = {
            "session": self.session,
            "events": self.events,
            "candidates": sorted(p.name for p in self.cand_dir.glob("*.png")),
        }
        (self.cand_dir / "index.json").write_text(
            json.dumps(index, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        print(
            f"[LearningRecorder] 종료: 이벤트 {len(self.events)}건, "
            f"후보 PNG {len(index['candidates'])}장 → {self.cand_dir}",
            flush=True,
        )
        return self.dir

    # ─── 이벤트 마킹 ───
    def mark(self, step_name: str, phase: str, meta: dict | None = None) -> None:
        evt = {
            "ts": time.time() - self._t0,
            "abs_ts": time.time(),
            "step": step_name,
            "phase": phase,
            "meta": meta or {},
        }
        self.events.append(evt)

        # 'after' / 'fail' 페이즈에서 즉시 디스크 저장
        if phase in SAVE_PHASES:
            self._save_current_frame(step_name, phase, meta)

    def _save_current_frame(self, step: str, phase: str, meta: dict | None) -> None:
        with self._latest_lock:
            frame = None if self._latest_frame is None else self._latest_frame.copy()
        if frame is None:
            print(f"[LearningRecorder] {step}/{phase} 프레임 없음 - 스킵", flush=True)
            return  # 캡처 시작 전 또는 캡처 실패

        seq = self._step_seq.get(step, 0) + 1
        self._step_seq[step] = seq

        meta = meta or {}
        room = meta.get("room", "")
        safe_room = "".join(c if c.isalnum() or c in "._-" else "_" for c in str(room))[:30]

        suffix = phase if phase != "after" else ""
        if suffix:
            fname = f"{step}__{suffix}__{safe_room or f'n{seq:02d}'}.png"
        else:
            fname = f"{step}__{safe_room or f'n{seq:02d}'}.png"

        path = self.cand_dir / fname
        try:
            cv2.imwrite(str(path), frame)
        except Exception as e:
            print(f"[LearningRecorder] PNG 저장 실패 ({step}/{phase}): {e}", flush=True)

    # ─── 내부 ───
    def _capture_loop(self) -> None:
        try:
            with mss.mss() as sct:
                monitor = sct.monitors[0]
                interval = 1.0 / self.fps
                next_t = time.time()
                while self._active:
                    try:
                        img = np.array(sct.grab(monitor))
                        frame = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)

                        # 최신 프레임만 보관 (이전 프레임은 GC 대상)
                        with self._latest_lock:
                            self._latest_frame = frame

                        # 비디오 스트리밍 (옵션)
                        if self.save_video:
                            if self._video_writer is None:
                                h, w = frame.shape[:2]
                                fourcc = cv2.VideoWriter_fourcc(*"mp4v")
                                self._video_writer = cv2.VideoWriter(
                                    str(self.dir / "video.mp4"), fourcc, self.fps, (w, h),
                                )
                            self._video_writer.write(frame)

                        # 즉시 메모리 해제
                        del img, frame
                    except MemoryError:
                        # 일시적 메모리 부족 — 잠깐 쉬고 계속
                        time.sleep(0.5)
                    except Exception as e:
                        print(f"[LearningRecorder] capture err: {e}", flush=True)

                    next_t += interval
                    delay = next_t - time.time()
                    if delay > 0:
                        time.sleep(delay)
                    else:
                        next_t = time.time()
        except Exception as e:
            print(f"[LearningRecorder] capture loop dead: {e}", flush=True)


# ─── 전역 싱글톤 ───
_active: LearningRecorder | None = None


def get_recorder() -> LearningRecorder | None:
    return _active


def set_recorder(rec: LearningRecorder | None) -> None:
    global _active
    _active = rec


def mark(step: str, phase: str, meta: dict | None = None) -> None:
    """파이프라인 어디서든 호출 가능 — 녹화 중이 아니면 no-op."""
    if _active is not None:
        _active.mark(step, phase, meta)
