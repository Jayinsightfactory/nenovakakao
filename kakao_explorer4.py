# -*- coding: utf-8 -*-
"""
카카오톡 탐색 4: 검색 전략 재설계 + 검증

전략 A: Ctrl+F 검색 (Ctrl+A 대신 Home+Shift+End로 텍스트 선택)
전략 B: 스크롤 + OCR 비전 매칭 + 더블클릭

두 전략 모두 테스트하고 어떤 게 안정적인지 판별.
"""
from __future__ import annotations

import ctypes
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import pyautogui
import pyperclip
import win32gui
import win32con
from PIL import Image

sys.path.insert(0, "C:/Users/USER/nenova_agent")
from core.vision_guard import compare_images

pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0.05

EXPLORER_DIR = Path("C:/Users/USER/nenova_agent/data/explorer")
CAPTURES_DIR = EXPLORER_DIR / "captures"
CAPTURES_DIR.mkdir(parents=True, exist_ok=True)

_cap_id = 200

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def cap(label, region=None) -> tuple[str, Image.Image]:
    global _cap_id
    _cap_id += 1
    fname = f"ex4_{_cap_id:03d}_{label}.png"
    path = CAPTURES_DIR / fname
    time.sleep(0.2)
    img = pyautogui.screenshot(region=region) if region else pyautogui.screenshot()
    img.save(path)
    return fname, img

def activate(hwnd):
    win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    time.sleep(0.1)
    ctypes.windll.user32.keybd_event(0x12, 0, 0, 0)
    ctypes.windll.user32.keybd_event(0x12, 0, 2, 0)
    ctypes.windll.user32.SetForegroundWindow(hwnd)
    time.sleep(0.5)

def find_kakao():
    results = []
    def cb(hwnd, _):
        if win32gui.GetWindowText(hwnd) == "카카오톡":
            if win32gui.GetClassName(hwnd) == "EVA_Window_Dblclk":
                results.append(hwnd)
    win32gui.EnumWindows(cb, None)
    if not results:
        raise RuntimeError("카카오톡 없음")
    hwnd = results[0]
    if not win32gui.IsWindowVisible(hwnd):
        activate(hwnd)
    return hwnd, win32gui.GetWindowRect(hwnd)

def reset(hwnd, rect):
    """깨끗한 채팅 목록 복구."""
    for _ in range(5):
        pyautogui.press("escape")
        time.sleep(0.2)
    activate(hwnd)
    time.sleep(0.3)
    pyautogui.click(rect[0] + 27, rect[1] + 115)
    time.sleep(0.5)


# ===================================================================
# 전략 A: Ctrl+F 검색 (수정된 버전)
# ===================================================================

