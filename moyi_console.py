"""MOYI PC Kakao bridge operations console.

Usage: ``python moyi_console.py``
The console can pause/resume polling at a safe boundary. It never retries or
mutates an individual delivery.
"""
from __future__ import annotations
import json, subprocess, sys, time, tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import ttk

import psutil

from core.moyi_control import is_paused, set_paused
from core import keyword_forward, keyword_alerts

ROOT = Path(__file__).parent
EVENT_LOG = ROOT / "data" / "moyi_events.jsonl"
JOURNAL = ROOT / "data" / "moyi_outbound_journal.jsonl"


def worker_running() -> bool:
    for process in psutil.process_iter(("cmdline",)):
        try:
            command = process.info.get("cmdline") or []
            if any(str(part).endswith("main.py") for part in command) and "moyi-worker" in command:
                return True
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            continue
    return False


def start_worker_if_needed() -> bool:
    if worker_running():
        return False
    data = ROOT / "data"
    data.mkdir(parents=True, exist_ok=True)
    stdout = (data / "moyi_worker_stdout.log").open("a", encoding="utf-8")
    stderr = (data / "moyi_worker_stderr.log").open("a", encoding="utf-8")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(
        [sys.executable, "-u", "main.py", "moyi-worker"], cwd=ROOT,
        stdout=stdout, stderr=stderr, creationflags=flags,
    )
    stdout.close()
    stderr.close()
    return True

