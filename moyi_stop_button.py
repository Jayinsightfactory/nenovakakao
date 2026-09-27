"""Always-on-top emergency stop button for the MOYI Kakao worker.

Usage: ``pythonw moyi_stop_button.py`` (started automatically with the worker).
One click persists the pause marker and terminates this installation's worker
through ``core.moyi_control.emergency_stop``. A separate start button resumes
the existing worker or starts one through the operations console helper.
"""
from __future__ import annotations

import sys
import threading
import tkinter as tk
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from core.moyi_control import emergency_stop, is_paused, worker_processes  # noqa: E402

LOCK = ROOT / 'data' / 'moyi_stop_button.lock'
WIDTH, HEIGHT = 264, 66
# pyautogui's fail-safe lives in the exact screen corners; stay clear of them
# and of a maximized window's close button.
MARGIN_RIGHT, MARGIN_TOP = 28, 44


def single_instance():
    """Hold an exclusive lock for the lifetime of this process."""
    import msvcrt
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    handle = LOCK.open('a+')
    try:
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        handle.close()
        return None
    return handle


class StopButton(tk.Tk):
    def __init__(self):
        super().__init__()
        self.overrideredirect(True)
        self.attributes('-topmost', True)
        self.attributes('-alpha', 0.92)
        x = self.winfo_screenwidth() - WIDTH - MARGIN_RIGHT
        self.geometry(f'{WIDTH}x{HEIGHT}+{x}+{MARGIN_TOP}')
        self.stopping = False
        self.starting = False
        actions = tk.Frame(self)
        actions.pack(fill='both', expand=True)
        self.start_button = tk.Button(actions, text='▶ 작업 시작', font=('Malgun Gothic', 12, 'bold'),
                                     bg='#207a3c', fg='white', bd=0, cursor='hand2',
                                     command=self.start)
        self.start_button.pack(side='left', fill='both', expand=True)
        self.button = tk.Button(actions, text='■ 동작 정지', font=('Malgun Gothic', 12, 'bold'),
                                fg='white', bd=0, relief='flat', cursor='hand2',
                                activeforeground='white', command=self.stop)
        self.button.pack(side='left', fill='both', expand=True)
        self.status = tk.Label(self, font=('Malgun Gothic', 8), fg='white')
        self.status.pack(fill='x')
        # Drag by the status strip so the button can be moved off other UI.
        self.status.bind('<ButtonPress-1>', self._grab)
        self.status.bind('<B1-Motion>', self._drag)
        self.refresh()

    def _grab(self, event):
        self._offset = (event.x_root - self.winfo_x(), event.y_root - self.winfo_y())

    def _drag(self, event):
        self.geometry(f'+{event.x_root - self._offset[0]}+{event.y_root - self._offset[1]}')

    def stop(self):
        if self.stopping or self.starting:
            return
        self.stopping = True
        self._paint('#7a1f1f', '정지하는 중…')

        def run():
            try:
                emergency_stop('floating stop button')
                self.failed = False
            except Exception:
                self.failed = True
            finally:
                self.stopping = False
        threading.Thread(target=run, daemon=True).start()

    def start(self):
        if self.stopping or self.starting:
            return
        self.starting = True
        self.failed = False
        self.start_button.configure(state='disabled')
        self._paint('#207a3c', '시작하는 중…')

        def run():
            from core.moyi_control import set_paused
            from moyi_console import start_worker_if_needed
            try:
                set_paused(False)
                start_worker_if_needed()
                self.start_failed = False
            except Exception:
                set_paused(True)
                self.start_failed = True
            finally:
                self.starting = False
        threading.Thread(target=run, daemon=True).start()

    def _paint(self, colour, text):
        self.button.configure(bg=colour, activebackground=colour)
        self.status.configure(bg=colour, text=text)
        self.configure(bg=colour)

    def refresh(self):
        if not self.stopping and not self.starting:
            try:
                running = any(worker_processes())
                paused = is_paused()
            except Exception:
                running, paused = False, True
            self.start_button.configure(state='disabled' if running and not paused else 'normal')
            if getattr(self, 'start_failed', False):
                self._paint('#b35900', '시작 실패 · 콘솔 확인')
            elif getattr(self, 'failed', False):
                self._paint('#b35900', '정지 실패 · 콘솔 확인')
            elif running and not paused:
                self._paint('#c62828', '실행 중 · 누르면 즉시 정지')
            elif running:
                self._paint('#b35900', '일시정지 · 워커 대기 중')
            else:
                self._paint('#555555', '정지됨')
        # Other apps can take the topmost slot; claim it back periodically.
        self.attributes('-topmost', True)
        self.lift()
        self.after(1000, self.refresh)


def main():
    lock = single_instance()
    if lock is None:
        return 0
    StopButton().mainloop()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
