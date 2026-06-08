"""W→K 필터 진단(읽기 전용, 화면 조작 없음).

저장된 워크룸 캡처(captures/_kwroom_*.png)를 Vision 으로 재추출해
각 메시지의 sender/content 와 cycle_once_v3 의 5단 필터 판정을 출력한다.
'왜 워크네이티브 신규 없음' 이 나오는지(어느 필터가 막는지) 실증.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# .env 로드
for ln in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
    ln = ln.strip()
    if ln and not ln.startswith("#") and "=" in ln:
        import os
        k, v = ln.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

from core.work_vision_reader import extract_messages
from core import work_bridge as wb


def main():
    caps = sorted((ROOT / "captures").glob("_kwroom_*.png"))
    if len(sys.argv) > 1:
        caps = [Path(sys.argv[1])]
    if not caps:
        print("캡처 없음 (captures/_kwroom_*.png)")
        return
    cap = caps[-1]
    print(f"[진단] 캡처: {cap.name}")
    print(f"[진단] 워크 멤버(정규화): {wb._work_member_names()}")
    msgs = extract_messages(cap)
    print(f"[진단] Vision 추출 메시지 {len(msgs)}개\n")
    kk = "(테스트방)"
    for i, m in enumerate(msgs):
        sender = (m.get("sender") or "").strip()
        content = (m.get("content") or "").strip()
        verdict = "[SEND]"
        why = ""
        if wb._is_non_user_message(content):
            verdict, why = "[X f1]", "비사용자(날짜/읽음/URL)"
        elif wb._is_bot_system_preview(content) or wb._looks_like_mirror_header(content):
            verdict, why = "[X f2]", "봇시스템/미러헤더"
        elif wb._is_mirror_origin(m):
            verdict, why = "[X f3]", f"미러출신(sender='{sender}' 멤버아님/공백)"
        elif wb._is_our_message(kk, content):
            verdict, why = "[X f4]", "에코(직전 송신)"
        else:
            why = "통과 -> 카톡 전송됨"
        print(f"  [{i}] sender='{sender}'  {verdict}  {why}")
        print(f"      content: {content[:70]!r}")
    print()


if __name__ == "__main__":
    main()
