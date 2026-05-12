"""
카톡 좌측 채팅 리스트를 캡쳐 + 2x 확대 + Claude Vision OCR 으로 방 이름 정확히 추출.

용도: Gemini OCR 의 한국어 오인식 회피.
"""
from __future__ import annotations

import base64
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env", override=True)


def main() -> int:
    import pyautogui
    from PIL import Image, ImageGrab
    from core.window_manager import focus_kakaotalk

    # fail-safe
    sw, sh = pyautogui.size()
    pyautogui.moveTo(sw // 2, sh // 2, duration=0)
    time.sleep(0.3)

    window = focus_kakaotalk()
    time.sleep(0.6)

    # 채팅 리스트 영역 클릭 후 맨 위로 (Home)
    pyautogui.click(window.left + 140, window.top + 400)
    time.sleep(0.2)
    pyautogui.press("home")
    time.sleep(0.6)

    # 좌측 패널 영역 캡쳐 (y 130 검색바 아래 ~ 하단)
    bbox = (
        window.left,
        window.top + 130,
        window.left + 280,
        window.top + window.height - 30,
    )
    img = ImageGrab.grab(bbox=bbox)
    print(f"캡쳐 영역: {bbox}  size={img.size}")

    # 2x 확대
    w, h = img.size
    img2x = img.resize((w * 2, h * 2), Image.LANCZOS)
    out_dir = ROOT / "data"
    out_dir.mkdir(exist_ok=True)
    top_path = out_dir / "chat_list_top.png"
    zoom_path = out_dir / "chat_list_2x.png"
    img.save(top_path)
    img2x.save(zoom_path, optimize=True)
    print(f"원본 저장: {top_path.name} {img.size} {top_path.stat().st_size:,}B")
    print(f"2x 확대:  {zoom_path.name} {img2x.size} {zoom_path.stat().st_size:,}B")

    # Claude Vision OCR
    import anthropic
    client = anthropic.Anthropic()
    with open(zoom_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()

    prompt = (
        "이 카카오톡 채팅방 리스트 스크린샷에서 보이는 채팅방 이름을 "
        "위에서부터 순서대로 추출해줘. "
        "출력 형식: 한 줄에 한 방 이름. 번호 매기지 말 것. "
        "멤버 수(예: 3), 시간(예: 오후 4:30), 미리보기 메시지, 안 읽음 카운트, "
        "광고, 안내 문구는 모두 제외하고 순수한 방 이름만. "
        "방 이름은 한글/영어/숫자/특수문자 가능. "
        "각 방은 보통 한 줄로 표시되니까 줄 단위로 식별해."
    )

    print()
    print("=== Claude Vision OCR (Haiku) ===")
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=2048,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": b64,
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ],
    )
    text = resp.content[0].text
    print(text)
    print()
    print(f"토큰: input={resp.usage.input_tokens} output={resp.usage.output_tokens}")

    # 결과 라인 파싱 → JSON 저장
    lines = [ln.strip(" -•").strip() for ln in text.splitlines() if ln.strip()]
    # 번호 prefix 제거 (1. xxx, 1) xxx, [1] xxx)
    import re
    cleaned = []
    for ln in lines:
        ln = re.sub(r"^\s*[\[\(]?\s*\d+[\.\)\]]\s*", "", ln).strip()
        if ln:
            cleaned.append(ln)
    print(f"\n파싱된 방 이름: {len(cleaned)}개")
    for i, n in enumerate(cleaned, 1):
        print(f"  {i:2d}. {n}")

    import json
    (out_dir / "chat_list_ocr.json").write_text(
        json.dumps({"rooms": cleaned, "raw": text}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n저장: data/chat_list_ocr.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
