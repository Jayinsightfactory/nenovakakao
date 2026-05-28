"""
마감시각 기반 카톡 브로드캐스트 (작업스케줄러 트리거).

사용 (마감 1분 전쯤 작업스케줄러가 호출):
  python scripts/broadcast.py 20            # 20시 마감 방들에 송신
  python scripts/broadcast.py 20 --dry-run  # 대상 방만 보고 송신 안 함

흐름:
  1) data/broadcast_links.json 에서 hour 의 링크/안내문 로드 (빈 값이면 스킵).
  2) data/room_mapping.json 에서 제목이 ~<hour> 로 끝나는 방 추출.
  3) kakao_lock 우선(request) 획득 → 모니터/답장서버 양보.
  4) 각 방에 송신 + 실패 시 1회 재시도.
  5) 결과 data/broadcast_log/<날짜>_<hour>.json 기록 + 실패 시 워크 이슈방 알람.
  6) 멱등: 같은 날 같은 hour 이미 성공한 방은 스킵(스케줄러 중복발동 대비).

링크 설정 예시 (data/broadcast_links.json):
  { "20": "https://forms.example/orders" }
  { "20": {"text": "오늘 발주 링크", "url": "https://..."} }
"""
from __future__ import annotations

import json
import re
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

DATA = ROOT / "data"
LINKS_FILE = DATA / "broadcast_links.json"
MAPPING_FILE = DATA / "room_mapping.json"
LOG_DIR = DATA / "broadcast_log"

# 제목 끝 마감시각 패턴: "06~20", "10 ~ 22" 등 허용 (마지막 ~숫자).
_HOUR_RE = re.compile(r"~\s*(\d{1,2})\b")


def _extract_deadline(title: str) -> int | None:
    m = _HOUR_RE.search(title or "")
    if not m:
        return None
    h = int(m.group(1))
    return h if 0 <= h <= 23 else None


def _load_link(hour: int) -> tuple[str, str]:
    """hour → (text_prefix, url). 비어있으면 ('','')."""
    if not LINKS_FILE.exists():
        return ("", "")
    try:
        cfg = json.loads(LINKS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return ("", "")
    entry = cfg.get(str(hour))
    if not entry:
        return ("", "")
    if isinstance(entry, str):
        return ("", entry.strip())
    if isinstance(entry, dict):
        return ((entry.get("text") or "").strip(),
                (entry.get("url") or "").strip())
    return ("", "")


def _today_log_path(hour: int) -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    return LOG_DIR / f"{date.today().isoformat()}_{hour:02d}.json"


def _load_done(hour: int) -> set:
    p = _today_log_path(hour)
    if not p.exists():
        return set()
    try:
        return set(json.loads(p.read_text(encoding="utf-8")).get("done", []))
    except Exception:
        return set()


def _save_log(hour: int, done: list, failed: list, link: str) -> None:
    p = _today_log_path(hour)
    p.write_text(json.dumps({
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "hour": hour, "link": link,
        "done": sorted(done), "failed": sorted(failed),
    }, ensure_ascii=False, indent=2), encoding="utf-8")


def _alert(title: str, body: str) -> None:
    try:
        from core.issue_reporter import send_issue_to_kakaowork
        send_issue_to_kakaowork(title, body)
    except Exception:
        pass


def _targets_for_hour(hour: int) -> list:
    try:
        mapping = json.loads(MAPPING_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []
    return sorted(n for n in mapping if _extract_deadline(n) == hour)


def main() -> int:
    if len(sys.argv) < 2:
        print("사용: python scripts/broadcast.py <시각 0-23> [--dry-run]")
        return 1
    try:
        hour = int(sys.argv[1])
    except ValueError:
        print(f"잘못된 hour: {sys.argv[1]}")
        return 1
    if not (0 <= hour <= 23):
        print(f"hour 범위 오류: {hour}")
        return 1
    dry = "--dry-run" in sys.argv

    text_prefix, url = _load_link(hour)
    targets = _targets_for_hour(hour)
    print(f"[BCAST] {hour}시 마감 대상 방 {len(targets)}개:")
    for n in targets:
        print(f"   • {n}")

    if dry:
        print(f"\n[BCAST] dry-run — 송신 안 함. 링크={url or '(미설정)'}")
        return 0
    if not url:
        print(f"[BCAST] {hour}시 링크 미설정 ({LINKS_FILE.name}) — 송신 스킵")
        return 0
    if not targets:
        print(f"[BCAST] {hour}시 마감 방 없음 (~{hour} 끝) — 송신 스킵")
        return 0

    msg = f"{text_prefix}\n{url}".strip() if text_prefix else url
    already = _load_done(hour)
    pending = [n for n in targets if n not in already]
    print(f"[BCAST] 이미발송 {len(already)} / 미발송 {len(pending)}")
    if not pending:
        print("[BCAST] 전부 이미 발송 — 종료(멱등)")
        return 0

    # 카톡 락 (우선 요청 → 모니터/답장서버 양보)
    from core import kakao_lock as klock
    from core import kakao_win32 as kw

    klock.request()
    got = klock.acquire("broadcast", timeout=60, respect_request=False)
    if not got:
        print("[BCAST] 락 획득 실패(60s) — 경보")
        _alert(f"{hour}시 마감 브로드캐스트 락 실패",
               "카톡 락을 60초 안에 못 잡아 송신 중단됨. 즉시 수동 발송 필요.\n"
               f"링크: {url}")
        klock.clear_request()
        return 1

    done = list(already)
    failed = []
    try:
        # 메인창 트레이 닫힘 대비 복원
        try:
            from core.window_manager import ensure_main_window_foreground
            ensure_main_window_foreground()
        except Exception:
            pass

        for name in pending:
            ok = False
            err = ""
            for attempt in range(2):
                try:
                    res = kw.send_message_to_room(name, msg)
                    if res.get("success"):
                        ok = True
                        break
                    err = res.get("error", "")
                except Exception as e:
                    err = f"{type(e).__name__}: {e}"
                time.sleep(1.2)
            if ok:
                done.append(name)
                print(f"  ✅ {name}", flush=True)
            else:
                failed.append(name)
                print(f"  ❌ {name}  ({err})", flush=True)
            time.sleep(0.5)
    finally:
        klock.release("broadcast")
        klock.clear_request()

    _save_log(hour, done, failed, url)
    new_done = len(done) - len(already)
    print(f"\n[BCAST] {hour}시 완료: 신규성공 {new_done} / 실패 {len(failed)} / 대상 {len(targets)}")
    if failed:
        body = (f"❌ {hour}시 마감 카톡 송신 실패 {len(failed)}건 — 즉시 수동 발송 필요\n"
                + "\n".join(f"  • {n}" for n in failed[:30])
                + f"\n링크: {url}")
        _alert(f"{hour}시 마감 카톡 송신 실패 {len(failed)}건", body)
    return 0 if not failed else 2


if __name__ == "__main__":
    sys.exit(main())
