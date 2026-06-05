"""
화면 우하단 상태 표시등

항상 최상위 작은 창으로 현재 상태를 표시:
  - 작업중: 빨간불 깜빡임
  - 대기:   초록불 고정
  - 이슈:   노란불 깜빡임 + 이슈 텍스트

별도 스레드에서 tkinter mainloop 실행.
"""
from __future__ import annotations

import threading
import tkinter as tk
from typing import Optional


class StatusOverlay:
    """화면 우하단 상태 표시등 + 중지 버튼"""

    # 상태별 색상
    COLORS = {
        "working": ("#FF0000", "#660000"),  # 빨강 깜빡임 (밝/어)
        "idle":    ("#00CC00", "#00CC00"),   # 초록 고정
        "issue":   ("#FFAA00", "#664400"),   # 노랑 깜빡임
    }

    def __init__(self, width: int = 320, height: int = 50):
        self._width = width
        self._height = height
        self._state = "idle"
        self._issue_text = ""
        self._status_text = ""  # 임의 상태 텍스트 (진행 정보)
        self._blink_on = True
        self._stop_requested = threading.Event()
        self._root: Optional[tk.Tk] = None
        self._canvas: Optional[tk.Canvas] = None
        self._label: Optional[tk.Label] = None
        self._stop_btn: Optional[tk.Button] = None
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()

    def start(self):
        """별도 스레드에서 오버레이 시작"""
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=5)

    def _run(self):
        self._root = tk.Tk()
        self._root.title("네노바 상태")
        self._root.overrideredirect(True)         # 타이틀바 제거
        self._root.attributes("-topmost", True)   # 항상 최상위
        self._root.attributes("-alpha", 0.85)     # 약간 투명

        # 화면 우하단 위치
        screen_w = self._root.winfo_screenwidth()
        screen_h = self._root.winfo_screenheight()
        x = screen_w - self._width - 20
        y = screen_h - self._height - 60  # 태스크바 위
        self._root.geometry(f"{self._width}x{self._height}+{x}+{y}")

        # 배경
        self._root.configure(bg="#1a1a1a")

        # 원형 표시등
        self._canvas = tk.Canvas(
            self._root, width=30, height=30,
            bg="#1a1a1a", highlightthickness=0,
        )
        self._canvas.pack(side=tk.LEFT, padx=(10, 5), pady=10)
        self._dot = self._canvas.create_oval(5, 5, 25, 25, fill="#00CC00")

        # 상태 텍스트
        self._label = tk.Label(
            self._root, text="대기", fg="white", bg="#1a1a1a",
            font=("맑은 고딕", 10, "bold"), anchor="w",
        )
        self._label.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))

        # 중지 버튼
        self._stop_btn = tk.Button(
            self._root, text="■", fg="white", bg="#CC0000",
            activebackground="#FF3333", activeforeground="white",
            font=("맑은 고딕", 10, "bold"),
            width=3, height=1, bd=0, relief=tk.FLAT,
            command=self._on_stop_click,
        )
        self._stop_btn.pack(side=tk.RIGHT, padx=(0, 8), pady=8)

        self._ready.set()
        self._blink_loop()
        self._root.mainloop()

    def _blink_loop(self):
        """깜빡임 애니메이션 루프"""
        if self._root is None:
            return

        colors = self.COLORS.get(self._state, self.COLORS["idle"])

        if self._state == "idle":
            color = colors[0]
        else:
            color = colors[0] if self._blink_on else colors[1]
            self._blink_on = not self._blink_on

        self._canvas.itemconfig(self._dot, fill=color)

        # 상태 텍스트 업데이트
        labels = {"working": "작업중", "idle": "대기", "issue": "이슈!"}
        base = labels.get(self._state, "대기")
        if self._state == "issue" and self._issue_text:
            text = f"이슈: {self._issue_text[:20]}"
        elif self._status_text:
            # 진행 정보가 있으면 우선 표시
            text = f"{base}: {self._status_text[:28]}"
        else:
            text = base
        self._label.config(text=text)

        self._root.after(500, self._blink_loop)

    def set_working(self, text: str = ""):
        """작업중 상태 (빨간불 깜빡임). text를 주면 오버레이에 표시."""
        self._state = "working"
        self._issue_text = ""
        self._status_text = text

    def set_status(self, text: str):
        """현재 작업 내용 표시 (상태 유지)"""
        self._status_text = text

    def set_idle(self):
        """대기 상태 (초록불 고정)"""
        self._state = "idle"
        self._issue_text = ""
        self._status_text = ""

    def set_issue(self, text: str = ""):
        """이슈 상태 (노란불 깜빡임)"""
        self._state = "issue"
        self._issue_text = text

    def _on_stop_click(self):
        """중지 버튼 클릭 시 호출 → 로그 자동 복사 → 2초 뒤 강제 종료."""
        import os, shutil, threading
        from pathlib import Path
        from datetime import datetime

        self._stop_requested.set()
        self._state = "idle"
        if self._label:
            self._label.config(text="중지됨 — 로그 저장중, 2초 뒤 종료")
        if self._stop_btn:
            self._stop_btn.config(text="--", state=tk.DISABLED, bg="#666666")

        # 로그 + 학습 보고서를 Downloads/nenova_logs/ 로 즉시 복사
        try:
            dst_dir = Path("C:/Users/USER/Downloads/nenova_logs")
            dst_dir.mkdir(parents=True, exist_ok=True)
            logs_dir = Path(__file__).parent.parent / "logs"
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")

            # 1) 최신 actions 로그 (전체, 수정 없이)
            actions = sorted(
                logs_dir.glob("actions_*.log"),
                key=lambda p: p.stat().st_mtime, reverse=True,
            )
            if actions:
                shutil.copy2(actions[0], dst_dir / f"actions_{ts}.log")

            # 2) 세션 학습 보고서 생성 + 복사
            try:
                from core.run_analyzer import dump_session_learning
                lp = dump_session_learning()
                shutil.copy2(lp, dst_dir / f"learning_{ts}.md")
            except Exception as e:
                print(f"[OVERLAY] 학습 보고서 생성 실패: {e}", flush=True)

            # 3) issues.jsonl (원본 이슈 이벤트)
            issues = logs_dir / "issues.jsonl"
            if issues.exists():
                shutil.copy2(issues, dst_dir / f"issues_{ts}.jsonl")

            # 4) learning.md (사이클별 누적)
            learning = logs_dir / "learning.md"
            if learning.exists():
                shutil.copy2(learning, dst_dir / f"learning_running_{ts}.md")

            print(f"[OVERLAY] 로그 + 학습 보고서 복사: {dst_dir}", flush=True)
        except Exception as e:
            print(f"[OVERLAY] 로그 복사 실패: {e}", flush=True)

        def _hard_kill():
            # 사용자 의도 정지 → _STOP 마커(시각) 남겨 '사용자 정지'임을 코드/외부에서 확인 가능,
            # 오류(exit 1)가 아니라 정상 종료(exit 0)로 끝낸다.
            try:
                from core.stop_button import request_stop
                request_stop()
            except Exception:
                pass
            try:
                print("[USER-STOP] 오버레이 강제정지 — 사용자 의도 정지(오류 아님). "
                      "_STOP 마커 기록됨, 정상 종료(exit 0).", flush=True)
            except Exception:
                pass
            os._exit(0)

        threading.Timer(2.0, _hard_kill).start()

    @property
    def should_stop(self) -> bool:
        """감시 루프에서 매 사이클마다 체크"""
        return self._stop_requested.is_set()

    def reset_stop(self):
        """중지 플래그 초기화 (재시작 시)"""
        self._stop_requested.clear()
        if self._stop_btn:
            try:
                self._root.after(0, lambda: self._stop_btn.config(
                    text="■", state=tk.NORMAL, bg="#CC0000"))
            except Exception:
                pass

    def stop(self):
        """오버레이 종료"""
        if self._root:
            try:
                self._root.after(0, self._root.quit)
            except Exception:
                pass
            self._root = None


# 싱글톤
_overlay: Optional["StatusOverlay"] = None


class _NoOverlay:
    """NENOVA_NO_OVERLAY=1 시 사용되는 더미. tk 창 안 띄움 + stop 버튼 오클릭 방지."""
    should_stop = False
    def start(self): pass
    def stop(self): pass
    def set_idle(self): pass
    def set_working(self, *a, **kw): pass
    def set_issue(self, *a, **kw): pass
    def set_status(self, *a, **kw): pass
    def reset_stop(self): pass


def get_overlay():
    """전역 오버레이 인스턴스. NENOVA_NO_OVERLAY=1 이면 no-op stub.
    (자동화 클릭이 우하단 중지 버튼을 실수로 눌러서 프로세스가 os._exit 되는 문제 방지)
    """
    global _overlay
    import os as _os
    if _overlay is None:
        if _os.getenv("NENOVA_NO_OVERLAY") == "1":
            _overlay = _NoOverlay()
            print("[OVERLAY] NENOVA_NO_OVERLAY=1 → stub (tk 창 생략)", flush=True)
        else:
            _overlay = StatusOverlay()
            _overlay.start()
    return _overlay