def test_strategy_a(hwnd, rect):
    log("\n" + "=" * 60)
    log("전략 A: Ctrl+F 검색 (Home+Shift+End로 텍스트 선택)")
    log("=" * 60)

    kakao_region = (rect[0], rect[1], rect[2] - rect[0], rect[3] - rect[1])
    results = {}

    test_rooms = [
        ("수입방", "짧은 정확한 이름"),
        ("네노바&선울", "특수문자 포함"),
        ("발번호및 입고수량확인방", "긴 이름"),
        ("존재안하는방", "없는 방"),
    ]

    for query, desc in test_rooms:
        log(f"\n  --- '{query}' ({desc}) ---")
        reset(hwnd, rect)

        # 기준 캡처
        _, img_before = cap(f"A_{query[:4]}_0_before", kakao_region)

        # Step 1: Ctrl+F
        activate(hwnd)
        pyautogui.hotkey("ctrl", "f")
        time.sleep(1.0)
        _, img_after_f = cap(f"A_{query[:4]}_1_ctrlf", kakao_region)
        sim_f = compare_images(img_before, img_after_f)
        fg_f = win32gui.GetForegroundWindow()
        fg_f_title = win32gui.GetWindowText(fg_f)
        log(f"    Ctrl+F 후: 변화={1-sim_f:.1%}, fg='{fg_f_title}' (hwnd={fg_f})")

        # 친구추가 창이 열렸는지 체크
        if "친구" in fg_f_title or "추가" in fg_f_title:
            log(f"    [!] 친구추가 열림! Ctrl+F가 친구추가를 여는 것으로 확인")
            results[query] = {"method": "ctrlf", "error": "친구추가 열림"}
            pyautogui.press("escape")
            time.sleep(0.3)
            continue

        # Step 2: 텍스트 선택 (Home + Shift+End) - Ctrl+A 대신
        pyautogui.press("home")
        time.sleep(0.1)
        pyautogui.hotkey("shift", "end")
        time.sleep(0.1)
        pyautogui.press("delete")
        time.sleep(0.2)

        # Step 3: 검색어 입력
        pyperclip.copy(query)
        pyautogui.hotkey("ctrl", "v")
        time.sleep(0.8)
        _, img_typed = cap(f"A_{query[:4]}_2_typed", kakao_region)
        fg_typed = win32gui.GetForegroundWindow()
        fg_typed_title = win32gui.GetWindowText(fg_typed)
        log(f"    입력 후: fg='{fg_typed_title}'")

        # 친구추가 체크 (혹시 Ctrl+V가 뭔가 트리거했을 수도)
        if "친구" in fg_typed_title or "추가" in fg_typed_title:
            log(f"    [!] 입력 단계에서 친구추가 감지")
            results[query] = {"method": "ctrlf", "error": "입력 중 친구추가 열림"}
            pyautogui.press("escape")
            time.sleep(0.3)
            continue

        # Step 4: Enter (검색 실행)
        pyautogui.press("enter")
        time.sleep(1.5)
        _, img_enter1 = cap(f"A_{query[:4]}_3_enter1", kakao_region)
        fg_e1 = win32gui.GetForegroundWindow()
        fg_e1_title = win32gui.GetWindowText(fg_e1)
        log(f"    Enter 1회 후: fg='{fg_e1_title}' (hwnd={fg_e1})")

        # 방이 열렸는지 판별
        room_opened = fg_e1 != hwnd
        room_title = fg_e1_title if room_opened else None

        # Enter 1회로 안 열렸으면 2회
        if not room_opened:
            pyautogui.press("enter")
            time.sleep(1.5)
            fg_e2 = win32gui.GetForegroundWindow()
            fg_e2_title = win32gui.GetWindowText(fg_e2)
            room_opened = fg_e2 != hwnd
            room_title = fg_e2_title if room_opened else None
            log(f"    Enter 2회 후: fg='{fg_e2_title}', 열림={room_opened}")

        # Ctrl+S로 실제 방 이름 확인 (방이 열렸으면)
        actual_room = None
        if room_opened:
            # 방 창에서 Ctrl+S -> 저장 다이얼로그에서 파일명으로 방 이름 확인
            # 대신 단순히 창 제목으로 확인
            actual_room = room_title
            log(f"    열린 방 제목: '{actual_room}'")
            correct = query in actual_room if actual_room else False
            log(f"    검색어 '{query}' vs 열린 방 '{actual_room}': {'일치' if correct else '불일치'}")

        _, img_final = cap(f"A_{query[:4]}_4_final", kakao_region)
        results[query] = {
            "method": "ctrlf_fixed",
            "room_opened": room_opened,
            "room_title": room_title,
            "search_correct": (actual_room and query in actual_room) if actual_room else False,
        }

    reset(hwnd, rect)
    return results


# ===================================================================
# 전략 B: 스크롤 + 비전 매칭 + 더블클릭
# ===================================================================

