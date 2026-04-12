# -*- coding: utf-8 -*-
"""미수집 5개 방 자동 수집 스크립트"""
import pyautogui, win32gui, ctypes, time, sys, pyperclip
sys.path.insert(0, 'C:/Users/USER/nenova_agent')
from core.message_extractor import save_chat_with_ctrl_s, read_and_process_saved_file, close_chat_room

pyautogui.FAILSAFE = False

missing = ['네노바&선울', '3.미우신라방', '발번호및 입고수량확인방', '주님방', '영업지원팀']

KAKAO_HWND = 263462

def activate_kakao():
    ctypes.windll.user32.keybd_event(0x12, 0, 0, 0)
    ctypes.windll.user32.keybd_event(0x12, 0, 2, 0)
    try:
        win32gui.SetForegroundWindow(KAKAO_HWND)
    except:
        pass
    time.sleep(0.5)

def capture(label):
    safe = label.replace('/', '_').replace('&', '_')[:20]
    img = pyautogui.screenshot(region=(0, 103, 500, 900))
    img.save(f'C:/Users/USER/nenova_agent/data/screen_{safe}.png')

activate_kakao()
capture('before')
print('시작')

ok = []
for room in missing:
    print(f'\n=== [{room}] ===', flush=True)
    activate_kakao()
    time.sleep(0.3)

    # Ctrl+F 방 검색
    pyautogui.hotkey('ctrl', 'f')
    time.sleep(1.0)

    # 방 이름 입력
    pyperclip.copy(room)
    pyautogui.hotkey('ctrl', 'v')
    time.sleep(0.5)
    pyautogui.press('enter')
    time.sleep(1.0)

    # 방 열기
    pyautogui.press('enter')
    time.sleep(1.5)
    capture(f'room_{room[:8]}')

    # Ctrl+S 저장
    saved = save_chat_with_ctrl_s()
    if saved:
        r = read_and_process_saved_file(saved)
        if r:
            ok.append(r['room_name'])
            print(f'  OK: {r["room_name"]} ({len(r["delta"])}자)', flush=True)
        else:
            print(f'  변경없음/실패', flush=True)
    else:
        print(f'  저장실패', flush=True)

    close_chat_room()
    time.sleep(0.3)
    pyautogui.press('escape')
    time.sleep(0.5)

print(f'\n완료: {len(ok)}개 수집 - {ok}')
