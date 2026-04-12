# -*- coding: utf-8 -*-
"""
카카오톡 추가 탐색: 뱃지(안읽음) + 스크롤 동작

사용법:
  PYTHONIOENCODING=utf-8 python kakao_explorer2.py
"""
from __future__ import annotations

import ctypes
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import pyautogui
import pygetwindow as gw
import win32gui
import win32con
from PIL import Image
import numpy as np

sys.path.insert(0, "C:/Users/USER/nenova_agent")
from core.vision_guard import compare_images, compare_regions

pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0.05

EXPLORER_DIR = Path("C:/Users/USER/nenova_agent/data/explorer")
CAPTURES_DIR = EXPLORER_DIR / "captures"
CAPTURES_DIR.mkdir(parents=True, exist_ok=True)

_cap_id = 0

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def cap(label, region=None) -> tuple[str, Image.Image]:
    global _cap_id
    _cap_id += 1
    fname = f"ex2_{_cap_id:03d}_{label}.png"
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
    """카카오톡 메인 창 hwnd + rect."""
    results = []
    def cb(hwnd, _):
        if win32gui.GetWindowText(hwnd) == "카카오톡":
            if win32gui.GetClassName(hwnd) == "EVA_Window_Dblclk":
                results.append(hwnd)
    win32gui.EnumWindows(cb, None)
    if not results:
        # visible이 아닌 것도 포함
        def cb2(hwnd, _):
            t = win32gui.GetWindowText(hwnd)
            if "카카오톡" in t:
                results.append(hwnd)
        win32gui.EnumWindows(cb2, None)
    if not results:
        raise RuntimeError("카카오톡 없음")
    hwnd = results[0]
    if not win32gui.IsWindowVisible(hwnd):
        activate(hwnd)
    rect = win32gui.GetWindowRect(hwnd)
    return hwnd, rect

# ===================================================================

def analyze_badges(img: Image.Image, rect: tuple) -> list[dict]:
    """
    방 리스트 이미지에서 빨간 뱃지(안읽음) 위치를 찾는다.
    빨간색 픽셀 클러스터 = R>180, G<100, B<100
    """
    arr = np.array(img)
    # 빨간 픽셀 마스크
    red_mask = (arr[:, :, 0] > 180) & (arr[:, :, 1] < 100) & (arr[:, :, 2] < 100)

    # 연결된 빨간 영역 찾기 (단순 행 스캔)
    badges = []
    visited = set()

    ys, xs = np.where(red_mask)
    if len(ys) == 0:
        return badges

    # 클러스터링: 가까운 빨간 픽셀 그룹핑
    from collections import defaultdict
    row_groups = defaultdict(list)
    for y, x in zip(ys, xs):
        row_groups[y].append(x)

    # 행별로 연속 구간 추출
    clusters = []
    for y in sorted(row_groups.keys()):
        xs_sorted = sorted(row_groups[y])
        start = xs_sorted[0]
        end = xs_sorted[0]
        for x in xs_sorted[1:]:
            if x - end <= 2:  # 2px 이내면 같은 클러스터
                end = x
            else:
                clusters.append((y, start, end))
                start = x
                end = x
        clusters.append((y, start, end))

    # Y 근접 클러스터 병합 -> 뱃지 영역
    if not clusters:
        return badges

    merged = []
    current = {"y_min": clusters[0][0], "y_max": clusters[0][0],
               "x_min": clusters[0][1], "x_max": clusters[0][2]}

    for y, x_start, x_end in clusters[1:]:
        if y - current["y_max"] <= 3 and abs(x_start - current["x_min"]) < 30:
            current["y_max"] = y
            current["x_min"] = min(current["x_min"], x_start)
            current["x_max"] = max(current["x_max"], x_end)
        else:
            merged.append(current)
            current = {"y_min": y, "y_max": y, "x_min": x_start, "x_max": x_end}
    merged.append(current)

    for m in merged:
        w = m["x_max"] - m["x_min"]
        h = m["y_max"] - m["y_min"]
        if w >= 3 and h >= 3:  # 최소 3x3 이상이어야 뱃지
            badges.append({
                "x": m["x_min"], "y": m["y_min"],
                "width": w, "height": h,
                "center_x": (m["x_min"] + m["x_max"]) // 2,
                "center_y": (m["y_min"] + m["y_max"]) // 2,
                # 절대 좌표
                "abs_x": rect[0] + m["x_min"],
                "abs_y": rect[1] + m["y_min"],
            })

    return badges


