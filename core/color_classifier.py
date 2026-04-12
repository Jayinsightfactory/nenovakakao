# -*- coding: utf-8 -*-
"""
네노바 화훼 품종별 색상 분류기 v1.0

이미지의 지배적 색상(HSV)을 분석하여 화훼 품종을 자동 판별.
- COLOR_PROFILES: 품종별 HSV 색상 범위 사전
- analyze_flower_color(): 이미지 → 지배적 색상 추출
- classify_variety(): 색상 기반 품종 매칭 (상위 3개 후보)
- build_reference_from_images(): 레이블 이미지로 프로파일 학습
- save/load: data/color_profiles.json 영속화
"""
from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path
from typing import Optional

import numpy as np

# PIL 우선, OpenCV 있으면 사용
try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

from PIL import Image

# ─── 경로 ───
DATA_DIR = Path(__file__).parent.parent / "data"
PROFILES_PATH = DATA_DIR / "color_profiles.json"

# ─── HSV 범위 헬퍼 ───
# H: 0-179 (OpenCV) 또는 0-360 (일반). 여기서는 0-360 기준으로 정의 후 내부 변환.
# S: 0-100 (%), V: 0-100 (%)
# PIL 변환 시 H: 0-360, S: 0-100, V: 0-100 으로 통일


def _hsv_range(h_min, h_max, s_min=20, s_max=100, v_min=20, v_max=100):
    """HSV 범위 딕셔너리 생성. H: 0-360, S/V: 0-100%."""
    return {
        "h_min": h_min, "h_max": h_max,
        "s_min": s_min, "s_max": s_max,
        "v_min": v_min, "v_max": v_max,
    }


# ─── 색상 프로파일 DB ───
# 각 품종: flower_type, name_kr, name_en, primary (지배적 색상 HSV 범위),
#          secondary (투톤인 경우), color_desc (사람이 읽을 수 있는 설명)

