"""
자동화 도중 사용자가 누르면 즉시 모든 동작이 멈추는 항상 위에 떠있는 정지 버튼.

설계:
  - 별도 스레드에서 tkinter 창 (200×100, TOPMOST, 화면 우상단)
  - 버튼 누름 → threading.Event 플래그 set + data/_STOP 파일 생성
  - 매 safe_click / safe_paste / safe_hotkey / safe_press 전에 check_stop() 호출
  - 정지 요청이면 StopRequested 예외 → 호출자가 즉시 중단

사용:
    from core.stop_button import start_stop_button, check_stop, stop_button_close, StopRequested

    start_stop_button()
    try:
        for x in items:
            check_stop()
            do_something()
    except StopRequested:
        print("[STOP] 사용자 정지")
    finally:
        stop_button_close()

UI 옵션:
  - 빨간 [🛑 즉시 정지] 버튼
  - 창 닫기(X)도 정지 요청으로 처리
  - 진행 중 메시지 표시 (.set_message(text))
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STOP_FILE = ROOT / "data" / "_STOP"

_stop_flag = threading.Event()
_window_thread: threading.Thread | None = None
_window_root = None  # tk.Tk — 실행 중일 때만 set
_status_var = None   # tk.StringVar


class StopRequested(Exception):
    """사용자가 정지 버튼을 눌렀거나 STOP 파일이 생성됨."""


def is_stop_requested() -> bool:
    """플래그 OR STOP 파일 둘 다 체크 (다른 프로세스 호환)."""
    if _stop_flag.is_set():
        return True
    try:
        if STOP_FILE.exists():
            _stop_flag.set()
            return True
    except Exception:
        pass
    return False


def check_stop() -> None:
    """안전 액션 호출 직전에 호출. 정지 요청이면 즉시 StopRequested 발생."""
    if is_stop_requested():
        raise StopRequested("사용자가 정지 버튼을 눌렀음")


def request_stop() -> None:
    """프로그램적으로 정지 요청 (테스트/외부 트리거용)."""
    _stop_flag.set()
    try:
        STOP_FILE.parent.mkdir(parents=True, exist_ok=True)
        STOP_FILE.write_text(
            f"stop requested at {time.strftime('%Y-%m-%d %H:%M:%S')}\n",
            encoding="utf-8",
        )
    except Exception:
        pass


def set_status(text: str) -> None:
    """현재 진행 중 메시지를 정지 버튼 창에 표시."""
    if _status_var is not None:
        try:
            _status_var.set(text[:60])
        except Exception:
            pass


def _run_window() -> None:
    """tkinter mainloop. 별도 스레드에서 실행."""
    global _window_root, _status_var
    try:
        import tkinter as tk
    except ImportError:
        return

    root = tk.Tk()
    _window_root = root
    root.title("네노바 자동화 정지")
    root.attributes("-topmost", True)
    try:
        root.attributes("-toolwindow", True)
    except Exception:
        pass

    win_w, win_h = 280, 130
    try:
        sw = root.winfo_screenwidth()
        x = sw - win_w - 20
        y = 20
    except Exception:
        x, y = 1200, 20
    root.geometry(f"{win_w}x{win_h}+{x}+{y}")
    root.resizable(False, False)

    # 창 X 닫기도 정지 요청으로 처리
    root.protocol("WM_DELETE_WINDOW", request_stop)

    frame = tk.Frame(root, bg="#fff5f5", padx=10, pady=10)
    frame.pack(fill="both", expand=True)

    title = tk.Label(
        frame, text="🔴 자동화 실행 중", bg="#fff5f5",
        fg="#1a1a1a", font=("맑은 고딕", 10, "bold"),
    )
    title.pack(pady=(0, 4))

    _status_var = tk.StringVar(value="진행 중...")
    status = tk.Label(
        frame, textvariable=_status_var, bg="#fff5f5",
        fg="#555", font=("맑은 고딕", 8), wraplength=260, justify="left",
    )
    status.pack(pady=(0, 6))

    btn = tk.Button(
        frame,
        text="🛑 즉시 정지 (STOP)",
        bg="#dc3545", fg="white",
        activebackground="#a51d2d", activeforeground="white",
        font=("맑은 고딕", 11, "bold"),
        relief="raised", borderwidth=2,
        cursor="hand2",
        command=request_stop,
    )
    btn.pack(fill="x", ipady=4)

    # 0.2 초마다 정지 플래그 확인 → set 되면 창 자동 닫음
    def _watchdog():
        try:
            if _stop_flag.is_set():
                # 사용자가 '정지' 누르면 시각 피드백 후 닫기
                try:
                    btn.config(text="정지됨", bg="#6c757d", state="disabled")
                    _status_var.set("자동화 정지 요청 전송됨")
                except Exception:
                    pass
                root.after(700, root.destroy)
                return
            root.after(200, _watchdog)
        except Exception:
            pass

    root.after(200, _watchdog)

    try:
        root.mainloop()
    except Exception:
        pass
    finally:
        _window_root = None


def start_stop_button() -> None:
    """별도 스레드에서 정지 버튼 창 띄움. 이전 STOP 파일은 자동 정리."""
    global _window_thread

    # 이전 세션의 stale STOP 파일 정리
    try:
        if STOP_FILE.exists():
            STOP_FILE.unlink()
    except Exception:
        pass
    _stop_flag.clear()

    if _window_thread is not None and _window_thread.is_alive():
        return  # 이미 떠있음

    _window_thread = threading.Thread(target=_run_window, daemon=True, name="StopButton")
    _window_thread.start()
    # 창이 뜨도록 잠시 대기 (TOPMOST 적용 시간)
    time.sleep(0.6)


def stop_button_close() -> None:
    """자동화 종료 시 정지 버튼 창 닫음 (정지 요청 안 함)."""
    global _window_root
    try:
        root = _window_root
        if root is not None:
            root.after(0, root.destroy)
    except Exception:
        pass
    # 다음 세션 위해 STOP 파일 정리 (단, 이번에 정지 요청 받았으면 보존하지 않음 — 깨끗하게)
    try:
        if STOP_FILE.exists():
            STOP_FILE.unlink()
    except Exception:
        pass