def test_badges_and_scroll():
    log("=" * 60)
    log("추가 탐색: 뱃지(안읽음) + 스크롤 동작")
    log("=" * 60)

    hwnd, rect = find_kakao()
    activate(hwnd)
    log(f"카카오톡: hwnd={hwnd}, rect={rect}")

    # 채팅탭 클릭
    pyautogui.click(rect[0] + 27, rect[1] + 115)
    time.sleep(0.5)

    kakao_w = rect[2] - rect[0]
    kakao_h = rect[3] - rect[1]

    # 방 리스트 영역 (탭바 아래 ~ 하단)
    list_left = rect[0] + 60
    list_top = rect[1] + 130
    list_right = rect[2]
    list_bottom = rect[3] - 20
    list_w = list_right - list_left
    list_h = list_bottom - list_top
    list_region = (list_left, list_top, list_w, list_h)

    findings = {"badges": {}, "scroll": {}}

    # ===== 1. 맨 위로 스크롤 =====
    log("\n[1] 맨 위로 스크롤")
    center_x = list_left + list_w // 2
    center_y = list_top + list_h // 2
    for _ in range(30):
        pyautogui.scroll(10, x=center_x, y=center_y)
        time.sleep(0.05)
    time.sleep(0.5)

    # ===== 2. 현재 화면 뱃지 분석 =====
    log("\n[2] 뱃지(안읽음) 분석 - 현재 보이는 방 리스트")
    fname, img_list = cap("badge_top", list_region)
    badges = analyze_badges(img_list, (list_left, list_top, list_right, list_bottom))
    log(f"  뱃지 {len(badges)}개 발견")
    for i, b in enumerate(badges):
        log(f"    #{i}: 위치=({b['x']},{b['y']}) 크기={b['width']}x{b['height']} "
            f"절대좌표=({b['abs_x']},{b['abs_y']})")
    findings["badges"]["top_view"] = {
        "count": len(badges),
        "badges": badges,
        "capture": fname,
    }

    # 전체 카카오톡 창도 캡처 (뱃지 위치 확인용)
    fname_full, img_full = cap("badge_full_top", (rect[0], rect[1], kakao_w, kakao_h))

    # ===== 3. 방 리스트 스크롤 탐색 =====
    log("\n[3] 스크롤 탐색 - 한 페이지씩 내려가며 캡처")
    scroll_pages = []
    prev_img = img_list

    for page in range(10):  # 최대 10페이지
        # 스크롤 다운
        for _ in range(5):
            pyautogui.scroll(-3, x=center_x, y=center_y)
            time.sleep(0.05)
        time.sleep(0.5)

        activate(hwnd)
        time.sleep(0.2)

        fname_p, img_p = cap(f"scroll_page_{page}", list_region)
        sim = compare_images(prev_img, img_p)

        # 뱃지 분석
        badges_p = analyze_badges(img_p, (list_left, list_top, list_right, list_bottom))

        page_info = {
            "page": page,
            "similarity_to_prev": round(sim, 3),
            "badge_count": len(badges_p),
            "badges": badges_p,
            "capture": fname_p,
        }
        scroll_pages.append(page_info)

        log(f"  페이지 {page}: 이전 대비 유사도={sim:.1%}, 뱃지={len(badges_p)}개")

        # 더 이상 스크롤 안 되면 (100% 동일) 중단
        if sim > 0.995:
            log(f"  -> 스크롤 끝 (더 이상 변화 없음)")
            break

        prev_img = img_p

    findings["scroll"]["pages"] = scroll_pages
    findings["scroll"]["total_pages"] = len(scroll_pages)

    # ===== 4. 스크롤 속도/정밀도 테스트 =====
    log("\n[4] 스크롤 정밀도 테스트")
    # 맨 위로 복귀
    for _ in range(30):
        pyautogui.scroll(10, x=center_x, y=center_y)
        time.sleep(0.05)
    time.sleep(0.5)

    scroll_precision = []
    fname_base, img_base = cap("scroll_precision_base", list_region)

    # 다양한 스크롤 양 테스트
    for scroll_amt in [-1, -2, -3, -5, -10]:
        # 먼저 맨 위로
        for _ in range(30):
            pyautogui.scroll(10, x=center_x, y=center_y)
            time.sleep(0.05)
        time.sleep(0.3)

        # 기준 캡처
        _, img_before = cap(f"scroll_prec_{abs(scroll_amt)}_before", list_region)

        # 스크롤
        pyautogui.scroll(scroll_amt, x=center_x, y=center_y)
        time.sleep(0.5)

        _, img_after = cap(f"scroll_prec_{abs(scroll_amt)}_after", list_region)
        sim = compare_images(img_before, img_after)

        info = {
            "scroll_amount": scroll_amt,
            "similarity": round(sim, 3),
            "change_pct": round((1 - sim) * 100, 1),
        }
        scroll_precision.append(info)
        log(f"  scroll({scroll_amt}): 변화={info['change_pct']}%")

    findings["scroll"]["precision"] = scroll_precision

    # ===== 5. 방 행(row) 높이 추정 =====
    log("\n[5] 방 행 높이 추정")
    # 맨 위로
    for _ in range(30):
        pyautogui.scroll(10, x=center_x, y=center_y)
        time.sleep(0.05)
    time.sleep(0.5)

    _, img_rows = cap("row_height_analysis", list_region)
    arr = np.array(img_rows.convert("L"))

    # 수평 라인 감지: 각 행의 평균 밝기 변화 → 행 경계 추정
    row_means = arr.mean(axis=1)
    # 밝기 변화가 큰 지점 = 행 구분선
    diffs = np.abs(np.diff(row_means))
    threshold = np.mean(diffs) + np.std(diffs) * 1.5
    boundaries = np.where(diffs > threshold)[0].tolist()

    # 인접한 경계 병합
    merged_boundaries = []
    for b in boundaries:
        if not merged_boundaries or b - merged_boundaries[-1] > 10:
            merged_boundaries.append(b)

    if len(merged_boundaries) >= 2:
        gaps = [merged_boundaries[i+1] - merged_boundaries[i]
                for i in range(len(merged_boundaries)-1)]
        avg_height = sum(gaps) / len(gaps)
        log(f"  경계선 {len(merged_boundaries)}개 감지")
        log(f"  행 간격: {gaps}")
        log(f"  평균 행 높이: {avg_height:.0f}px")
        findings["scroll"]["row_height"] = {
            "boundaries": merged_boundaries[:20],
            "gaps": gaps[:20],
            "avg_height": round(avg_height, 1),
        }
    else:
        log(f"  경계선 부족 ({len(merged_boundaries)}개)")
        findings["scroll"]["row_height"] = {"error": "insufficient boundaries"}

    # ===== 6. 각 행의 뱃지 유무 + 위치 매핑 =====
    log("\n[6] 행별 뱃지 위치 매핑")
    # 맨 위로
    for _ in range(30):
        pyautogui.scroll(10, x=center_x, y=center_y)
        time.sleep(0.05)
    time.sleep(0.5)

    _, img_map = cap("badge_row_mapping", list_region)
    all_badges = analyze_badges(img_map, (list_left, list_top, list_right, list_bottom))

    if merged_boundaries and all_badges:
        row_badges = []
        for b in all_badges:
            # 어느 행에 속하는지
            row_idx = 0
            for i, boundary in enumerate(merged_boundaries):
                if b["y"] > boundary:
                    row_idx = i + 1
            row_badges.append({
                "badge": b,
                "row_index": row_idx,
                "row_y_start": merged_boundaries[row_idx-1] if row_idx > 0 else 0,
            })
            log(f"  뱃지 ({b['x']},{b['y']}): 행 #{row_idx}")
        findings["badges"]["row_mapping"] = row_badges

    # ===== 7. 안 읽음 vs 읽음 시각적 차이 =====
    log("\n[7] 안읽음 vs 읽음 - 행 시각적 차이 비교")
    if len(merged_boundaries) >= 3 and all_badges:
        # 뱃지가 있는 행 vs 없는 행 비교
        badge_rows = set()
        for b in all_badges:
            for i, boundary in enumerate(merged_boundaries):
                if b["y"] > boundary:
                    badge_rows.add(i + 1)

        badge_row = list(badge_rows)[0] if badge_rows else None
        no_badge_row = None
        for i in range(len(merged_boundaries)):
            if i not in badge_rows:
                no_badge_row = i
                break

        if badge_row is not None and no_badge_row is not None:
            # 각 행 잘라서 비교
            def get_row_slice(img, boundaries, idx):
                y_start = boundaries[idx-1] if idx > 0 else 0
                y_end = boundaries[idx] if idx < len(boundaries) else img.height
                return img.crop((0, y_start, img.width, y_end))

            row_badge = get_row_slice(img_map, merged_boundaries, badge_row)
            row_clean = get_row_slice(img_map, merged_boundaries, no_badge_row)

            row_badge.save(CAPTURES_DIR / "row_with_badge.png")
            row_clean.save(CAPTURES_DIR / "row_without_badge.png")

            # 빨간 픽셀 비율 비교
            arr_b = np.array(row_badge)
            arr_c = np.array(row_clean)
            red_b = ((arr_b[:,:,0] > 180) & (arr_b[:,:,1] < 100) & (arr_b[:,:,2] < 100)).sum()
            red_c = ((arr_c[:,:,0] > 180) & (arr_c[:,:,1] < 100) & (arr_c[:,:,2] < 100)).sum()

            log(f"  뱃지 있는 행 #{badge_row}: 빨간 픽셀 {red_b}개")
            log(f"  뱃지 없는 행 #{no_badge_row}: 빨간 픽셀 {red_c}개")
            findings["badges"]["visual_diff"] = {
                "badge_row": badge_row, "badge_red_pixels": int(red_b),
                "clean_row": no_badge_row, "clean_red_pixels": int(red_c),
            }

    # ===== 결과 저장 =====
    findings_path = EXPLORER_DIR / "findings_scroll_badge.json"
    with open(findings_path, "w", encoding="utf-8") as f:
        json.dump(findings, f, ensure_ascii=False, indent=2, default=str)
    log(f"\n저장: {findings_path}")

    # 요약
    log("\n" + "=" * 60)
    log("요약")
    log("=" * 60)
    total_badges = sum(p.get("badge_count", 0) for p in scroll_pages)
    total_badges += findings["badges"]["top_view"]["count"]
    log(f"  총 뱃지(안읽음): {total_badges}개 (모든 페이지)")
    log(f"  스크롤 페이지 수: {len(scroll_pages)}")
    if scroll_precision:
        log(f"  스크롤 정밀도:")
        for sp in scroll_precision:
            log(f"    scroll({sp['scroll_amount']}): {sp['change_pct']}% 변화")


if __name__ == "__main__":
    test_badges_and_scroll()
