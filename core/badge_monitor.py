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


def detect_badge_positions(image_path: Path) -> list[int]:
    """
    방 리스트 캡처 이미지에서 빨간 뱃지의 y좌표 목록을 반환.

    Args:
        image_path: 방 리스트 영역 캡처 이미지

    Returns:
        뱃지가 있는 y좌표 리스트 (이미지 내 상대 좌표, 위에서부터 순서)
    """
    img = Image.open(image_path).convert("RGB")
    width, height = img.size
    pixels = img.load()

    # 뱃지 스캔 영역 (우측 부분만)
    x_start = int(width * BADGE_X_RATIO_START)
    x_end = int(width * BADGE_X_RATIO_END)

    # y좌표별 빨간 픽셀 카운트
    red_counts: dict[int, int] = {}
    for y in range(height):
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
