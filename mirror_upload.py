# -*- coding: utf-8 -*-
"""
모든 수집된 카카오톡 대화를 카카오워크 미러방에 업로드.
txt 파일을 읽어서 방 이름별로 매핑된 워크 방에 전송.
카카오워크 API 한도 고려: 3000자씩 분할, 1초 딜레이.
"""
import json
import os
import re
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path("C:/Users/USER/nenova_agent/.env"))

DATA_DIR = Path("C:/Users/USER/nenova_agent/data")
TXT_DIR = Path("C:/Users/USER/Downloads/카톡대화데이터")
ROOM_MAP = DATA_DIR / "room_mapping.json"

API_BASE = "https://api.kakaowork.com/v1"
BOT_TOKEN = os.getenv("KAKAOWORK_BOT_TOKEN")
MAX_MSG_LEN = 3000
DELAY = 1.0  # API 호출간 딜레이 (초)


def headers():
    return {
        "Authorization": f"Bearer {BOT_TOKEN}",
        "Content-Type": "application/json",
    }


def send_message(conv_id: str, text: str) -> bool:
    """카카오워크 방에 메시지 전송."""
    if len(text) > MAX_MSG_LEN:
        text = text[:MAX_MSG_LEN] + "\n... (잘림)"
    payload = {
        "conversation_id": int(conv_id),
        "text": text,
    }
    try:
        resp = requests.post(f"{API_BASE}/messages.send",
                             headers=headers(), json=payload, timeout=10)
        data = resp.json()
        return data.get("success", False)
    except Exception as e:
        print(f"    [ERROR] {e}")
        return False


def extract_room_name(txt_content: str) -> str:
    """txt 파일 첫 줄에서 방 이름 추출."""
    first_line = txt_content.strip().splitlines()[0] if txt_content.strip() else ""
    if "카카오톡 대화" in first_line:
        parts = first_line.split("카카오톡 대화")[0].strip()
        for suffix in ["님과", "임과", "과"]:
            if parts.endswith(suffix):
                parts = parts[:-len(suffix)].strip()
                break
        if parts:
            return parts
    return ""


def find_conv_id(room_name: str, mapping: dict) -> str | None:
    """방 이름으로 미러방 conversation_id 찾기. 유사 매칭 지원."""
    # 정확 매칭
    if room_name in mapping:
        return mapping[room_name]
    # 부분 매칭
    for key, cid in mapping.items():
        if room_name in key or key in room_name:
            return cid
        # 3글자 연속 매칭
        for i in range(len(room_name) - 2):
            if room_name[i:i+3] in key:
                return cid
    return None


def split_messages(content: str, chunk_size: int = MAX_MSG_LEN) -> list[str]:
    """긴 텍스트를 줄 단위로 분할."""
    lines = content.splitlines()
    chunks = []
    current = []
    current_len = 0

    for line in lines:
        line_len = len(line) + 1  # +1 for newline
        if current_len + line_len > chunk_size and current:
            chunks.append("\n".join(current))
            current = []
            current_len = 0
        current.append(line)
        current_len += line_len

    if current:
        chunks.append("\n".join(current))
    return chunks


def main():
    if not BOT_TOKEN:
        print("[FATAL] KAKAOWORK_BOT_TOKEN 없음!")
        sys.exit(1)

    with open(ROOM_MAP, encoding="utf-8") as f:
        mapping = json.load(f)

    print(f"미러방 매핑: {len(mapping)}개")
    print(f"txt 파일 디렉토리: {TXT_DIR}")

    txt_files = sorted(TXT_DIR.glob("*.txt"))
    print(f"txt 파일: {len(txt_files)}개\n")

    stats = {"success": 0, "fail": 0, "skip": 0, "total_chunks": 0}

    for txt_file in txt_files:
        content = txt_file.read_text(encoding="utf-8", errors="ignore")
        if not content.strip():
            continue

        room_name = extract_room_name(content)
        if not room_name:
            print(f"  [SKIP] {txt_file.name}: 방 이름 추출 실패")
            stats["skip"] += 1
            continue

        conv_id = find_conv_id(room_name, mapping)
        if not conv_id:
            print(f"  [SKIP] {txt_file.name}: '{room_name}' 미러방 없음")
            stats["skip"] += 1
            continue

        print(f"  [{room_name}] -> 미러방 {conv_id}")
        print(f"    파일: {txt_file.name} ({len(content):,}자)")

        # 분할 전송
        chunks = split_messages(content)
        print(f"    분할: {len(chunks)}개 메시지")

        sent = 0
        for i, chunk in enumerate(chunks):
            # 첫 번째 청크에 헤더 추가
            if i == 0:
                header = f"[카톡 미러] {room_name}\n{'='*30}\n"
                chunk = header + chunk

            ok = send_message(conv_id, chunk)
            if ok:
                sent += 1
            else:
                print(f"    [!] 청크 {i+1}/{len(chunks)} 전송 실패")

            time.sleep(DELAY)

        print(f"    전송: {sent}/{len(chunks)} 성공")
        stats["success"] += sent
        stats["fail"] += len(chunks) - sent
        stats["total_chunks"] += len(chunks)

    print(f"\n{'='*50}")
    print(f"완료:")
    print(f"  전송 성공: {stats['success']}건")
    print(f"  전송 실패: {stats['fail']}건")
    print(f"  스킵: {stats['skip']}건")
    print(f"  총 청크: {stats['total_chunks']}건")


if __name__ == "__main__":
    main()
