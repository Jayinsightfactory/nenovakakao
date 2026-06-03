"""
Phase 1.4: 빨간 뱃지 감시

방 리스트 캡처 이미지에서 빨간 뱃지(읽지 않은 메시지 표시)를 픽셀 색상으로 감지.
OCR/API 없이 순수 이미지 처리로 동작.
"""
from __future__ import annotations

from pathlib import Path

import pyautogui
from PIL import Image

# 빨간 뱃지 색상 범위 (RGB)
# 카카오톡 뱃지: 밝은 빨강~주홍
RED_MIN = (180, 0, 0)
RED_MAX = (255, 100, 100)

# 뱃지가 나타나는 영역: 방 리스트의 우측 20%
BADGE_X_RATIO_START = 0.70
BADGE_X_RATIO_END = 0.95

# ── 상위 N개 행만 보기 (광고는 하단 고정이므로 자연스레 제외) ──
# 카톡은 신규 메시지 방을 항상 맨 위로 올리므로 상위 5~7개만 봐도 충분.
TOP_ROWS = 9      # 페이지당 광고 제외 9행 (사용자 확정값)
ROW_HEIGHT = 65   # 방 한 행의 평균 높이 (px) - 캡처 이미지 기준
AD_BOTTOM_PX = 100  # 카톡 창 하단 N px = 광고 영역 (절대 클릭 금지)

# (호환성용) 캡처 이미지에서 무시할 하단 비율 — 폴백
BADGE_Y_MAX_RATIO = 0.88

# 최소 빨간 픽셀 수 (노이즈 제거)
MIN_RED_PIXELS_PER_CLUSTER = 15

# 같은 방으로 간주할 y좌표 거리 (픽셀)
Y_CLUSTER_THRESHOLD = 30


def _is_red(r: int, g: int, b: int) -> bool:
    """픽셀이 빨간 뱃지 색상인지 판별"""
    return (
        RED_MIN[0] <= r <= RED_MAX[0]
        and RED_MIN[1] <= g <= RED_MAX[1]
        and RED_MIN[2] <= b <= RED_MAX[2]
    )