class Console(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("MOYI Kakao Connector")
        self.geometry("1080x620")
        self.minsize(800, 480)
        self.status = tk.StringVar(value="대기 중")
        self.pause_text = tk.StringVar()
        self._build()
        self._update_pause_display()
        self.after(1000, self.refresh)

    def _build(self):
        top = ttk.Frame(self, padding=12); top.pack(fill="x")
        ttk.Label(top, text="MOYI 카카오 연동 운영 콘솔", font=("Segoe UI", 16, "bold")).pack(side="left")
        ttk.Button(top, textvariable=self.pause_text, command=self.toggle_pause).pack(side="right", padx=(8, 0))
        ttk.Label(top, textvariable=self.status).pack(side="right")
        routing = ttk.LabelFrame(self, text='키워드 자동 전달 · 영업방 → 현장 추가취소방', padding=8)
        routing.pack(fill='x', padx=12)
        self.route_enabled = tk.BooleanVar(value=keyword_forward.config().get('enabled', False))
        ttk.Checkbutton(routing, text='자동 전달 사용 (전체 일시정지 연동)', variable=self.route_enabled,
                        command=lambda: keyword_forward.set_enabled(self.route_enabled.get())).pack(anchor='w')
        cfg = keyword_forward.config()
        ttk.Label(routing, text=f"키워드: 추가 / 취소 / 변경 · 시작: {cfg.get('start_at', '미설정')} · 동일 본문 생략").pack(anchor='w')
        ttk.Label(routing, text='모든 전달 후보 → 임재용대리 묶음 승인 · 답변: 요청번호 제외번호 / 모두전달·생략 번호').pack(anchor='w')
        self.route_summary = tk.StringVar()
        ttk.Label(routing, textvariable=self.route_summary).pack(anchor='w')
        self.route_table = ttk.Treeview(routing, columns=('time', 'status', 'sender', 'keyword', 'detail'), show='headings', height=5)
        for col, label, width in [('time', '시간', 130), ('status', '결과', 100), ('sender', '보낸 사람', 100), ('keyword', '감지 단어', 110), ('detail', '본문 / 상세 결과', 480)]:
            self.route_table.heading(col, text=label)
            self.route_table.column(col, width=width)
        self.route_table.pack(fill='x')
        cards = ttk.Frame(self, padding=(12, 0)); cards.pack(fill="x")
        self.metrics = {}
        for key, label in (("leased", "처리 중"), ("sent", "전송 확인"), ("unknown_result", "확인 필요"), ("room_verified", "방 검증")):
            frame = ttk.LabelFrame(cards, text=label, padding=10); frame.pack(side="left", fill="x", expand=True, padx=(0, 8))
            var = tk.StringVar(value="0"); self.metrics[key] = var
            ttk.Label(frame, textvariable=var, font=("Segoe UI", 20, "bold")).pack()
        columns = ("time", "state", "room", "id", "detail")
        self.table = ttk.Treeview(self, columns=columns, show="headings", height=18)
        for col, text, width in (("time", "시간", 150), ("state", "상태", 130), ("room", "카카오 방", 220), ("id", "전송 ID", 180), ("detail", "상세 결과", 350)):
            self.table.heading(col, text=text); self.table.column(col, width=width, anchor="w")
        self.table.tag_configure("bad", foreground="#b42318")
        self.table.tag_configure("ok", foreground="#067647")
        self.table.pack(fill="both", expand=True, padx=12, pady=12)
        bottom = ttk.Frame(self, padding=(12, 0, 12, 12)); bottom.pack(fill="x")
        ttk.Label(bottom, text="확인 필요 항목은 자동 재전송하지 않습니다. 워커 로그와 서버 ACK를 함께 확인하세요.").pack(side="left")
        ttk.Button(bottom, text="로그 폴더 열기", command=self.open_log_folder).pack(side="right")

    def read_events(self):
        if not EVENT_LOG.exists(): return []
        rows = []
        for line in EVENT_LOG.read_text(encoding="utf-8", errors="ignore").splitlines()[-200:]:
            try: rows.append(json.loads(line))
            except json.JSONDecodeError: pass
        return rows

    def refresh(self):
        try:
            routing = list(keyword_forward.read_json(keyword_forward.STATE, {}).values())
            counts_r = {label: sum(r['status'] == label for r in routing) for label in ('전송 성공', '중복 생략', '승인대기', '승인거절', '확인 필요', '결과 불명', '전송 확인중')}
            for row in routing:
                # All items from one scan share a request detail. Alert once
                # for the batch, while preserving per-item dashboard rows.
                if keyword_alerts.claim_alert(row):
                    self.bell()
                    alert = tk.Toplevel(self)
                    alert.title('키워드 메시지 전달 승인 대기')
                    members = [r['preview'] for r in routing if r['status'] == '승인대기' and keyword_alerts.alert_key(r) == keyword_alerts.alert_key(row)]
                    ttk.Label(alert, text=f'{len(members)}건 승인 대기 · 카톡에서 전체 목록 확인\n\n' + members[0] + '\n\n' + row['detail'], wraplength=500, padding=15).pack()
                    ttk.Button(alert, text='닫기 (승인은 임재용대리 카톡 답변)', command=alert.destroy).pack(pady=10)
            self.route_summary.set(' · '.join(f'{label} {n}건' for label, n in counts_r.items()))
            for child in self.route_table.get_children(): self.route_table.delete(child)
            for row in sorted(routing, key=lambda r: r['at'])[-30:]:
                self.route_table.insert('', 'end', values=(datetime.fromtimestamp(row['at']).strftime('%m-%d %H:%M:%S'), row['status'], row['sender'], ', '.join(row['keywords']), row['preview'] + ' | ' + row['detail']))
        except (OSError, ValueError, KeyError):
            self.route_summary.set('전달 기록 읽기 실패 — 확인 필요')
        rows = self.read_events(); counts = {key: 0 for key in self.metrics}
        for row in rows:
            if row.get("state") in counts: counts[row["state"]] += 1
        for key, var in self.metrics.items(): var.set(str(counts[key]))
        for item in self.table.get_children(): self.table.delete(item)
        for row in rows[-100:]:
            stamp = datetime.fromtimestamp(row.get("at", 0)).strftime("%m-%d %H:%M:%S")
            state = row.get("state", "")
            tag = "bad" if state == "unknown_result" else ("ok" if state == "sent" else "")
            self.table.insert("", "end", values=(stamp, state, row.get("room", ""), row.get("outbox_id", ""), row.get("detail", "")), tags=(tag,))
        mode = self._update_pause_display()
        self.status.set(f"{mode} · {datetime.now().strftime('%H:%M:%S')} · 이벤트 {len(rows)}건")
        self.after(1000, self.refresh)

    def toggle_pause(self):
        if is_paused():
            set_paused(False)
            start_worker_if_needed()
        else:
            set_paused(True)
        self._update_pause_display()

    def _update_pause_display(self) -> str:
        paused = is_paused()
        self.pause_text.set("재개" if paused else "일시중지")
        if paused:
            return "일시중지됨"
        return "실행 중" if worker_running() else "워커 정지"

    def open_log_folder(self):
        import os
        os.startfile(str(EVENT_LOG.parent))

if __name__ == "__main__":
    Console().mainloop()
