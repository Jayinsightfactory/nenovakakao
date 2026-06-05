"""
실시간 액션 로그 창.

모든 키보드 입력, 마우스 클릭, 스크롤, 포커스 변화를 별도 창에 기록.
"친구추가" 같은 의도치 않은 창이 왜 떴는지 역추적 가능.

사용법 (main.py 등에서):
    from core.action_logger import get_logger, log
    log("감시 시작")
    log(f"방 클릭 ({x},{y})")
"""
from __future__ import annotations

import threading
import tkinter as tk
import time
from collections import deque
from typing import Optional


MAX_LINES = 500


class ActionLogger:
    _instance: Optional["ActionLogger"] = None

    def __init__(self):
        self._queue: deque[tuple[float, str, str]] = deque(maxlen=MAX_LINES)
        self._lock = threading.Lock()
        self._root: Optional[tk.Tk] = None
        self._text: Optional[tk.Text] = None
        self._ready = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_foreground: str = ""
        self._last_action: str = ""

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=5)

        # 포그라운드 감시 스레드
        t = threading.Thread(target=self._fg_watcher, daemon=True)
        t.start()

    def _run(self):
        self._root = tk.Tk()
        self._root.title("네노바 액션 로그 (Ctrl+C 복사 가능)")
        self._root.attributes("-topmost", True)
        self._root.attributes("-alpha", 0.92)
        self._root.overrideredirect(False)

        # 위치: 우측 세로띠 (분리창 x=1000~1600 회피, x>=1620 권장)
        screen_w = self._root.winfo_screenwidth()
        screen_h = self._root.winfo_screenheight()
        W = 380
        H = screen_h - 100
        x = max(1620, screen_w - W - 10)
        y = 40
        self._root.geometry(f"{W}x{H}+{x}+{y}")
        self._root.configure(bg="#0a0a0a")

        # WS_EX_NOACTIVATE — 포커스 강탈 방지 (복사는 가능, 자동 활성화 안 됨)
        def _set_no_activate():
            try:
                import win32api, win32con
                hwnd = int(self._root.frame(), 16)
                exstyle = win32api.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
                win32api.SetWindowLong(
                    hwnd, win32con.GWL_EXSTYLE,
                    exstyle | win32con.WS_EX_NOACTIVATE,
                )
            except Exception as e:
                print(f"  [LOG-WIN] NOACTIVATE 설정 실패: {e}", flush=True)

        self._root.after(100, _set_no_activate)

        # 상단 버튼: 전체 복사
        btn_frame = tk.Frame(self._root, bg="#0a0a0a")
        btn_frame.pack(fill=tk.X, padx=5, pady=(5, 0))
        tk.Button(
            btn_frame, text="📋 전체 복사", fg="white", bg="#2266AA",
            font=("맑은 고딕", 9, "bold"), bd=0, padx=10,
            command=self._copy_all,
        ).pack(side=tk.LEFT)
        tk.Button(
            btn_frame, text="🗑 지우기", fg="white", bg="#666",
            font=("맑은 고딕", 9), bd=0, padx=10,
            command=self._clear_log,
        ).pack(side=tk.LEFT, padx=(5, 0))
        # 🛑 정지 — 자동화 모든 동작 중지 (graceful stop, _STOP 신호)
        self._stop_btn = tk.Button(
            btn_frame, text="🛑 정지", fg="white", bg="#CC0000",
            activebackground="#a51d2d", activeforeground="white",
            font=("맑은 고딕", 9, "bold"), bd=0, padx=12,
            command=self._request_stop,
        )
        self._stop_btn.pack(side=tk.LEFT, padx=(5, 0))
        # 💀 강제정지 — 진행 중 작업까지 즉시 중단 (os._exit). 오클릭 방지 위해
        # 우측에 분리 배치 + 검은색. graceful 🛑 정지와 떨어뜨림.
        self._hardkill_btn = tk.Button(
            btn_frame, text="💀 강제정지", fg="white", bg="#000000",
            activebackground="#330000", activeforeground="white",
            font=("맑은 고딕", 9, "bold"), bd=0, padx=10,
            command=self._hard_kill,
        )
        self._hardkill_btn.pack(side=tk.RIGHT, padx=(5, 0))
        self._file_label = tk.Label(
            btn_frame, text="", fg="#888", bg="#0a0a0a",
            font=("Consolas", 8),
        )
        self._file_label.pack(side=tk.RIGHT)

        frame = tk.Frame(self._root, bg="#0a0a0a")
        frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        scrollbar = tk.Scrollbar(frame, bg="#333")
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # state=NORMAL → 선택/복사 가능. Ctrl+C는 기본 바인딩.
        self._text = tk.Text(
            frame, bg="#0a0a0a", fg="#00FF00",
            font=("Consolas", 9), wrap=tk.NONE,
            yscrollcommand=scrollbar.set,
            state=tk.NORMAL,
            insertbackground="#00FF00",
        )
        self._text.pack(fill=tk.BOTH, expand=True)
        scrollbar.config(command=self._text.yview)

        # 로그 파일: 시작 시점 기준 파일명
        from pathlib import Path
        from datetime import datetime
        log_dir = Path(__file__).parent.parent / "logs"
        log_dir.mkdir(exist_ok=True)
        self._log_file = log_dir / f"actions_{datetime.now():%Y%m%d_%H%M%S}.log"
        try:
            self._file_handle = open(self._log_file, "a", encoding="utf-8", buffering=1)
            self._file_label.config(text=f"→ {self._log_file.name}")
        except Exception:
            self._file_handle = None

        # 태그 색상
        self._text.tag_config("TIME", foreground="#888888")
        self._text.tag_config("KEY", foreground="#00FFFF")
        self._text.tag_config("MOUSE", foreground="#FFFF00")
        self._text.tag_config("FG", foreground="#FF00FF")
        self._text.tag_config("INFO", foreground="#00FF00")
        self._text.tag_config("WARN", foreground="#FF8800")
        self._text.tag_config("ERR", foreground="#FF0000")

        self._ready.set()
        self._drain_loop()
        self._root.mainloop()

    def _drain_loop(self):
        """큐에 쌓인 로그를 Text 위젯 + 파일에 반영 (메인 스레드)."""
        if self._root is None:
            return
        with self._lock:
            pending = list(self._queue)
            self._queue.clear()
        if pending and self._text:
            for ts, tag, msg in pending:
                tstr = time.strftime("%H:%M:%S", time.localtime(ts))
                ms = int((ts - int(ts)) * 1000)
                line = f"[{tstr}.{ms:03d}] [{tag}] {msg}\n"
                self._text.insert(tk.END, f"[{tstr}.{ms:03d}] ", "TIME")
                self._text.insert(tk.END, f"{msg}\n", tag)
                # 파일에도 기록
                if self._file_handle:
                    try:
                        self._file_handle.write(line)
                    except Exception:
                        pass
            self._text.see(tk.END)
        self._root.after(100, self._drain_loop)

    def _copy_all(self):
        """Text 위젯의 전체 내용을 클립보드로."""
        try:
            import pyperclip
            content = self._text.get("1.0", tk.END)
            pyperclip.copy(content)
            # 버튼 옆 상태 표시
            self._file_label.config(text=f"✓ 복사됨 ({len(content)}자) — 파일: {self._log_file.name}")
        except Exception as e:
            self._file_label.config(text=f"복사 실패: {e}")

    def _clear_log(self):
        """Text 위젯 내용 지우기 (파일은 유지)."""
        try:
            self._text.delete("1.0", tk.END)
            self._file_label.config(text=f"→ {self._log_file.name}")
        except Exception:
            pass

    def _request_stop(self):
        """🛑 정지 버튼 → 자동화 graceful stop 요청 (플래그 + _STOP 파일).
        os._exit 가 아니라 신호만 보내므로 안전 — 루프가 다음 체크포인트에서 종료.
        """
        try:
            from core.stop_button import request_stop
            request_stop()
            self.log("🛑 정지 버튼 클릭 — 자동화 중지 요청 전송", "ERR")
            try:
                self._stop_btn.config(text="정지 요청됨", bg="#6c757d", state=tk.DISABLED)
            except Exception:
                pass
        except Exception as e:
            self.log(f"정지 요청 실패: {e}", "ERR")

    def _hard_kill(self):
        """💀 강제정지 — 진행 중 작업(사진 다운로드 등)까지 즉시 중단하고
        프로세스를 바로 종료한다. graceful 정지가 안 먹힐 때의 최후 수단.
        _STOP 신호도 남겨 재시작/외부 로직과 일관성 유지.
        """
        try:
            from core.stop_button import request_stop
            request_stop()
        except Exception:
            pass
        try:
            # 사용자 의도 정지 → 오류(exit 1)가 아니라 정상 종료(exit 0).
            # 위 request_stop() 이 _STOP 마커(시각)를 남겨 '사용자 정지'임을 코드/외부에서 확인 가능.
            print("[USER-STOP] 💀 강제정지 버튼 — 사용자 의도 정지(오류 아님). "
                  "_STOP 마커 기록됨, 정상 종료(exit 0).", flush=True)
        except Exception:
            pass
        import os
        os._exit(0)

    def _fg_watcher(self):
        """포그라운드 창 변화 감시 → 변할 때마다 로그."""
        import win32gui
        while True:
            try:
                hwnd = win32gui.GetForegroundWindow()
                title = win32gui.GetWindowText(hwnd) if hwnd else ""
                if title != self._last_foreground:
                    # 위험 키워드 감지
                    tag = "FG"
                    is_risk = any(k in title for k in ["친구 추가", "친구추가", "광고", "이벤트", "가입", "알림"])
                    if is_risk:
                        tag = "ERR"
                        self.log(f"[!!위험창!!] '{title}' ← 직전 액션: '{self._last_action}'", tag)
                    else:
                        self.log(f"[포그라운드] '{title}'", tag)
                    self._last_foreground = title
            except Exception:
                pass
            time.sleep(0.15)

    def log(self, msg: str, tag: str = "INFO"):
        with self._lock:
            self._queue.append((time.time(), tag, msg))
        # stdout로도 (백업)
        try:
            print(f"[LOG] {msg}", flush=True)
        except Exception:
            pass

    def record_action(self, action: str):
        """키보드/마우스 액션 기록 (포그라운드 변화 역추적용)."""
        self._last_action = action


