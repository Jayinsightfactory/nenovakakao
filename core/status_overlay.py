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
    """화면 우하단 상태 표시등"""

    # 상태별 색상
    COLORS = {
        "working": ("#FF0000", "#660000"),  # 빨강 깜빡임 (밝/어)
        "idle":    ("#00CC00", "#00CC00"),   # 초록 고정
        "issue":   ("#FFAA00", "#664400"),   # 노랑 깜빡임
    }

    def __init__(self, width: int = 180, height: int = 50):
        self._width = width
        self._height = height
        self._state = "idle"
        self._issue_text = ""
        self._blink_on = True
        self._root: Optional[tk.Tk] = None
        self._canvas: Optional[tk.Canvas] = None
        self._label: Optional[tk.Label] = None
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
        self._label.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10))

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
        text = labels.get(self._state, "대기")
        if self._state == "issue" and self._issue_text:
            text = f"이슈: {self._issue_text[:12]}"
        self._label.config(text=text)

        self._root.after(500, self._blink_loop)

    def set_working(self):
        """작업중 상태 (빨간불 깜빡임)"""
        self._state = "working"
        self._issue_text = ""

    def set_idle(self):
        """대기 상태 (초록불 고정)"""
        self._state = "idle"
        self._issue_text = ""

    def set_issue(self, text: str = ""):
        """이슈 상태 (노란불 깜빡임)"""
        self._state = "issue"
        self._issue_text = text

    def stop(self):
        """오버레이 종료"""
        if self._root:
            try:
                self._root.after(0, self._root.quit)
            except Exception:
                pass
            self._root = None


# 싱글톤
_overlay: Optional[StatusOverlay] = None


def get_overlay() -> StatusOverlay:
    """전역 오버레이 인스턴스 (최초 호출 시 자동 시작)"""
    global _overlay
    if _overlay is None:
        _overlay = StatusOverlay()
        _overlay.start()
    return _overlay
