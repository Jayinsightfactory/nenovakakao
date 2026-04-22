"""
시각 디버깅: 모든 pyautogui 클릭에 마우스 이동 + 빨간 원 잔상 표시.

main.py 시작 시 환경변수 VISUAL_DEBUG=1 또는 enable_visual_debug() 호출.
"""
from __future__ import annotations

import os
import threading
import time

import pyautogui


def _show_overlay(x: int, y: int, duration: float = 0.4, color: str = "red") -> None:
    """좌표에 빨간 원을 띄우고 duration 후 사라짐. 별도 스레드."""
    def _run():
        try:
            import tkinter as tk
            root = tk.Tk()
            root.overrideredirect(True)
            root.attributes("-topmost", True)
            root.attributes("-alpha", 0.7)
            try:
                root.attributes("-transparentcolor", "black")
            except Exception:
                pass
            size = 50
            root.geometry(f"{size}x{size}+{x - size // 2}+{y - size // 2}")
            canvas = tk.Canvas(root, width=size, height=size, bg="black", highlightthickness=0)
            canvas.pack()
            canvas.create_oval(4, 4, size - 4, size - 4, outline=color, width=4)
            canvas.create_oval(size // 2 - 3, size // 2 - 3, size // 2 + 3, size // 2 + 3,
                               fill=color, outline=color)
            root.after(int(duration * 1000), root.destroy)
            root.mainloop()
        except Exception:
            pass
    threading.Thread(target=_run, daemon=True).start()


def enable_visual_debug() -> None:
    """pyautogui의 클릭/입력 함수들을 wrap해 시각 표시 추가."""
    _orig_click = pyautogui.click
    _orig_double = pyautogui.doubleClick
    _orig_right = pyautogui.rightClick
    _orig_triple = pyautogui.tripleClick
    _orig_move = pyautogui.moveTo

    def _wrap_click(orig, color):
        def wrapped(x=None, y=None, **kw):
            if x is not None and y is not None:
                _show_overlay(int(x), int(y), 0.4, color)
                pyautogui.moveTo(int(x), int(y), duration=0.15)
                time.sleep(0.05)
            return orig(x, y, **kw)
        return wrapped

    pyautogui.click = _wrap_click(_orig_click, "red")
    pyautogui.doubleClick = _wrap_click(_orig_double, "orange")
    pyautogui.rightClick = _wrap_click(_orig_right, "yellow")
    pyautogui.tripleClick = _wrap_click(_orig_triple, "magenta")

    print("[VISUAL] 클릭 시각 디버깅 활성 (빨강=click, 주황=double, 노랑=right, 자홍=triple)", flush=True)


# 자동 활성: 환경변수 VISUAL_DEBUG=1
if os.getenv("VISUAL_DEBUG", "0") == "1":
    try:
        enable_visual_debug()
    except Exception as e:
        print(f"[VISUAL] 활성 실패: {e}", flush=True)