COLOR_PROFILES: dict[str, dict] = {
    # ══════════════════════════════════════
    #  카네이션 (18 품종)
    # ══════════════════════════════════════
    "moonlight": {
        "flower_type": "카네이션",
        "name_kr": "문라이트",
        "name_en": "Moonlight",
        "color_desc": "연노랑/크림색",
        "primary": _hsv_range(40, 65, 15, 50, 75, 100),   # 연노랑~크림
        "secondary": None,
    },
    "doncel": {
        "flower_type": "카네이션",
        "name_kr": "돈셀",
        "name_en": "Doncel",
        "color_desc": "핑크",
        "primary": _hsv_range(320, 350, 30, 80, 60, 100),  # 핑크
        "secondary": None,
    },
    "hermes": {
        "flower_type": "카네이션",
        "name_kr": "헤르메스",
        "name_en": "Hermes",
        "color_desc": "주황/살구색",
        "primary": _hsv_range(15, 35, 40, 80, 65, 100),    # 주황~살구
        "secondary": None,
    },
    "hermes_orange": {
        "flower_type": "카네이션",
        "name_kr": "헤르메스오렌지",
        "name_en": "Hermes Orange",
        "color_desc": "진한 주황",
        "primary": _hsv_range(10, 30, 60, 100, 60, 100),   # 진주황
        "secondary": None,
    },
    "novia": {
        "flower_type": "카네이션",
        "name_kr": "노비아",
        "name_en": "Novia",
        "color_desc": "연핑크/화이트",
        "primary": _hsv_range(330, 360, 5, 30, 85, 100),   # 연핑크~화이트
        "secondary": None,
    },
    "giogia": {
        "flower_type": "카네이션",
        "name_kr": "지오지아",
        "name_en": "Giogia",
        "color_desc": "빨강/딥핑크",
        "primary": _hsv_range(340, 360, 60, 100, 40, 85),  # 딥핑크~빨강
        "secondary": _hsv_range(0, 10, 60, 100, 40, 85),   # 빨강 wrap-around
    },
    "polimnia": {
        "flower_type": "카네이션",
        "name_kr": "폴림니아",
        "name_en": "Polimnia",
        "color_desc": "연보라/라벤더",
        "primary": _hsv_range(260, 290, 15, 50, 60, 95),   # 라벤더
        "secondary": None,
    },
    "cherio": {
        "flower_type": "카네이션",
        "name_kr": "체리오",
        "name_en": "Cherio",
        "color_desc": "빨강+화이트 투톤",
        "primary": _hsv_range(350, 360, 50, 100, 50, 100), # 빨강
        "secondary": _hsv_range(0, 30, 0, 15, 85, 100),    # 화이트
        "is_bicolor": True,
    },
    "eucari_cherry": {
        "flower_type": "카네이션",
        "name_kr": "유카리체리",
        "name_en": "Eucari Cherry",
        "color_desc": "체리핑크",
        "primary": _hsv_range(335, 355, 50, 90, 50, 90),   # 체리핑크
        "secondary": None,
    },
    "brut": {
        "flower_type": "카네이션",
        "name_kr": "브루트",
        "name_en": "Brut",
        "color_desc": "연핑크",
        "primary": _hsv_range(330, 355, 10, 40, 80, 100),  # 연핑크
        "secondary": None,
    },
    "ness": {
        "flower_type": "카네이션",
        "name_kr": "네스",
        "name_en": "Ness",
        "color_desc": "진분홍",
        "primary": _hsv_range(325, 350, 50, 90, 50, 90),   # 진분홍
        "secondary": None,
    },
    "mariposa": {
        "flower_type": "카네이션",
        "name_kr": "마리포사",
        "name_en": "Mariposa",
        "color_desc": "연분홍/살구",
        "primary": _hsv_range(5, 25, 20, 50, 75, 100),     # 살구~연분홍
        "secondary": _hsv_range(330, 350, 10, 35, 80, 100), # 연분홍
    },
    "electric_purple": {
        "flower_type": "카네이션",
        "name_kr": "일렉트릭퍼플",
        "name_en": "Electric Purple",
        "color_desc": "보라/퍼플",
        "primary": _hsv_range(270, 310, 40, 100, 30, 80),  # 퍼플
        "secondary": None,
    },
    "colibri": {
        "flower_type": "카네이션",
        "name_kr": "콜리브리",
        "name_en": "Colibri",
        "color_desc": "다양 (농장명)",
        "primary": _hsv_range(0, 360, 10, 100, 20, 100),   # 전 범위
        "secondary": None,
        "is_wildcard": True,
    },
    "farida": {
        "flower_type": "카네이션",
        "name_kr": "파리다",
        "name_en": "Farida",
        "color_desc": "빨강",
        "primary": _hsv_range(350, 360, 60, 100, 40, 90),
        "secondary": _hsv_range(0, 10, 60, 100, 40, 90),
    },
    "minuetto": {
        "flower_type": "카네이션",
        "name_kr": "미뉴에또",
        "name_en": "Minuetto",
        "color_desc": "화이트+핑크엣지",
        "primary": _hsv_range(0, 360, 0, 12, 88, 100),     # 화이트 바탕
        "secondary": _hsv_range(330, 355, 25, 65, 70, 100), # 핑크 엣지
        "is_bicolor": True,
    },
    "spray_white": {
        "flower_type": "카네이션",
        "name_kr": "스프레이화이트",
        "name_en": "Spray White",
        "color_desc": "화이트",
        "primary": _hsv_range(0, 360, 0, 12, 88, 100),     # 화이트
        "secondary": None,
    },
    "spray_light_pink": {
        "flower_type": "카네이션",
        "name_kr": "스프레이연핑크",
        "name_en": "Spray Light Pink",
        "color_desc": "연핑크",
        "primary": _hsv_range(330, 355, 8, 35, 82, 100),   # 연핑크
        "secondary": None,
    },

    # ══════════════════════════════════════
    #  장미 (17 품종)
    # ══════════════════════════════════════
    "proud": {
        "flower_type": "장미",
        "name_kr": "프라우드",
        "name_en": "Proud",
        "color_desc": "화이트",
        "primary": _hsv_range(0, 360, 0, 12, 88, 100),
        "secondary": None,
    },
    "candlelight": {
        "flower_type": "장미",
        "name_kr": "캔들라이트",
        "name_en": "Candlelight",
        "color_desc": "노랑/크림",
        "primary": _hsv_range(40, 65, 20, 60, 75, 100),
        "secondary": None,
    },
    "mandala": {
        "flower_type": "장미",
        "name_kr": "만달라",
        "name_en": "Mandala",
        "color_desc": "핑크",
        "primary": _hsv_range(320, 350, 30, 75, 60, 100),
        "secondary": None,
    },
    "coral_reef": {
        "flower_type": "장미",
        "name_kr": "코랄리프",
        "name_en": "Coral Reef",
        "color_desc": "코랄/살구",
        "primary": _hsv_range(5, 25, 30, 70, 70, 100),     # 코랄
        "secondary": None,
    },
    "pink_floyd": {
        "flower_type": "장미",
        "name_kr": "핑크플로이드",
        "name_en": "Pink Floyd",
        "color_desc": "핑크",
        "primary": _hsv_range(320, 345, 35, 80, 55, 95),
        "secondary": None,
    },
    "star_platinum": {
        "flower_type": "장미",
        "name_kr": "스타플레티넘",
        "name_en": "Star Platinum",
        "color_desc": "화이트/크림",
        "primary": _hsv_range(30, 60, 5, 25, 85, 100),     # 크림~화이트
        "secondary": None,
    },
    "laura": {
        "flower_type": "장미",
        "name_kr": "로라",
        "name_en": "Laura",
        "color_desc": "핑크/코랄",
        "primary": _hsv_range(340, 360, 30, 70, 60, 100),
        "secondary": _hsv_range(0, 15, 30, 70, 60, 100),
    },
    "red_panther": {
        "flower_type": "장미",
        "name_kr": "레드팬서",
        "name_en": "Red Panther",
        "color_desc": "빨강",
        "primary": _hsv_range(350, 360, 65, 100, 35, 85),
        "secondary": _hsv_range(0, 10, 65, 100, 35, 85),
    },
    "pink_mondial": {
        "flower_type": "장미",
        "name_kr": "핑크몬디알",
        "name_en": "Pink Mondial",
        "color_desc": "연핑크",
        "primary": _hsv_range(330, 355, 10, 40, 80, 100),
        "secondary": None,
    },
    "black_jack": {
        "flower_type": "장미",
        "name_kr": "블랙잭",
        "name_en": "Black Jack",
        "color_desc": "진빨강/다크레드",
        "primary": _hsv_range(345, 360, 60, 100, 15, 55),  # 다크레드
        "secondary": _hsv_range(0, 10, 60, 100, 15, 55),
    },
    "pink_expression": {
        "flower_type": "장미",
        "name_kr": "핑크익스프레션",
        "name_en": "Pink Expression",
        "color_desc": "핑크",
        "primary": _hsv_range(325, 350, 30, 70, 60, 95),
        "secondary": None,
    },
    "jumilia": {
        "flower_type": "장미",
        "name_kr": "주밀리아",
        "name_en": "Jumilia",
        "color_desc": "핑크+화이트",
        "primary": _hsv_range(325, 350, 20, 60, 70, 100),  # 핑크
        "secondary": _hsv_range(0, 360, 0, 12, 88, 100),   # 화이트
        "is_bicolor": True,
    },
    "pink_snowberg": {
        "flower_type": "장미",
        "name_kr": "핑크스노우버그",
        "name_en": "Pink Snowberg",
        "color_desc": "연핑크",
        "primary": _hsv_range(330, 350, 8, 30, 85, 100),
        "secondary": None,
    },
    "sweet_avalanche": {
        "flower_type": "장미",
        "name_kr": "스윗아발란체",
        "name_en": "Sweet Avalanche",
        "color_desc": "연핑크/화이트",
        "primary": _hsv_range(330, 355, 5, 25, 88, 100),
        "secondary": None,
    },
    "julring": {
        "flower_type": "장미",
        "name_kr": "줄링",
        "name_en": "Julring",
        "color_desc": "빨강",
        "primary": _hsv_range(350, 360, 60, 100, 40, 90),
        "secondary": _hsv_range(0, 10, 60, 100, 40, 90),
    },
    "matina": {
        "flower_type": "장미",
        "name_kr": "마티나",
        "name_en": "Matina",
        "color_desc": "빨강",
        "primary": _hsv_range(350, 360, 55, 100, 35, 85),
        "secondary": _hsv_range(0, 10, 55, 100, 35, 85),
    },

    # ══════════════════════════════════════
    #  수국 (5 색상)
    # ══════════════════════════════════════
    "hydrangea_white": {
        "flower_type": "수국",
        "name_kr": "화이트",
        "name_en": "Hydrangea White",
        "color_desc": "화이트",
        "primary": _hsv_range(0, 360, 0, 15, 85, 100),
        "secondary": None,
    },
    "hydrangea_blue": {
        "flower_type": "수국",
        "name_kr": "블루",
        "name_en": "Hydrangea Blue",
        "color_desc": "파랑/보라",
        "primary": _hsv_range(210, 280, 25, 80, 30, 85),
        "secondary": None,
    },
    "hydrangea_light_pink": {
        "flower_type": "수국",
        "name_kr": "연핑크",
        "name_en": "Hydrangea Light Pink",
        "color_desc": "연핑크",
        "primary": _hsv_range(330, 355, 10, 40, 75, 100),
        "secondary": None,
    },
    "hydrangea_pink": {
        "flower_type": "수국",
        "name_kr": "핑크",
        "name_en": "Hydrangea Pink",
        "color_desc": "핑크",
        "primary": _hsv_range(320, 350, 30, 75, 55, 95),
        "secondary": None,
    },
    "hydrangea_green": {
        "flower_type": "수국",
        "name_kr": "그린",
        "name_en": "Hydrangea Green",
        "color_desc": "연두/그린",
        "primary": _hsv_range(80, 150, 15, 60, 50, 90),
        "secondary": None,
    },

    # ══════════════════════════════════════
    #  기타
    # ══════════════════════════════════════
    "ruscus": {
        "flower_type": "기타",
        "name_kr": "루스커스",
        "name_en": "Ruscus",
        "color_desc": "짙은 녹색 잎",
        "primary": _hsv_range(100, 160, 30, 80, 20, 60),
        "secondary": None,
    },
    "lemon_leaf": {
        "flower_type": "기타",
        "name_kr": "레몬잎",
        "name_en": "Lemon Leaf",
        "color_desc": "밝은 녹색 잎",
        "primary": _hsv_range(80, 145, 25, 70, 45, 85),
        "secondary": None,
    },
    "mokara": {
        "flower_type": "기타",
        "name_kr": "모카라",
        "name_en": "Mokara",
        "color_desc": "주황~빨강 난",
        "primary": _hsv_range(0, 30, 50, 100, 50, 100),
        "secondary": None,
    },
    "alstroemeria": {
        "flower_type": "기타",
        "name_kr": "알스트로",
        "name_en": "Alstroemeria",
        "color_desc": "다양한 색",
        "primary": _hsv_range(0, 360, 10, 100, 20, 100),
        "secondary": None,
        "is_wildcard": True,
    },
    "tulip": {
        "flower_type": "기타",
        "name_kr": "튤립",
        "name_en": "Tulip",
        "color_desc": "다양한 색",
        "primary": _hsv_range(0, 360, 10, 100, 20, 100),
        "secondary": None,
        "is_wildcard": True,
    },
}


