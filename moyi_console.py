"""MOYI PC Kakao bridge operations console.

Usage: ``python moyi_console.py``
The console is read-only: it never retries or mutates a delivery. This keeps
human review separate from the sending worker.
"""
from __future__ import annotations
import json, time, tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import ttk

ROOT = Path(__file__).parent
EVENT_LOG = ROOT / "data" / "moyi_events.jsonl"
JOURNAL = ROOT / "data" / "moyi_outbound_journal.jsonl"

class Console(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("MOYI Kakao Connector")
        self.geometry("1080x620")
        self.minsize(800, 480)
        self.status = tk.StringVar(value="대기 중")
        self._build()
        self.after(1000, self.refresh)

    def _build(self):
        top = ttk.Frame(self, padding=12); top.pack(fill="x")
        ttk.Label(top, text="MOYI 카카오 연동 운영 콘솔", font=("Segoe UI", 16, "bold")).pack(side="left")
        ttk.Label(top, textvariable=self.status).pack(side="right")
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
        self.status.set(f"마지막 갱신 {datetime.now().strftime('%H:%M:%S')} · 이벤트 {len(rows)}건")
        self.after(1000, self.refresh)

    def open_log_folder(self):
        import os
        os.startfile(str(EVENT_LOG.parent))

if __name__ == "__main__":
    Console().mainloop()
