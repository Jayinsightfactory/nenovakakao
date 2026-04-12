# -*- coding: utf-8 -*-
"""
카카오톡 탐색 3: Ctrl+F 검색 정확도 + 스크롤 전략

테스트 항목:
  A. Ctrl+F 검색
    1. 검색창 열기/닫기 상태 정확 판별
    2. 다양한 방 이름 검색 (정확한 이름, 부분 이름, 특수문자)
    3. 검색 결과가 나왔는지 판별
    4. Enter로 방이 열렸는지 판별
    5. 검색 후 상태 복구
    6. 연속 검색 (1방 검색 -> 닫기 -> 2방 검색) 안정성

  B. 스크롤 전략
    1. 방 리스트 전체 높이 측정 (맨 위 vs 맨 아래 해시)
    2. 스크롤 단위별 이동량 (픽셀 단위 정밀 측정)
    3. 위->아래 순회하며 모든 방 발견
    4. 특정 방까지 스크롤로 도달하는 전략
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
import numpy as np
from PIL import Image

sys.path.insert(0, "C:/Users/USER/nenova_agent")
from core.vision_guard import compare_images, compare_regions

pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0.05

EXPLORER_DIR = Path("C:/Users/USER/nenova_agent/data/explorer")
CAPTURES_DIR = EXPLORER_DIR / "captures"
CAPTURES_DIR.mkdir(parents=True, exist_ok=True)

_cap_id = 100  # explorer2와 구분

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def cap(label, region=None) -> tuple[str, Image.Image]:
    global _cap_id
    _cap_id += 1
    fname = f"ex3_{_cap_id:03d}_{label}.png"
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

def reset_to_clean(hwnd, rect):
    """깨끗한 채팅 목록 상태로 복구."""
    for _ in range(5):
        pyautogui.press("escape")
        time.sleep(0.2)
    activate(hwnd)
    time.sleep(0.3)
    # 채팅탭 클릭
    pyautogui.click(rect[0] + 27, rect[1] + 115)
    time.sleep(0.5)

def get_list_region(rect):
    """방 리스트 영역 (캡처용 region tuple)."""
    return (rect[0] + 60, rect[1] + 130,
            rect[2] - rect[0] - 60, rect[3] - rect[1] - 150)

def get_search_bar_region(rect):
    """검색바 영역 (상단 부분)."""
    return (rect[0] + 60, rect[1] + 55,
            rect[2] - rect[0] - 60, 80)

def scroll_to_top(rect):
    """방 리스트 맨 위로."""
    cx = rect[0] + 250
    cy = rect[1] + 500
    for _ in range(30):
        pyautogui.scroll(10, x=cx, y=cy)
        time.sleep(0.05)
    time.sleep(0.3)


# ===================================================================
# A. Ctrl+F 검색 정확도 테스트
# ===================================================================

def test_search_accuracy(hwnd, rect):
    log("\n" + "=" * 60)
    log("A. Ctrl+F 검색 정확도 테스트")
    log("=" * 60)

    kakao_region = (rect[0], rect[1], rect[2] - rect[0], rect[3] - rect[1])
    list_region = get_list_region(rect)
    search_region = get_search_bar_region(rect)
    results = {}

    # --- A1. 검색창 열기/닫기 상태 판별 ---
    log("\n[A1] 검색창 열기/닫기 상태 판별")
    reset_to_clean(hwnd, rect)

    # 기준: 검색 안 열린 상태
    _, img_no_search = cap("search_closed", kakao_region)

    # Ctrl+F 열기
    pyautogui.hotkey("ctrl", "f")
    time.sleep(1.0)
    _, img_search_open = cap("search_opened", kakao_region)

    # 전체 유사도
    sim_full = compare_images(img_no_search, img_search_open)
    log(f"  전체 유사도: {sim_full:.1%}")

    # 상단 검색바 영역만 비교
    _, img_bar_closed = cap("searchbar_closed", search_region)
    pyautogui.press("escape")
    time.sleep(0.3)

    # 다시 열어서 검색바 캡처
    pyautogui.hotkey("ctrl", "f")
    time.sleep(1.0)
    _, img_bar_open = cap("searchbar_opened", search_region)
    sim_bar = compare_images(img_bar_closed, img_bar_open)
    log(f"  검색바 영역 유사도: {sim_bar:.1%}")

    # 방 리스트 영역만 비교
    reset_to_clean(hwnd, rect)
    _, img_list_closed = cap("list_no_search", list_region)
    pyautogui.hotkey("ctrl", "f")
    time.sleep(1.0)
    _, img_list_open = cap("list_with_search", list_region)
    sim_list = compare_images(img_list_closed, img_list_open)
    log(f"  리스트 영역 유사도: {sim_list:.1%}")

    results["state_detection"] = {
        "full_similarity": round(sim_full, 3),
        "searchbar_similarity": round(sim_bar, 3),
        "list_similarity": round(sim_list, 3),
        "note": "유사도가 낮을수록 검색창 열림/닫힘 구분 가능",
    }

    # --- A2. 다양한 방 이름 검색 ---
    log("\n[A2] 다양한 방 이름으로 검색 테스트")

    test_rooms = [
        # (검색어, 설명)
        ("수입방", "정확한 이름, 짧은 단어"),
        ("네노바", "부분 이름 (여러 방이 매칭될 수 있음)"),
        ("네노바&선울", "특수문자 & 포함"),
        ("발번호및 입고수량확인방", "긴 이름, 띄어쓰기"),
        ("현장단체방", "정확한 이름"),
        ("존재안하는방", "없는 방 검색"),
        ("견적", "부분 단어"),
    ]

    search_results = []

    for query, desc in test_rooms:
        log(f"\n  --- 검색: '{query}' ({desc}) ---")
        reset_to_clean(hwnd, rect)

        # 기준 캡처
        _, img_before = cap(f"search_{query[:6]}_before", kakao_region)

        # Ctrl+F
        pyautogui.hotkey("ctrl", "f")
        time.sleep(1.0)

        # 기존 검색어 제거 + 새 검색어 입력
        pyautogui.hotkey("ctrl", "a")
        time.sleep(0.1)
        pyautogui.press("delete")
        time.sleep(0.2)
        pyperclip.copy(query)
        pyautogui.hotkey("ctrl", "v")
        time.sleep(0.8)

        # 검색 결과 캡처
        _, img_typed = cap(f"search_{query[:6]}_typed", kakao_region)
        sim_typed = compare_images(img_before, img_typed)

        # 클립보드 확인 (입력 검증)
        clip = pyperclip.paste()
        input_ok = clip == query
        log(f"    입력 확인: 클립보드='{clip}', 일치={input_ok}")

        # Enter 1회 (검색 실행)
        _, img_pre_enter = cap(f"search_{query[:6]}_pre_enter", kakao_region)
        pyautogui.press("enter")
        time.sleep(1.5)
        _, img_post_enter = cap(f"search_{query[:6]}_post_enter", kakao_region)
        sim_enter = compare_images(img_pre_enter, img_post_enter)

        # 포그라운드 확인 (방이 열렸나?)
        fg = win32gui.GetForegroundWindow()
        fg_title = win32gui.GetWindowText(fg)
        fg_class = win32gui.GetClassName(fg)
        room_opened = fg != hwnd

        log(f"    Enter 후: fg='{fg_title}' (class={fg_class}), 방열림={room_opened}")
        log(f"    화면 변화: 입력시={1-sim_typed:.1%}, Enter시={1-sim_enter:.1%}")

        # 열린 방의 hwnd/rect 기록
        room_info = None
        if room_opened:
            room_rect = win32gui.GetWindowRect(fg)
            room_info = {
                "hwnd": fg, "title": fg_title, "class": fg_class,
                "rect": list(room_rect),
                "size": [room_rect[2]-room_rect[0], room_rect[3]-room_rect[1]],
            }
            log(f"    열린 방: {fg_title} @ {room_rect}")

            # 한 번 더 Enter (혹시 검색에서 한 번, 방 열기에 한 번?)
            # -> 이미 열렸으면 스킵

        # Enter 2회 테스트 (검색 결과 선택 -> 방 열기가 분리된 경우)
        if not room_opened:
            log(f"    Enter 1회로 안 열림 -> 2회째 Enter")
            pyautogui.press("enter")
            time.sleep(1.5)
            fg2 = win32gui.GetForegroundWindow()
            fg2_title = win32gui.GetWindowText(fg2)
            room_opened_2 = fg2 != hwnd
            log(f"    Enter 2회 후: fg='{fg2_title}', 방열림={room_opened_2}")
            if room_opened_2:
                room_rect = win32gui.GetWindowRect(fg2)
                room_info = {
                    "hwnd": fg2, "title": fg2_title,
                    "class": win32gui.GetClassName(fg2),
                    "rect": list(room_rect),
                    "enter_count": 2,
                }

        result = {
            "query": query,
            "desc": desc,
            "input_correct": input_ok,
            "change_on_type": round(1 - sim_typed, 3),
            "change_on_enter": round(1 - sim_enter, 3),
            "room_opened": room_opened or (room_info is not None),
            "room_info": room_info,
        }
        search_results.append(result)

        # 캡처: 최종 상태
        cap(f"search_{query[:6]}_final", kakao_region)

    results["search_tests"] = search_results

    # --- A3. 연속 검색 안정성 ---
    log("\n[A3] 연속 검색 안정성 (3방 연속)")
    reset_to_clean(hwnd, rect)
    _, img_clean = cap("sequential_clean", kakao_region)

    sequential = []
    seq_rooms = ["수입방", "견적방", "한국방역"]

    for i, room in enumerate(seq_rooms):
        log(f"\n  [{i+1}/3] '{room}' 검색")

        # 초기화
        reset_to_clean(hwnd, rect)
        time.sleep(0.3)

        # 현재 상태 확인 (깨끗한지)
        _, img_pre = cap(f"seq_{i}_pre", kakao_region)
        sim_to_clean = compare_images(img_clean, img_pre)
        log(f"    초기 상태 유사도: {sim_to_clean:.1%}")

        # 검색
        pyautogui.hotkey("ctrl", "f")
        time.sleep(1.0)
        pyautogui.hotkey("ctrl", "a")
        time.sleep(0.1)
        pyautogui.press("delete")
        time.sleep(0.2)
        pyperclip.copy(room)
        pyautogui.hotkey("ctrl", "v")
        time.sleep(0.8)
        pyautogui.press("enter")
        time.sleep(1.5)

        fg = win32gui.GetForegroundWindow()
        fg_title = win32gui.GetWindowText(fg)
        opened = fg != hwnd

        if opened:
            log(f"    열림: '{fg_title}'")
            # ESC로 닫기
            pyautogui.press("escape")
            time.sleep(0.5)
        else:
            log(f"    안 열림 (fg={fg_title})")

        sequential.append({
            "room": room, "index": i,
            "clean_similarity": round(sim_to_clean, 3),
            "opened": opened,
            "opened_title": fg_title if opened else None,
        })

    results["sequential"] = sequential

    # --- 최종 복구 ---
    reset_to_clean(hwnd, rect)

    return results


# ===================================================================
# B. 스크롤 전략 테스트
# ===================================================================

def test_scroll_strategy(hwnd, rect):
    log("\n" + "=" * 60)
    log("B. 스크롤 전략 테스트")
    log("=" * 60)

    list_region = get_list_region(rect)
    kakao_region = (rect[0], rect[1], rect[2] - rect[0], rect[3] - rect[1])
    cx = rect[0] + 250  # 스크롤 X 좌표
    cy = rect[1] + 500  # 스크롤 Y 좌표
    results = {}

    reset_to_clean(hwnd, rect)

    # --- B1. 전체 스크롤 범위 측정 ---
    log("\n[B1] 전체 스크롤 범위 측정")

    # 맨 위
    scroll_to_top(rect)
    _, img_top = cap("scroll_absolute_top", list_region)
    _, img_top_full = cap("scroll_absolute_top_full", kakao_region)

    # 맨 아래로 (큰 스크롤)
    for _ in range(50):
        pyautogui.scroll(-10, x=cx, y=cy)
        time.sleep(0.05)
    time.sleep(0.5)
    _, img_bottom = cap("scroll_absolute_bottom", list_region)
    _, img_bottom_full = cap("scroll_absolute_bottom_full", kakao_region)

    sim_top_bottom = compare_images(img_top, img_bottom)
    log(f"  맨 위 vs 맨 아래: 유사도={sim_top_bottom:.1%}, 변화={1-sim_top_bottom:.1%}")
    results["range"] = {
        "top_vs_bottom_similarity": round(sim_top_bottom, 3),
    }

    # --- B2. 정밀 스크롤 단위 측정 ---
    log("\n[B2] 정밀 스크롤 단위 측정")
    log("  (맨 위에서 시작하여 1칸씩 내려가며 변화 측정)")

    scroll_to_top(rect)
    time.sleep(0.3)

    # 방 리스트 내부에 마우스 올리고 클릭 (포커스 확보)
    pyautogui.click(cx, cy)
    time.sleep(0.3)

    precision_data = []
    _, prev_img = cap("scroll_step_base", list_region)

    for step in range(15):
        pyautogui.scroll(-3, x=cx, y=cy)
        time.sleep(0.4)
        fname, curr_img = cap(f"scroll_step_{step}", list_region)
        sim = compare_images(prev_img, curr_img)
        changed = sim < 0.998

        log(f"  step {step}: scroll(-3) -> 변화={1-sim:.1%} {'[이동]' if changed else '[정지]'}")
        precision_data.append({
            "step": step, "scroll_amount": -3,
            "similarity": round(sim, 3),
            "changed": changed,
            "capture": fname,
        })

        if not changed and step > 2:
            log(f"  -> 바닥 도달 (step {step})")
            break

        prev_img = curr_img

    results["precision"] = precision_data

    # --- B3. 다양한 스크롤 양 비교 ---
    log("\n[B3] 스크롤 양별 이동 비교")

    amounts = [-1, -2, -3, -5, -7, -10, -15, -20]
    amount_data = []

    for amt in amounts:
        scroll_to_top(rect)
        # 포커스 확보: 리스트 내부 클릭
        pyautogui.click(cx, cy)
        time.sleep(0.3)

        _, img_before = cap(f"scroll_amt_{abs(amt)}_before", list_region)
        pyautogui.scroll(amt, x=cx, y=cy)
        time.sleep(0.5)
        _, img_after = cap(f"scroll_amt_{abs(amt)}_after", list_region)

        sim = compare_images(img_before, img_after)
        change = round((1 - sim) * 100, 1)
        log(f"  scroll({amt}): 변화={change}%")
        amount_data.append({
            "amount": amt, "change_pct": change,
            "similarity": round(sim, 3),
        })

    results["amounts"] = amount_data

    # --- B4. 위->아래 순회: 모든 방 발견 ---
    log("\n[B4] 위->아래 순회하며 모든 방 스냅샷")

    scroll_to_top(rect)
    pyautogui.click(cx, cy)
    time.sleep(0.3)

    all_snapshots = []
    _, prev_img = cap("traverse_start", list_region)
    all_snapshots.append("traverse_start")

    for i in range(20):  # 최대 20 스텝
        pyautogui.scroll(-5, x=cx, y=cy)
        time.sleep(0.4)
        fname, curr_img = cap(f"traverse_{i}", list_region)
        sim = compare_images(prev_img, curr_img)

        if sim > 0.998:
            log(f"  traverse step {i}: 바닥 (변화 없음)")
            break

        log(f"  traverse step {i}: 변화={1-sim:.1%}")
        all_snapshots.append(fname)
        prev_img = curr_img

    results["traverse"] = {
        "total_steps": len(all_snapshots),
        "snapshots": all_snapshots,
    }

    # --- B5. 특정 방까지 스크롤로 도달 ---
    log("\n[B5] 특정 방 스크롤 도달 테스트")
    log("  (맨 위에서 시작하여 1스텝씩 내려가며 각 위치의 전체 창 캡처)")

    scroll_to_top(rect)
    pyautogui.click(cx, cy)
    time.sleep(0.3)

    # 전체 창을 캡처하면서 내려감
    full_snapshots = []
    _, prev_full = cap("full_traverse_start", kakao_region)
    full_snapshots.append({"step": -1, "capture": "full_traverse_start"})

    for i in range(10):
        pyautogui.scroll(-5, x=cx, y=cy)
        time.sleep(0.4)
        fname, curr_full = cap(f"full_traverse_{i}", kakao_region)
        sim = compare_images(prev_full, curr_full)

        if sim > 0.998:
            log(f"  full step {i}: 바닥")
            break

        log(f"  full step {i}: 변화={1-sim:.1%}")
        full_snapshots.append({"step": i, "capture": fname, "sim": round(sim, 3)})
        prev_full = curr_full

    results["full_traverse"] = full_snapshots

    # --- B6. 맨 위로 복귀 속도 측정 ---
    log("\n[B6] 맨 위 복귀 속도")
    # 현재 맨 아래 상태
    t0 = time.time()
    scroll_to_top(rect)
    dur = time.time() - t0
    log(f"  맨 위 복귀: {dur:.1f}초")
    results["scroll_to_top_duration"] = round(dur, 1)

    reset_to_clean(hwnd, rect)
    return results


# ===================================================================
# 메인
# ===================================================================

def main():
    log("=" * 60)
    log("카카오톡 탐색 3: Ctrl+F 검색 + 스크롤 전략")
    log(f"시작: {datetime.now().isoformat()}")
    log("=" * 60)

    hwnd, rect = find_kakao()
    activate(hwnd)
    log(f"카카오톡: hwnd={hwnd}, rect={rect}")

    all_findings = {}

    try:
        search_results = test_search_accuracy(hwnd, rect)
        all_findings["search"] = search_results
    except Exception as e:
        log(f"[ERROR] 검색 테스트 실패: {e}")
        import traceback; log(traceback.format_exc())
        reset_to_clean(hwnd, rect)

    try:
        scroll_results = test_scroll_strategy(hwnd, rect)
        all_findings["scroll"] = scroll_results
    except Exception as e:
        log(f"[ERROR] 스크롤 테스트 실패: {e}")
        import traceback; log(traceback.format_exc())

    # 저장
    out_path = EXPLORER_DIR / "findings_search_scroll.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(all_findings, f, ensure_ascii=False, indent=2, default=str)
    log(f"\n저장: {out_path}")

    # 요약
    log("\n" + "=" * 60)
    log("요약")
    log("=" * 60)
    if "search" in all_findings:
        st = all_findings["search"].get("search_tests", [])
        for r in st:
            ok = "열림" if r["room_opened"] else "안열림"
            title = r["room_info"]["title"] if r["room_info"] else "-"
            log(f"  검색 '{r['query']}': {ok} -> '{title}'")
        seq = all_findings["search"].get("sequential", [])
        for s in seq:
            log(f"  연속검색 '{s['room']}': "
                f"{'열림' if s['opened'] else '안열림'} "
                f"(초기상태={s['clean_similarity']:.1%})")
    if "scroll" in all_findings:
        sc = all_findings["scroll"]
        log(f"  스크롤 범위: 위vs아래 유사도={sc.get('range',{}).get('top_vs_bottom_similarity','?')}")
        log(f"  순회 스텝: {sc.get('traverse',{}).get('total_steps','?')}")
        log(f"  복귀 시간: {sc.get('scroll_to_top_duration','?')}초")


if __name__ == "__main__":
    main()
