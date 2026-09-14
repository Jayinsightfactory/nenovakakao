"""MOYI PC Kakao bridge operations console.

Usage: ``python moyi_console.py``
The console can pause/resume polling at a safe boundary. It never retries or
mutates an individual delivery.
"""
from __future__ import annotations
import json, subprocess, sys, time, tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import ttk, messagebox

import psutil

from core.moyi_control import is_paused, set_paused, emergency_stop, audit, worker_processes
from core import keyword_forward, keyword_alerts, import_order, credential_store

ROOT = Path(__file__).parent
EVENT_LOG = ROOT / "data" / "moyi_events.jsonl"
JOURNAL = ROOT / "data" / "moyi_outbound_journal.jsonl"


def worker_running() -> bool:
    return any(worker_processes())


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
        self.geometry("1120x760")
        self.minsize(800, 480)
        self.status = tk.StringVar(value="대기 중")
        self.pause_text = tk.StringVar()
        self._build()
        self._update_pause_display()
        from core.stop_hotkey import StopHotkey
        self.hotkey = StopHotkey(self.hotkey_stop)
        audit('hotkey_ready' if self.hotkey.registered else 'hotkey_failed', 'Ctrl+Alt+F10')
        self.hotkey_label.set('긴급 정지: Ctrl+Alt+F10 (다른 앱에서도 작동)' if self.hotkey.registered
                              else '단축키 등록 실패 — 재개 차단. 다른 콘솔을 닫고 다시 실행하세요.')
        self.protocol('WM_DELETE_WINDOW', self.close_console)
        self.after(1000, self.refresh)

    def _build(self):
        top = ttk.Frame(self, padding=12); top.pack(fill="x")
        ttk.Label(top, text="MOYI 카카오 연동 운영 콘솔", font=("Segoe UI", 16, "bold")).pack(side="left")
        ttk.Button(top, textvariable=self.pause_text, command=self.toggle_pause).pack(side="right", padx=(8, 0))
        ttk.Label(top, textvariable=self.status).pack(side="right")
        self.hotkey_label = tk.StringVar()
        ttk.Label(self, textvariable=self.hotkey_label).pack(anchor='w', padx=12)
        from core.operator_settings import operator_name
        operator = ttk.Frame(self, padding=(12, 4)); operator.pack(fill='x')
        ttk.Label(operator, text='보고·승인 담당자 카톡 이름').pack(side='left')
        self.operator_name = tk.StringVar(value=operator_name())
        ttk.Entry(operator, textvariable=self.operator_name, width=22).pack(side='left', padx=8)
        ttk.Button(operator, text='담당자 저장', command=self.save_operator).pack(side='left')
        self.operator_status = tk.StringVar(value=f'현재 대상: {operator_name()}')
        ttk.Label(operator, textvariable=self.operator_status).pack(side='left', padx=8)
        ttk.Label(self, text='친구 목록에서 정확한 이름으로 1:1 열기 · 완료·오류·상황 보고와 신규 승인 요청에 적용 · 변경 시 일시정지').pack(anchor='w', padx=12)
        ttk.Button(top, text='긴급 정지', command=self.stop_now).pack(side='right')
        routing = ttk.LabelFrame(self, text='키워드 자동 전달 · 영업방 → 현장 추가취소방', padding=8)
        routing.pack(fill='x', padx=12)
        self.route_enabled = tk.BooleanVar(value=keyword_forward.config().get('enabled', False))
        ttk.Checkbutton(routing, text='자동 전달 사용 (전체 일시정지 연동)', variable=self.route_enabled,
                        command=lambda: keyword_forward.set_enabled(self.route_enabled.get())).pack(anchor='w')
        cfg = keyword_forward.config()
        ttk.Label(routing, text=f"키워드: 추가 / 취소 / 변경 · 시작: {cfg.get('start_at', '미설정')} · 동일 본문 생략").pack(anchor='w')
        ttk.Label(routing, text='항목별 승인: 가 보내 / 나 안보내 · 따로 답변 가능 · 미응답 대기 · 과거 요청은 요청번호 필수').pack(anchor='w')
        self.route_summary = tk.StringVar()
        ttk.Label(routing, textvariable=self.route_summary).pack(anchor='w')
        self.route_table = ttk.Treeview(routing, columns=('time', 'status', 'sender', 'keyword', 'detail'), show='headings', height=5)
        for col, label, width in [('time', '시간', 130), ('status', '결과', 100), ('sender', '보낸 사람', 100), ('keyword', '감지 단어', 110), ('detail', '본문 / 상세 결과', 480)]:
            self.route_table.heading(col, text=label)
            self.route_table.column(col, width=width)
        self.route_table.pack(fill='x')
        orders = ttk.LabelFrame(self, text='3순위 · 수입방 수집 → 주문·담당자 매칭 → 검토 시트', padding=8)
        orders.pack(fill='x', padx=12, pady=(6, 0))
        self.order_summary = tk.StringVar()
        ttk.Label(orders, textvariable=self.order_summary).pack(anchor='w')
        ttk.Label(orders, text='최우선: 영업방 수집 → 승인·완료 알림 반복 · 수입방 약 1분 · 기타 작업 30분').pack(anchor='w')
        ttk.Button(orders, text='주문 검토 시트 열기', command=self.open_order_review).pack(anchor='e')
        ttk.Button(orders, text='담당자별 네노바 로그인 설정', command=self.setup_nenova_credential).pack(anchor='e')
        self.order_table = ttk.Treeview(orders, columns=('time','status','id','staff','customer','week','items','detail'), show='headings', height=4)
        for col, label, width in [('time','시간',110),('status','상태',110),('id','요청번호',120),('staff','담당자',100),('customer','거래처',100),('week','차수',70),('items','품목',55),('detail','상세',360)]:
            self.order_table.heading(col, text=label); self.order_table.column(col, width=width)
        self.order_table.pack(fill='x')
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

    def open_order_review(self):
        import webbrowser
        path = ROOT / 'data' / 'order_review_sync_status.json'
        if path.exists():
            webbrowser.open(json.loads(path.read_text(encoding='utf-8'))['url'])
        else:
            messagebox.showinfo('검토 시트', '첫 시트 저장이 완료되면 열 수 있습니다.', parent=self)

    def save_operator(self):
        from core.operator_settings import configure
        try:
            name = configure(self.operator_name.get())
            self.operator_name.set(name)
            self.operator_status.set(f'저장됨: {name} · 일시정지 중, 재개 버튼으로 시작')
            audit('operator_changed', name)
            self._update_pause_display()
        except (OSError, ValueError) as exc:
            messagebox.showerror('담당자 저장 실패', str(exc), parent=self)

    def setup_nenova_credential(self):
        dialog = tk.Toplevel(self); dialog.title('담당자별 네노바 로그인 설정'); dialog.resizable(False, False)
        values = {key: tk.StringVar() for key in ('staff','room','username','password')}
        for row, (key, label) in enumerate((('staff','수입방 발신자 이름'),('room','담당자 1:1 카톡방 이름'),('username','네노바 아이디'),('password','네노바 비밀번호'))):
            ttk.Label(dialog, text=label).grid(row=row, column=0, padx=10, pady=6, sticky='e')
            ttk.Entry(dialog, textvariable=values[key], width=34, show='*' if key == 'password' else '').grid(row=row, column=1, padx=10, pady=6)
        enable_review = tk.BooleanVar(value=False)
        ttk.Checkbutton(dialog, text='이 담당자의 주문 확인 답변 사용 허용 (실제 등록은 미연결)', variable=enable_review).grid(row=4,column=0,columnspan=2,padx=10,pady=6)
        ttk.Label(dialog, text='비밀번호는 Windows 자격 증명 관리자에 저장됩니다. 계정 저장만으로 실제 등록이 활성화되지는 않습니다.', wraplength=420).grid(row=5,column=0,columnspan=2,padx=10,pady=6)
        def submit():
            try:
                room = values['room'].get().strip()
                credential_store._target(values['staff'].get())
                credential_store._target(room)
                credential_store.save(room, values['username'].get(), values['password'].get())
                audit('credential_saved', room)
                if enable_review.get():
                    import_order.configure_staff(values['staff'].get(), room)
                values['password'].set('')
                messagebox.showinfo('저장 완료', '계정을 저장했습니다. 실제 등록은 API·권한·결과 검증 전까지 차단됩니다.', parent=dialog)
                dialog.destroy()
            except Exception as exc:
                values['password'].set('')
                messagebox.showerror('저장 실패', str(exc), parent=dialog)
        ttk.Button(dialog, text='보안 저장', command=submit).grid(row=6,column=0,columnspan=2,pady=10)

    def read_events(self):
        rows = []
        for path in (EVENT_LOG, EVENT_LOG.parent / 'moyi_control_events.jsonl', import_order.LOG):
            if not path.exists(): continue
            # Do not reread an ever-growing full log every GUI refresh.
            with path.open('rb') as stream:
                stream.seek(0, 2)
                offset = max(0, stream.tell() - 262144)
                stream.seek(offset)
                if offset: stream.readline()
                lines = stream.read().decode('utf-8', errors='replace').splitlines()[-200:]
            for line in lines:
                try:
                    row = json.loads(line)
                    row.setdefault('state', row.get('action', ''))
                    row.setdefault('outbox_id', row.get('id', ''))
                    rows.append(row)
                except json.JSONDecodeError: pass
        return sorted(rows, key=lambda row: row.get('at', 0))[-200:]

    def refresh(self):
        try:
            routing = list(keyword_forward.read_json(keyword_forward.STATE, {}).values())
            counts_r = {label: sum(r['status'] == label for r in routing) for label in ('전송 성공', '중복 생략', '승인요청 전송대기', '승인대기', '승인거절', '확인 필요', '결과 불명', '전송 확인중')}
            from core import keyword_approval
            approvals = keyword_forward.read_json(keyword_approval.REQUESTS, {}).values()
            request_counts = {status: 0 for status in ('queued', 'waiting', 'awaiting_late_reply', 'request_unknown', 'historical_review', 'operator_changed_review', 'stale_review')}
            for request in approvals:
                if request['status'] in request_counts: request_counts[request['status']] += 1
            self.route_summary.set(' · '.join(f'{label} {n}건' for label, n in counts_r.items()) + '\n' +
                f"시간초과 별도검토 {request_counts['stale_review']}묶음 · 이전 담당자 별도검토 {request_counts['operator_changed_review']}묶음 · 질문 전송대기 {request_counts['queued']}묶음 · 답변 대기 {request_counts['waiting']}묶음 · "
                f"15분 미응답 {request_counts['awaiting_late_reply']}묶음 · 과거 별도검토 {request_counts['historical_review']}묶음 · 전송결과 확인 필요 {request_counts['request_unknown']}묶음")
            for child in self.route_table.get_children(): self.route_table.delete(child)
            for row in sorted(routing, key=lambda r: r['at'])[-30:]:
                self.route_table.insert('', 'end', values=(datetime.fromtimestamp(row['at']).strftime('%m-%d %H:%M:%S'), row['status'], row['sender'], ', '.join(row['keywords']), row['preview'] + ' | ' + row['detail']))
        except (OSError, ValueError, KeyError):
            self.route_summary.set('전달 기록 읽기 실패 — 확인 필요')
        try:
            order_rows = list(import_order._read(import_order.STATE, {}).values())
            counts = {}
            for row in order_rows: counts[row['status']] = counts.get(row['status'], 0) + 1
            self.order_summary.set(' · '.join(f'{key} {value}건' for key, value in sorted(counts.items())) or '신규 주문 대기 없음')
            for child in self.order_table.get_children(): self.order_table.delete(child)
            for row in sorted(order_rows, key=lambda r: r.get('created_at', 0))[-20:]:
                detail = row.get('error') or ('질문 ' + ' / '.join(row.get('questions', [])) if row.get('questions') else '')
                if row['status'] == 'draft':
                    blockers = import_order._waiting_in_room(
                        {r['id']: r for r in order_rows}, row.get('staff_room'))
                    if blockers:
                        detail = '앞선 요청 처리 대기: ' + ', '.join(
                            f"{r['id']} ({r['status']})" for _, r in blockers)
                elif row['status'] in ('reply_pending', 'request_unknown', 'registration_unknown'):
                    detail = detail or '결과 확인 필요 · 자동 재전송/재등록 차단'
                self.order_table.insert('', 'end', values=(datetime.fromtimestamp(row.get('created_at',0)).strftime('%m-%d %H:%M'), row['status'], row['id'], row.get('staff',''), row.get('customer',''), row.get('week',''), len(row.get('items',[])), detail))
        except (OSError, ValueError, KeyError):
            self.order_summary.set('주문 검토 기록 읽기 실패 — 확인 필요')
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
            if not self.hotkey.registered:
                messagebox.showerror('재개 차단', '정지 단축키 등록을 먼저 확인해주세요.', parent=self)
                return
            set_paused(False)
            try:
                start_worker_if_needed()
                audit('worker_started')
            except Exception:
                set_paused(True)
                audit('worker_start_failed')
                messagebox.showerror('실행 실패', '워커 로그를 확인해주세요.', parent=self)
        else:
            set_paused(True)
        self._update_pause_display()

    def hotkey_stop(self):
        try:
            emergency_stop('Ctrl+Alt+F10')
        except Exception:
            audit('stop_failed', '단축키 정지 실패 — 워커 상태 확인 필요')

    def stop_now(self):
        try:
            emergency_stop('console button')
        except Exception as exc:
            messagebox.showerror('정지 확인 필요', str(exc), parent=self)
        self._update_pause_display()

    def close_console(self):
        try:
            emergency_stop('console closed')
        except Exception as exc:
            messagebox.showerror('종료 차단', str(exc), parent=self)
            return
        self.hotkey.close()
        self.destroy()

    def _update_pause_display(self) -> str:
        paused = is_paused()
        self.pause_text.set("재개" if paused else "일시중지")
        if paused:
            return "정지 요청됨 · 현재 작업 종료 대기" if worker_running() else "정지됨"
        return "실행 중" if worker_running() else "워커 정지"

    def open_log_folder(self):
        import os
        os.startfile(str(EVENT_LOG.parent))

if __name__ == "__main__":
    Console().mainloop()