def _is_disabled() -> bool:
    """NENOVA_NO_ACTION_LOG=1 이면 GUI 로그 창 비활성화 (stdout 만 사용).
    배경: 로그창이 tk.Tk 로 띄워지면서 포커스/클릭 이벤트 가로채는 문제 방지.
    """
    import os
    return os.getenv("NENOVA_NO_ACTION_LOG") == "1"


class _StubLogger:
    """로그창 없는 더미. stdout 으로만 출력."""
    def log(self, msg: str, tag: str = "INFO"):
        print(f"[LOG-{tag}] {msg}", flush=True)
    def start(self): pass
    def log_foreground(self, title): pass
    def log_action(self, action): pass


def get_logger():
    if ActionLogger._instance is None:
        if _is_disabled():
            ActionLogger._instance = _StubLogger()
        else:
            ActionLogger._instance = ActionLogger()
            ActionLogger._instance.start()
    return ActionLogger._instance


def log(msg: str, tag: str = "INFO"):
    """간편 로그 함수."""
    try:
        lg = get_logger()
        lg.log(msg, tag)
    except Exception:
        print(f"[LOG-FAIL] {msg}")


# ═══════════════════════════════════════════════════════
# pyautogui 래핑: 모든 키보드/마우스 액션 로그
# ═══════════════════════════════════════════════════════

