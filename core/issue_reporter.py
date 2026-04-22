"""
이슈 보고 시스템

이슈 발생 시:
  1. 화면에 팝업 표시 (자동화 일시정지)
  2. 카카오워크 이슈전용방에 내용 전송
  3. 관리자가 팝업 닫으면 자동화 재개

이슈전용방은 최초 실행 시 Bot API로 자동 생성.
"""
from __future__ import annotations

import json
import os
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import scrolledtext

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env", override=True)

DATA_DIR = Path(__file__).parent.parent / "data"
ISSUE_ROOM_FILE = DATA_DIR / "issue_room.json"

API_BASE = "https://api.kakaowork.com/v1"
ADMIN_USER_ID = 11826656


def _headers() -> dict:
    token = os.getenv("KAKAOWORK_BOT_TOKEN")
    if not token:
        raise RuntimeError("KAKAOWORK_BOT_TOKEN이 .env에 설정되지 않았습니다.")
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def _get_issue_room_id() -> str:
    """
    이슈전용방 conversation_id를 반환.
    없으면 Bot API로 새로 생성.
    """
    if ISSUE_ROOM_FILE.exists():
        with open(ISSUE_ROOM_FILE, encoding="utf-8") as f:
            data = json.load(f)
            return data["conversation_id"]

    # 신규 생성
    print("[ISSUE] 이슈전용 카카오워크 방 생성 중...")
    resp = requests.post(
        f"{API_BASE}/conversations.open",
        headers=_headers(),
        json={
            "user_ids": [ADMIN_USER_ID],
            "conversation_name": "[에이전트] 이슈 알림",
        },
        timeout=10,
    )
    data = resp.json()
    if not data.get("success"):
        raise RuntimeError(f"이슈전용방 생성 실패: {data}")

    conv_id = str(data["conversation"]["id"])

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(ISSUE_ROOM_FILE, "w", encoding="utf-8") as f:
        json.dump({"conversation_id": conv_id, "name": "[에이전트] 이슈 알림"}, f, ensure_ascii=False, indent=2)

    print(f"[ISSUE] 이슈전용방 생성 완료: {conv_id}")
    return conv_id


def send_issue_to_kakaowork(title: str, detail: str) -> bool:
    """이슈전용 워크방에 이슈 내용 전송"""
    try:
        conv_id = _get_issue_room_id()
        text = (
            f"[에이전트 이슈] {title}\n"
            f"시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"---\n"
            f"{detail}"
        )
        resp = requests.post(
            f"{API_BASE}/messages.send",
            headers=_headers(),
            json={"conversation_id": conv_id, "text": text},
            timeout=10,
        )
        return resp.json().get("success", False)
    except Exception as e:
        print(f"[ERROR] 이슈 워크 전송 실패: {e}")
        return False


def show_issue_popup(title: str, detail: str) -> None:
    """
    이슈 팝업 표시 (블로킹).
    관리자가 '확인' 버튼을 누를 때까지 자동화 일시정지.
    """
    dismissed = threading.Event()

    def _popup():
        root = tk.Tk()
        root.title(f"네노바 에이전트 - 이슈 발생")
        root.attributes("-topmost", True)
        root.configure(bg="#2d2d2d")

        # 화면 중앙 배치
        w, h = 500, 350
        sx = root.winfo_screenwidth() // 2 - w // 2
        sy = root.winfo_screenheight() // 2 - h // 2
        root.geometry(f"{w}x{h}+{sx}+{sy}")

        # 제목
        tk.Label(
            root, text=f"  {title}",
            fg="#FF4444", bg="#2d2d2d",
            font=("맑은 고딕", 14, "bold"), anchor="w",
        ).pack(fill=tk.X, padx=15, pady=(15, 5))

        # 시각
        tk.Label(
            root, text=datetime.now().strftime("  발생: %Y-%m-%d %H:%M:%S"),
            fg="#AAAAAA", bg="#2d2d2d",
            font=("맑은 고딕", 9), anchor="w",
        ).pack(fill=tk.X, padx=15)

        # 상세 내용
        txt = scrolledtext.ScrolledText(
            root, wrap=tk.WORD, width=55, height=10,
            bg="#1a1a1a", fg="#EEEEEE",
            font=("Consolas", 10),
            insertbackground="white",
        )
        txt.pack(padx=15, pady=10, fill=tk.BOTH, expand=True)
        txt.insert(tk.END, detail)
        txt.config(state=tk.DISABLED)

        # 확인 버튼
        btn = tk.Button(
            root, text="확인 후 재개",
            command=lambda: (dismissed.set(), root.destroy()),
            bg="#FF4444", fg="white",
            font=("맑은 고딕", 11, "bold"),
            width=20, height=2,
            relief=tk.FLAT, cursor="hand2",
        )
        btn.pack(pady=(0, 15))

        # 창 닫기(X)도 재개로 처리
        root.protocol("WM_DELETE_WINDOW", lambda: (dismissed.set(), root.destroy()))
        root.mainloop()

    # 별도 스레드에서 팝업 (tkinter는 메인 스레드 제약 있지만 독립 Tk()는 가능)
    popup_thread = threading.Thread(target=_popup, daemon=True)
    popup_thread.start()

    # 관리자가 닫을 때까지 대기 (블로킹)
    dismissed.wait()


def report_issue(title: str, detail: str):
    """
    이슈 보고 통합 함수.

    1. 상태 오버레이를 이슈 모드로 전환
    2. 카카오워크 이슈방에 전송
    3. 팝업 표시 (자동화 일시정지)
    4. 관리자 확인 후 오버레이 복귀
    """
    # 오버레이 상태 변경
    try:
        from core.status_overlay import get_overlay
        get_overlay().set_issue(title)
    except Exception:
        pass

    # 워크 전송
    send_issue_to_kakaowork(title, detail)

    # 팝업 (블로킹 — 관리자 확인까지 대기)
    print(f"[ISSUE] {title}")
    print(f"        자동화 일시정지. 관리자 확인 대기 중...")
    show_issue_popup(title, detail)
    print(f"[ISSUE] 관리자 확인 완료. 자동화 재개.")

    # 오버레이 복귀
    try:
        from core.status_overlay import get_overlay
        get_overlay().set_working()
    except Exception:
        pass
