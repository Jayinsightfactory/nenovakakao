# -*- coding: utf-8 -*-
"""
카카오톡 동작 원리 탐색기 (KakaoExplorer)

모든 자동화 전에 먼저 실행하여 카카오톡의 실제 동작을 학습한다.
각 테스트마다 여러 방식을 시도하고, 캡처+결과를 기록한다.

사용법:
  python kakao_explorer.py                  # 전체 탐색
  python kakao_explorer.py windows          # 창 탐지만
  python kakao_explorer.py activate         # 활성화 방법만
  python kakao_explorer.py search           # 검색 동작만
  python kakao_explorer.py room             # 방 열기/닫기만
  python kakao_explorer.py save             # Ctrl+S 저장만
  python kakao_explorer.py ordering         # 방 순서 변화만
  python kakao_explorer.py recovery         # 실패 복구 방법만

결과:
  data/explorer/findings.json    -학습된 원리 정리
  data/explorer/captures/        -단계별 캡처 이미지
  data/explorer/report.txt       -사람이 읽을 수 있는 보고서
"""
from __future__ import annotations

import ctypes
import json
import os
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional

import pyautogui
import pygetwindow as gw
import pyperclip
import win32gui
import win32con
import win32process
from PIL import Image

sys.path.insert(0, "C:/Users/USER/nenova_agent")
from core.vision_guard import compare_images, safe_screenshot, pixel_hash

pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0.05

# ---------------------------------------------------------------------------
# 출력 디렉토리
# ---------------------------------------------------------------------------
EXPLORER_DIR = Path("C:/Users/USER/nenova_agent/data/explorer")
CAPTURES_DIR = EXPLORER_DIR / "captures"
FINDINGS_FILE = EXPLORER_DIR / "findings.json"
REPORT_FILE = EXPLORER_DIR / "report.txt"


# ---------------------------------------------------------------------------
# 기록 유틸
# ---------------------------------------------------------------------------

@dataclass
class TestResult:
    test_name: str
    method: str
    success: bool
    duration_ms: int = 0
    details: dict = field(default_factory=dict)
    captures: list[str] = field(default_factory=list)
    error: Optional[str] = None