# ─── 유틸리티 ───

def _rgb_to_hsv(r: int, g: int, b: int) -> tuple[float, float, float]:
    """RGB (0-255) → HSV (H: 0-360, S: 0-100, V: 0-100)."""
    r_, g_, b_ = r / 255.0, g / 255.0, b / 255.0
    mx = max(r_, g_, b_)
    mn = min(r_, g_, b_)
    diff = mx - mn

    # Hue
    if diff == 0:
        h = 0
    elif mx == r_:
        h = (60 * ((g_ - b_) / diff) + 360) % 360
    elif mx == g_:
        h = (60 * ((b_ - r_) / diff) + 120) % 360
    else:
        h = (60 * ((r_ - g_) / diff) + 240) % 360

    # Saturation
    s = 0 if mx == 0 else (diff / mx) * 100

    # Value
    v = mx * 100

    return h, s, v


def _crop_center(img: Image.Image, ratio: float = 0.5) -> Image.Image:
    """이미지 중앙 영역 크롭. ratio=0.5이면 중앙 50% 영역."""
    w, h = img.size
    margin_x = int(w * (1 - ratio) / 2)
    margin_y = int(h * (1 - ratio) / 2)
    return img.crop((margin_x, margin_y, w - margin_x, h - margin_y))


def _remove_background_pixels(pixels_hsv: list[tuple], bg_threshold_s: float = 8.0,
                                bg_threshold_v_low: float = 10.0,
                                bg_threshold_v_high: float = 98.0) -> list[tuple]:
    """배경(거의 무채색이거나 극단적 밝기) 픽셀 제거."""
    filtered = []
    for h, s, v in pixels_hsv:
        # 너무 어둡거나 (거의 검정) 너무 밝고 채도 없는 (거의 흰 배경) 픽셀 제외
        if v < bg_threshold_v_low:
            continue
        # 채도가 극히 낮고 밝기가 극히 높으면 흰 배경일 가능성
        # 단, 화이트 꽃도 있으므로 완전히 제거하지 않음
        filtered.append((h, s, v))
    return filtered


