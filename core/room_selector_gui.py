"""
Phase 1.3: 방 선택 체크박스 GUI (tkinter)

스캔된 방 목록을 보여주고, 사용자가 감시할 방을 선택한다.
방 이름 수정, 추가, 삭제 기능 포함.
결과를 data/selected_rooms.json에 저장.
"""
from __future__ import annotations

import json
import tkinter as tk
from tkinter import messagebox, simpledialog
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
DETECTED_FILE = DATA_DIR / "rooms_detected.json"
SELECTED_FILE = DATA_DIR / "selected_rooms.json"


class RoomSelectorApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("네노바 에이전트 — 감시 방 선택")
        self.root.geometry("600x700")
        self.root.resizable(True, True)
        self.root.configure(bg="#f5f5f5")

        # 방 데이터: [{name, unread, order, selected}]
        self.rooms: list[dict] = []
        self.check_vars: list[tk.BooleanVar] = []
        self.row_frames: list[tk.Frame] = []

        self._load_rooms()
        self._build_ui()

    def _load_rooms(self):
        """rooms_detected.json 로드 + 기존 선택 상태 복원"""
        if DETECTED_FILE.exists():
            with open(DETECTED_FILE, encoding="utf-8") as f:
                self.rooms = json.load(f)
        else:
            self.rooms = []

        # 기존 선택 상태 복원
        selected_names = set()
        if SELECTED_FILE.exists():
            with open(SELECTED_FILE, encoding="utf-8") as f:
                for r in json.load(f):
                    selected_names.add(r["name"])

        for room in self.rooms:
            room["selected"] = room["name"] in selected_names

    def _build_ui(self):
        # 상단 제목
        header = tk.Frame(self.root, bg="#2c3e50", pady=10)
        header.pack(fill="x")
        tk.Label(
            header,
            text="네노바 AI 에이전트 — 감시할 방 선택",
            font=("맑은 고딕", 14, "bold"),
            fg="white",
            bg="#2c3e50",
        ).pack()
        tk.Label(
            header,
            text="체크된 방만 감시합니다. 방 이름을 수정하거나 새 방을 추가할 수 있습니다.",
            font=("맑은 고딕", 9),
            fg="#bdc3c7",
            bg="#2c3e50",
        ).pack()

        # 스크롤 가능한 방 리스트 영역
        list_frame = tk.Frame(self.root, bg="#f5f5f5")
        list_frame.pack(fill="both", expand=True, padx=10, pady=5)

        canvas = tk.Canvas(list_frame, bg="#f5f5f5", highlightthickness=0)
        scrollbar = tk.Scrollbar(list_frame, orient="vertical", command=canvas.yview)
        self.scroll_inner = tk.Frame(canvas, bg="#f5f5f5")

        self.scroll_inner.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.create_window((0, 0), window=self.scroll_inner, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        # 마우스 휠 스크롤
        canvas.bind_all(
            "<MouseWheel>",
            lambda e: canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"),
        )

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self._render_rooms()

        # 방 추가 영역
        add_frame = tk.Frame(self.root, bg="#ecf0f1", pady=8, padx=10)
        add_frame.pack(fill="x", padx=10, pady=(0, 5))

        tk.Label(
            add_frame, text="방 추가:", font=("맑은 고딕", 10), bg="#ecf0f1"
        ).pack(side="left")

        self.add_entry = tk.Entry(add_frame, font=("맑은 고딕", 11), width=30)
        self.add_entry.pack(side="left", padx=5)
        self.add_entry.bind("<Return>", lambda e: self._add_room())

        tk.Button(
            add_frame,
            text="추가",
            font=("맑은 고딕", 10),
            command=self._add_room,
            bg="#27ae60",
            fg="white",
            relief="flat",
            padx=10,
        ).pack(side="left")

        # 하단 버튼들
        btn_frame = tk.Frame(self.root, bg="#f5f5f5", pady=10)
        btn_frame.pack(fill="x", padx=10)

        tk.Button(
            btn_frame,
            text="전체 선택",
            font=("맑은 고딕", 10),
            command=self._select_all,
            bg="#3498db",
            fg="white",
            relief="flat",
            padx=15,
        ).pack(side="left", padx=3)

        tk.Button(
            btn_frame,
            text="전체 해제",
            font=("맑은 고딕", 10),
            command=self._deselect_all,
            bg="#95a5a6",
            fg="white",
            relief="flat",
            padx=15,
        ).pack(side="left", padx=3)

        tk.Button(
            btn_frame,
            text="저장하고 닫기",
            font=("맑은 고딕", 12, "bold"),
            command=self._save_and_close,
            bg="#e74c3c",
            fg="white",
            relief="flat",
            padx=25,
            pady=5,
        ).pack(side="right", padx=3)

        # 상태바
        self.status_var = tk.StringVar(value=f"총 {len(self.rooms)}개 방")
        tk.Label(
            self.root,
            textvariable=self.status_var,
            font=("맑은 고딕", 9),
            fg="#7f8c8d",
            bg="#f5f5f5",
            anchor="w",
        ).pack(fill="x", padx=10, pady=(0, 5))

    def _render_rooms(self):
        """방 리스트 UI 렌더링"""
        # 기존 위젯 제거
        for w in self.scroll_inner.winfo_children():
            w.destroy()
        self.check_vars.clear()
        self.row_frames.clear()

        for i, room in enumerate(self.rooms):
            var = tk.BooleanVar(value=room.get("selected", False))
            self.check_vars.append(var)

            row = tk.Frame(self.scroll_inner, bg="white", pady=3, padx=5)
            row.pack(fill="x", pady=1, padx=2)
            self.row_frames.append(row)

            # 체크박스 + 방 이름
            cb = tk.Checkbutton(
                row,
                variable=var,
                font=("맑은 고딕", 11),
                text=room["name"],
                bg="white",
                activebackground="white",
                anchor="w",
            )
            cb.pack(side="left", fill="x", expand=True)

            # 읽지 않은 메시지 뱃지
            if room.get("unread", 0) > 0:
                tk.Label(
                    row,
                    text=str(room["unread"]),
                    font=("맑은 고딕", 9, "bold"),
                    fg="white",
                    bg="#e74c3c",
                    padx=5,
                    pady=1,
                ).pack(side="left", padx=3)

            # 수정 버튼
            edit_btn = tk.Button(
                row,
                text="수정",
                font=("맑은 고딕", 9),
                command=lambda idx=i: self._edit_room(idx),
                bg="#f39c12",
                fg="white",
                relief="flat",
                padx=5,
            )
            edit_btn.pack(side="right", padx=2)

            # 삭제 버튼
            del_btn = tk.Button(
                row,
                text="삭제",
                font=("맑은 고딕", 9),
                command=lambda idx=i: self._delete_room(idx),
                bg="#e74c3c",
                fg="white",
                relief="flat",
                padx=5,
            )
            del_btn.pack(side="right", padx=2)

    def _add_room(self):
        """새 방 추가"""
        name = self.add_entry.get().strip()
        if not name:
            return

        # 중복 체크
        existing = {r["name"] for r in self.rooms}
        if name in existing:
            messagebox.showwarning("중복", f"'{name}'은 이미 목록에 있습니다.")
            return

        self.rooms.append({
            "name": name,
            "unread": 0,
            "order": len(self.rooms) + 1,
            "selected": True,
        })
        self.add_entry.delete(0, "end")
        self._render_rooms()
        self._update_status()

    def _edit_room(self, idx: int):
        """방 이름 수정"""
        old_name = self.rooms[idx]["name"]
        new_name = simpledialog.askstring(
            "방 이름 수정",
            f"'{old_name}'을(를) 수정:",
            initialvalue=old_name,
            parent=self.root,
        )
        if new_name and new_name.strip():
            self.rooms[idx]["name"] = new_name.strip()
            self._render_rooms()

    def _delete_room(self, idx: int):
        """방 삭제"""
        name = self.rooms[idx]["name"]
        if messagebox.askyesno("삭제 확인", f"'{name}'을(를) 삭제할까요?"):
            self.rooms.pop(idx)
            self._render_rooms()
            self._update_status()

    def _select_all(self):
        for var in self.check_vars:
            var.set(True)

    def _deselect_all(self):
        for var in self.check_vars:
            var.set(False)

    def _update_status(self):
        self.status_var.set(f"총 {len(self.rooms)}개 방")

    def _save_and_close(self):
        """선택된 방만 selected_rooms.json에 저장"""
        selected = []
        for i, room in enumerate(self.rooms):
            if self.check_vars[i].get():
                selected.append({
                    "name": room["name"],
                    "order": len(selected) + 1,
                })

        if not selected:
            messagebox.showwarning("선택 없음", "최소 1개 방을 선택해주세요.")
            return

        # rooms_detected.json도 업데이트 (수정/추가/삭제 반영)
        for i, room in enumerate(self.rooms):
            room["order"] = i + 1
            room["selected"] = self.check_vars[i].get()

        DATA_DIR.mkdir(parents=True, exist_ok=True)

        with open(DETECTED_FILE, "w", encoding="utf-8") as f:
            json.dump(
                [{"name": r["name"], "unread": r.get("unread", 0), "order": r["order"]}
                 for r in self.rooms],
                f, ensure_ascii=False, indent=2,
            )

        with open(SELECTED_FILE, "w", encoding="utf-8") as f:
            json.dump(selected, f, ensure_ascii=False, indent=2)

        print(f"\n[저장 완료] {len(selected)}개 방 선택됨 → {SELECTED_FILE.name}")
        for s in selected:
            print(f"  {s['order']}. {s['name']}")

        messagebox.showinfo(
            "저장 완료",
            f"{len(selected)}개 방이 감시 대상으로 저장되었습니다.\n\n"
            f"저장 위치: {SELECTED_FILE}",
        )
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main():
    app = RoomSelectorApp()
    app.run()


if __name__ == "__main__":
    main()