class Explorer:
    """카카오톡 동작 탐색기."""

    def __init__(self):
        CAPTURES_DIR.mkdir(parents=True, exist_ok=True)
        self.results: list[TestResult] = []
        self.findings: dict = {}
        self._test_id = 0
        self._log_lines: list[str] = []
        self._kakao_hwnd: Optional[int] = None  # 한 번 찾으면 기억
        # 기존 findings 로드
        if FINDINGS_FILE.exists():
            with open(FINDINGS_FILE, "r", encoding="utf-8") as f:
                self.findings = json.load(f)

    def log(self, msg: str):
        line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
        print(line, flush=True)
        self._log_lines.append(line)

    def capture(self, label: str, region=None) -> str:
        """스크린샷 저장. 파일명 반환."""
        self._test_id += 1
        fname = f"{self._test_id:03d}_{label}.png"
        path = CAPTURES_DIR / fname
        time.sleep(0.2)
        img = pyautogui.screenshot(region=region) if region else pyautogui.screenshot()
        img.save(path)
        return fname

    def add_result(self, r: TestResult):
        self.results.append(r)
        icon = "OK" if r.success else "FAIL"
        self.log(f"  [{icon}] [{r.method}] {r.details.get('summary', '')}")

    def _find_kakao(self) -> tuple[int, tuple]:
        """카카오톡 hwnd와 rect를 반환. 캐시+숨김 창 대응."""
        # 캐시된 hwnd가 유효하면 사용
        if self._kakao_hwnd and win32gui.IsWindow(self._kakao_hwnd):
            # 숨겨져 있으면 복원
            if not win32gui.IsWindowVisible(self._kakao_hwnd):
                self._restore_kakao(self._kakao_hwnd)
            rect = win32gui.GetWindowRect(self._kakao_hwnd)
            return self._kakao_hwnd, rect

        # win32gui로 직접 탐색 (숨김 창 포함)
        kakao_hwnds = []
        def enum_cb(hwnd, _):
            title = win32gui.GetWindowText(hwnd)
            cls = win32gui.GetClassName(hwnd)
            if "카카오톡" in title and cls == "EVA_Window_Dblclk":
                rect = win32gui.GetWindowRect(hwnd)
                w = rect[2] - rect[0]
                h = rect[3] - rect[1]
                kakao_hwnds.append((hwnd, w * h, rect))
        win32gui.EnumWindows(enum_cb, None)

        if not kakao_hwnds:
            # pygetwindow 폴백
            wins = gw.getWindowsWithTitle("카카오톡")
            if wins:
                main = max(wins, key=lambda w: w.width * w.height)
                self._kakao_hwnd = main._hWnd
                rect = win32gui.GetWindowRect(self._kakao_hwnd)
                return self._kakao_hwnd, rect
            raise RuntimeError("카카오톡 없음")

        # 가장 큰 창 선택
        kakao_hwnds.sort(key=lambda x: x[1], reverse=True)
        self._kakao_hwnd = kakao_hwnds[0][0]
        if not win32gui.IsWindowVisible(self._kakao_hwnd):
            self._restore_kakao(self._kakao_hwnd)
        rect = win32gui.GetWindowRect(self._kakao_hwnd)
        return self._kakao_hwnd, rect

    def _restore_kakao(self, hwnd: int):
        """숨겨진 카카오톡 창을 복원."""
        self.log(f"  [RESTORE] 카카오톡 숨김 -> 복원 (hwnd={hwnd})")
        win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        time.sleep(0.3)
        ctypes.windll.user32.keybd_event(0x12, 0, 0, 0)
        ctypes.windll.user32.keybd_event(0x12, 0, 2, 0)
        ctypes.windll.user32.SetForegroundWindow(hwnd)
        time.sleep(0.5)

    def save_all(self):
        """findings + report 저장."""
        # findings.json
        with open(FINDINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(self.findings, f, ensure_ascii=False, indent=2)
        # report.txt
        with open(REPORT_FILE, "w", encoding="utf-8") as f:
            f.write("카카오톡 동작 탐색 보고서\n")
            f.write(f"일시: {datetime.now().isoformat()}\n")
            f.write("=" * 60 + "\n\n")
            for line in self._log_lines:
                f.write(line + "\n")
            f.write("\n\n결과 요약:\n")
            for r in self.results:
                icon = "✓" if r.success else "✗"
                f.write(f"  {icon} {r.test_name} / {r.method}: "
                        f"{r.details.get('summary', '')} ({r.duration_ms}ms)\n")
                if r.error:
                    f.write(f"    에러: {r.error}\n")
        self.log(f"\n저장 완료: {FINDINGS_FILE}")
        self.log(f"           {REPORT_FILE}")

    # ===================================================================
    # TEST 1: 창 탐지 -카카오톡 윈도우를 찾는 모든 방법
    # ===================================================================

    def test_windows(self):
        self.log("\n" + "=" * 60)
        self.log("TEST: 창 탐지 - 카카오톡 윈도우를 찾는 모든 방법")
        self.log("=" * 60)

        window_info = {}

        # --- 방법 1: pygetwindow 타이틀 검색 ---
        t0 = time.time()
        try:
            wins = gw.getWindowsWithTitle("카카오톡")
            dur = int((time.time() - t0) * 1000)
            details = []
            for w in wins:
                info = {
                    "title": w.title, "hwnd": w._hWnd,
                    "pos": (w.left, w.top), "size": (w.width, w.height),
                    "visible": w.visible, "minimized": w.isMinimized,
                }
                details.append(info)
            self.add_result(TestResult(
                "창탐지", "pygetwindow_title",
                success=len(wins) > 0, duration_ms=dur,
                details={"summary": f"{len(wins)}개 발견", "windows": details}
            ))
            window_info["pygetwindow"] = details
        except Exception as e:
            self.add_result(TestResult("창탐지", "pygetwindow_title",
                                       success=False, error=str(e)))

        # --- 방법 2: win32gui.FindWindow 클래스명 ---
        t0 = time.time()
        try:
            # 카카오톡 클래스명 탐색
            class_names = ["EVA_Window_Dblclk", "EVA_Window", "#32770"]
            found = []
            for cls in class_names:
                hwnd = win32gui.FindWindow(cls, None)
                if hwnd:
                    title = win32gui.GetWindowText(hwnd)
                    rect = win32gui.GetWindowRect(hwnd)
                    found.append({"class": cls, "hwnd": hwnd, "title": title, "rect": rect})
            dur = int((time.time() - t0) * 1000)
            self.add_result(TestResult(
                "창탐지", "FindWindow_class",
                success=len(found) > 0, duration_ms=dur,
                details={"summary": f"{len(found)}개 클래스 매칭", "found": found}
            ))
            window_info["findwindow_class"] = found
        except Exception as e:
            self.add_result(TestResult("창탐지", "FindWindow_class",
                                       success=False, error=str(e)))

        # --- 방법 3: EnumWindows 전수 조사 ---
        t0 = time.time()
        try:
            all_kakao = []
            def enum_cb(hwnd, _):
                if win32gui.IsWindowVisible(hwnd):
                    title = win32gui.GetWindowText(hwnd)
                    if "카카오톡" in title or "KakaoTalk" in title:
                        cls = win32gui.GetClassName(hwnd)
                        rect = win32gui.GetWindowRect(hwnd)
                        _, pid = win32process.GetWindowThreadProcessId(hwnd)
                        all_kakao.append({
                            "hwnd": hwnd, "title": title, "class": cls,
                            "rect": rect, "pid": pid,
                        })
            win32gui.EnumWindows(enum_cb, None)
            dur = int((time.time() - t0) * 1000)
            self.add_result(TestResult(
                "창탐지", "EnumWindows_전수",
                success=len(all_kakao) > 0, duration_ms=dur,
                details={"summary": f"{len(all_kakao)}개 카카오톡 창", "windows": all_kakao}
            ))
            window_info["enum_windows"] = all_kakao
        except Exception as e:
            self.add_result(TestResult("창탐지", "EnumWindows_전수",
                                       success=False, error=str(e)))

        # --- 방법 4: 모든 보이는 창 조사 (전체 데스크탑 상태) ---
        t0 = time.time()
        try:
            all_visible = []
            def enum_all(hwnd, _):
                if win32gui.IsWindowVisible(hwnd):
                    title = win32gui.GetWindowText(hwnd)
                    if title and len(title) > 1:
                        cls = win32gui.GetClassName(hwnd)
                        rect = win32gui.GetWindowRect(hwnd)
                        all_visible.append({
                            "hwnd": hwnd, "title": title[:50], "class": cls,
                            "rect": list(rect),
                        })
            win32gui.EnumWindows(enum_all, None)
            dur = int((time.time() - t0) * 1000)
            self.add_result(TestResult(
                "창탐지", "전체_데스크탑_스캔",
                success=True, duration_ms=dur,
                details={"summary": f"총 {len(all_visible)}개 보이는 창", "count": len(all_visible)}
            ))
            window_info["all_visible_count"] = len(all_visible)
            window_info["all_visible"] = all_visible
        except Exception as e:
            self.add_result(TestResult("창탐지", "전체_데스크탑_스캔",
                                       success=False, error=str(e)))

        # 전체 화면 캡처
        cap = self.capture("windows_desktop_full")

        self.findings["windows"] = window_info
        self.log(f"  캡처: {cap}")

    # ===================================================================
    # TEST 2: 활성화 -포그라운드로 가져오는 모든 방법
    # ===================================================================

    def test_activate(self):
        self.log("\n" + "=" * 60)
        self.log("TEST: 활성화 -포그라운드로 가져오는 모든 방법")
        self.log("=" * 60)

        # 먼저 카카오톡 hwnd 찾기
        try:
            hwnd, _ = self._find_kakao()
        except RuntimeError:
            self.log("  [SKIP] 카카오톡 없음")
            return
        self.log(f"  대상: hwnd={hwnd}")

        activate_results = {}

        methods = [
            ("pygetwindow_activate", self._activate_pygetwindow),
            ("SetForegroundWindow_직접", self._activate_setforeground),
            ("Alt트릭+SetForeground", self._activate_alt_trick),
            ("ShowWindow+BringToTop", self._activate_bring_to_top),
            ("minimize_restore", self._activate_min_restore),
            ("AttachThreadInput", self._activate_attach_thread),
        ]

        for name, method in methods:
            # 먼저 다른 곳으로 포커스 이동 (탐색기 등)
            try:
                desktop = win32gui.GetDesktopWindow()
                pyautogui.click(960, 540)  # 화면 중앙 클릭
                time.sleep(0.5)
            except:
                pass

            t0 = time.time()
            try:
                success = method(hwnd)
                dur = int((time.time() - t0) * 1000)
                fg = win32gui.GetForegroundWindow()
                is_fg = fg == hwnd
                cap = self.capture(f"activate_{name}")
                self.add_result(TestResult(
                    "활성화", name,
                    success=is_fg, duration_ms=dur,
                    captures=[cap],
                    details={
                        "summary": f"포그라운드={'예' if is_fg else '아니오'} "
                                   f"(fg={fg}, 기대={hwnd})",
                        "fg_hwnd": fg, "fg_title": win32gui.GetWindowText(fg),
                    }
                ))
                activate_results[name] = {"success": is_fg, "duration_ms": dur}
            except Exception as e:
                self.add_result(TestResult("활성화", name,
                                           success=False, error=str(e)))
                activate_results[name] = {"success": False, "error": str(e)}

            time.sleep(0.5)

        self.findings["activation"] = activate_results

    def _activate_pygetwindow(self, hwnd):
        wins = gw.getWindowsWithTitle("카카오톡")
        main = max(wins, key=lambda w: w.width * w.height)
        main.activate()
        time.sleep(0.3)
        return True

    def _activate_setforeground(self, hwnd):
        win32gui.SetForegroundWindow(hwnd)
        time.sleep(0.3)
        return True

    def _activate_alt_trick(self, hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        time.sleep(0.1)
        ctypes.windll.user32.keybd_event(0x12, 0, 0, 0)  # Alt down
        ctypes.windll.user32.keybd_event(0x12, 0, 2, 0)  # Alt up
        ctypes.windll.user32.SetForegroundWindow(hwnd)
        time.sleep(0.3)
        return True

    def _activate_bring_to_top(self, hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.BringWindowToTop(hwnd)
        time.sleep(0.3)
        return True

    def _activate_min_restore(self, hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
        time.sleep(0.3)
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        time.sleep(0.5)
        return True

    def _activate_attach_thread(self, hwnd):
        """AttachThreadInput으로 스레드 연결 후 활성화."""
        fg_hwnd = win32gui.GetForegroundWindow()
        fg_tid, _ = win32process.GetWindowThreadProcessId(fg_hwnd)
        target_tid, _ = win32process.GetWindowThreadProcessId(hwnd)
        if fg_tid != target_tid:
            ctypes.windll.user32.AttachThreadInput(fg_tid, target_tid, True)
        win32gui.SetForegroundWindow(hwnd)
        win32gui.BringWindowToTop(hwnd)
        if fg_tid != target_tid:
            ctypes.windll.user32.AttachThreadInput(fg_tid, target_tid, False)
        time.sleep(0.3)
        return True

    # ===================================================================
    # TEST 3: 검색 -Ctrl+F 동작 원리 파악
    # ===================================================================

    def test_search(self):
        self.log("\n" + "=" * 60)
        self.log("TEST: 검색 -Ctrl+F 동작 원리 파악")
        self.log("=" * 60)

        try:
            hwnd, rect = self._find_kakao()
        except RuntimeError:
            self.log("  [SKIP] 카카오톡 없음")
            return
        kakao_region = (rect[0], rect[1], rect[2] - rect[0], rect[3] - rect[1])

        # 활성화
        self._activate_alt_trick(hwnd)
        time.sleep(0.5)

        search_info = {}

        # --- 상태 0: 현재 화면 (기준선) ---
        cap_before = self.capture("search_0_before", kakao_region)
        img_before = pyautogui.screenshot(region=kakao_region)
        self.log(f"  기준 캡처: {cap_before}")

        # --- 채팅탭 클릭 ---
        pyautogui.click(rect[0] + 27, rect[1] + 115)
        time.sleep(0.5)
        cap_chat = self.capture("search_1_chat_tab", kakao_region)
        img_chat = pyautogui.screenshot(region=kakao_region)
        sim = compare_images(img_before, img_chat)
        self.log(f"  채팅탭 클릭 후 유사도: {sim:.1%}")
        search_info["chat_tab_click_similarity"] = round(sim, 3)

        # --- 방법 1: Ctrl+F ---
        self.log("\n  [방법 1] Ctrl+F")
        self._activate_alt_trick(hwnd)
        time.sleep(0.3)
        img_pre_f = pyautogui.screenshot(region=kakao_region)
        pyautogui.hotkey("ctrl", "f")
        time.sleep(1.0)
        cap_ctrlf = self.capture("search_2_ctrlf", kakao_region)
        img_post_f = pyautogui.screenshot(region=kakao_region)
        sim_f = compare_images(img_pre_f, img_post_f)
        fg_after = win32gui.GetForegroundWindow()
        fg_title = win32gui.GetWindowText(fg_after)
        self.log(f"  Ctrl+F 후 유사도: {sim_f:.1%}, 포그라운드: {fg_title} (hwnd={fg_after})")
        search_info["ctrlf"] = {
            "similarity_change": round(1 - sim_f, 3),
            "fg_hwnd": fg_after, "fg_title": fg_title,
            "fg_is_kakao": fg_after == hwnd,
        }

        # 검색창 상태에서 추가 정보 수집
        # 현재 보이는 모든 창 기록
        visible_after_f = []
        def enum_visible(h, _):
            if win32gui.IsWindowVisible(h):
                t = win32gui.GetWindowText(h)
                if t:
                    visible_after_f.append({"hwnd": h, "title": t[:40],
                                            "class": win32gui.GetClassName(h)})
        win32gui.EnumWindows(enum_visible, None)
        search_info["ctrlf_visible_windows"] = visible_after_f

        # ESC로 닫기 테스트
        pyautogui.press("escape")
        time.sleep(0.5)
        cap_esc = self.capture("search_3_after_esc", kakao_region)
        img_esc = pyautogui.screenshot(region=kakao_region)
        sim_esc = compare_images(img_chat, img_esc)
        self.log(f"  ESC 후 원래 화면 유사도: {sim_esc:.1%}")
        search_info["esc_recovery_similarity"] = round(sim_esc, 3)

        # --- 방법 2: 검색 아이콘 직접 클릭 ---
        self.log("\n  [방법 2] 검색 아이콘 클릭")
        self._activate_alt_trick(hwnd)
        time.sleep(0.3)
        # 카카오톡 검색 아이콘 위치: 상단 돋보기
        # 다양한 위치 시도
        search_icon_candidates = [
            (rect[0] + 100, rect[1] + 55, "상단_돋보기_100x55"),
            (rect[0] + 130, rect[1] + 55, "상단_돋보기_130x55"),
            (rect[0] + 250, rect[1] + 55, "상단_검색_250x55"),
            (rect[0] + 200, rect[1] + 80, "상단_검색_200x80"),
        ]
        for sx, sy, label in search_icon_candidates:
            self._activate_alt_trick(hwnd)
            time.sleep(0.3)
            img_pre = pyautogui.screenshot(region=kakao_region)
            pyautogui.click(sx, sy)
            time.sleep(0.8)
            img_post = pyautogui.screenshot(region=kakao_region)
            sim = compare_images(img_pre, img_post)
            changed = sim < 0.95
            cap = self.capture(f"search_icon_{label}", kakao_region)
            self.log(f"    {label}: 변화={'예' if changed else '아니오'} ({1-sim:.1%})")
            search_info[f"icon_{label}"] = {
                "pos": (sx - rect[0], sy - rect[1]),
                "changed": changed, "similarity": round(sim, 3)
            }
            # 원상 복구
            pyautogui.press("escape")
            time.sleep(0.3)

        # --- 검색어 입력 테스트 ---
        self.log("\n  [검색어 입력 테스트]")
        self._activate_alt_trick(hwnd)
        time.sleep(0.3)
        pyautogui.hotkey("ctrl", "f")
        time.sleep(1.0)

        # 입력 방법 1: 직접 타이핑
        test_word = "테스트"
        pyautogui.hotkey("ctrl", "a")
        time.sleep(0.1)
        pyautogui.press("delete")
        time.sleep(0.2)
        pyperclip.copy(test_word)
        pyautogui.hotkey("ctrl", "v")
        time.sleep(0.5)
        cap_typed = self.capture("search_4_typed", kakao_region)

        # 클립보드 확인 (입력 성공 여부)
        current_clip = pyperclip.paste()
        self.log(f"  입력 후 클립보드: '{current_clip}'")

        # 검색 상태에서 Enter 동작 관찰
        img_pre_enter = pyautogui.screenshot(region=kakao_region)
        pyautogui.press("enter")
        time.sleep(1.0)
        img_post_enter = pyautogui.screenshot(region=kakao_region)
        cap_enter = self.capture("search_5_after_enter", kakao_region)
        sim_enter = compare_images(img_pre_enter, img_post_enter)
        fg_after_enter = win32gui.GetForegroundWindow()
        self.log(f"  Enter 후 변화: {1-sim_enter:.1%}, 포그라운드: "
                 f"{win32gui.GetWindowText(fg_after_enter)} (hwnd={fg_after_enter})")
        search_info["enter_after_search"] = {
            "similarity_change": round(1 - sim_enter, 3),
            "fg_hwnd": fg_after_enter,
            "fg_changed": fg_after_enter != hwnd,
        }

        # 새 창이 열렸는지 확인
        visible_after_enter = []
        def enum_after(h, _):
            if win32gui.IsWindowVisible(h):
                t = win32gui.GetWindowText(h)
                if t:
                    visible_after_enter.append({"hwnd": h, "title": t[:40]})
        win32gui.EnumWindows(enum_after, None)
        search_info["windows_after_enter"] = visible_after_enter

        # ESC로 모두 닫기
        for _ in range(5):
            pyautogui.press("escape")
            time.sleep(0.3)

        self.findings["search"] = search_info

    # ===================================================================
    # TEST 4: 방 열기/닫기 -창 동작 원리
    # ===================================================================

    def test_room(self):
        self.log("\n" + "=" * 60)
        self.log("TEST: 방 열기/닫기 -창 동작 원리")
        self.log("=" * 60)

        try:
            hwnd, rect = self._find_kakao()
        except RuntimeError:
            self.log("  [SKIP] 카카오톡 없음")
            return
        kakao_region = (rect[0], rect[1], rect[2] - rect[0], rect[3] - rect[1])

        room_info = {}

        # 활성화 + 채팅탭
        self._activate_alt_trick(hwnd)
        time.sleep(0.5)
        pyautogui.click(rect[0] + 27, rect[1] + 115)
        time.sleep(0.5)

        # --- 방 목록 첫번째 방 영역 ---
        # 카카오톡 방 리스트는 약 (60, 130) ~ (500, 900) 영역
        room_list_top = rect[1] + 130
        first_room_y = room_list_top + 30  # 첫번째 방 중앙
        room_x = rect[0] + 250  # 방 리스트 중앙

        # 현재 전체 윈도우 목록 기록
        hwnds_before = set()
        def enum_before(h, _):
            if win32gui.IsWindowVisible(h):
                hwnds_before.add(h)
        win32gui.EnumWindows(enum_before, None)

        # --- 방법 1: 단일 클릭 ---
        self.log("\n  [방법 1] 단일 클릭")
        img_pre = pyautogui.screenshot(region=kakao_region)
        pyautogui.click(room_x, first_room_y)
        time.sleep(1.5)
        img_post = pyautogui.screenshot(region=kakao_region)
        cap = self.capture("room_1_single_click", kakao_region)
        sim = compare_images(img_pre, img_post)
        fg_after = win32gui.GetForegroundWindow()

        hwnds_after = set()
        def enum_after_1(h, _):
            if win32gui.IsWindowVisible(h):
                hwnds_after.add(h)
        win32gui.EnumWindows(enum_after_1, None)
        new_hwnds = hwnds_after - hwnds_before

        new_windows = []
        for h in new_hwnds:
            t = win32gui.GetWindowText(h)
            c = win32gui.GetClassName(h)
            r = win32gui.GetWindowRect(h)
            new_windows.append({"hwnd": h, "title": t, "class": c, "rect": list(r)})

        self.log(f"  단일클릭 후: 변화={1-sim:.1%}, 새 창={len(new_windows)}개")
        for nw in new_windows:
            self.log(f"    새 창: {nw['title']} ({nw['class']}) @ {nw['rect']}")

        room_info["single_click"] = {
            "similarity_change": round(1 - sim, 3),
            "new_windows": new_windows,
            "fg_hwnd": fg_after,
            "fg_title": win32gui.GetWindowText(fg_after),
        }

        # ESC 복구
        for _ in range(3):
            pyautogui.press("escape")
            time.sleep(0.3)
        self._activate_alt_trick(hwnd)
        time.sleep(0.5)
        pyautogui.click(rect[0] + 27, rect[1] + 115)
        time.sleep(0.5)

        # 윈도우 목록 새로고침
        hwnds_before2 = set()
        def enum_before2(h, _):
            if win32gui.IsWindowVisible(h):
                hwnds_before2.add(h)
        win32gui.EnumWindows(enum_before2, None)

        # --- 방법 2: 더블 클릭 ---
        self.log("\n  [방법 2] 더블 클릭")
        img_pre = pyautogui.screenshot(region=kakao_region)
        pyautogui.doubleClick(room_x, first_room_y)
        time.sleep(1.5)
        img_post = pyautogui.screenshot(region=kakao_region)
        cap = self.capture("room_2_double_click", kakao_region)
        sim = compare_images(img_pre, img_post)
        fg_after = win32gui.GetForegroundWindow()

        hwnds_after2 = set()
        def enum_after_2(h, _):
            if win32gui.IsWindowVisible(h):
                hwnds_after2.add(h)
        win32gui.EnumWindows(enum_after_2, None)
        new_hwnds2 = hwnds_after2 - hwnds_before2

        new_windows2 = []
        for h in new_hwnds2:
            t = win32gui.GetWindowText(h)
            c = win32gui.GetClassName(h)
            r = win32gui.GetWindowRect(h)
            new_windows2.append({"hwnd": h, "title": t, "class": c, "rect": list(r)})

        self.log(f"  더블클릭 후: 변화={1-sim:.1%}, 새 창={len(new_windows2)}개")
        for nw in new_windows2:
            self.log(f"    새 창: {nw['title']} ({nw['class']}) @ {nw['rect']}")

        room_info["double_click"] = {
            "similarity_change": round(1 - sim, 3),
            "new_windows": new_windows2,
            "fg_hwnd": fg_after,
            "fg_title": win32gui.GetWindowText(fg_after),
        }

        # --- 방이 열린 상태에서 추가 조사 ---
        if new_windows2:
            room_hwnd = new_windows2[0]["hwnd"]
            self.log(f"\n  [열린 방 조사] hwnd={room_hwnd}")

            # 방 창 크기/위치
            r = win32gui.GetWindowRect(room_hwnd)
            self.log(f"    위치: {r}")
            self.capture("room_3_opened_full")

            # 방 창에서 Ctrl+S 테스트
            room_info["opened_room"] = {
                "hwnd": room_hwnd,
                "rect": list(r),
                "size": (r[2] - r[0], r[3] - r[1]),
                "class": win32gui.GetClassName(room_hwnd),
            }

        # ESC로 모두 닫기
        for _ in range(5):
            pyautogui.press("escape")
            time.sleep(0.3)

        # --- 방법 3: Enter로 열기 (검색 후) ---
        self.log("\n  [방법 3] Ctrl+F 검색 후 Enter")
        self._activate_alt_trick(hwnd)
        time.sleep(0.3)
        pyautogui.click(rect[0] + 27, rect[1] + 115)
        time.sleep(0.3)
        # 방 목록 맨 위로
        for _ in range(10):
            pyautogui.scroll(10, x=room_x, y=first_room_y + 100)
            time.sleep(0.1)
        time.sleep(0.5)
        # Enter로 첫번째 방 열기
        pyautogui.press("enter")
        time.sleep(1.5)
        cap = self.capture("room_4_enter_from_list", kakao_region)
        fg_after = win32gui.GetForegroundWindow()
        self.log(f"  Enter 후 포그라운드: {win32gui.GetWindowText(fg_after)}")
        room_info["enter_from_list"] = {
            "fg_hwnd": fg_after,
            "fg_title": win32gui.GetWindowText(fg_after),
        }

        # 정리
        for _ in range(5):
            pyautogui.press("escape")
            time.sleep(0.3)

        self.findings["room"] = room_info

    # ===================================================================
    # TEST 5: Ctrl+S 저장 -다이얼로그 동작
    # ===================================================================

    def test_save(self):
        self.log("\n" + "=" * 60)
        self.log("TEST: Ctrl+S 저장 -다이얼로그 동작")
        self.log("=" * 60)

        try:
            hwnd, rect = self._find_kakao()
        except RuntimeError:
            self.log("  [SKIP] 카카오톡 없음")
            return

        save_info = {}

        # 먼저 방 하나를 열어야 함
        self._activate_alt_trick(hwnd)
        time.sleep(0.3)
        pyautogui.click(rect[0] + 27, rect[1] + 115)
        time.sleep(0.3)

        # 첫번째 방 더블클릭
        room_x = rect[0] + 250
        room_y = rect[1] + 160
        pyautogui.doubleClick(room_x, room_y)
        time.sleep(1.5)

        fg = win32gui.GetForegroundWindow()
        if fg == hwnd:
            self.log("  [!] 방이 안 열림 -메인 창 그대로")
            self.findings["save"] = {"error": "방 열기 실패"}
            return

        room_hwnd = fg
        room_title = win32gui.GetWindowText(fg)
        self.log(f"  열린 방: {room_title} (hwnd={room_hwnd})")

        # --- Ctrl+S 전 상태 ---
        hwnds_before = set()
        def enum_b(h, _):
            if win32gui.IsWindowVisible(h):
                hwnds_before.add(h)
        win32gui.EnumWindows(enum_b, None)

        save_dir = Path("C:/Users/USER/Downloads/카톡대화데이터")
        files_before = set(str(p) for p in save_dir.rglob("*.txt")) if save_dir.exists() else set()

        cap_pre = self.capture("save_0_before_ctrls")

        # --- Ctrl+S 실행 ---
        self.log("\n  [Ctrl+S 실행]")
        pyautogui.hotkey("ctrl", "s")
        time.sleep(2.0)

        cap_dialog = self.capture("save_1_after_ctrls")
        fg_after = win32gui.GetForegroundWindow()
        fg_title = win32gui.GetWindowText(fg_after)
        fg_class = win32gui.GetClassName(fg_after)
        self.log(f"  Ctrl+S 후 포그라운드: {fg_title} ({fg_class}, hwnd={fg_after})")

        # 새 창 (다이얼로그) 확인
        hwnds_after = set()
        def enum_a(h, _):
            if win32gui.IsWindowVisible(h):
                hwnds_after.add(h)
        win32gui.EnumWindows(enum_a, None)
        new_hwnds = hwnds_after - hwnds_before
        dialogs = []
        for h in new_hwnds:
            t = win32gui.GetWindowText(h)
            c = win32gui.GetClassName(h)
            r = win32gui.GetWindowRect(h)
            dialogs.append({"hwnd": h, "title": t, "class": c, "rect": list(r)})
            self.log(f"    다이얼로그: {t} ({c}) @ {r}")

        save_info["dialog"] = {
            "fg_hwnd": fg_after, "fg_title": fg_title, "fg_class": fg_class,
            "new_dialogs": dialogs,
        }

        # --- Enter (저장 확인) ---
        self.log("\n  [Enter 1 - 저장]")
        pyautogui.press("enter")
        time.sleep(2.0)
        cap_after1 = self.capture("save_2_after_enter1")
        fg2 = win32gui.GetForegroundWindow()
        fg2_title = win32gui.GetWindowText(fg2)
        self.log(f"  Enter1 후 포그라운드: {fg2_title} (hwnd={fg2})")
        save_info["enter1"] = {
            "fg_hwnd": fg2, "fg_title": fg2_title,
        }

        # --- Enter (완료 팝업 닫기) ---
        self.log("\n  [Enter 2 - 완료]")
        pyautogui.press("enter")
        time.sleep(1.0)
        cap_after2 = self.capture("save_3_after_enter2")
        fg3 = win32gui.GetForegroundWindow()
        fg3_title = win32gui.GetWindowText(fg3)
        self.log(f"  Enter2 후 포그라운드: {fg3_title} (hwnd={fg3})")
        save_info["enter2"] = {
            "fg_hwnd": fg3, "fg_title": fg3_title,
        }

        # --- 파일 생성 확인 ---
        files_after = set(str(p) for p in save_dir.rglob("*.txt")) if save_dir.exists() else set()
        new_files = files_after - files_before
        if new_files:
            for nf in new_files:
                p = Path(nf)
                self.log(f"  새 파일: {p.name} ({p.stat().st_size:,}B)")
            save_info["new_files"] = [str(f) for f in new_files]
        else:
            self.log(f"  [!] 새 파일 없음")
            save_info["new_files"] = []

        # 정리
        for _ in range(5):
            pyautogui.press("escape")
            time.sleep(0.3)

        self.findings["save"] = save_info

    # ===================================================================
    # TEST 6: 방 순서 변화 -새 메시지 시 리스트 변동
    # ===================================================================

    def test_ordering(self):
        self.log("\n" + "=" * 60)
        self.log("TEST: 방 순서 변화 -리스트 변동 관찰")
        self.log("=" * 60)

        try:
            hwnd, rect = self._find_kakao()
        except RuntimeError:
            self.log("  [SKIP] 카카오톡 없음")
            return

        # 방 리스트 영역
        list_region = (rect[0] + 60, rect[1] + 130,
                       rect[2] - rect[0] - 60, rect[3] - rect[1] - 150)

        # 활성화 + 채팅탭
        self._activate_alt_trick(hwnd)
        time.sleep(0.3)
        pyautogui.click(rect[0] + 27, rect[1] + 115)
        time.sleep(0.5)

        # 맨 위로 스크롤
        for _ in range(20):
            pyautogui.scroll(10, x=rect[0] + 250, y=rect[1] + 400)
            time.sleep(0.05)
        time.sleep(0.5)

        # 5초 간격으로 3번 캡처하여 변동 관찰
        ordering_info = {"snapshots": []}
        prev_img = None

        for i in range(3):
            self._activate_alt_trick(hwnd)
            time.sleep(0.3)
            img = pyautogui.screenshot(region=list_region)
            cap = self.capture(f"ordering_{i}", list_region)
            h = pixel_hash(img)

            snapshot = {"index": i, "capture": cap, "hash": h}
            if prev_img:
                sim = compare_images(prev_img, img)
                snapshot["similarity_to_prev"] = round(sim, 3)
                changed = sim < 0.99
                self.log(f"  스냅샷 {i}: {'변동 감지' if changed else '동일'} "
                         f"(유사도 {sim:.1%})")
            else:
                self.log(f"  스냅샷 {i}: 기준선")

            ordering_info["snapshots"].append(snapshot)
            prev_img = img

            if i < 2:
                self.log(f"  5초 대기...")
                time.sleep(5)

        self.findings["ordering"] = ordering_info

    # ===================================================================
    # TEST 7: 실패 복구 -비정상 상태에서 원래 상태로 돌아오기
    # ===================================================================

    def test_recovery(self):
        self.log("\n" + "=" * 60)
        self.log("TEST: 실패 복구 -비정상 상태에서 복구")
        self.log("=" * 60)

        try:
            hwnd, rect = self._find_kakao()
        except RuntimeError:
            self.log("  [SKIP] 카카오톡 없음")
            return
        kakao_region = (rect[0], rect[1], rect[2] - rect[0], rect[3] - rect[1])

        recovery_info = {}

        # --- 기준 상태 캡처 (깨끗한 채팅 목록) ---
        self._activate_alt_trick(hwnd)
        time.sleep(0.3)
        pyautogui.click(rect[0] + 27, rect[1] + 115)
        time.sleep(0.5)
        img_clean = pyautogui.screenshot(region=kakao_region)
        cap_clean = self.capture("recovery_0_clean", kakao_region)

        # --- 시나리오 1: 검색이 열린 상태에서 복구 ---
        self.log("\n  [시나리오 1] 검색 열린 상태 → 복구")
        pyautogui.hotkey("ctrl", "f")
        time.sleep(0.5)
        pyperclip.copy("asdfjkl")
        pyautogui.hotkey("ctrl", "v")
        time.sleep(0.3)

        methods_1 = [
            ("ESC_1회", lambda: pyautogui.press("escape")),
            ("ESC_3회", lambda: [pyautogui.press("escape") or time.sleep(0.2) for _ in range(3)]),
            ("채팅탭_클릭", lambda: pyautogui.click(rect[0] + 27, rect[1] + 115)),
        ]

        for name, action in methods_1:
            # 다시 검색 열기
            self._activate_alt_trick(hwnd)
            time.sleep(0.3)
            pyautogui.hotkey("ctrl", "f")
            time.sleep(0.5)
            pyperclip.copy("asdfjkl")
            pyautogui.hotkey("ctrl", "v")
            time.sleep(0.3)

            action()
            time.sleep(0.5)
            self._activate_alt_trick(hwnd)
            time.sleep(0.3)
            img_after = pyautogui.screenshot(region=kakao_region)
            sim = compare_images(img_clean, img_after)
            cap = self.capture(f"recovery_1_{name}", kakao_region)
            self.log(f"    {name}: 복구 유사도 {sim:.1%}")
            recovery_info[f"search_recovery_{name}"] = round(sim, 3)

        # --- 시나리오 2: 방이 열린 상태에서 복구 ---
        self.log("\n  [시나리오 2] 방 열린 상태 → 복구")
        self._activate_alt_trick(hwnd)
        time.sleep(0.3)
        pyautogui.click(rect[0] + 27, rect[1] + 115)
        time.sleep(0.3)
        room_x = rect[0] + 250
        room_y = rect[1] + 160
        pyautogui.doubleClick(room_x, room_y)
        time.sleep(1.5)

        # ESC로 닫기
        pyautogui.press("escape")
        time.sleep(0.5)
        self._activate_alt_trick(hwnd)
        time.sleep(0.3)
        img_after = pyautogui.screenshot(region=kakao_region)
        sim = compare_images(img_clean, img_after)
        cap = self.capture("recovery_2_room_esc", kakao_region)
        self.log(f"    방 ESC 후 복구 유사도: {sim:.1%}")
        recovery_info["room_esc_recovery"] = round(sim, 3)

        # --- 시나리오 3: 저장 다이얼로그가 열린 상태에서 복구 ---
        self.log("\n  [시나리오 3] 포그라운드 상실 → 복구")
        # 다른 앱으로 포커스 이동 시뮬레이션
        pyautogui.click(960, 540)  # 화면 중앙 (다른 창)
        time.sleep(1.0)
        fg_lost = win32gui.GetForegroundWindow()
        self.log(f"    포커스 이동: {win32gui.GetWindowText(fg_lost)}")

        # 복구
        self._activate_alt_trick(hwnd)
        time.sleep(0.3)
        pyautogui.click(rect[0] + 27, rect[1] + 115)
        time.sleep(0.5)
        img_after = pyautogui.screenshot(region=kakao_region)
        sim = compare_images(img_clean, img_after)
        cap = self.capture("recovery_3_focus_lost", kakao_region)
        self.log(f"    포커스 복구 유사도: {sim:.1%}")
        recovery_info["focus_lost_recovery"] = round(sim, 3)

        self.findings["recovery"] = recovery_info

    # ===================================================================
    # 전체 실행
    # ===================================================================

    def run_all(self):
        self.log("=" * 60)
        self.log("카카오톡 동작 원리 탐색기 v1.0")
        self.log(f"시작: {datetime.now().isoformat()}")
        self.log("=" * 60)

        tests = [
            ("windows", self.test_windows),
            ("activate", self.test_activate),
            ("search", self.test_search),
            ("room", self.test_room),
            ("save", self.test_save),
            ("ordering", self.test_ordering),
            ("recovery", self.test_recovery),
        ]

        for name, test_fn in tests:
            try:
                test_fn()
            except Exception as e:
                self.log(f"\n[ERROR] {name} 테스트 실패: {e}")
                self.log(traceback.format_exc())
            # 각 테스트 후 카카오톡 상태 복원
            try:
                for _ in range(5):
                    pyautogui.press("escape")
                    time.sleep(0.2)
                hwnd, _ = self._find_kakao()
                self._activate_alt_trick(hwnd)
                time.sleep(0.5)
            except Exception:
                pass
                self.findings[name] = {"error": str(e)}

        self.log(f"\n완료: {datetime.now().isoformat()}")
        self.save_all()

    def run_single(self, test_name: str):
        test_map = {
            "windows": self.test_windows,
            "activate": self.test_activate,
            "search": self.test_search,
            "room": self.test_room,
            "save": self.test_save,
            "ordering": self.test_ordering,
            "recovery": self.test_recovery,
        }
        if test_name not in test_map:
            print(f"알 수 없는 테스트: {test_name}")
            print(f"가능한 테스트: {', '.join(test_map.keys())}")
            return
        self.log(f"단일 테스트: {test_name}")
        try:
            test_map[test_name]()
        except Exception as e:
            self.log(f"[ERROR] {e}")
            self.log(traceback.format_exc())
        self.save_all()


def main():
    explorer = Explorer()
    if len(sys.argv) > 1:
        explorer.run_single(sys.argv[1])
    else:
        explorer.run_all()


if __name__ == "__main__":
    main()