def _hsv_in_range(h: float, s: float, v: float, rng: dict) -> bool:
    """HSV 값이 주어진 범위 안에 있는지 확인."""
    h_min, h_max = rng["h_min"], rng["h_max"]
    # H가 wrap-around (예: 350-10) 할 수 있음
    if h_min <= h_max:
        h_ok = h_min <= h <= h_max
    else:
        h_ok = h >= h_min or h <= h_max
    s_ok = rng["s_min"] <= s <= rng["s_max"]
    v_ok = rng["v_min"] <= v <= rng["v_max"]
    return h_ok and s_ok and v_ok


def _hsv_distance(h1: float, s1: float, v1: float, rng: dict) -> float:
    """HSV 값과 범위 중심 사이의 거리. 낮을수록 유사."""
    h_center = (rng["h_min"] + rng["h_max"]) / 2
    if rng["h_min"] > rng["h_max"]:  # wrap-around
        h_center = ((rng["h_min"] + rng["h_max"] + 360) / 2) % 360
    s_center = (rng["s_min"] + rng["s_max"]) / 2
    v_center = (rng["v_min"] + rng["v_max"]) / 2

    # Hue는 원형이므로 최소 각도 차이 계산
    dh = min(abs(h1 - h_center), 360 - abs(h1 - h_center))
    ds = abs(s1 - s_center)
    dv = abs(v1 - v_center)

    # Hue 가중치를 높게 (색상 구분이 핵심)
    return math.sqrt((dh * 2.0) ** 2 + ds ** 2 + (dv * 0.5) ** 2)


