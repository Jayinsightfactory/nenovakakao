"""
앵커 후보 확인 GUI — 학습 세션에서 추출된 후보들을 관리자가 검토/확정.

워크플로:
1. data/anchor_candidates/<session>/*.png 를 순회
2. 각 후보 이미지를 보여줌
3. 관리자가 ROI 박스를 드래그로 지정 (선택적, 미지정 시 전체 프레임)
4. [승인] → data/anchors/<step>.png 로 저장 + anchors_meta.json 에 ROI 기록
5. [기각] → 다음 후보로

'넓게 학습 → 좁혀가기' 철학: ROI를 점점 작게 줄이면서 매칭 정확도 향상.
"""
from __future__ import annotations

import json
import tkinter as tk
from pathlib import Path
from tkinter import messagebox

import cv2
from PIL import Image, ImageTk

ROOT = Path(__file__).parent.parent
CAND_DIR = ROOT / "data" / "anchor_candidates"
ANCHOR_DIR = ROOT / "data" / "anchors"
META_PATH = ROOT / "data" / "anchors_meta.json"


def _latest_session() -> Path | None:
    if not CAND_DIR.exists():
        return None
    sessions = sorted([p for p in CAND_DIR.iterdir() if p.is_dir()], reverse=True)
    return sessions[0] if sessions else None


