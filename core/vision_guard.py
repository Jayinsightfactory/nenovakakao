# -*- coding: utf-8 -*-
"""
Vision-Verified Safe Automation Framework (VisionGuard)

모든 자동화 동작을 '캡처→판단→실행→캡처→검증' 루프로 감싼다.
기대와 다르면 분석 후 재시도하거나 안전하게 중단한다.

사용 예:
    guard = VisionGuard(capture_dir="data/guard")
    with guard.step("채팅탭 클릭") as s:
        s.pre_check(guard.expect_foreground, hwnd=KAKAO_HWND)
        s.action(lambda: pyautogui.click(27, 115))
        s.post_check(guard.expect_region_changed, region=(60, 100, 500, 900))
"""
from __future__ import annotations

import hashlib
import json
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import pyautogui
import win32gui
from PIL import Image, ImageChops, ImageStat

# ---------------------------------------------------------------------------
# 설정
# ---------------------------------------------------------------------------
DEFAULT_SIMILARITY_THRESHOLD = 0.90   # 90% 이상이면 "동일"
DEFAULT_CHANGE_THRESHOLD = 0.05       # 5% 이상 달라야 "변화 있음"
MAX_RETRIES = 3
CAPTURE_DELAY = 0.3  # 캡처 전 안정화 대기(초)


# ---------------------------------------------------------------------------
# 이미지 비교 유틸
# ---------------------------------------------------------------------------

def compare_images(img1: Image.Image, img2: Image.Image) -> float:
    """
    두 이미지의 픽셀 유사도를 0.0~1.0으로 반환.
    1.0 = 완전 동일, 0.0 = 완전히 다름.
    크기가 다르면 작은 쪽에 맞춰 리사이즈.
    """
    if img1.size != img2.size:
        img2 = img2.resize(img1.size, Image.LANCZOS)

    img1_gray = img1.convert("L")
    img2_gray = img2.convert("L")

    diff = ImageChops.difference(img1_gray, img2_gray)
    stat = ImageStat.Stat(diff)
    # mean of absolute diff (0~255), normalize to 0~1
    mean_diff = stat.mean[0] / 255.0
    return 1.0 - mean_diff


def compare_regions(img1: Image.Image, img2: Image.Image,
                    region: tuple[int, int, int, int]) -> float:
    """특정 영역만 잘라서 비교. region = (left, top, right, bottom) 상대좌표."""
    crop1 = img1.crop(region)
    crop2 = img2.crop(region)
    return compare_images(crop1, crop2)


def pixel_hash(img: Image.Image) -> str:
    """이미지의 빠른 해시 (변화 감지용)."""
    small = img.resize((64, 64), Image.LANCZOS).convert("L")
    return hashlib.md5(small.tobytes()).hexdigest()


# ---------------------------------------------------------------------------
# 화면 상태 감지
# ---------------------------------------------------------------------------

def get_foreground_hwnd() -> int:
    """현재 포그라운드 윈도우 핸들."""
    return win32gui.GetForegroundWindow()


def get_window_title(hwnd: int) -> str:
    """윈도우 핸들의 제목."""
    try:
        return win32gui.GetWindowText(hwnd)
    except Exception:
        return ""


def is_window_foreground(hwnd: int) -> bool:
    """특정 윈도우가 포그라운드인지 확인."""
    return get_foreground_hwnd() == hwnd


def detect_new_window(before_hwnds: set[int]) -> Optional[int]:
    """이전에 없던 새 윈도우가 나타났는지 확인."""
    def enum_cb(hwnd, results):
        if win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd):
            results.append(hwnd)
    current = []
    win32gui.EnumWindows(enum_cb, current)
    new = set(current) - before_hwnds
    return next(iter(new), None)


def get_visible_hwnds() -> set[int]:
    """현재 보이는 윈도우 핸들 집합."""
    results = []
    def enum_cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd):
            results.append(hwnd)
    win32gui.EnumWindows(enum_cb, None)
    return set(results)


# ---------------------------------------------------------------------------
# 캡처 유틸
# ---------------------------------------------------------------------------

