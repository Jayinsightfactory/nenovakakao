"""매일 자가수정 보고(+이카운트 수집 현황)를 카카오톡 방 하나에 보낸다 (2026-10-09 사장님: "카톡으로 매일 보고해줘").

- 입력: ~/.orbit/self-tune-report.json (nenova-work-features/self-tune-report.js 가 매일 07:00 생성), ~/.orbit/ecount-daemon.log
- 대상 방: ~/.orbit/kakao-report-room.txt 첫 줄 = 카톡방 제목(검색어). 파일이 없으면 보내지 않고 종료코드 0(run-all 계속).
- 발송: core.moyi_outbound 의 검증된 경로(카톡 메인창 Ctrl+F → 방 제목 → Enter → 붙여넣기 → Enter). 카톡 PC가 로그인·실행 중이어야 함.
- 안전: 읽기만 하고 카톡엔 메시지 1건만 보낸다. 같은 날 이미 보냈으면(~/.orbit/kakao-report-sent.txt) 다시 안 보냄.
사용: python send_report.py            # 발송
      python send_report.py --dry-run  # 본문만 출력(카톡 안 건드림)
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ORBIT = Path.home() / ".orbit"
ROOM_FILE = ORBIT / "kakao-report-room.txt"
SENT_FILE = ORBIT / "kakao-report-sent.txt"
KST = timezone(timedelta(hours=9))


def today() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d")


def ecount_status() -> str:
    """이카운트 데몬 로그의 마지막 사이클 한 줄 요약."""
    p = ORBIT / "ecount-daemon.log"
    if not p.exists():
        return "이카운트: 로그 없음"
    lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
    idx = [i for i, l in enumerate(lines) if "수집 사이클" in l]
    if not idx:
        return "이카운트: 사이클 기록 없음"
    block = lines[idx[-1]:]
    m = re.search(r"\[(.+?)\] 수집 사이클", block[0])
    when = m.group(1) if m else "?"
    got = {}
    for l in block[1:]:
        mm = re.match(r"\s*\[(cash|ar|ap|sales)\] rows=(\d+).*→ (\w+)", l)
        if mm:
            got[mm.group(1)] = f"{mm.group(2)}행 {mm.group(3)}"
    names = {"cash": "입출금", "ar": "채권", "ap": "채무", "sales": "판매"}
    miss = [names[k] for k in names if k not in got]
    parts = ", ".join(f"{names[k]} {got[k]}" for k in names if k in got)
    return f"이카운트 마지막 수집 {when}: {parts or '없음'}" + (f" / 누락 {', '.join(miss)}" if miss else "")


def completeness_line() -> str:
    """작업데이터 수신 완전성 한 줄 (Orbit /api/completeness/report-line, 2026-10-21 설계). 실패 시 한 줄 안내."""
    try:
        import urllib.request
        cfg = json.loads((Path.home() / ".orbit-config.json").read_text(encoding="utf-8-sig"))
        req = urllib.request.Request(cfg["serverUrl"].rstrip("/") + "/api/completeness/report-line?days=7", headers={"Authorization": "Bearer " + cfg["token"]})
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read().decode("utf-8").strip()[:500] or "작업데이터 수신 지표: 응답 비어 있음"
    except Exception as e:  # noqa: BLE001
        return f"작업데이터 수신 지표 조회 실패: {str(e)[:80]}"


def watchdog_section() -> str:
    """결과 감시(nenova-watchdog, 08:50) 이상 항목 — 보고 맨 앞 (2026-11-02 사장님: 자체 문제확인)."""
    try:
        j = json.loads((ORBIT / "output-watchdog.json").read_text(encoding="utf-8"))
    except Exception:
        return "[결과 감시] 결과 파일 없음 — 감시 작업 미실행"
    if j.get("error"):
        return f"[결과 감시] 실행 오류: {j['error'][:80]}"
    bad = [c for c in j.get("checks") or [] if not c.get("ok")]
    if not bad:
        return f"[결과 감시] 이상 없음 ({j.get('total')}개 항목, 기준일 {j.get('ref')})"
    lines = [f"[결과 감시] 이상 {len(bad)}/{j.get('total')} (기준일 {j.get('ref')})"]
    lines += [f"✗ {c['msg']}" + (" → 자동복구함" if c.get("fixed") else "") for c in bad[:6]]
    return "\n".join(lines)


def pm_section() -> str:
    """PM 루프(nenova-pm) 업무 대장 — 미결·승인대기·상향 (2026-11-02 사장님: PM 자동 처리)."""
    try:
        j = json.loads((ORBIT / "pm-status.json").read_text(encoding="utf-8"))
    except Exception:
        return "[PM] 상태 파일 없음"
    names = {"needs-approval": "승인필요", "escalated": "3일째·확인요청", "planned": "조사예정", "verifying": "복구후검증", "open": "미결"}
    lines = [f"[PM {'그림자' if j.get('mode') != 'live' else '실행'}] 미결 {j.get('open')} · 오늘 완료 {j.get('doneToday')}"]
    order = ["needs-approval", "escalated", "planned", "verifying", "open"]
    for t in sorted(j.get("tasks") or [], key=lambda t: order.index(t["state"]) if t["state"] in order else 9)[:5]:
        lines.append(f"· {names.get(t['state'], t['state'])}: {(t.get('decision') or '')[:50]}")
    return "\n".join(lines)


def issues_section() -> str:
    """업무 카톡 이슈트래킹(Orbit /api/admin/kakao-intel) 요약 — 미해결 중요 상위 5 + 거래처별 미해결 (2026-11-02 사장님: 보고 못 받음)."""
    try:
        import urllib.request
        cfg = json.loads((Path.home() / ".orbit-config.json").read_text(encoding="utf-8-sig"))
        req = urllib.request.Request(cfg["serverUrl"].rstrip("/") + "/api/admin/kakao-intel?hours=72", headers={"Authorization": "Bearer " + cfg["token"]})
        with urllib.request.urlopen(req, timeout=90) as r:
            j = json.loads(r.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        return f"[업무 카톡 이슈] 조회 실패: {str(e)[:80]}"
    s = j.get("issueSummary") or {}
    live = [i for i in j.get("issues") or [] if not i.get("resolved") and not i.get("expired")]
    crit = [i for i in live if i.get("critical")]
    lines = [f"[업무 카톡 이슈] 미해결 {s.get('unresolved', len(live))}건 (중요 {len(crit)}) · 해결 {s.get('resolved', 0)} · 만료 {s.get('expired', 0)}"]
    for i in (crit or live)[:5]:
        lines.append(f"⚠ {i.get('seq') or ''} {(i.get('product') or i.get('key') or '')[:20]}: {(i.get('summary') or '')[:60]}")
    cust = {}
    for i in live:
        for c in i.get("customers") or []:
            cust[c] = cust.get(c, 0) + 1
    if cust:
        lines.append("- 거래처별 미해결: " + ", ".join(f"{k} {v}" for k, v in sorted(cust.items(), key=lambda x: -x[1])[:6]))
    return "\n".join(lines)


def compose() -> str:
    rp = ORBIT / "self-tune-report.json"
    if not rp.exists():
        body = "자가수정 보고 파일이 없습니다 (run-all self-tune-report 미실행)."
    else:
        rep = json.loads(rp.read_text(encoding="utf-8"))
        d = (rep.get("days") or [])[-1] if rep.get("days") else None
        if not d:
            body = "자가수정 보고: 오늘 항목 없음."
        else:
            lines = [f"[네노바 자가수정 보고 {d.get('date')}]"]
            lines += [f"- {s}" for s in d.get("summary") or []]
            near = (d.get("aliases") or {}).get("nearly") or []
            if near:
                lines.append("- 채택 임박 별칭: " + ", ".join(f"{p['nick']}→{p['canonical']}({p['need']})" for p in near[:3]))
            conf = d.get("confidence") or []
            if conf:
                top = sorted(conf, key=lambda c: -(c.get("confirmed") or 0))[:5]
                lines.append("- 확증 상위: " + ", ".join(f"{c['name']} {c['confirmed']}({c['pct']}%)" for c in top))
                low = [c["name"] for c in conf if (c.get("pct") or 0) < 10]
                if low:
                    lines.append("- 확증 10% 미만: " + ", ".join(low))
            body = "\n".join(lines)
    iss = watchdog_section() + "\n" + pm_section() + "\n\n" + issues_section()  # 감시·이슈를 맨 앞에 — 1800자 절단에 잘리지 않게
    text = iss + "\n\n" + body[:1800 - len(iss) - 400] + "\n- " + ecount_status() + "\n- " + completeness_line() + "\n(자세히: nenovaweb.com/my-work → 도입 레이더)"
    return text[:1800]  # 카톡 한 메시지 안전 길이


def save_failure_screenshot() -> None:
    """방을 못 열었을 때 카톡 메인 창 모습을 PC 안(~/.orbit)에만 남긴다 — 다음 날 원인 확인용. 실패해도 무시."""
    try:
        import pygetwindow as gw
        from PIL import ImageGrab
        mains = [w for w in gw.getWindowsWithTitle("카카오톡") if w.title == "카카오톡" and w.visible and w.width > 0]
        if not mains:
            print("실패 화면 저장 생략: 카카오톡 메인 창 없음")
            return
        w = mains[0]
        ImageGrab.grab(bbox=(w.left, w.top, w.left + w.width, w.top + w.height), all_screens=True).save(ORBIT / "kakao-report-fail.png")
    except Exception as e:  # noqa: BLE001
        print(f"실패 화면 저장 못 함: {str(e)[:80]}")


def main(argv: list[str]) -> int:
    text = compose()
    if "--dry-run" in argv:
        try:
            sys.stdout.reconfigure(errors="replace")  # cp949 콘솔에서 ⚠ 같은 기호로 죽지 않게(발송 경로는 클립보드라 무관)
        except Exception:
            pass
        print(text)
        return 0
    if not ROOM_FILE.exists():
        print(f"카톡 보고 생략: 대상 방 파일 없음 ({ROOM_FILE}) — 첫 줄에 카톡방 제목을 적어 두면 다음날부터 발송")
        return 0
    room = ROOM_FILE.read_text(encoding="utf-8").strip().splitlines()[0].strip()
    if not room:
        print("카톡 보고 생략: 방 이름 비어 있음")
        return 0
    if SENT_FILE.exists() and SENT_FILE.read_text(encoding="utf-8").strip() == today() and "--force" not in argv:
        print("카톡 보고 생략: 오늘 이미 보냄")
        return 0
    sys.path.insert(0, str(Path(__file__).parent))
    from core.moyi_outbound import open_room_verified, send_text_to_open_room, check_send_limit, record_send  # pyautogui 경로(검증된 발송 방식)

    # 2026-10-15 사장님: 발송이 클립보드(pyperclip)로 붙여넣기라 보고문이 클립보드에 남음 → 발송 전 내용을 저장했다가 되돌린다.
    import pyperclip
    try:
        prev_clip = pyperclip.paste()
    except Exception:
        prev_clip = None
    try:
        check_send_limit(room)
        # 방 열기는 아무것도 입력하지 않으므로 다시 해도 안전하다. 카톡이 아직 준비 안 된 아침에 대비해 몇 분 간격으로 재시도(11/13).
        rounds = int(os.getenv("KAKAO_REPORT_OPEN_ROUNDS", "3"))
        wait = float(os.getenv("KAKAO_REPORT_OPEN_WAIT_SEC", "180"))
        for n in range(rounds):
            try:
                open_room_verified(room)
                break
            except RuntimeError as e:
                print(f"[{datetime.now(KST):%m-%d %H:%M}] 방 열기 실패 {n + 1}/{rounds}: {e}")
                save_failure_screenshot()
                if n + 1 >= rounds:
                    print("카톡 보고 미발송: 방을 열지 못해 아무것도 보내지 않음")
                    return 1
                time.sleep(wait)
        time.sleep(0.5)
        send_text_to_open_room(text, expected_title=room)  # 창 제목 불일치면 붙여넣기 전에 중단(11/02 오발송 재발 방지)
        record_send(room)
    finally:
        time.sleep(0.5)
        try:
            pyperclip.copy(prev_clip if prev_clip is not None else "")
        except Exception:
            pass
    SENT_FILE.write_text(today(), encoding="utf-8")
    print(f"카톡 보고 발송: 방 '{room}' {len(text)}자")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