def install_pyautogui_hooks():
    """pyautogui의 주요 함수를 monkey-patch해서 액션 로그 자동 기록.
    NENOVA_NO_ACTION_LOG=1 이면 훅 설치 스킵 (클릭 오버헤드/간섭 제거).
    """
    if _is_disabled():
        print("[LOG] NENOVA_NO_ACTION_LOG=1 → pyautogui 훅 스킵", flush=True)
        return
    import pyautogui

    orig_click = pyautogui.click
    orig_double = pyautogui.doubleClick
    orig_press = pyautogui.press
    orig_hotkey = pyautogui.hotkey
    orig_scroll = pyautogui.scroll
    orig_write = pyautogui.write
    orig_moveto = pyautogui.moveTo

    def _click(*args, **kw):
        x = args[0] if args else kw.get("x")
        y = args[1] if len(args) > 1 else kw.get("y")
        lg = get_logger()
        action = f"click ({x},{y})"
        lg.record_action(action)
        lg.log(action, "MOUSE")
        return orig_click(*args, **kw)

    def _dbl(*args, **kw):
        x = args[0] if args else kw.get("x")
        y = args[1] if len(args) > 1 else kw.get("y")
        lg = get_logger()
        action = f"doubleClick ({x},{y})"
        lg.record_action(action)
        lg.log(action, "MOUSE")
        return orig_double(*args, **kw)

    def _press(key, *args, **kw):
        lg = get_logger()
        action = f"press '{key}'"
        lg.record_action(action)
        lg.log(action, "KEY")
        return orig_press(key, *args, **kw)

    def _hotkey(*keys, **kw):
        lg = get_logger()
        action = f"hotkey {'+'.join(str(k) for k in keys)}"
        lg.record_action(action)
        lg.log(action, "KEY")
        return orig_hotkey(*keys, **kw)

    def _scroll(clicks, *args, **kw):
        x = kw.get("x") or (args[0] if args else None)
        y = kw.get("y") or (args[1] if len(args) > 1 else None)
        lg = get_logger()
        action = f"scroll {clicks} at ({x},{y})"
        lg.record_action(action)
        lg.log(action, "MOUSE")
        return orig_scroll(clicks, *args, **kw)

    def _write(msg, *args, **kw):
        lg = get_logger()
        action = f"write '{str(msg)[:30]}'"
        lg.record_action(action)
        lg.log(action, "KEY")
        return orig_write(msg, *args, **kw)

    def _moveto(*args, **kw):
        x = args[0] if args else kw.get("x")
        y = args[1] if len(args) > 1 else kw.get("y")
        lg = get_logger()
        lg.record_action(f"moveTo ({x},{y})")
        return orig_moveto(*args, **kw)

    pyautogui.click = _click
    pyautogui.doubleClick = _dbl
    pyautogui.press = _press
    pyautogui.hotkey = _hotkey
    pyautogui.scroll = _scroll
    pyautogui.write = _write
    pyautogui.moveTo = _moveto