class AnchorPicker:
    def __init__(self, session_dir: Path):
        self.session_dir = session_dir
        self.candidates = sorted(session_dir.glob("*.png"))
        self.idx = 0
        self.meta = self._load_meta()

        self.root = tk.Tk()
        self.root.title(f"앵커 검증 — {session_dir.name}")
        self.root.geometry("1400x900")

        # 상단 정보
        self.info = tk.Label(self.root, text="", font=("맑은 고딕", 12), fg="white", bg="#333")
        self.info.pack(fill="x")

        # 중앙 캔버스 (이미지 + 드래그 ROI)
        self.canvas = tk.Canvas(self.root, bg="black", cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Button-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)

        # 하단 버튼
        btns = tk.Frame(self.root, bg="#222")
        btns.pack(fill="x")
        tk.Button(btns, text="◀ 이전", command=self.prev, width=10).pack(side="left", padx=5, pady=5)
        tk.Button(btns, text="ROI 초기화", command=self.reset_roi, width=10).pack(side="left", padx=5)
        tk.Button(btns, text="기각 (스킵)", command=self.reject, width=12, bg="#a44").pack(side="left", padx=5)
        tk.Button(btns, text="승인 (저장) ▶", command=self.accept, width=15, bg="#4a4").pack(side="right", padx=5, pady=5)

        self.roi_box = None  # (x1, y1, x2, y2) in image coords
        self.drag_start = None
        self.tk_img = None
        self.img_scale = 1.0
        self.img_offset = (0, 0)
        self.pil_img: Image.Image | None = None

        self._load_current()

    def _load_meta(self) -> dict:
        if META_PATH.exists():
            return json.loads(META_PATH.read_text(encoding="utf-8"))
        return {}

    def _save_meta(self) -> None:
        META_PATH.write_text(json.dumps(self.meta, ensure_ascii=False, indent=2), encoding="utf-8")

    def _load_current(self) -> None:
        if not self.candidates:
            self.info.config(text="후보 없음 — 먼저 'python main.py learn' 실행")
            return
        if self.idx < 0:
            self.idx = 0
        if self.idx >= len(self.candidates):
            self.info.config(text="✅ 모든 후보 검토 완료!")
            self.canvas.delete("all")
            return

        png = self.candidates[self.idx]
        step = png.stem
        existing = "(이미 확정됨)" if (ANCHOR_DIR / f"{step}.png").exists() else ""
        self.info.config(text=f"[{self.idx+1}/{len(self.candidates)}]  {step}  {existing}   "
                              f"— 드래그로 앵커 영역 선택 (없으면 전체 프레임)")

        self.pil_img = Image.open(png)
        self._redraw()
        self.roi_box = None

    def _redraw(self) -> None:
        if self.pil_img is None:
            return
        self.canvas.update()
        cw = self.canvas.winfo_width() or 1400
        ch = self.canvas.winfo_height() or 800
        iw, ih = self.pil_img.size
        scale = min(cw / iw, ch / ih, 1.0)
        new_w, new_h = int(iw * scale), int(ih * scale)
        resized = self.pil_img.resize((new_w, new_h), Image.LANCZOS)
        self.tk_img = ImageTk.PhotoImage(resized)
        self.canvas.delete("all")
        ox = (cw - new_w) // 2
        oy = (ch - new_h) // 2
        self.canvas.create_image(ox, oy, anchor="nw", image=self.tk_img)
        self.img_scale = scale
        self.img_offset = (ox, oy)

        if self.roi_box:
            x1, y1, x2, y2 = self.roi_box
            cx1 = ox + x1 * scale
            cy1 = oy + y1 * scale
            cx2 = ox + x2 * scale
            cy2 = oy + y2 * scale
            self.canvas.create_rectangle(cx1, cy1, cx2, cy2, outline="#0f0", width=3)

    # ─── 이벤트 ───
    def _on_press(self, e):
        self.drag_start = (e.x, e.y)

    def _on_drag(self, e):
        if self.drag_start is None:
            return
        self._redraw()
        x1, y1 = self.drag_start
        self.canvas.create_rectangle(x1, y1, e.x, e.y, outline="#0f0", width=2)

    def _on_release(self, e):
        if self.drag_start is None:
            return
        x1, y1 = self.drag_start
        x2, y2 = e.x, e.y
        if abs(x2 - x1) < 10 or abs(y2 - y1) < 10:
            self.drag_start = None
            return
        # 캔버스 좌표 → 이미지 좌표
        ox, oy = self.img_offset
        s = self.img_scale
        ix1 = max(0, int((min(x1, x2) - ox) / s))
        iy1 = max(0, int((min(y1, y2) - oy) / s))
        ix2 = int((max(x1, x2) - ox) / s)
        iy2 = int((max(y1, y2) - oy) / s)
        self.roi_box = (ix1, iy1, ix2, iy2)
        self.drag_start = None
        self._redraw()

    # ─── 버튼 ───
    def prev(self):
        self.idx = max(0, self.idx - 1)
        self._load_current()

    def reset_roi(self):
        self.roi_box = None
        self._redraw()

    def reject(self):
        self.idx += 1
        self._load_current()

    def accept(self):
        if not self.candidates or self.idx >= len(self.candidates):
            return
        png = self.candidates[self.idx]
        step = png.stem
        ANCHOR_DIR.mkdir(parents=True, exist_ok=True)

        if self.roi_box and self.pil_img:
            # ROI 크롭 → 앵커 이미지로 저장
            x1, y1, x2, y2 = self.roi_box
            cropped = self.pil_img.crop((x1, y1, x2, y2))
            cropped.save(ANCHOR_DIR / f"{step}.png")
            # 메타에 절대 ROI 기록 (화면 좌표계 — 학습 시 화면 전체 기준)
            self.meta[step] = {
                "roi": [x1, y1, x2 - x1, y2 - y1],
                "threshold": 0.85,
                "source": png.name,
            }
        else:
            # 전체 프레임 앵커
            self.pil_img.save(ANCHOR_DIR / f"{step}.png")
            self.meta[step] = {"threshold": 0.85, "source": png.name}

        self._save_meta()
        print(f"[Anchor] 저장: {step}.png", flush=True)
        self.idx += 1
        self._load_current()

    def run(self) -> int:
        if not self.candidates:
            messagebox.showwarning("앵커 후보 없음", "먼저 'python main.py learn' 으로 학습을 수행하세요.")
            return 1
        self.root.mainloop()
        return 0


def run_picker() -> int:
    session = _latest_session()
    if session is None:
        print("[ERROR] data/anchor_candidates/ 가 비어있습니다.")
        print("  먼저 'python main.py learn' 으로 학습을 실행하세요.")
        return 1
    print(f"[Picker] 최신 세션 로드: {session}")
    picker = AnchorPicker(session)
    return picker.run()