# ─── 핵심 함수 ───

def extract_dominant_colors(image_path: str, top_n: int = 3,
                             center_ratio: float = 0.5,
                             quantize_h: int = 10,
                             quantize_s: int = 10,
                             quantize_v: int = 10) -> list[dict]:
    """
    이미지에서 지배적 HSV 색상 상위 N개 추출.

    Returns:
        [{"h": float, "s": float, "v": float, "ratio": float}, ...]
        ratio는 해당 색상이 전체 유효 픽셀에서 차지하는 비율 (0-1).
    """
    img = Image.open(image_path).convert("RGB")

    # 성능을 위해 리사이즈 (최대 300px)
    max_dim = 300
    if max(img.size) > max_dim:
        ratio = max_dim / max(img.size)
        img = img.resize((int(img.width * ratio), int(img.height * ratio)),
                         Image.Resampling.LANCZOS)

    # 중앙 크롭
    img = _crop_center(img, center_ratio)

    # 픽셀 → HSV 변환
    pixels = np.array(img)  # (H, W, 3)
    flat = pixels.reshape(-1, 3)

    hsv_pixels = []
    for r, g, b in flat:
        hsv_pixels.append(_rgb_to_hsv(int(r), int(g), int(b)))

    # 배경 제거
    hsv_pixels = _remove_background_pixels(hsv_pixels)
    if not hsv_pixels:
        return []

    # 양자화하여 유사 색상 그룹핑
    quantized = Counter()
    for h, s, v in hsv_pixels:
        qh = round(h / quantize_h) * quantize_h
        qs = round(s / quantize_s) * quantize_s
        qv = round(v / quantize_v) * quantize_v
        quantized[(qh % 360, min(qs, 100), min(qv, 100))] += 1

    total = len(hsv_pixels)
    top = quantized.most_common(top_n)

    results = []
    for (h, s, v), count in top:
        results.append({
            "h": h, "s": s, "v": v,
            "ratio": round(count / total, 4),
        })
    return results