def safe_screenshot(region: Optional[tuple[int, int, int, int]] = None) -> Image.Image:
    """안전한 스크린샷. region = (left, top, width, height)."""
    time.sleep(CAPTURE_DELAY)
    if region:
        return pyautogui.screenshot(region=region)
    return pyautogui.screenshot()


# ---------------------------------------------------------------------------
# StepContext — 단일 스텝의 안전 실행 컨텍스트
# ---------------------------------------------------------------------------

@dataclass
class StepResult:
    """단일 스텝 실행 결과."""
    name: str
    success: bool
    attempts: int = 1
    pre_capture: Optional[Image.Image] = None
    post_capture: Optional[Image.Image] = None
    similarity: float = 0.0
    error: Optional[str] = None
    details: dict = field(default_factory=dict)


class StepContext:
    """
    with guard.step("이름") as s: 블록 안에서 사용.
    s.pre_check(...)   → 실행 전 검증
    s.action(...)      → 실행
    s.post_check(...)  → 실행 후 검증
    """

    def __init__(self, name: str, guard: "VisionGuard",
                 capture_region: Optional[tuple] = None,
                 retries: int = MAX_RETRIES):
        self.name = name
        self.guard = guard
        self.capture_region = capture_region
        self.retries = retries
        self._pre_checks: list[tuple[Callable, dict]] = []
        self._action_fn: Optional[Callable] = None
        self._post_checks: list[tuple[Callable, dict]] = []
        self._pre_img: Optional[Image.Image] = None
        self._post_img: Optional[Image.Image] = None
        self.result = StepResult(name=name, success=False)

    def pre_check(self, check_fn: Callable[..., bool], **kwargs):
        """실행 전 검증 함수 등록. check_fn(**kwargs) → bool."""
        self._pre_checks.append((check_fn, kwargs))

    def action(self, action_fn: Callable):
        """실행할 동작 등록."""
        self._action_fn = action_fn

    def post_check(self, check_fn: Callable[..., bool], **kwargs):
        """실행 후 검증 함수 등록. check_fn(**kwargs) → bool."""
        self._post_checks.append((check_fn, kwargs))

    def _capture(self) -> Image.Image:
        return safe_screenshot(self.capture_region)

    def _run_checks(self, checks: list, phase: str) -> tuple[bool, str]:
        for check_fn, kwargs in checks:
            try:
                # pre/post 이미지를 자동 주입
                if "pre_img" not in kwargs and self._pre_img:
                    kwargs.setdefault("_pre_img", self._pre_img)
                if "post_img" not in kwargs and self._post_img:
                    kwargs.setdefault("_post_img", self._post_img)

                ok = check_fn(**kwargs)
                if not ok:
                    msg = f"{phase} 실패: {check_fn.__name__}"
                    return False, msg
            except Exception as e:
                msg = f"{phase} 예외: {check_fn.__name__}: {e}"
                return False, msg
        return True, ""

    def execute(self) -> StepResult:
        """전체 스텝 실행 (재시도 포함)."""
        for attempt in range(1, self.retries + 1):
            self.result.attempts = attempt
            self.guard._log(f"  [{self.name}] 시도 {attempt}/{self.retries}")

            # 1. Pre-capture
            self._pre_img = self._capture()
            self.result.pre_capture = self._pre_img

            # 2. Pre-checks
            ok, msg = self._run_checks(self._pre_checks, "PRE")
            if not ok:
                self.guard._log(f"    ✗ {msg}")
                if attempt < self.retries:
                    self.guard._log(f"    → 재시도 대기 1초")
                    time.sleep(1.0)
                    continue
                self.result.error = msg
                self._save_debug("pre_fail")
                return self.result

            # 3. Action
            if self._action_fn:
                try:
                    self._action_fn()
                except Exception as e:
                    self.result.error = f"ACTION 예외: {e}"
                    self.guard._log(f"    ✗ {self.result.error}")
                    self._save_debug("action_fail")
                    if attempt < self.retries:
                        time.sleep(1.0)
                        continue
                    return self.result

            # 4. Post-capture (액션 후 안정화 대기)
            time.sleep(0.5)
            self._post_img = self._capture()
            self.result.post_capture = self._post_img

            # 5. Pre/Post 유사도 기록
            if self._pre_img and self._post_img:
                self.result.similarity = compare_images(self._pre_img, self._post_img)
                self.guard._log(f"    유사도: {self.result.similarity:.1%}")

            # 6. Post-checks
            ok, msg = self._run_checks(self._post_checks, "POST")
            if not ok:
                self.guard._log(f"    ✗ {msg}")
                self._save_debug("post_fail")
                if attempt < self.retries:
                    self.guard._log(f"    → 재시도 대기 1초")
                    time.sleep(1.0)
                    continue
                self.result.error = msg
                return self.result

            # 성공
            self.result.success = True
            self.guard._log(f"    ✓ 성공")
            return self.result

        return self.result

    def _save_debug(self, phase: str):
        """실패 시 디버그 캡처 저장."""
        ts = datetime.now().strftime("%H%M%S")
        safe_name = self.name.replace(" ", "_")[:20]
        if self._pre_img:
            path = self.guard.capture_dir / f"{ts}_{safe_name}_{phase}_pre.png"
            self._pre_img.save(path)
        if self._post_img:
            path = self.guard.capture_dir / f"{ts}_{safe_name}_{phase}_post.png"
            self._post_img.save(path)


