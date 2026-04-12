# -*- coding: utf-8 -*-
import pyautogui, win32gui, win32con, ctypes, time, sys, pyperclip
sys.path.insert(0, 'C:/Users/USER/nenova_agent')
from core.message_extractor import save_chat_with_ctrl_s, read_and_process_saved_file, close_chat_room
pyautogui.FAILSAFE = False
rooms = ['네노바&선울', '발번호및 입고수량확인방']

KAKAO_HWND = 263462

def force_activate():
    """카카오톡을 강제로 포그라운드로"""
    for attempt in range(3):
        win32gui.ShowWindow(KAKAO_HWND, win32con.SW_SHOW)
        win32gui.ShowWindow(KAKAO_HWND, win32con.SW_RESTORE)
        time.sleep(0.1)
        ctypes.windll.user32.keybd_event(0x12, 0, 0, 0)
        ctypes.windll.user32.keybd_event(0x12, 0, 2, 0)
        ctypes.windll.user32.SetForegroundWindow(KAKAO_HWND)
        time.sleep(0.3)
        fg = win32gui.GetForegroundWindow()
        if fg == KAKAO_HWND:
            return True
        time.sleep(0.5)
    return False

def check_fg():
    fg = win32gui.GetForegroundWindow()
    ok = fg == KAKAO_HWND
    if not ok:
        print(f'  [!] 포커스 빠짐: {win32gui.GetWindowText(fg)} → 재활성화', flush=True)
        force_activate()
    return ok

# 시작
if not force_activate():
    print('카카오톡 활성화 실패!', flush=True)
    sys.exit(1)
print('카카오톡 활성화 OK', flush=True)

for room in rooms:
    print(f'\n=== [{room}] ===', flush=True)

    # 1. 강제 활성화
    force_activate()
    time.sleep(0.5)
    check_fg()

    # 2. 채팅탭 클릭
    rect = win32gui.GetWindowRect(KAKAO_HWND)
    pyautogui.click(rect[0] + 27, rect[1] + 115)
    time.sleep(0.3)

    # 3. 다시 확인 후 Ctrl+F
    check_fg()
    pyautogui.hotkey('ctrl', 'f')
    time.sleep(1.0)
    check_fg()

    # 4. 검색어 입력
    pyperclip.copy(room)
    pyautogui.hotkey('ctrl', 'v')
    time.sleep(0.5)

    # 5. Enter (검색)
    pyautogui.press('enter')
    time.sleep(1.0)

    # 6. Enter (방 열기)
    pyautogui.press('enter')
    time.sleep(1.5)

    # 캡처
    img = pyautogui.screenshot(region=(0, 0, 800, 600))
    img.save(f'C:/Users/USER/nenova_agent/data/screen_v2_{room[:4]}.png')

    # 7. Ctrl+S
    saved = save_chat_with_ctrl_s()
    if saved:
        r = read_and_process_saved_file(saved)
        if r: print(f'  OK {r["room_name"]} {len(r["delta"])}자', flush=True)
        else: print('  skip (변경없음)', flush=True)
    else: print('  fail (저장안됨)', flush=True)

    close_chat_room()
    time.sleep(0.3)
    pyautogui.press('escape')
    time.sleep(0.5)

print('\ndone')
