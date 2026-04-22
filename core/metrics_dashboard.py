"""
스텝별 성공률/재시도/평균시간 대시보드.

- show_cli(): 터미널 표
- show_gui(): tkinter 대시보드 (실시간 새로고침)
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from core.traced_actions import load_metrics

ROOT = Path(__file__).parent.parent
METRICS_FILE = ROOT / "data" / "step_metrics.json"


def _render_rows(m: dict) -> list[dict]:
    """메트릭 dict → 정렬된 row 리스트."""
    rows = []
    for step, s in sorted(m.items()):
        total = s.get("total", 0)
        success = s.get("success", 0)
        fail = s.get("fail", 0)
        rate = (success / total * 100) if total > 0 else 0
        rows.append({
            "step": step,
            "total": total,
            "success": success,
            "fail": fail,
            "rate": rate,
            "retries": s.get("retries_used", 0),
            "streak": s.get("streak", 0),
            "locked": s.get("locked", False),
            "avg_ms": s.get("avg_time_ms", 0),
            "last_fail": s.get("last_fail_reason", "")[:60],
        })
    return rows


def show_cli() -> None:
    m = load_metrics()
    if not m:
        print("[METRICS] 아직 수집된 데이터가 없습니다.")
        print("          python main.py learn 또는 main.py 로 파이프라인을 1회 이상 실행하세요.")
        return
    rows = _render_rows(m)
    # 요약
    total_all = sum(r["total"] for r in rows)
    success_all = sum(r["success"] for r in rows)
    locked = sum(1 for r in rows if r["locked"])
    print(f"\n=== 스텝 메트릭 ({len(rows)}개 스텝, 총 {total_all}회 실행) ===")
    print(f"    전체 성공률: {success_all/total_all*100:.1f}% "
          f"({success_all}/{total_all}), Lock: {locked}개")
    print()
    hdr = f"{'STEP':<40} {'RATE':>6} {'OK':>5} {'FAIL':>5} {'STRK':>5} {'LOCK':>5} {'AVGms':>7}"
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        lock_mark = "LOCK" if r["locked"] else ""
        rate_str = f"{r['rate']:.0f}%"
        print(
            f"{r['step']:<40} {rate_str:>6} {r['success']:>5} {r['fail']:>5} "
            f"{r['streak']:>5} {lock_mark:>5} {r['avg_ms']:>7.0f}"
        )
    print()
    # 최근 실패 상세
    recent_fails = sorted(
        [r for r in rows if r["fail"] > 0 and r["last_fail"]],
        key=lambda r: r["rate"],
    )[:5]
    if recent_fails:
        print("=== 실패율 높은 상위 5 스텝 ===")
        for r in recent_fails:
            print(f"  [{r['rate']:.0f}%] {r['step']}")
            print(f"          └ {r['last_fail']}")
        print()


def show_gui() -> None:
    """tkinter 대시보드 — 3초마다 새로고침."""
    try:
        import tkinter as tk
        from tkinter import ttk
    except ImportError:
        print("[METRICS] tkinter 없음 — CLI 모드로 실행하세요.")
        show_cli()
        return

    root = tk.Tk()
    root.title("네노바 에이전트 스텝 메트릭")
    root.geometry("1200x700")

    # 상단 요약
    summary = tk.Label(root, text="", font=("맑은 고딕", 12), fg="white", bg="#333")
    summary.pack(fill="x")

    # 트리 (표)
    cols = ("step", "rate", "ok", "fail", "streak", "locked", "avg_ms", "last_fail")
    tree = ttk.Treeview(root, columns=cols, show="headings")
    widths = {"step": 350, "rate": 60, "ok": 50, "fail": 50, "streak": 60,
              "locked": 60, "avg_ms": 70, "last_fail": 400}
    for c in cols:
        tree.heading(c, text=c.upper())
        tree.column(c, width=widths.get(c, 80), anchor="w" if c in ("step", "last_fail") else "center")
    tree.pack(fill="both", expand=True)

    tree.tag_configure("locked", background="#dff0d8")
    tree.tag_configure("low", background="#f2dede")

    def refresh():
        m = load_metrics()
        tree.delete(*tree.get_children())
        rows = _render_rows(m)
        if rows:
            total_all = sum(r["total"] for r in rows)
            success_all = sum(r["success"] for r in rows)
            locked = sum(1 for r in rows if r["locked"])
            pct = success_all / total_all * 100 if total_all > 0 else 0
            summary.config(
                text=f"총 스텝 {len(rows)} / 실행 {total_all}회 / 전체 성공률 {pct:.1f}% / 락 {locked}개",
            )
            for r in rows:
                tags = []
                if r["locked"]:
                    tags.append("locked")
                elif r["rate"] < 80 and r["total"] >= 3:
                    tags.append("low")
                tree.insert(
                    "", "end",
                    values=(
                        r["step"], f"{r['rate']:.0f}%", r["success"], r["fail"],
                        r["streak"], "LOCK" if r["locked"] else "",
                        f"{r['avg_ms']:.0f}", r["last_fail"],
                    ),
                    tags=tags,
                )
        else:
            summary.config(text="아직 수집된 데이터가 없습니다.")
        root.after(3000, refresh)

    refresh()
    root.mainloop()


if __name__ == "__main__":
    import sys
    if "--gui" in sys.argv:
        show_gui()
    else:
        show_cli()