def test_strategy_b(hwnd, rect):
    log("\n" + "=" * 60)
    log("전략 B: 스크롤 + 비전 매칭 (더블클릭)")
    log("=" * 60)

    kakao_region = (rect[0], rect[1], rect[2] - rect[0], rect[3] - rect[1])
    list_left = rect[0] + 60
    list_top = rect[1] + 130
    list_w = rect[2] - rect[0] - 60
    list_h = rect[3] - rect[1] - 150
    list_region = (list_left, list_top, list_w, list_h)
    cx = rect[0] + 250
    cy = rect[1] + 500

    results = {}

    # 1. 맨 위로 스크롤
    reset(hwnd, rect)
    for _ in range(30):
        pyautogui.scroll(10, x=cx, y=cy)
        time.sleep(0.05)
    time.sleep(0.5)

    # 2. 전체 방 리스트 스캔 (위에서 아래로 스크롤하며 캡처)
    log("\n[B1] 전체 방 리스트 스캔")
    snapshots = []
    _, prev_img = cap("B_scan_start", list_region)
    snapshots.append({"step": -1, "capture": "B_scan_start"})

    for step in range(30):
        pyautogui.scroll(-3, x=cx, y=cy)
        time.sleep(0.3)
        fname, curr_img = cap(f"B_scan_{step}", list_region)
        sim = compare_images(prev_img, curr_img)

        if sim > 0.998:
            log(f"  step {step}: 바닥 (변화 없음)")
            break

        snapshots.append({"step": step, "capture": fname, "sim": round(sim, 3)})
        prev_img = curr_img

    log(f"  총 {len(snapshots)} 스냅샷")
    results["scan"] = {"total_snapshots": len(snapshots), "snapshots": snapshots}

    # 3. 특정 방 찾기 테스트 - 수입방을 스크롤로 찾아서 더블클릭
    log("\n[B2] 수입방 찾기 + 더블클릭 테스트")
    reset(hwnd, rect)
    # 맨 위로
    for _ in range(30):
        pyautogui.scroll(10, x=cx, y=cy)
        time.sleep(0.05)
    time.sleep(0.5)

    # 현재 보이는 전체 창 캡처
    _, img_full = cap("B_find_room_start", kakao_region)

    # 방 리스트에서 특정 Y 위치의 방을 더블클릭해서 어떤 방이 열리는지 확인
    # 행 높이 ~70px 기준으로 각 행의 중앙을 더블클릭
    row_results = []
    row_height = 70
    room_list_start_y = rect[1] + 145  # 첫 번째 방 시작 Y

    for row_idx in range(12):  # 최대 12개 방
        reset(hwnd, rect)
        # 맨 위로
        for _ in range(30):
            pyautogui.scroll(10, x=cx, y=cy)
            time.sleep(0.05)
        time.sleep(0.3)

        click_y = room_list_start_y + row_idx * row_height + 35  # 행 중앙
        click_x = rect[0] + 250  # 방 이름 영역

        # 화면 밖이면 중단
        if click_y > rect[3] - 30:
            log(f"  row {row_idx}: 화면 밖 (y={click_y} > {rect[3]-30})")
            break

        # 더블클릭
        pyautogui.doubleClick(click_x, click_y)
        time.sleep(1.5)

        fg = win32gui.GetForegroundWindow()
        fg_title = win32gui.GetWindowText(fg)
        opened = fg != hwnd

        if opened:
            room_rect = win32gui.GetWindowRect(fg)
            log(f"  row {row_idx} (y={click_y}): 열림 -> '{fg_title}' @ {room_rect}")
            row_results.append({
                "row": row_idx, "click_y": click_y,
                "opened": True, "room_title": fg_title,
                "room_hwnd": fg, "room_rect": list(room_rect),
            })
        else:
            log(f"  row {row_idx} (y={click_y}): 안 열림")
            row_results.append({
                "row": row_idx, "click_y": click_y,
                "opened": False,
            })

    results["row_click"] = row_results

    reset(hwnd, rect)
    return results


# ===================================================================
# 메인
# ===================================================================

def main():
    log("=" * 60)
    log("카카오톡 탐색 4: 검색 전략 재설계 + 검증")
    log(f"시작: {datetime.now().isoformat()}")
    log("=" * 60)

    hwnd, rect = find_kakao()
    activate(hwnd)
    log(f"카카오톡: hwnd={hwnd}, rect={rect}")

    all_findings = {}

    # 전략 A
    try:
        a_results = test_strategy_a(hwnd, rect)
        all_findings["strategy_a"] = a_results
    except Exception as e:
        log(f"[ERROR] 전략 A 실패: {e}")
        import traceback; log(traceback.format_exc())
        reset(hwnd, rect)

    # 전략 B
    try:
        b_results = test_strategy_b(hwnd, rect)
        all_findings["strategy_b"] = b_results
    except Exception as e:
        log(f"[ERROR] 전략 B 실패: {e}")
        import traceback; log(traceback.format_exc())
        reset(hwnd, rect)

    # 저장
    out_path = EXPLORER_DIR / "findings_strategy.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(all_findings, f, ensure_ascii=False, indent=2, default=str)
    log(f"\n저장: {out_path}")

    # 요약
    log("\n" + "=" * 60)
    log("요약")
    log("=" * 60)

    if "strategy_a" in all_findings:
        log("\n전략 A (Ctrl+F 검색):")
        for q, r in all_findings["strategy_a"].items():
            if "error" in r:
                log(f"  '{q}': {r['error']}")
            else:
                ok = "정확" if r.get("search_correct") else "불일치/실패"
                title = r.get("room_title", "?")
                log(f"  '{q}': {ok} (열린 방: '{title}')")

    if "strategy_b" in all_findings:
        log("\n전략 B (스크롤+더블클릭):")
        rows = all_findings["strategy_b"].get("row_click", [])
        for r in rows:
            if r["opened"]:
                log(f"  row {r['row']} (y={r['click_y']}): '{r['room_title']}'")
            else:
                log(f"  row {r['row']} (y={r['click_y']}): 안 열림")


if __name__ == "__main__":
    main()