def analyze_flower_color(image_path: str, center_ratio: float = 0.5) -> dict:
    """
    이미지를 분석하여 지배적 색상 + 전체 품종 후보 반환.

    Returns:
        {
            "dominant_colors": [{"h", "s", "v", "ratio"}, ...],
            "candidates": [(품종key, 품종정보, 신뢰도), ...],
        }
    """
    dominant = extract_dominant_colors(image_path, top_n=5, center_ratio=center_ratio)
    if not dominant:
        return {"dominant_colors": [], "candidates": []}

    profiles = load_profiles()
    scores: dict[str, float] = {}

    for key, profile in profiles.items():
        if profile.get("is_wildcard"):
            continue

        best_score = 0.0
        for dc in dominant:
            h, s, v = dc["h"], dc["s"], dc["v"]
            weight = dc["ratio"]

            # primary 매칭
            primary = profile["primary"]
            if _hsv_in_range(h, s, v, primary):
                match_score = 1.0
            else:
                dist = _hsv_distance(h, s, v, primary)
                match_score = max(0, 1.0 - dist / 180.0)

            # secondary 매칭 (투톤)
            secondary = profile.get("secondary")
            if secondary and not _hsv_in_range(h, s, v, primary):
                if _hsv_in_range(h, s, v, secondary):
                    match_score = max(match_score, 0.8)
                else:
                    dist2 = _hsv_distance(h, s, v, secondary)
                    match_score = max(match_score, 0.8 * max(0, 1.0 - dist2 / 180.0))

            best_score = max(best_score, match_score * weight)

        # 비율 가중 합산
        total_score = 0.0
        for dc in dominant:
            h, s, v = dc["h"], dc["s"], dc["v"]
            w = dc["ratio"]
            primary = profile["primary"]
            if _hsv_in_range(h, s, v, primary):
                total_score += w * 1.0
            else:
                dist = _hsv_distance(h, s, v, primary)
                total_score += w * max(0, 1.0 - dist / 180.0)
            secondary = profile.get("secondary")
            if secondary:
                if _hsv_in_range(h, s, v, secondary):
                    total_score += w * 0.3
                else:
                    dist2 = _hsv_distance(h, s, v, secondary)
                    total_score += w * 0.3 * max(0, 1.0 - dist2 / 180.0)

        scores[key] = total_score

    # 상위 후보
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    candidates = []
    for key, score in ranked[:10]:
        p = profiles[key]
        candidates.append({
            "variety_key": key,
            "flower_type": p["flower_type"],
            "name_kr": p["name_kr"],
            "name_en": p["name_en"],
            "color_desc": p["color_desc"],
            "confidence": round(min(score, 1.0), 4),
        })

    return {
        "dominant_colors": dominant,
        "candidates": candidates,
    }


def classify_variety(image_path: str, flower_type: Optional[str] = None,
                      top_n: int = 3, center_ratio: float = 0.5) -> list[tuple]:
    """
    품종 분류. flower_type이 주어지면 해당 카테고리 내에서만 매칭.

    Args:
        image_path: 꽃 이미지 경로
        flower_type: "카네이션", "장미", "수국", "기타" 또는 None (전체)
        top_n: 반환할 상위 후보 수
        center_ratio: 중앙 크롭 비율

    Returns:
        [(품종한글명, 신뢰도, 지배적색상설명), ...]
    """
    result = analyze_flower_color(image_path, center_ratio=center_ratio)
    candidates = result.get("candidates", [])
    dominant = result.get("dominant_colors", [])

    # 지배적 색상 설명 생성
    color_desc = ""
    if dominant:
        top_color = dominant[0]
        h, s, v = top_color["h"], top_color["s"], top_color["v"]
        color_desc = _describe_hsv(h, s, v)

    # flower_type 필터
    if flower_type:
        candidates = [c for c in candidates if c["flower_type"] == flower_type]

    # 상위 N개
    top = candidates[:top_n]
    return [(c["name_kr"], c["confidence"], color_desc) for c in top]


