"""Explicit read-only foreground observer; never activates a window or sends keys."""
import argparse
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--room', required=True, help='Exact already-open Kakao room title')
    parser.add_argument('--roi', required=True, nargs=4, type=int, metavar=('X','Y','WIDTH','HEIGHT'),
                        help='Calibrated client-relative message area; exclude input box and toolbar')
    parser.add_argument('--seconds', type=int, default=30)
    parser.add_argument('--judge', '--jev', dest='jev', action='store_true', help='Classify changed OCR text using subscription CLI only')
    args = parser.parse_args()
    from dotenv import load_dotenv
    from core.moyi_control import worker_processes
    from core.screen_ocr import read
    from core.jev_shadow import Shadow, Jev
    import win32gui, win32process, psutil
    from PIL import ImageGrab
    root = Path(__file__).resolve().parents[1]
    load_dotenv(root / '.env')
    if not 1 <= args.seconds <= 3600 or min(args.roi) < 0 or min(args.roi[2:]) == 0:
        parser.error('Invalid duration or ROI')
    shadow = Shadow(root/'data/jev_shadow.sqlite3', Jev() if args.jev else None)
    last = None
    try:
        end = time.monotonic() + args.seconds
        while time.monotonic() < end:
            if list(worker_processes()):
                raise RuntimeError('Existing UI worker active; observer exits')
            hwnd = win32gui.GetForegroundWindow()
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if win32gui.GetWindowText(hwnd) != args.room or psutil.Process(pid).name().lower() != 'kakaotalk.exe':
                print('waiting_exact_foreground_room', flush=True)
                time.sleep(2); continue
            x,y,w,h = args.roi
            rect = win32gui.GetClientRect(hwnd)
            if x+w > rect[2] or y+h > rect[3]:
                raise RuntimeError('ROI outside client area; recalibrate')
            left,top = win32gui.ClientToScreen(hwnd,(x,y))
            shot = ImageGrab.grab((left,top,left+w,top+h))
            if hwnd != win32gui.GetForegroundWindow():
                continue
            fingerprint = hashlib.sha256(shot.tobytes()).hexdigest()
            if fingerprint != last:
                with tempfile.TemporaryDirectory(prefix='nenova-screen-') as tmp:
                    image = Path(tmp)/'frame.png';shot.save(image)
                    lines = read(image)
                if hwnd != win32gui.GetForegroundWindow():
                    continue
                print(shadow.observe(args.room,lines),flush=True)
                last = fingerprint
            time.sleep(2)
    finally:
        shadow.db.close()

if __name__ == '__main__':
    main()
