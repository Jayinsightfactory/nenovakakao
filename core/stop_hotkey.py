"""전역 강제정지 핫키 (기본 Ctrl+Alt+Q) → 즉시 정지.

배경: 화면 자동화(pyautogui 마우스 클릭)가 GUI 정지버튼을 '오클릭'해서 프로세스가
os._exit 되던 사고(2026-06-04) 방지를 위해 오버레이/액션로그 GUI 를 껐다. 그 결과
사람이 누를 정지 수단이 _STOP 파일밖에 안 남았다. 이 모듈은 마우스가 절대 누를 수 없는
'키보드 전역 핫키'를 제공한다(win32 RegisterHotKey, pywin32/keyboard 의존 없이 ctypes).

설계:
  - 전용 데몬 스레드에서 RegisterHotKey(NULL hwnd) → 그 스레드 메시지 큐로 WM_HOTKEY 수신.
  - 눌리면 _STOP 마커(시각) 남기고 [USER-STOP] 로그 후 os._exit(0).
    → 종료코드 0 = '사용자 의도 정지(오류 아님)'. (crash 는 exit!=0)
  - 모니터가 보내는 키(Ctrl+S/V/K/F, Enter 등)와 겹치지 않는 조합이라 자가발동 없음.
"""
from __future__ import annotations

import threading
import ctypes
from ctypes import wintypes

_started = False

# 기본 핫키: Ctrl+Alt+Q  (Q=Quit). 모니터가 절대 전송하지 않는 조합.
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
VK_Q = 0x51
_HOTKEY_ID = 0xB001


def _default_force_stop() -> None:
    # _STOP 마커(시각) → 외부/코드에서 '사용자 정지'로 확인 가능
    try:
        from core.stop_button import request_stop
        request_stop()
    except Exception:
        pass
    try:
        print("[USER-STOP] ⌨️ 강제정지 핫키(Ctrl+Alt+Q) — 사용자 의도 정지(오류 아님). "
              "정상 종료(exit 0).", flush=True)
    except Exception:
        pass
    import os
    os._exit(0)


def start_stop_hotkey(on_stop=None, *, hotkey_name: str = "Ctrl+Alt+Q") -> None:
    """전역 핫키 등록(데몬 스레드). 중복 호출 무시. 실패해도 예외 안 던짐."""
    global _started
    if _started:
        return
    _started = True
    cb = on_stop or _default_force_stop

    def _loop() -> None:
        user32 = ctypes.windll.user32
        try:
            ok = user32.RegisterHotKey(None, _HOTKEY_ID,
                                       MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, VK_Q)
        except Exception as e:
            print(f"[HOTKEY] 등록 예외: {e} — _STOP/stop_nenova.bat 로 정지하세요", flush=True)
            return
        if not ok:
            print(f"[HOTKEY] {hotkey_name} 등록 실패(이미 사용중?) — "
                  f"_STOP/stop_nenova.bat 로 정지하세요", flush=True)
            return
        print(f"[HOTKEY] 🛑 강제정지 핫키 등록됨: {hotkey_name} (언제든 누르면 즉시 정지)", flush=True)
        msg = wintypes.MSG()
        try:
            while True:
                r = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if r in (0, -1):
                    break
                if msg.message == WM_HOTKEY and msg.wParam == _HOTKEY_ID:
                    cb()
                    break
        finally:
            try:
                user32.UnregisterHotKey(None, _HOTKEY_ID)
            except Exception:
                pass

    threading.Thread(target=_loop, daemon=True, name="StopHotkey").start()
