"""Windows global stop shortcut. The console must remain open."""
import ctypes
from ctypes import wintypes
import threading


class StopHotkey:
    def __init__(self, callback):
        self.callback = callback
        self.ready = threading.Event()
        self.registered = False
        self.thread_id = None
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        self.ready.wait(3)

    def _run(self):
        user32 = ctypes.windll.user32
        self.thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        # CTRL + ALT + F10, suppress autorepeat.
        self.registered = bool(user32.RegisterHotKey(None, 1, 0x4003, 0x79))
        self.ready.set()
        if not self.registered:
            return
        try:
            message = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                if message.message == 0x0312:
                    self.callback()
        finally:
            user32.UnregisterHotKey(None, 1)
            self.registered = False

    def close(self):
        if self.thread_id and self.registered:
            ctypes.windll.user32.PostThreadMessageW(self.thread_id, 0x0012, 0, 0)
            self.thread.join(timeout=2)
