"""
네노바 AI 에이전트 v2.1 진입점

Usage:
    python main.py           # 기본 감시 모드 (Phase 1.4~1.7)
    python main.py scan      # 방 리스트 재스캔 (Phase 1.1~1.2)
    python main.py select    # 감시 방 재선택 (Phase 1.3)
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).parent


def cmd_scan() -> int:
    """Phase 1.1 + 1.2: 카톡 창 감지 → 전체 방 리스트 스크롤 스캔 → Gemini OCR"""
    from core.window_detector import activate_kakaotalk, switch_to_chat_tab
    from core.room_scanner import scan_rooms_full, save_rooms

    print("[SCAN] 카카오톡 창 감지 중...")
    window = activate_kakaotalk()
    print(f"       창 위치: ({window.left},{window.top}) 크기: {window.width}x{window.height}")

    print("[SCAN] 채팅 탭 강제 전환 중...")
    switch_to_chat_tab(window)

    captures = ROOT / "captures"

    print("[SCAN] 전체 방 스크롤 스캔 시작...")
    rooms_data = scan_rooms_full(window, captures)
    out = save_rooms(rooms_data, ROOT / "data" / "rooms_detected.json")
    print(f"\n[SCAN] 총 {len(rooms_data)}개 방 감지 → {out.name}")

    for r in rooms_data:
        badge = f" ({r['unread']})" if r.get("unread") else ""
        print(f"       {r['order']}. {r['name']}{badge}")

    return 0


def cmd_select() -> int:
    """Phase 1.3: 체크박스 GUI로 감시 방 선택"""
    from core.room_selector_gui import main as gui_main
    gui_main()
    return 0


def cmd_mirror() -> int:
    """카카오워크에 미러 방 일괄 생성"""
    from core.kakaowork_router import create_all_mirror_rooms
    print("[MIRROR] 카카오워크 미러 방 생성 시작...")
    create_all_mirror_rooms()
    return 0


def cmd_monitor() -> int:
    """Phase 1.4~1.7 + 1.5: 감시 루프 (텍스트 + 사진 통합)"""
    import time
    import traceback
    from core.window_detector import capture_room_list
    from core.window_manager import (
        cleanup_popups, focus_kakaotalk, focus_kakaowork, return_to_kakaotalk,
    )
    from core.badge_monitor import detect_badge_positions, badge_y_to_absolute
    from core.message_extractor import extract_from_room
    from core.kakaowork_router import send_to_mirror_room
    from core.kakaowork_app import upload_to_nv_room
    from core.drawer_handler import extract_photos_from_room
    from core.status_overlay import get_overlay
    from core.issue_reporter import report_issue
    from core.gsheet_sync import classify_and_log_delta, process_admin_feedback

    POLL_INTERVAL = 5  # 초

    # 선택된 방 로드
    selected_file = ROOT / "data" / "selected_rooms.json"
    if not selected_file.exists():
        print("[ERROR] selected_rooms.json이 없습니다. 먼저 select를 실행하세요.")
        return 1

    # 상태 오버레이 시작
    overlay = get_overlay()

    print("[MONITOR] 네노바 AI 에이전트 v2.1 감시 모드 시작")
    print(f"          폴링 간격: {POLL_INTERVAL}초")

    # 초기화: 잔여 창 정리 → 카톡 활성화
    try:
        cleanup_popups()
        window = focus_kakaotalk()
    except Exception as e:
        print(f"[ERROR] 초기화 실패: {e}")
        return 1

    print(f"          창 위치: ({window.left},{window.top}) {window.width}x{window.height}")
    print("[MONITOR] 감시 시작... (중단: Ctrl+C 또는 마우스를 화면 모서리로)")
    print()

    captures_dir = ROOT / "captures"
    cycle = 0

    try:
        while True:
            cycle += 1
            overlay.set_idle()

            # ── 0. 매 사이클 시작: 잔여 창 정리 + 카톡 활성화 ──
            cleanup_popups()
            window = focus_kakaotalk()

            # ── 1. 방 리스트 캡처 + 뱃지 감지 ──
            img_path = capture_room_list(window, captures_dir / "monitor_current.png")
            badge_ys = detect_badge_positions(img_path)

            if not badge_ys:
                time.sleep(POLL_INTERVAL)
                continue

            overlay.set_working()
            print(f"[{cycle}] 뱃지 {len(badge_ys)}개 감지!")

            coords = badge_y_to_absolute(
                badge_ys,
                window.left, window.top,
                window.width, window.height,
            )

            # ── 2. 각 뱃지 방 처리 ──
            for i, (x, y) in enumerate(coords):
                try:
                    # 2-1. 카톡 활성화 → 방 클릭 → 텍스트 저장
                    focus_kakaotalk()
                    time.sleep(0.3)

                    print(f"     방 {i + 1}/{len(coords)} 처리 중 (클릭: {x},{y})...")
                    result = extract_from_room(x, y)

                    if not result:
                        print(f"     → 변경 없음 (이미 처리됨)")
                        continue

                    room_name = result["room_name"]
                    delta = result["delta"]       # 신규 내용만
                    content = result["content"]   # 전체 (사진 감지용)
                    print(f"     → {room_name}: 신규 {len(delta)}자 수집")

                    # 2-2. [사진] 감지 시 서랍에서 다운로드
                    downloaded_files = []
                    photo_count = delta.count("[사진]") + delta.count("[Photo]")
                    if photo_count > 0:
                        print(f"     → [사진] {photo_count}개 감지 — 서랍 열기...")
                        # 채팅방을 다시 열어야 함 (extract_from_room이 ESC로 닫았으므로)
                        focus_kakaotalk()
                        from core.message_extractor import click_room
                        click_room(x, y)
                        time.sleep(0.5)

                        import win32gui
                        chat_hwnd = win32gui.GetForegroundWindow()
                        downloaded_files = extract_photos_from_room(chat_hwnd, photo_count=photo_count)

                        if downloaded_files:
                            print(f"     → {len(downloaded_files)}개 사진 다운로드 완료")
                        else:
                            print(f"     → 사진 다운로드 실패/없음")

                        # 채팅방 닫기
                        from core.message_extractor import close_chat_room
                        close_chat_room()

                    # 2-3. 구글시트에 분류 기록
                    try:
                        logged = classify_and_log_delta(room_name, delta)
                        if logged:
                            print(f"     → 구글시트 {logged}건 기록")
                    except Exception as e:
                        print(f"     → 시트 기록 실패: {e}")

                    # 2-4. 카카오워크 미러 방에 신규 내용만 전송 (Bot API)
                    send_to_mirror_room(room_name, delta)
                    print(f"     → 워크 미러 방 신규 내용 전송 완료")

                    # 2-4. 사진이 있으면 카카오워크 앱으로 업로드
                    if downloaded_files:
                        print(f"     → 워크 앱 이미지 업로드 중...")
                        try:
                            focus_kakaowork()
                            upload_to_nv_room(room_name, downloaded_files)
                            print(f"     → 이미지 업로드 완료")
                        except Exception as e:
                            report_issue(
                                "워크 이미지 업로드 실패",
                                f"방: {room_name}\n파일: {[f.name for f in downloaded_files]}\n에러: {e}",
                            )
                        finally:
                            return_to_kakaotalk()

                except Exception as e:
                    error_detail = (
                        f"방 처리 중 에러 (y={badge_ys[i]})\n"
                        f"{traceback.format_exc()}"
                    )
                    report_issue(f"방 처리 에러 ({badge_ys[i]})", error_detail)

            # ── 3. 사이클 마무리 ──
            # 관리자 수정분 체크 → 패턴 학습 (10사이클마다)
            if cycle % 10 == 0:
                try:
                    learned = process_admin_feedback()
                    if learned:
                        print(f"[LEARN] 관리자 수정 {learned}건 → 패턴 업데이트")
                except Exception as e:
                    print(f"[LEARN] 피드백 체크 실패: {e}")

            cleanup_popups()
            focus_kakaotalk()
            time.sleep(POLL_INTERVAL)

    except KeyboardInterrupt:
        print("\n[MONITOR] Ctrl+C 감지. 감시 종료.")
        overlay.stop()
        return 0


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        return cmd_monitor()

    cmd = argv[1].lower()
    if cmd == "scan":
        return cmd_scan()
    elif cmd == "select":
        return cmd_select()
    elif cmd == "mirror":
        return cmd_mirror()
    else:
        print(f"[ERROR] 알 수 없는 명령: {cmd}")
        print(__doc__)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