def _describe_hsv(h: float, s: float, v: float) -> str:
    """HSV 값을 사람이 읽을 수 있는 색상명으로 변환."""
    if s < 10:
        if v > 85:
            return "화이트"
        elif v > 50:
            return "그레이"
        else:
            return "다크그레이/블랙"

    if v < 20:
        return "블랙"

    # Hue 기반
    if h < 15 or h >= 345:
        if s > 50 and v < 50:
            return "다크레드"
        return "빨강"
    elif h < 35:
        if s < 40:
            return "살구/코랄"
        return "주황"
    elif h < 65:
        if s < 30:
            return "크림"
        return "노랑"
    elif h < 90:
        return "연두"
    elif h < 160:
        if v < 50:
            return "짙은 녹색"
        return "그린"
    elif h < 210:
        return "청록/시안"
    elif h < 260:
        return "파랑"
    elif h < 290:
        if s > 40:
            return "퍼플/보라"
        return "라벤더"
    elif h < 320:
        return "보라/마젠타"
    elif h < 345:
        if s < 30:
            return "연핑크"
        elif s < 60:
            return "핑크"
        return "진분홍/딥핑크"
    return "빨강"


# ─── 프로파일 학습 ───

def build_reference_from_images(image_dir: str, labels: dict[str, str]) -> dict:
    """
    레이블된 이미지들로 색상 프로파일 자동 학습/보정.

    Args:
        image_dir: 이미지 폴더 경로
        labels: {파일명: 품종key} 매핑
            예: {"IMG_001.jpg": "moonlight", "IMG_002.jpg": "doncel"}

    Returns:
        학습된 프로파일 딕셔너리 (기존 프로파일에 병합됨)
    """
    from collections import defaultdict

    learned: dict[str, list] = defaultdict(list)
    image_dir_path = Path(image_dir)

    for filename, variety_key in labels.items():
        img_path = image_dir_path / filename
        if not img_path.exists():
            continue

        dominant = extract_dominant_colors(str(img_path), top_n=3, center_ratio=0.5)
        if dominant:
            learned[variety_key].append(dominant)

    # 각 품종별로 평균 HSV 범위 계산
    updated_profiles = {}
    for variety_key, all_dominants in learned.items():
        all_h, all_s, all_v = [], [], []
        for dom_list in all_dominants:
            for dc in dom_list[:2]:  # 상위 2개만
                all_h.append(dc["h"])
                all_s.append(dc["s"])
                all_v.append(dc["v"])

        if not all_h:
            continue

        # 표준편차 기반 범위 설정
        h_arr = np.array(all_h)
        s_arr = np.array(all_s)
        v_arr = np.array(all_v)

        # Hue는 원형이므로 circular mean 사용
        h_rad = np.deg2rad(h_arr)
        h_mean = np.rad2deg(np.arctan2(np.mean(np.sin(h_rad)),
                                        np.mean(np.cos(h_rad)))) % 360
        h_std = float(np.std(h_arr))  # 근사적

        s_mean, s_std = float(np.mean(s_arr)), float(np.std(s_arr))
        v_mean, v_std = float(np.mean(v_arr)), float(np.std(v_arr))

        margin = 2.0  # 2 sigma
        new_range = _hsv_range(
            h_min=max(0, h_mean - h_std * margin),
            h_max=min(360, h_mean + h_std * margin),
            s_min=max(0, s_mean - s_std * margin),
            s_max=min(100, s_mean + s_std * margin),
            v_min=max(0, v_mean - v_std * margin),
            v_max=min(100, v_mean + v_std * margin),
        )

        updated_profiles[variety_key] = {
            "learned_primary": new_range,
            "sample_count": len(all_dominants),
            "h_mean": round(h_mean, 1),
            "s_mean": round(s_mean, 1),
            "v_mean": round(v_mean, 1),
        }

    # 기존 프로파일에 learned 데이터 병합
    profiles = load_profiles()
    for key, learned_data in updated_profiles.items():
        if key in profiles:
            profiles[key]["learned"] = learned_data
            # 충분한 샘플이 있으면 primary 범위를 학습 결과로 대체
            if learned_data["sample_count"] >= 5:
                profiles[key]["primary"] = learned_data["learned_primary"]

    save_profiles(profiles)
    return updated_profiles