# ---------------------------------------------------------------------------
# VisionGuard — 메인 클래스
# ---------------------------------------------------------------------------

class VisionGuard:
    """
    비전 검증 안전 자동화 프레임워크.

    사용법:
        guard = VisionGuard("data/guard")

        with guard.step("채팅탭 클릭") as s:
            s.pre_check(guard.expect_foreground, hwnd=KAKAO_HWND)
            s.action(lambda: pyautogui.click(27, 115))
            s.post_check(guard.expect_screen_changed)

        if guard.has_failure:
            guard.abort("중단: 이전 단계 실패")
    """

    def __init__(self, capture_dir: str | Path, log_fn: Callable = print):
        self.capture_dir = Path(capture_dir)
        self.capture_dir.mkdir(parents=True, exist_ok=True)
        self._log_fn = log_fn
        self.steps: list[StepResult] = []
        self.has_failure = False
        self._aborted = False
        self._abort_reason = ""
        # 실행 로그
        self._run_log: list[dict] = []

    def _log(self, msg: str):
        self._log_fn(msg)
        self._run_log.append({
            "time": datetime.now().isoformat(),
            "msg": msg,
        })

    def step(self, name: str,
             capture_region: Optional[tuple] = None,
             retries: int = MAX_RETRIES) -> StepContext:
        """안전한 스텝 컨텍스트를 생성. with 문과 함께 사용."""
        return _StepContextManager(self, name, capture_region, retries)

    def _register_result(self, result: StepResult):
        """스텝 결과를 등록."""
        self.steps.append(result)
        if not result.success:
            self.has_failure = True

    def abort(self, reason: str):
        """실행 중단. 이후 스텝은 스킵."""
        self._aborted = True
        self._abort_reason = reason
        self._log(f"[ABORT] {reason}")

    @property
    def is_aborted(self) -> bool:
        return self._aborted

    def save_run_log(self):
        """실행 로그를 JSON으로 저장."""
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = self.capture_dir / f"run_log_{ts}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self._run_log, f, ensure_ascii=False, indent=2)
        self._log(f"[LOG] 실행 로그 저장: {path}")

    def summary(self) -> str:
        """실행 결과 요약."""
        total = len(self.steps)
        ok = sum(1 for s in self.steps if s.success)
        fail = total - ok
        lines = [f"총 {total}단계: 성공 {ok} / 실패 {fail}"]
        for s in self.steps:
            icon = "✓" if s.success else "✗"
            retry = f" (시도 {s.attempts})" if s.attempts > 1 else ""
            err = f" — {s.error}" if s.error else ""
            lines.append(f"  {icon} {s.name}{retry}{err}")
        return "\n".join(lines)

    # -------------------------------------------------------------------
    # 내장 검증 함수들 (pre_check / post_check 에 사용)
    # -------------------------------------------------------------------

    @staticmethod
    def expect_foreground(hwnd: int, **_) -> bool:
        """특정 윈도우가 포그라운드인지 확인."""
        fg = get_foreground_hwnd()
        if fg == hwnd:
            return True
        title = get_window_title(fg)
        print(f"    [!] 포그라운드 불일치: 기대={hwnd}, 실제={fg} ({title})")
        return False

    @staticmethod
    def expect_screen_changed(_pre_img: Image.Image = None,
                              _post_img: Image.Image = None,
                              min_diff: float = DEFAULT_CHANGE_THRESHOLD,
                              region: Optional[tuple] = None, **_) -> bool:
        """실행 전후 화면이 충분히 변했는지 확인."""
        if _pre_img is None or _post_img is None:
            return True  # 이미지 없으면 스킵
        if region:
            sim = compare_regions(_pre_img, _post_img, region)
        else:
            sim = compare_images(_pre_img, _post_img)
        diff = 1.0 - sim
        if diff < min_diff:
            print(f"    [!] 변화 부족: {diff:.1%} < 기대 {min_diff:.1%}")
            return False
        return True

    @staticmethod
    def expect_screen_similar(_pre_img: Image.Image = None,
                              _post_img: Image.Image = None,
                              min_sim: float = DEFAULT_SIMILARITY_THRESHOLD,
                              region: Optional[tuple] = None, **_) -> bool:
        """실행 전후 화면이 크게 변하지 않았는지 (안정성) 확인."""
        if _pre_img is None or _post_img is None:
            return True
        if region:
            sim = compare_regions(_pre_img, _post_img, region)
        else:
            sim = compare_images(_pre_img, _post_img)
        if sim < min_sim:
            print(f"    [!] 예상치 못한 변화: 유사도 {sim:.1%} < 기대 {min_sim:.1%}")
            return False
        return True

    @staticmethod
    def expect_new_file(directory: str | Path, before_files: set[str],
                        **_) -> bool:
        """디렉토리에 새 파일이 생성되었는지 확인."""
        d = Path(directory)
        if not d.exists():
            return False
        current = set(str(p) for p in d.rglob("*.txt"))
        new = current - before_files
        if new:
            print(f"    [+] 새 파일: {next(iter(new))}")
            return True
        print(f"    [!] 새 파일 없음")
        return False

    @staticmethod
    def expect_window_title_contains(keyword: str, **_) -> bool:
        """포그라운드 윈도우 제목에 키워드 포함 여부."""
        fg = get_foreground_hwnd()
        title = get_window_title(fg)
        if keyword in title:
            return True
        print(f"    [!] 윈도우 제목 불일치: '{title}'에 '{keyword}' 없음")
        return False

    @staticmethod
    def expect_no_new_popup(before_hwnds: set[int], **_) -> bool:
        """예기치 않은 팝업이 뜨지 않았는지 확인."""
        new = detect_new_window(before_hwnds)
        if new:
            title = get_window_title(new)
            print(f"    [!] 예기치 않은 팝업: {title} (hwnd={new})")
            return False
        return True


# ---------------------------------------------------------------------------
# ContextManager 래퍼 (with 문 지원)
# ---------------------------------------------------------------------------

class _StepContextManager:
    def __init__(self, guard: VisionGuard, name: str,
                 capture_region, retries):
        self._guard = guard
        self._ctx = StepContext(name, guard, capture_region, retries)

    def __enter__(self) -> StepContext:
        if self._guard.is_aborted:
            self._guard._log(f"  [{self._ctx.name}] SKIP (중단됨)")
            # 빈 결과 반환
            self._ctx.result.error = "이전 단계 중단으로 스킵"
            return self._ctx
        return self._ctx

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._guard.is_aborted and not self._ctx._action_fn:
            self._guard._register_result(self._ctx.result)
            return True  # 예외 억제
        if exc_type:
            self._ctx.result.error = f"예외: {exc_val}"
            self._guard._log(f"  [{self._ctx.name}] 예외: {exc_val}")
            self._guard._register_result(self._ctx.result)
            return True  # 예외 억제 (안전하게 중단)
        # 정상 흐름: 스텝 실행
        if self._ctx._action_fn or self._ctx._pre_checks or self._ctx._post_checks:
            self._ctx.execute()
        self._guard._register_result(self._ctx.result)
        return False
