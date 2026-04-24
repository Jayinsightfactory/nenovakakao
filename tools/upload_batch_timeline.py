"""C': 차수흐름요약 → 구글시트 "차수흐름요약" 탭 업로드.

data/batch_flow_analysis.json (tools/batch_flow_audit.py 산출물) 을 읽어
시트에 배치 기록. 기존 행 전부 삭제 후 새로 쓰는 refresh 방식.

실행:
  python tools/upload_batch_timeline.py          # 전체 업로드
  python tools/upload_batch_timeline.py --dry    # 미리보기 (콘솔만)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.gsheet_sync import _ensure_worksheets, _get_sheet  # noqa: E402

INPUT_JSON = ROOT / "data" / "batch_flow_analysis.json"


def build_rows(report: dict) -> list[list]:
    rows = []
    batches = report.get("차수별_상세_top30", [])
    for b in batches:
        visit_str = " → ".join(
            f"{v['room']}({v['첫등장'][-11:-3]})" for v in b.get("방_방문_순서", [])
        )
        room_event_str = ", ".join(f"{k}:{v}" for k, v in b.get("방별_이벤트수", {}).items())
        sender_str = ", ".join(f"{n}:{c}" for n, c in b.get("발신자_top5", []))
        event_str = ", ".join(f"{k}:{v}" for k, v in b.get("이벤트_분포", {}).items())
        rows.append([
            b.get("차수", ""),
            b.get("이벤트수", 0),
            b.get("첫등장", ""),
            b.get("마지막", ""),
            b.get("기간", ""),
            visit_str[:480],
            room_event_str[:480],
            sender_str[:240],
            event_str[:240],
        ])
    return rows


def main(dry: bool = False) -> None:
    if not INPUT_JSON.exists():
        print(f"[ERR] {INPUT_JSON} 없음. 먼저 tools/batch_flow_audit.py 실행 필요.")
        return
    report = json.loads(INPUT_JSON.read_text(encoding="utf-8"))
    rows = build_rows(report)
    meta = report.get("_meta", {})
    print(f"[INFO] 차수 {meta.get('고유차수_수', '?')}개 중 상위 {len(rows)}개 업로드 준비")

    if dry:
        print()
        print("=" * 70)
        print("[DRY-RUN] 업로드될 행 미리보기 (상위 5)")
        print("=" * 70)
        for r in rows[:5]:
            print(f"\n▶ 차수 {r[0]} ({r[1]}건, {r[4]})")
            print(f"  방문: {r[5]}")
            print(f"  방별: {r[6]}")
            print(f"  발신자: {r[7]}")
        print(f"\n(전체 {len(rows)}행 준비 완료. 실제 업로드하려면 --dry 없이 실행)")
        return

    print("[INFO] 시트 연결 및 탭 준비...")
    _ensure_worksheets()
    sh = _get_sheet()
    ws = sh.worksheet("차수흐름요약")
    # 헤더 보존, 데이터 영역만 교체
    ws.batch_clear(["A2:I10000"])
    if rows:
        ws.append_rows(rows, value_input_option="USER_ENTERED")
    print(f"[OK] {len(rows)}행 업로드 완료 → '차수흐름요약' 탭")


if __name__ == "__main__":
    dry = "--dry" in sys.argv
    main(dry=dry)