# ─── 저장 / 로드 ───

def save_profiles(profiles: Optional[dict] = None):
    """프로파일을 data/color_profiles.json에 저장."""
    if profiles is None:
        profiles = COLOR_PROFILES
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(PROFILES_PATH, "w", encoding="utf-8") as f:
        json.dump(profiles, f, ensure_ascii=False, indent=2)


def load_profiles() -> dict:
    """
    프로파일 로드. 파일이 없으면 기본 COLOR_PROFILES 반환.
    파일 프로파일과 코드 프로파일을 병합 (코드가 기본, 파일이 오버라이드).
    """
    base = dict(COLOR_PROFILES)  # 코드에 하드코딩된 기본값
    if PROFILES_PATH.exists():
        try:
            with open(PROFILES_PATH, "r", encoding="utf-8") as f:
                saved = json.load(f)
            # 저장된 프로파일로 오버라이드 (learned 데이터 등 보존)
            for key, val in saved.items():
                if key in base:
                    base[key].update(val)
                else:
                    base[key] = val
        except (json.JSONDecodeError, IOError):
            pass
    return base


def init_profiles():
    """초기 프로파일 파일 생성 (없으면)."""
    if not PROFILES_PATH.exists():
        save_profiles(COLOR_PROFILES)


# ─── 편의 함수 ───

def get_varieties_by_type(flower_type: str) -> list[dict]:
    """특정 화훼 타입의 모든 품종 반환."""
    profiles = load_profiles()
    return [
        {"key": k, **v}
        for k, v in profiles.items()
        if v.get("flower_type") == flower_type
    ]


def quick_classify(image_path: str, flower_type: Optional[str] = None) -> str:
    """
    빠른 분류 — 사람이 읽기 좋은 한 줄 결과.
    예: "카네이션 > 문라이트 (연노랑/크림색) [신뢰도: 82%]"
    """
    results = classify_variety(image_path, flower_type=flower_type, top_n=1)
    if not results:
        return "분류 불가 (유효한 색상을 감지하지 못했습니다)"

    name, conf, color = results[0]
    # 프로파일에서 flower_type 찾기
    profiles = load_profiles()
    ft = "?"
    for p in profiles.values():
        if p["name_kr"] == name:
            ft = p["flower_type"]
            break

    pct = int(conf * 100)
    return f"{ft} > {name} ({color}) [신뢰도: {pct}%]"


# ─── 모듈 초기화 ───
# import 시 프로파일 파일이 없으면 자동 생성
init_profiles()


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("사용법: python color_classifier.py <이미지경로> [화훼종류]")
        print("  화훼종류: 카네이션, 장미, 수국, 기타")
        print()
        print(f"등록된 품종: {len(COLOR_PROFILES)}개")
        for ft in ["카네이션", "장미", "수국", "기타"]:
            varieties = get_varieties_by_type(ft)
            print(f"  {ft}: {len(varieties)}개 — "
                  + ", ".join(v["name_kr"] for v in varieties))
        sys.exit(0)

    img_path = sys.argv[1]
    ft = sys.argv[2] if len(sys.argv) > 2 else None

    print(f"이미지 분석: {img_path}")
    if ft:
        print(f"화훼 종류 필터: {ft}")

    print()
    print(quick_classify(img_path, flower_type=ft))
    print()

    results = classify_variety(img_path, flower_type=ft, top_n=5)
    print("상위 후보:")
    for i, (name, conf, color) in enumerate(results, 1):
        print(f"  {i}. {name} — 신뢰도 {int(conf*100)}% (지배적 색상: {color})")