def detect_badge_positions(
    image_path: Path,
    *,
    y_max_ratio: float | None = None,
    top_rows: int | None = None,
    row_height: int | None = None,
) -> list[int]:
    """
    방 리스트 캡처 이미지에서 빨간 뱃지의 y좌표 목록을 반환.

    Args:
        image_path: 방 리스트 영역 캡처 이미지
        y_max_ratio: 스캔 영역 하한 비율 (폴백, BADGE_Y_MAX_RATIO=0.88)
        top_rows: 상위 N개 행만 본다 (기본 TOP_ROWS=5)
        row_height: 방 한 행의 평균 높이 (기본 ROW_HEIGHT=65)

    상위 N개 행만 보는 이유:
      - 카톡은 신규 메시지 방을 항상 맨 위로 올림
      - 광고 배너는 항상 하단 고정 → 상위만 보면 절대 안 잡힘

    Returns:
        뱃지가 있는 y좌표 리스트 (이미지 내 상대 좌표, 위에서부터 순서)
    """
    img = Image.open(image_path).convert("RGB")
    width, height = img.size
    pixels = img.load()

    # 뱃지 스캔 영역 (우측 부분만)
    x_start = int(width * BADGE_X_RATIO_START)
    x_end = int(width * BADGE_X_RATIO_END)

    # ── 상위 N개 행 제한 (1차 우선 — 광고 영역 자연 배제) ──
    n_rows = top_rows if top_rows is not None else TOP_ROWS
    rh = row_height if row_height is not None else ROW_HEIGHT
    rows_limit = n_rows * rh
    # 비율 폴백
    ratio_limit = int(height * (y_max_ratio if y_max_ratio is not None else BADGE_Y_MAX_RATIO))
    y_limit = min(rows_limit, ratio_limit, height)

    # y좌표별 빨간 픽셀 카운트
    red_counts: dict[int, int] = {}
    for y in range(y_limit):
        count = 0
        for x in range(x_start, x_end):
            r, g, b = pixels[x, y]
            if _is_red(r, g, b):
                count += 1
        if count > 0:
            red_counts[y] = count

    # 클러스터링: 인접한 y좌표들을 하나의 뱃지로 묶기
    if not red_counts:
        return []

    sorted_ys = sorted(red_counts.keys())
    clusters: list[list[int]] = []
    current_cluster: list[int] = [sorted_ys[0]]

    for y in sorted_ys[1:]:
        if y - current_cluster[-1] <= 3:  # 인접한 줄
            current_cluster.append(y)
        else:
            clusters.append(current_cluster)
            current_cluster = [y]
    clusters.append(current_cluster)

    # 각 클러스터의 중앙 y좌표 + 최소 픽셀 수 필터
    badge_ys = []
    for cluster in clusters:
        total_red = sum(red_counts[y] for y in cluster)
        if total_red >= MIN_RED_PIXELS_PER_CLUSTER:
            center_y = cluster[len(cluster) // 2]
            badge_ys.append(center_y)

    return badge_ys


def _is_blue(r: int, g: int, b: int) -> bool:
    """카카오워크 안읽음 뱃지(밝은 파랑 ~RGB(0,120,240)) 판별."""
    return b >= 180 and b > r + 60 and 60 <= g <= 200 and r <= 140


def detect_blue_badge_rows(
    image_path: Path,
    *,
    x_ratio_start: float = 0.80,
    x_ratio_end: float = 1.0,
    min_blue: int = 25,
    top_skip_px: int = 5,
    bottom_skip_px: int = 5,
) -> list[int]:
    """카카오워크 룸목록 crop 에서 '파란 안읽음 뱃지' y좌표(crop 내 상대) 반환.

    워크 뱃지는 룸목록 우측 끝 파란 숫자원. detect_unread_badge_rows(빨강)의 워크판.
    capture_region('kakaowork_roomlist') crop 이미지에 사용.
    """
    img = Image.open(image_path).convert("RGB")
    width, height = img.size
    pixels = img.load()
    x_start = int(width * x_ratio_start)
    x_end = min(width, int(width * x_ratio_end))
    y_top = max(0, top_skip_px)
    y_bot = max(y_top + 1, height - bottom_skip_px)

    rc: dict[int, int] = {}
    for y in range(y_top, y_bot):
        c = 0
        for x in range(x_start, x_end):
            r, g, b = pixels[x, y]
            if _is_blue(r, g, b):
                c += 1
        if c > 0:
            rc[y] = c
    if not rc:
        return []
    sorted_ys = sorted(rc.keys())
    clusters: list[list[int]] = []
    cur = [sorted_ys[0]]
    for y in sorted_ys[1:]:
        if y - cur[-1] <= 10:
            cur.append(y)
        else:
            clusters.append(cur)
            cur = [y]
    clusters.append(cur)
    out = []
    for cl in clusters:
        if sum(rc[y] for y in cl) >= min_blue:
            out.append(cl[len(cl) // 2])
    return out


def detect_unread_badge_rows(
    image_path: Path,
    *,
    x_ratio_start: float = 0.90,
    x_ratio_end: float = 0.99,
    min_red: int = 30,
    top_skip_px: int = 20,
    bottom_skip_px: int = 110,
) -> list[int]:
    """안읽음 탭 룸리스트 캡처에서 '안읽음 뱃지(우측 끝 빨간 숫자원)' y좌표만 반환.

    detect_badge_positions 보다 우측으로 좁혀(프로필 사진 빨강 배제) + 상단 잔상/
    하단 광고 제외. 모니터가 '뱃지 있는 행만 클릭'하도록 쓰는 정밀 버전.

    Args:
        x_ratio_start/end: 뱃지가 있는 우측 폭 비율 (0.90~0.99 = 맨 오른쪽)
        min_red: 뱃지로 인정할 최소 빨간픽셀 합
        top_skip_px / bottom_skip_px: 상단 잔상 / 하단 광고 영역 제외(px)

    Returns:
        뱃지 중심 y좌표 리스트(이미지 내 상대, 위→아래 순)
    """
    img = Image.open(image_path).convert("RGB")
    width, height = img.size
    pixels = img.load()
    x_start = int(width * x_ratio_start)
    x_end = int(width * x_ratio_end)
    y_top = max(0, top_skip_px)
    y_bot = max(y_top + 1, height - bottom_skip_px)

    red_counts: dict[int, int] = {}
    for y in range(y_top, y_bot):
        c = 0
        for x in range(x_start, x_end):
            r, g, b = pixels[x, y]
            if _is_red(r, g, b):
                c += 1
        if c > 0:
            red_counts[y] = c
    if not red_counts:
        return []

    sorted_ys = sorted(red_counts.keys())
    clusters: list[list[int]] = []
    cur: list[int] = [sorted_ys[0]]
    for y in sorted_ys[1:]:
        if y - cur[-1] <= 8:
            cur.append(y)
        else:
            clusters.append(cur)
            cur = [y]
    clusters.append(cur)

    out: list[int] = []
    for cl in clusters:
        total = sum(red_counts[y] for y in cl)
        if total >= min_red:
            out.append(cl[len(cl) // 2])
    return out


def badge_y_to_absolute(
    badge_ys: list[int], window_left: int, window_top: int,
    window_width: int, window_height: int,
    room_list_left_ratio: float = 0.12,
    room_list_top_ratio: float = 0.11,
) -> list[tuple[int, int]]:
    """
    이미지 내 뱃지 y좌표를 화면 절대 좌표(x, y)로 변환.
    클릭할 좌표 = 방 리스트 영역의 중앙 x, 뱃지의 y.

    Returns:
        [(절대x, 절대y), ...] 리스트
    """
    room_left = window_left + int(window_width * room_list_left_ratio)
    room_top = window_top + int(window_height * room_list_top_ratio)
    room_right = window_left + window_width

    click_x = (room_left + room_right) // 2  # 방 이름 중앙 클릭

    absolute_coords = []
    for y in badge_ys:
        abs_y = room_top + y
        absolute_coords.append((click_x, abs_y))

    return absolute_coords


if __name__ == "__main__":
    import sys

    # 스탠드얼론 테스트
    test_image = Path("captures/kakaotalk_rooms.png")
    if not test_image.exists():
        print("[ERROR] captures/kakaotalk_rooms.png 없음. 먼저 scan 실행하세요.")
        sys.exit(1)

    print("[TEST] 빨간 뱃지 감지 중...")
    positions = detect_badge_positions(test_image)
    print(f"[결과] {len(positions)}개 뱃지 감지")
    for i, y in enumerate(positions):
        print(f"  뱃지 {i + 1}: y={y}")
