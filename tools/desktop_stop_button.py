"""바탕화면 강제정지 버튼 — 모니터와 '별개 프로세스'로 뜨는 항상-위 작은 창.

왜 별개 프로세스인가:
  - 같은 프로세스 안에서 Tk 창을 여러 개 띄우면 Tcl_AsyncDelete 크래시 (main.py 주석 참고).
  - 또, 모니터의 화면 자동화가 이 버튼을 '오클릭'해도 — 별개 프로세스라 os._exit 크래시가
    아니라 graceful 정지(=_STOP 생성)만 유발한다. (2026-06-04 오버레이/액션로그 버튼 사고 방지)

동작:
  - 🛑 강제정지 클릭 → data/_STOP 생성(=사용자 정지 마커) → 4초 graceful 대기 →
    그래도 안 멈추면 monitor(main.py) 프로세스 강제 종료(force).
  - 모니터가 (핫키/stop_nenova.bat/이 버튼 등으로) 멈추면 _STOP 이 생기므로 창도 자동으로 닫힘.

실행:
  - run_nenova_realtime.bat 이 모니터와 함께 자동으로 띄움.
  - 수동: python tools/desktop_stop_button.py
"""
import sys
import time
import threading
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STOP_FILE = ROOT / "data" / "_STOP"

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def _create_stop() -> None:
    try:
        STOP_FILE.parent.mkdir(parents=True, exist_ok=True)
        STOP_FILE.write_text(
            "stop requested by 바탕화면 강제정지 버튼\n", encoding="utf-8")
    except Exception:
        pass


_PS_FIND = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
            "Where-Object { $_.CommandLine -like '*main.py*' }")


def _monitor_running() -> bool:
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command", f"@({_PS_FIND}).Count"],
            capture_output=True, text=True, timeout=10)
        return (r.stdout or "").strip() not in ("", "0")
    except Exception:
        return False


def _force_kill_monitor() -> None:
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"{_PS_FIND} | ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force }}"],
            timeout=10)
    except Exception:
        pass


def main() -> None:
    import tkinter as tk
    root = tk.Tk()
    root.title("강제정지")
    root.attributes("-topmost", True)
    try:
        root.attributes("-toolwindow", True)  # 작업표시줄에서 숨김
    except Exception:
        pass
    root.resizable(False, False)

    W, H = 210, 120
    # 모니터 클릭 경로(카톡 좌측 x<350, 워크 룸목록 x~728)와 겹치지 않게 우하단.
    try:
        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()
        x, y = sw - W - 24, sh - H - 70
    except Exception:
        x, y = 1280, 800
    root.geometry(f"{W}x{H}+{x}+{y}")
    root.configure(bg="#141414")

    status = tk.StringVar(value="🔴 모니터 작동 중")
    tk.Label(root, textvariable=status, bg="#141414", fg="#eaeaea",
             font=("맑은 고딕", 9, "bold")).pack(pady=(10, 6))

    _clicked = {"v": False}

    def _do_stop():
        if _clicked["v"]:
            return
        _clicked["v"] = True
        status.set("정지 요청 중...")
        _create_stop()

        def _bg():
            # graceful 우선 (≈4초), 안 멈추면 force kill
            stopped = False
            for _ in range(8):
                time.sleep(0.5)
                if not _monitor_running():
                    stopped = True
                    break
            if not stopped:
                status.set("강제 종료 중...")
                _force_kill_monitor()
            status.set("✅ 정지됨")
            time.sleep(1.0)
            try:
                root.destroy()
            except Exception:
                pass
        threading.Thread(target=_bg, daemon=True).start()

    btn = tk.Button(root, text="🛑 강제정지", bg="#dc3545", fg="white",
                    activebackground="#a51d2d", activeforeground="white",
                    font=("맑은 고딕", 14, "bold"), relief="raised", bd=3,
                    cursor="hand2", command=_do_stop)
    btn.pack(fill="x", padx=12, ipady=8)

    # 모니터가 다른 방법(핫키/stop_nenova.bat)으로 멈추면 _STOP 이 생김 → 창 자동 닫기.
    # 시작 직후 stale _STOP 오작동 방지로 5초 grace.
    def _watch():
        if _clicked["v"]:
            return
        try:
            if STOP_FILE.exists():
                root.destroy()
                return
        except Exception:
            pass
        root.after(2000, _watch)
    root.after(5000, _watch)

    root.mainloop()


if __name__ == "__main__":
    main()
