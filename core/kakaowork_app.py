"""
카카오워크 PC 앱 화면 자동화 — 이미지/파일 업로드

검증 완료된 작동 방식 (2026-04-10):
1. Bot API로 대상 방에 텍스트 전송 → 방이 목록 맨 위로 올라옴
2. 카카오워크 앱 활성화 → 왼쪽 패널 첫 번째 방 클릭
3. 채팅 입력란 클릭 (포커스 확보)
4. Ctrl+T → Windows 파일 다이얼로그 → 경로 붙여넣기 → Enter
5. 전송 확인 팝업 → Enter
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pyautogui
import pygetwindow as gw
import pyperclip
import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

KAKAOWORK_TITLE = "카카오워크"
DATA_DIR = Path(__file__).parent.parent / "data"
NV_MAPPING_FILE = DATA_DIR / "room_mapping_nv.json"

# 왼쪽 패널 첫 번째 방 (창 기준 상대좌표)
FIRST_ROOM_X_OFFSET = 80
FIRST_ROOM_Y_OFFSET = 60


def _load_nv_mapping() -> dict:
    if NV_MAPPING_FILE.exists():
        with open(NV_MAPPING_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _send_bot_api(conv_id: str, text: str) -> bool:
    """Bot API로 텍스트 전송 (방을 목록 맨 위로 올리기 위함)"""
    token = os.getenv("KAKAOWORK_BOT_TOKEN")
    if not token:
        return False
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        resp = requests.post(
            "https://api.kakaowork.com/v1/messages.send",
            headers=headers,
            json={"conversation_id": conv_id, "text": text},
            timeout=10,
        )
        return resp.json().get("success", False)
    except Exception:
        return False


def find_kakaowork_window():
    """카카오워크 앱 창을 찾아 활성화"""
    windows = gw.getWindowsWithTitle(KAKAOWORK_TITLE)
    if not windows:
        raise RuntimeError("카카오워크 앱이 실행 중이지 않습니다.")
    main = max(windows, key=lambda w: w.width * w.height)
    if main.isMinimized:
        main.restore()
        time.sleep(0.3)
    try:
        main.activate()
    except Exception:
        main.minimize()
        time.sleep(0.2)
        main.restore()
        time.sleep(0.3)
    time.sleep(0.5)
    return main


def upload_file_to_room(file_path: Path, window) -> bool:
    """현재 열린 방에 파일 1개 업로드 (Ctrl+T 방식)"""
    if not file_path.exists():
        return False

    # 채팅 입력란 클릭 (포커스)
    chat_x = window.left + window.width // 3
    chat_y = window.top + window.height - 50
    pyautogui.click(chat_x, chat_y)
    time.sleep(0.3)

    # Ctrl+T → 파일 다이얼로그
    pyautogui.hotkey("ctrl", "t")
    time.sleep(1.5)

    # 파일 경로 붙여넣기
    pyperclip.copy(str(file_path.resolve()))
    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.5)

    # Enter → 파일 선택
    pyautogui.press("enter")
    time.sleep(2.0)

    # Enter → 전송 확인
    pyautogui.press("enter")
    time.sleep(1.0)

    return True


def upload_to_nv_room(kakaotalk_room_name: str, files: list[Path]):
    """
    카카오워크 NV 미러 방에 파일 업로드.

    1. Bot API로 텍스트 전송 → 방이 맨 위로
    2. 앱에서 첫 번째 방 클릭
    3. Ctrl+T로 각 파일 업로드

    Args:
        kakaotalk_room_name: 카카오톡 원본 방 이름
        files: 업로드할 파일 목록
    """
    mapping = _load_nv_mapping()
    info = mapping.get(kakaotalk_room_name)
    if not info:
        print(f"       [WARN] '{kakaotalk_room_name}' NV 매핑 없음")
        return

    # 1. Bot API → 방을 맨 위로
    _send_bot_api(info["conv_id"], f"[{info['nv_code']}] {len(files)}개 파일 수신")
    time.sleep(1.5)

    # 2. 카카오워크 앱 활성화
    window = find_kakaowork_window()

    # 3. 첫 번째 방 클릭
    pyautogui.click(window.left + FIRST_ROOM_X_OFFSET, window.top + FIRST_ROOM_Y_OFFSET)
    time.sleep(1.0)

    # 4. 각 파일 업로드
    for f in files:
        try:
            ok = upload_file_to_room(f, window)
            if ok:
                print(f"       [UPLOAD] {info['nv_code']} {f.name} OK")
            else:
                print(f"       [WARN] {f.name} not found")
        except Exception as e:
            print(f"       [ERROR] {info['nv_code']} {f.name}: {e}")
