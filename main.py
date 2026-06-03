"""
네노바 AI 에이전트 v2.1 진입점

Usage:
    python main.py                          # 기본 감시 모드 (Phase 1.4~1.7)
    python main.py run                      # 원트리거: 전체 파이프라인 1회 실행
    python main.py scan                     # 방 리스트 재스캔 (Phase 1.1~1.2)
    python main.py select                   # 감시 방 재선택 (Phase 1.3)
    python main.py mirror                   # 카카오워크 미러 방 일괄 생성
    python main.py cleanup-mirrors          # 중복 미러 방 리네이밍 + mapping 정리
    python main.py cleanup-mirrors --dry-run  # 변경 없이 탐지만
    python main.py cleanup-mirrors --ui     # 리네이밍 후 카카오워크 앱에서 나가기까지
    python main.py learn                    # 학습 녹화 (전체 화면 + 이벤트 로그)
    python main.py anchors                  # 앵커 후보 GUI 확인/승인
    python main.py auto-anchor --commit     # 반복 후보를 자동 클러스터링해 승인
    python main.py metrics [--gui]          # 스텝별 성공률/재시도 메트릭
    python main.py unlock <step> | --all    # 앵커 검증 락 해제 (재학습 대상으로)
    python main.py calibrate                # learn → auto-anchor → metrics 1사이클
    python main.py learn-uploads [24|all]   # 업로드 실패 ledger 요약 (기본 24시간)
"""
from __future__ import annotations

import sys
from pathlib import Path

# Windows cp949 터미널에서 한글/이모지/기타 유니코드 출력 안전화
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).parent

# 시각 디버깅 자동 활성 (환경변수 또는 직접 import)
try:
    from core import visual_debug  # noqa: F401
except Exception:
    pass


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


def _process_room_result(
    result: dict,
    x: int, y: int,
    *,
    focus_kakaotalk,
    focus_kakaowork,
    return_to_kakaotalk,
    send_to_mirror_room,
    extract_photos_from_room,
    classify_and_log_delta,
    report_issue,
):
    """방 하나의 처리 결과를 미러링+분석하는 공통 로직"""
    import time

    import os as _os

    room_name = result["room_name"]
    delta = result["delta"]

    # 정지 요청이면 즉시 반환 (자동화 시작 안 함)
    try:
        from core.stop_button import is_stop_requested as _sr
    except Exception:
        _sr = lambda: False
    if _sr():
        print(f"  [STOP] {room_name} 처리 전 정지 요청 — 스킵", flush=True)
        return

    # 작업 시작 배너
    print(f"\n  ┌─ 🔧 작업방: [{room_name}] 신규 {len(delta)}자 ─")

    from core.kakaowork_router import count_photo_messages
    downloaded_files = []
    photo_count = count_photo_messages(delta)

    # 사진 다운로드 스킵 토글 — 드로어(서랍) 저장이 불안정하면 사진 단계가
    # 무한 재시도하며 락을 오래 쥐어 답장·텍스트미러를 막는다.
    # NENOVA_SKIP_PHOTOS=1 이면 텍스트만 미러(안정), 사진은 건너뜀.
    if _os.environ.get("NENOVA_SKIP_PHOTOS") == "1" and photo_count > 0:
        print(f"     → [사진] {photo_count}개 감지 — NENOVA_SKIP_PHOTOS=1 → 사진 스킵(텍스트만)", flush=True)
        photo_count = 0

    # ── 첫 가동/대량 baseline 가드 ──
    # 첫 monitor 가동 시 카톡 전체 히스토리가 통째로 "신규(delta)"로 인식되어
    # 미러 송신 폭주 + 사진 수천 장 다운로드. read_and_process_saved_file 이 이미
    # _save_last_content 로 캐시를 갱신했으므로 여기서 송신만 스킵하면 다음 사이클부터
    # 진짜 신규분만 처리됨. NENOVA_SEND_ALL=1 로 강제 전체 송신 가능.
    #   - is_first_seen: 이 방을 monitor 가 처음 본 경우 (캐시 없음) → 무조건 baseline
    #     (크기 무관 — 6800자 같은 중간 크기 첫 히스토리도 차단)
    #   - 추가 안전망: 캐시 있어도 delta 가 비정상 과대면 baseline
    BASELINE_CHAR_LIMIT = 8000
    BASELINE_PHOTO_LIMIT = 30
    if not _os.environ.get("NENOVA_SEND_ALL"):
        is_first = result.get("is_first_seen", False)
        if is_first or len(delta) > BASELINE_CHAR_LIMIT or photo_count > BASELINE_PHOTO_LIMIT:
            reason = "첫 처리(캐시없음)" if is_first else f"delta {len(delta)}자/사진 {photo_count}장 과대"
            print(f"  [BASELINE] {room_name}: {reason} → 송신 스킵 "
                  f"(캐시 갱신됨, 다음 사이클부터 신규분만 송신)", flush=True)
            return
    if photo_count > 0 and _sr():
        print(f"  [STOP] {room_name} 사진 다운로드 전 정지 요청 — 사진 스킵", flush=True)
        photo_count = 0
    if photo_count > 0:
        print(f"     → [사진] {photo_count}개 감지 - 서랍 열기...")
        import win32gui
        norm_room = room_name.replace(" ", "")

        # 이미 분리창이 열려 있으면 click_room 호출 스킵 (더블클릭 토글로 닫히는 걸 방지)
        def _has_existing_separate() -> bool:
            found = False
            def _cb(h, _):
                nonlocal found
                if found or not win32gui.IsWindow(h):
                    return
                t = win32gui.GetWindowText(h) or ""
                if not t or t == "카카오톡":
                    return
                nt = t.replace(" ", "")
                if nt == norm_room or norm_room in nt or nt in norm_room:
                    found = True
            win32gui.EnumWindows(_cb, None)
            return found

        focus_kakaotalk()
        if _has_existing_separate():
            print(f"     → 기존 분리창 재사용 (재클릭 스킵)")
            time.sleep(0.3)
        else:
            from core.message_extractor import click_room
            click_room(x, y)
            time.sleep(1.2)

        # ── 채팅방 hwnd 찾기 (3단계 폴백) ──
        # 1. foreground 검증 (분리창 모드면 OK)
        # 2. 모든 카톡 자식창 enumerate해서 제목 매칭
        # 3. 카톡 메인 창 자체 (단일창 모드: 채팅방이 메인창 내부 패널)
        chat_hwnd = None
        chat_title = ""

        fg = win32gui.GetForegroundWindow()
        fg_title = win32gui.GetWindowText(fg) if fg else ""
        norm_fg = fg_title.replace(" ", "")
        if (fg and fg_title and fg_title != "카카오톡"
                and (norm_fg == norm_room or norm_room in norm_fg or norm_fg in norm_room)):
            chat_hwnd = fg
            chat_title = fg_title
            print(f"     → 분리창 발견: '{chat_title}' (hwnd={chat_hwnd})")
        else:
            # 모든 창 중 카톡 방 제목 매칭 — 최소화 포함 (포커스 뺏겨서 숨은 경우)
            # IsWindowVisible 은 최소화된 창도 True 반환. IsIconic 으로 최소화 체크.
            cands = []
            def _enum(h, lst):
                if not win32gui.IsWindow(h):
                    return
                t = win32gui.GetWindowText(h)
                if not t or t == "카카오톡":
                    return
                nt = t.replace(" ", "")
                if nt == norm_room or norm_room in nt or nt in norm_room:
                    iconic = win32gui.IsIconic(h)
                    visible = win32gui.IsWindowVisible(h)
                    lst.append((h, t, iconic, visible))
            win32gui.EnumWindows(_enum, cands)
            if cands:
                # visible + non-minimized 우선 → visible + minimized → 기타
                cands.sort(key=lambda c: (not c[3], c[2]))
                h, t, iconic, visible = cands[0]
                chat_hwnd, chat_title = h, t
                if iconic:
                    print(f"     → 분리창 최소화 복원: '{t}' (hwnd={h})")
                    import win32con as _wc
                    try:
                        win32gui.ShowWindow(h, _wc.SW_RESTORE)
                        time.sleep(0.3)
                    except Exception as e:
                        print(f"     → 복원 실패 ({e}) — 계속 진행")
                print(f"     → 분리창(enum): '{chat_title}' (hwnd={chat_hwnd}) "
                      f"[visible={visible}, iconic={iconic}]")
            else:
                # 단일창 모드 — 카톡 메인 창 자체를 채팅방으로 취급
                # ⚠ 단일창 모드에서는 ≡ 메뉴가 메인창에 없음 → 서랍 열기 실패 확실
                # 이 경우 사진 스킵하고 텍스트만 전송하도록 chat_hwnd=None 유지
                kakao_results = []
                def _find_kakao(h, lst):
                    if win32gui.IsWindowVisible(h) and win32gui.GetWindowText(h) == "카카오톡":
                        lst.append(h)
                win32gui.EnumWindows(_find_kakao, kakao_results)
                if kakao_results:
                    # 분리창이 없으면 더블클릭 한 번 더 시도 — KakaoTalk 이 분리창 자동 생성
                    print(f"     → 분리창 없음, 재-더블클릭으로 분리창 유도 시도", flush=True)
                    try:
                        import pyautogui as _pag
                        _pag.doubleClick(x, y)
                        time.sleep(1.5)
                        # 재탐색
                        cands2 = []
                        win32gui.EnumWindows(_enum, cands2)
                        if cands2:
                            cands2.sort(key=lambda c: (not c[3], c[2]))
                            h2, t2, ic2, vis2 = cands2[0]
                            if ic2:
                                import win32con as _wc
                                win32gui.ShowWindow(h2, _wc.SW_RESTORE)
                                time.sleep(0.3)
                            chat_hwnd, chat_title = h2, t2
                            print(f"     → 재시도 성공: '{chat_title}' (hwnd={chat_hwnd})")
                    except Exception as e:
                        print(f"     → 재-더블클릭 실패: {e}")

                if not chat_hwnd:
                    # 여전히 분리창 없음 — 단일창 모드로 간주
                    # 이 모드에서는 서랍 열기 불가 (≡가 메인창에 노출 안 됨)
                    # → chat_hwnd=None 유지해서 사진 스킵, 텍스트만 진행
                    print(f"     → 분리창 생성 불가 — 사진 다운로드 스킵 (텍스트는 정상)")
                    # chat_hwnd 는 None 으로 남겨둠

        if not chat_hwnd:
            print(f"     → 사진 스킵: 어떤 창도 못 찾음 (foreground='{fg_title}')")
        else:
            # 분리창 위치 고정 (좌표 자동화 안정화)
            try:
                from core.window_manager import fix_chat_window_position
                if chat_title != "카카오톡":  # 메인창은 건드리지 않음
                    fix_chat_window_position(chat_hwnd)
            except Exception as e:
                print(f"     → 분리창 고정 실패: {e}")
            downloaded_files = extract_photos_from_room(
                chat_hwnd,
                photo_count=photo_count,
                room_name=room_name,  # breadcrumb OCR 검증 + 좌측 재선택
            )

        if downloaded_files:
            print(f"     → {len(downloaded_files)}개 사진 다운로드 완료")
        else:
            print(f"     → 사진 다운로드 실패/없음 (요청 {photo_count}장)")

        from core.message_extractor import close_chat_room
        close_chat_room()

        # 모달 stuck 잔존 다이얼로그/뷰어 강제 청소 (다음 방 깨끗한 상태)
        try:
            import win32gui as _w32, win32con as _wc
            _DLG_KEYS = ("다른 이름으로 저장", "Save As", "폴더 선택",
                          "Select Folder", "Browse For Folder")
            def _purge(h, _):
                if not _w32.IsWindow(h): return
                t = _w32.GetWindowText(h) or ""
                cls = _w32.GetClassName(h) or ""
                # 다이얼로그 / 뷰어 / 서랍 모두 정리 (분리창은 이미 close_chat_room 완료)
                kill = False
                if any(k in t for k in _DLG_KEYS):
                    kill = True
                elif "EVA_Window_Dblclk" in cls and any(yr in t for yr in ("2026-", "2025-")):
                    kill = True
                elif "채팅방 서랍" in t:
                    kill = True
                if kill:
                    try:
                        _w32.PostMessage(h, _wc.WM_CLOSE, 0, 0)
                        # 화면 밖 이동 (close 무시 대비)
                        r = _w32.GetWindowRect(h)
                        _w32.MoveWindow(h, -3000, -3000, r[2]-r[0], r[3]-r[1], False)
                    except Exception:
                        pass
            _w32.EnumWindows(_purge, None)
            time.sleep(0.3)
        except Exception as e:
            print(f"     → 잔존 정리 예외 (무시): {e}")

    # ── 구글시트에 분류 기록 ──
    try:
        logged = classify_and_log_delta(room_name, delta)
        if logged:
            print(f"     → 구글시트 {logged}건 기록")
    except Exception as e:
        print(f"     → 시트 기록 실패: {e}")

    # ── 카카오워크 미러 방에 **시간순 교차** 전송 ──
    # 텍스트는 Bot API, 사진은 카카오워크 앱 Ctrl+T 업로드.
    # 각 사진 직전에 "[발신자] [시각] [사진]" 헤더를 Bot API로 선전송 →
    # 도착시간·발신자·첨부파일이 실제 카톡 순서대로 미러 방에 표시됨.
    from core.kakaowork_router import send_delta_interleaved
    r = None
    try:
        if downloaded_files:
            focus_kakaowork()
        r = send_delta_interleaved(room_name, delta, downloaded_files)
        print(
            f"     → 워크 전송 완료: 텍스트 {r['text_sent']}건 / "
            f"사진 {r['photos_uploaded']}장 "
            f"(누락 {r['photos_missing']}, 꼬리 {r.get('trailing_uploaded', 0)})"
        )
    except Exception as e:
        report_issue(
            "워크 교차 전송 실패",
            f"방: {room_name}\n파일: {[f.name for f in downloaded_files]}\n에러: {e}",
        )
    finally:
        if downloaded_files:
            return_to_kakaotalk()

    # 작업 완료 배너 — 한 줄 요약
    if r:
        status = "✅" if r['photos_missing'] == 0 else "⚠️"
        print(
            f"  └─ {status} [{room_name}] 감지:{photo_count} 다운:{len(downloaded_files)} "
            f"워크텍스트:{r['text_sent']} 워크사진:{r['photos_uploaded']} 누락:{r['photos_missing']}\n"
        )
    else:
        print(f"  └─ ❌ [{room_name}] 워크 전송 실패\n")

    return room_name


def cmd_monitor(*, with_recorder: bool = False) -> int:
    """Phase 1.4~1.7 + 1.5: 감시 루프 (텍스트 + 사진 통합).

    with_recorder=True: LearningRecorder를 가동하여 모든 자동화 마크를 PNG로 누적.
    뱃지·드로어·업로드 동작이 일어날 때마다 data/anchor_candidates/<session>/에 저장.
    """
    import ctypes
    import json
    import time
    import traceback

    # ── 액션 로그 창 + 키보드/마우스 후크 설치 (감시 최우선) ──
    from core.action_logger import get_logger, log as _log, install_pyautogui_hooks
    get_logger()  # 로그 창 띄우기
    install_pyautogui_hooks()
    _log("감시 모드 시작", "INFO")

    if with_recorder:
        from datetime import datetime
        from core.learning_recorder import LearningRecorder, set_recorder
        sess = f"monitor_{datetime.now():%Y%m%d_%H%M%S}"
        rec = LearningRecorder(sess, fps=2)  # 2fps면 디스크 부담 적음
        set_recorder(rec)
        rec.start()

    # Windows: 다른 프로세스 창 활성화 허용
    ctypes.windll.user32.AllowSetForegroundWindow(-1)
    from core.window_detector import (
        capture_room_list, scroll_room_list_to_top,
    )
    from core.window_manager import (
        cleanup_popups, focus_kakaotalk, focus_kakaowork, return_to_kakaotalk,
        get_room_list_click_y_offset,
    )
    from core.badge_monitor import detect_badge_positions, badge_y_to_absolute
    from core.message_extractor import extract_from_room
    from core.kakaowork_router import send_to_mirror_room, send_delta_interleaved
    # upload_to_nv_room은 send_delta_interleaved 내부에서 lazy import
    # ── 사진: 검증된 서랍 '저장' 경로 (5/7 방식) ──
    # 카톡 cache 는 암호화된 .cng (AES-128-CBC) 라 직접 복사 시 표시 불가.
    # 서랍 '저장' 은 카톡이 복호화해서 내보내므로 실제 이미지가 나온다.
    # 레이아웃 기반 일괄 다운로드만 사용 (legacy 드로어 검증/좌측 스크롤 제거).
    # 파일명/저장 다이얼로그 멈춤은 drawer_handler 수정(f4dba71)으로 완화됨.
    from core.drawer_layout_auto import (
        extract_photos_from_chat_via_layout,
        LAYOUT_FILE,
    )

    def extract_photos_from_room(chat_hwnd, photo_count=0, room_name=""):
        """layout 기반만 사용. 실패 시 빈 리스트 반환 (서랍 스크롤 없음)."""
        if not LAYOUT_FILE.exists():
            print(f"     → layout 파일 없음, 사진 스킵 (measure_drawer_layout.py 실행 필요)", flush=True)
            return []
        try:
            return extract_photos_from_chat_via_layout(
                chat_hwnd, photo_count=photo_count, room_name=room_name,
            )
        except Exception as e:
            print(f"     → 레이아웃 기반 에러({e}), 사진 스킵", flush=True)
            return []
    from core.status_overlay import get_overlay
    from core.issue_reporter import report_issue
    from core.gsheet_sync import classify_and_log_delta, process_admin_feedback

    POLL_INTERVAL = 2       # 초 (5→2로 단축, 반응 속도 2.5배)
    SWEEP_EVERY = 3         # N사이클마다 hash 체크 (3 x 2초 = 6초) - 변화 없으면 스윕 스킵
                            # 안읽음 탭 상위 5행을 빨간 뱃지 유무 무관하게 강제 클릭 →
                            # 우리가 이미 처리해서 뱃지가 사라진 방, 모바일에서 읽어
                            # 뱃지 사라진 방, 인원수만 표시된 방 모두 누락 방지.
                            # 변경 없으면 ESC로 즉시 닫혀 0.5초 만에 다음 방으로 넘어감.
    ROOM_ROW_HEIGHT = 60    # 카톡 방 리스트 행 높이 (px) - 보수적으로 줄임
    SWEEP_STEP = 50         # 스윕 시 y 이동 간격 (행 높이보다 작게 → 빠짐 방지)

    # 공통 처리 함수에 넘길 의존성
    # (send_messages_individually / upload_to_nv_room은 send_delta_interleaved
    #  내부에서 처리되므로 더 이상 전달하지 않음)
    process_deps = dict(
        focus_kakaotalk=focus_kakaotalk,
        focus_kakaowork=focus_kakaowork,
        return_to_kakaotalk=return_to_kakaotalk,
        send_to_mirror_room=send_to_mirror_room,
        extract_photos_from_room=extract_photos_from_room,
        classify_and_log_delta=classify_and_log_delta,
        report_issue=report_issue,
    )

    # 시작 시 mapping ↔ selected 자동 동기화 (Phase 1)
    try:
        from core.room_sync import sync_selected_from_mapping
        sync_selected_from_mapping()
    except Exception as e:
        print(f"[ROOM-SYNC] 시작 동기화 실패: {e}")

    # 선택된 방 로드
    selected_file = ROOT / "data" / "selected_rooms.json"
    if not selected_file.exists():
        print("[ERROR] selected_rooms.json이 없습니다. 먼저 select를 실행하세요.")
        return 1

    with open(selected_file, encoding="utf-8") as f:
        selected_rooms = json.load(f)
    selected_names = {r["name"] for r in selected_rooms}

    # 상태 오버레이 시작 (NENOVA_NO_OVERLAY=1 이면 stub — 자동화 오클릭 방지)
    overlay = get_overlay()

    # 🛑 정지 버튼은 액션로그 창(get_logger)에 통합되어 있다.
    # (별도 Tk 창은 Tcl_AsyncDelete 크래시 유발 → 단일 Tk 루트로 통합)
    # 버튼 클릭 → core.stop_button.request_stop() → 플래그+_STOP 파일 →
    # 루프가 _stop_requested() 로 감지.
    # ⚠️ _STOP 은 멀티프로세스 공용 정지 latch다. '모니터를 새로 시작 = 재개 의도'로
    #    보고 여기서 한 번만 해제한다. (개별 프로세스가 제각각 지우면 P0 충돌 → clear_stop
    #    단일 진입점으로 통일.) work_bridge/답장서버는 자기 시작 시 latch 를 지우지 않음.
    try:
        from core.stop_button import clear_stop
        clear_stop()
    except Exception:
        pass

    print("[MONITOR] 네노바 AI 에이전트 v2.1 감시 모드 시작")
    print(f"          폴링 간격: {POLL_INTERVAL}초 / 전체 스윕: 매 {SWEEP_EVERY}사이클")
    print(f"          감시 대상: {len(selected_names)}개 방")
    # 방 목록 명시적 출력
    print(f"\n{'='*60}")
    print(f"[작업 대상 방 {len(selected_names)}개]")
    print(f"{'='*60}")
    for i, name in enumerate(sorted(selected_names), 1):
        print(f"  {i:>2}. {name}")
    print(f"{'='*60}\n")

    # 초기화: 잔여 창 정리 → 카톡 활성화
    try:
        cleanup_popups()
        window = focus_kakaotalk()
    except Exception as e:
        print(f"[ERROR] 초기화 실패: {e}")
        return 1

    # 안읽음 탭 강제 클릭: 방 리스트를 안읽은 방으로만 축소 → 스크롤 효율↑
    # 900x900 화면 기준 "안읽음" 필터 버튼 위치 (캡처로 확인: (190, 96) 근처)
    try:
        import pyautogui as _pag
        _pag.click(window.left + 190, window.top + 96)
        time.sleep(0.5)
        print(f"[MONITOR] '안읽음' 탭 클릭 (190, 96) — 안 읽은 방만 표시")
    except Exception as e:
        print(f"[MONITOR] 안읽음 탭 클릭 실패 (무시): {e}")

    print(f"          창 위치: ({window.left},{window.top}) {window.width}x{window.height}")
    print("[MONITOR] 감시 시작... (중단: Ctrl+C 또는 마우스를 화면 모서리로)")
    print()

    import os as _os

    captures_dir = ROOT / "captures"
    cycle = 0

    # 새 방 자동 채택 스캔 주기 (Claude Vision 으로 전체 목록 읽어 새 그룹방 미러 생성)
    # 기본 OFF (NENOVA_NEWROOM_SCAN=1 로 활성). 실시간 win32 채택이 주력이라 보강용.
    NEWROOM_SCAN_INTERVAL = 5 * 3600  # 5시간마다
    last_newroom_scan_ts = time.time()  # 첫 스캔은 +5시간 후

    # 서킷브레이커: 카톡 방이 연속으로 안 열리면(화면잠금/로그아웃/창최소화) 무한
    # 헛클릭을 막기 위해 자동 정지한다. 2026-05-22 야간 17.5시간 폭주 재발 방지.
    consecutive_dead_cycles = 0
    DEAD_CYCLE_MIN_MISSES = 3   # 한 사이클에 방 미열림이 이만큼 이상 발생 +
    MAX_DEAD_CYCLES = 3         # 그런 '완전 실패' 사이클이 연속 이만큼이면 자동 정지

    # 동일 좌표 반복 차단: {y_절대좌표: (마지막사이클, 연속실패횟수)}
    # 같은 y에서 3회 연속 "변경 없음" 시 5사이클 동안 차단 (광고/스팸 영역 방지)
    coord_failures: dict[int, tuple[int, int]] = {}
    COORD_FAIL_THRESHOLD = 3      # 연속 실패 N회 후 차단
    COORD_BLOCK_CYCLES = 5         # 차단 지속 사이클 수

    # 방 리스트 변화 감지: 픽셀 hash. 변화 없으면 사이클 전체 스킵 (idle).
    last_room_list_hash: str = ""

    # 주기적 동기화 카운터
    SYNC_MAPPING_EVERY = 360    # 30분마다 mapping ↔ selected 재동기화
    DISCOVER_ROOMS_EVERY = 720  # 1시간마다 카톡 OCR로 신규 방 발견
    FORCE_SWEEP_EVERY = 60      # 5분마다 idle 가드 무관 강제 풀 스윕 (안전망)

    # Claude Computer Use 자율 회복
    last_cu_recover_ts: float = 0.0
    CU_RECOVER_COOLDOWN = 60        # 1분 cooldown (5분 → 1분 단축)
    guard_fail_streak: int = 0      # 가드 실패 연속 카운트
    GUARD_FAIL_TRIGGER = 3          # 가드 N회 연속 실패 시 강제 recover

    def _compute_room_list_hash(win) -> str:
        try:
            from PIL import ImageGrab, Image
            import hashlib
            l, t, r, b = win.room_list_bbox()
            narrow = (l, t, max(l + 50, r - 80), b)
            img = ImageGrab.grab(bbox=narrow)
            # 250x500 — 미리보기 텍스트 변화도 잡을 수 있을 정도의 해상도
            small = img.convert("L").resize((250, 500), Image.Resampling.LANCZOS)
            return hashlib.md5(small.tobytes()).hexdigest()
        except Exception:
            return ""

    # 누적 통계 (전체 세션): {room_name: {"no_change": N, "processed": N}}
    _session_stats: dict[str, dict[str, int]] = {}

    def _record_room(room_name: str, kind: str):
        """사이클-간 누적 기록. kind = 'processed' | 'no_change' | 'not_target'"""
        if not room_name:
            return
        if room_name not in _session_stats:
            _session_stats[room_name] = {"processed": 0, "no_change": 0, "not_target": 0}
        _session_stats[room_name][kind] = _session_stats[room_name].get(kind, 0) + 1

    def _print_cycle_summary(cycle_num: int, cycle_elapsed: int,
                              new_count: int, nochange_names: list[str],
                              skipped_names: list[str]):
        print(f"\n{'='*60}", flush=True)
        print(f"[사이클 {cycle_num} 요약] (실제 {cycle_elapsed}초)", flush=True)
        print(f"  ✅ 신규 감지: {new_count}개", flush=True)
        print(f"  ⚪ 변경 없음: {len(nochange_names)}개 — {', '.join(n[:12] for n in nochange_names[:10])}", flush=True)
        if skipped_names:
            print(f"  🚫 미감시: {len(skipped_names)}개 — {', '.join(n[:12] for n in skipped_names[:5])}", flush=True)
        # 누적 변경없음 Top 5
        top_nc = sorted(_session_stats.items(),
                        key=lambda kv: kv[1].get("no_change", 0), reverse=True)[:5]
        if top_nc:
            print(f"  📊 누적 '변경 없음' Top 5:", flush=True)
            for name, s in top_nc:
                if s.get("no_change", 0) >= 1:
                    print(f"     {name[:25]}: {s['no_change']}회", flush=True)
        print("=" * 60 + "\n", flush=True)

    # ── 단순 3페이지 × 8행 스윕 모드 ──
    # 뱃지 감지/해시 비교/idle 가드 모두 제거. 매 사이클마다:
    #   페이지 2 (아래로 1660) → 8행 아래→위 처리
    #   페이지 1 (아래로 830)  → 8행 아래→위 처리
    #   페이지 0 (맨 위)       → 8행 아래→위 처리
    # 변경 없는 방은 extract_from_room이 None 반환 → 즉시 스킵.
    PAGE_SCROLL = 277    # 한 페이지 = 한 행(60px) × ~4.6배 wheel notches
    ROWS_PER_PAGE = 9
    SWEEP_ROW_HEIGHT = 60
    # 뱃지 모드(기본): 안읽음 방은 카톡에서 '맨 위'로 올라온다. 페이지0(맨위)의
    # 안읽음을 처리하면 읽음→목록에서 빠지고 다음 안읽음이 위로 올라오므로,
    # 페이지0만 반복(사이클 반복)하면 안읽음 9개 초과여도 전부 소진된다.
    # 20페이지 순회는 중복/빈행 헛클릭(중복 1087회 사고)만 유발 → 페이지0만.
    # NENOVA_BADGE_SCAN=0(옛 고정행) 일 때만 20페이지 순회.
    import os as _os_pg
    if _os_pg.environ.get("NENOVA_BADGE_SCAN", "1") != "0":
        PAGES = [0]            # 뱃지 모드: 맨 위 페이지만(반복 사이클로 소진)
    else:
        PAGES = list(range(0, 20))  # 옛 방식: 20페이지 순회

    # 안전한 정지 신호: data/_STOP 파일 (stop_nenova.bat 가 생성).
    # 오버레이 stub 모드(NENOVA_NO_OVERLAY=1)에서는 overlay.should_stop 이 항상 False 라
    # 자동화가 실수로 못 누르는 파일 기반 정지를 함께 검사한다.
    from core.stop_button import is_stop_requested as _stop_requested
    # 워크→카톡 답장과 카톡 창 제어 충돌 방지 락
    from core import kakao_lock as _klock

    try:
        while True:
            # ── 중지 체크 (오버레이 버튼 OR data/_STOP 파일) ──
            if overlay.should_stop or _stop_requested():
                print("[MONITOR] 중지 요청 감지 (버튼/_STOP) -- 감시 종료", flush=True)
                break

            # ── 워크→카톡 답장 진행 중이면 이번 사이클 시작 보류 (충돌 방지) ──
            # reactive 가 우선 요청을 남기면 답장이 끝날 때까지 카톡을 양보한다.
            _yield_waited = 0.0
            while _klock.is_requested() and _yield_waited < 30 and not _stop_requested():
                if _yield_waited == 0.0:
                    print("[MONITOR] 워크 답장 진행 중 — 카톡 양보 대기", flush=True)
                time.sleep(0.5)
                _yield_waited += 0.5

            cycle += 1
            overlay.set_idle()

            # 이번 사이클에서 처리된 방 이름 추적
            processed_this_cycle: set[str] = set()
            cycle_start_ts = time.time()
            cycle_nochange: list[str] = []
            cycle_skipped: list[str] = []

            # ── 매 사이클 시작: 정체 다이얼로그 처치 + 잔여 분리창 정리 + 팝업 정리 + 카톡 활성화 ──
            try:
                import win32gui as _w32
                import win32con as _wc
                import ctypes as _ct

                # (0a) 카톡 메인 창 강제 visible (숨겨진 상태 복원)
                kakao_main_hwnd = None
                def _find_kakao_any(h, _):
                    nonlocal kakao_main_hwnd
                    if kakao_main_hwnd:
                        return
                    if _w32.IsWindow(h) and _w32.GetWindowText(h) == "카카오톡":
                        kakao_main_hwnd = h
                _w32.EnumWindows(_find_kakao_any, None)
                if kakao_main_hwnd and not _w32.IsWindowVisible(kakao_main_hwnd):
                    print(f"[{cycle}] 카톡 메인 숨김 상태 → SW_SHOW 강제 복원", flush=True)
                    _w32.ShowWindow(kakao_main_hwnd, _wc.SW_SHOW)
                    time.sleep(0.3)
                    _w32.ShowWindow(kakao_main_hwnd, _wc.SW_RESTORE)
                    time.sleep(0.3)
                    # Alt 트릭 + foreground
                    _ct.windll.user32.keybd_event(0x12, 0, 0, 0)
                    time.sleep(0.05)
                    _ct.windll.user32.keybd_event(0x12, 0, 0x0002, 0)
                    time.sleep(0.1)
                    try:
                        _w32.SetForegroundWindow(kakao_main_hwnd)
                    except Exception:
                        pass
                    time.sleep(0.3)

                # (0b) "다른 이름으로 저장" 등 정체 다이얼로그 자동 처리
                stuck_dialogs = []
                def _find_stuck(h, _):
                    if not _w32.IsWindowVisible(h):
                        return
                    t = _w32.GetWindowText(h) or ""
                    if any(k in t for k in ("다른 이름으로 저장", "Save As", "파일 저장")):
                        r = _w32.GetWindowRect(h)
                        if r[2] - r[0] > 300:  # 실제 다이얼로그 (큰 창)
                            stuck_dialogs.append((h, t, r))
                _w32.EnumWindows(_find_stuck, None)
                if stuck_dialogs:
                    print(f"[{cycle}] 정체 다이얼로그 {len(stuck_dialogs)}개 감지 → ESC/닫기", flush=True)
                    import pyautogui as _pag
                    for h, t, r in stuck_dialogs:
                        try:
                            _w32.SetForegroundWindow(h)
                            time.sleep(0.2)
                            _pag.press("escape")
                            time.sleep(0.3)
                            # 여전히 살아있으면 WM_CLOSE
                            if _w32.IsWindow(h) and _w32.IsWindowVisible(h):
                                _w32.PostMessage(h, _wc.WM_CLOSE, 0, 0)
                                time.sleep(0.3)
                        except Exception as e:
                            print(f"[{cycle}] 다이얼로그 닫기 실패 ({t[:20]}): {e}", flush=True)
                    time.sleep(0.5)

                from core.message_extractor import close_all_chat_separators
                n_closed = close_all_chat_separators()
                if n_closed:
                    print(f"[{cycle}] 이전 분리창 {n_closed}개 정리", flush=True)

                # 검색창 잔류 텍스트 비우기 — 워크 답장 검색이 남긴 방이름으로
                # 목록이 필터된 채면 좌표 클릭이 빗나가므로 매 사이클 초기화.
                try:
                    from core.kakao_win32 import clear_chat_search
                    clear_chat_search()
                except Exception:
                    pass

                # 보강 A: selected_rooms 에 없는 분리창 (개인톡 등) 도 강제 정리
                stray_seps = []
                def _find_strays(h, _):
                    if not _w32.IsWindowVisible(h):
                        return
                    t = _w32.GetWindowText(h) or ""
                    if not t or t == "카카오톡":
                        return
                    cls = _w32.GetClassName(h) or ""
                    if not cls.startswith("EVA_"):
                        return
                    r = _w32.GetWindowRect(h)
                    w, hh = r[2]-r[0], r[3]-r[1]
                    if 300 <= w <= 900 and 500 <= hh <= 1000:  # 분리창 크기
                        stray_seps.append((h, t))
                _w32.EnumWindows(_find_strays, None)
                if stray_seps:
                    print(f"[{cycle}] 잔여 분리창 강제 정리: {[t[:15] for _, t in stray_seps]}", flush=True)
                    for h, t in stray_seps:
                        try:
                            _w32.PostMessage(h, _wc.WM_CLOSE, 0, 0)
                        except Exception:
                            pass
                    time.sleep(0.5)

                # 보강 B: 무명 EVA_Window_Dblclk 잔여 팝업 (광고/알림 등) 정리
                # 이전 ≡ 메뉴가 닫히지 않고 남아 있어서 새 ≡ 클릭과 충돌하는 케이스 방지.
                stray_popups = []
                def _find_stray_popups(h, _):
                    if not _w32.IsWindowVisible(h):
                        return
                    t = _w32.GetWindowText(h) or ""
                    if t:  # 제목 있으면 스킵 (분리창/메인이면 위에서 처리)
                        return
                    cls = _w32.GetClassName(h) or ""
                    if "EVA_" not in cls:
                        return
                    r = _w32.GetWindowRect(h)
                    w, hh = r[2]-r[0], r[3]-r[1]
                    # 팝업 크기 범위 (180~500 x 100~700) — 광고 팝업도 포함
                    if 100 <= w <= 500 and 100 <= hh <= 700:
                        stray_popups.append((h, cls, r))
                _w32.EnumWindows(_find_stray_popups, None)
                if stray_popups:
                    print(f"[{cycle}] 잔여 EVA 팝업 {len(stray_popups)}개 정리: {[(c, r) for _,c,r in stray_popups[:3]]}", flush=True)
                    for h, c, r in stray_popups:
                        try:
                            _w32.PostMessage(h, _wc.WM_CLOSE, 0, 0)
                        except Exception:
                            pass
                    time.sleep(0.3)

                cleanup_popups()
                window = focus_kakaotalk()
            except Exception as e:
                print(f"[{cycle}] 카톡 포커스 실패: {e} — 1초 후 재시도")
                time.sleep(1.0)
                continue

            # ── 주기적 mapping ↔ selected 동기화 ──
            if cycle % SYNC_MAPPING_EVERY == 0:
                try:
                    from core.room_sync import sync_selected_from_mapping
                    added = sync_selected_from_mapping()
                    if added:
                        with open(selected_file, encoding="utf-8") as _f:
                            selected_rooms = json.load(_f)
                        selected_names = {r["name"] for r in selected_rooms}
                except Exception as e:
                    print(f"[ROOM-SYNC] 동기화 실패: {e}")

            # ══════════════════════════════════════════════
            # 3페이지 순회: 각 페이지에서 뱃지 있는 방만 열어 처리
            # ══════════════════════════════════════════════
            print(f"[{cycle}] 3페이지 순회 (뱃지 있는 방만 처리)")
            overlay.set_working(f"[사이클 {cycle}] 시작")

            from core.window_detector import scroll_room_list
            cycle_found = 0
            cycle_misses = 0   # 이번 사이클 chat_didnt_open(방 미열림) 수
            cycle_opened = 0   # 이번 사이클 실제로 열린 방 수 (성공/중복/무변경/대상아님 포함)
            cycle_adopted = 0  # 이번 사이클 자동 채택(새 그룹방 미러 생성) 수
            MAX_ADOPT_PER_CYCLE = 3  # 폭주 방지: 사이클당 자동 미러 생성 상한
            ROOM_ETA_BASE = 5
            ROOM_ETA_PHOTO = 6
            n_pages = len(PAGES)
            DELAY = 0.7  # 모든 액션 간 고정 딜레이
            MAX_ITERATIONS_PER_PAGE = 15  # 같은 스크롤 위치에서 최대 시도 (중복 나오면 더 빨리 끝남)

            def _force_kakao_main_foreground_inline() -> bool:
                """카톡 메인창을 foreground로. 트레이로 닫혀 있으면 복원+재배치까지.
                반환 True=메인창 활성(클릭 안전), False=실패(클릭 보류해야 함)."""
                try:
                    from core.window_manager import ensure_main_window_foreground
                    return ensure_main_window_foreground()
                except Exception:
                    return False

            def _auto_adopt_group_room(room_name: str) -> str:
                """모니터가 연 방(정확한 win32 제목)이 매핑에 없으면 새 그룹방으로 자동 채택.
                OCR 아닌 실제 창 제목 기반이라 변형 사고 없음.
                반환: 'adopted'(미러 새로 생성) / 'mapped'(이미 매핑됨) / 'skip'(1:1·시스템·잘림·실패)
                """
                try:
                    from core.kakaowork_router import _load_room_mapping, ensure_mirror_for_rooms
                    from core.room_sync import (
                        _is_group_room, _is_1to1_room, _is_system_room,
                        sync_selected_from_mapping,
                    )
                except Exception:
                    return "skip"
                # 이미 매핑됨(공백 무시)? → selected 만 보강
                mapping = _load_room_mapping()
                nn = room_name.replace(" ", "")
                for k in mapping:
                    if k.replace(" ", "") == nn:
                        return "mapped"
                # 제목 잘림(", ..." / "…")이면 이름 불완전 → 자동채택 보류
                if "..." in room_name or "…" in room_name:
                    print(f"     [AUTO-ADOPT] '{room_name}' 제목 잘림 → 보류", flush=True)
                    return "skip"
                # 그룹방만 (1:1/시스템 제외)
                if _is_system_room(room_name) or _is_1to1_room(room_name) or not _is_group_room(room_name):
                    return "skip"
                try:
                    res = ensure_mirror_for_rooms([room_name])
                    sync_selected_from_mapping()
                    if res.get("created") or room_name in (res.get("mapping") or {}):
                        print(f"     [AUTO-ADOPT] 새 그룹방 워크 미러 생성: {room_name}", flush=True)
                        return "adopted"
                except Exception as e:
                    print(f"     [AUTO-ADOPT] '{room_name}' 생성 실패: {e}", flush=True)
                return "skip"

            def _scroll_to_page(win, page_idx):
                """맨 위 → N회 -830 스크롤 (0.7초 딜레이)."""
                scroll_room_list_to_top(win)
                time.sleep(DELAY)
                for step in range(page_idx):
                    scroll_room_list(win, direction=-PAGE_SCROLL, focus_click=(step == 0))
                    time.sleep(DELAY)

            # 행 Y 좌표 (맨 아래 = 9번째 행만 사용)
            _lt, _tp, _rt, _bt = window.room_list_bbox()
            CLICK_X = (_lt + _rt) // 2
            BOTTOM_ROW_Y = _tp + 35 + (ROWS_PER_PAGE - 1) * SWEEP_ROW_HEIGHT  # y=614

            # ── 맨 위 안읽음 첫 방 우선 처리 (사이클당 1회) ──
            # 안읽음 방은 카톡에서 항상 맨 위로 온다. 그런데 _scroll_to_page 가
            # home(맨위)→스크롤 로 페이지를 내려가면서, 맨 위에 막 올라온 안읽음
            # 첫 방을 그냥 지나치는 경우가 있었다(사용자 지적). 페이지 순회 전에
            # 맨 위 첫 방을 한 번 먼저 잡는다.
            _badge_mode_cyc = _os.environ.get("NENOVA_BADGE_SCAN", "1") != "0"
            if not (overlay.should_stop or _stop_requested()) and \
               _klock.acquire("monitor", timeout=15, respect_request=True):
                try:
                    _force_kakao_main_foreground_inline()
                    scroll_room_list_to_top(window)
                    time.sleep(DELAY)
                    _top_y = _tp + 35  # 맨 위 첫 방
                    _do_top = True
                    if _badge_mode_cyc:
                        # 뱃지 모드: 맨 위에 안읽음 뱃지가 실제로 있을 때만
                        try:
                            from PIL import ImageGrab as _IGt
                            from core.badge_monitor import detect_unread_badge_rows as _dubt
                            _IGt.grab(bbox=(_lt, _tp, _rt, _bt)).save(captures_dir / "_badge_top.png")
                            _tys = _dubt(captures_dir / "_badge_top.png")
                            if _tys:
                                _top_y = _tp + _tys[0]
                            else:
                                _do_top = False
                        except Exception:
                            pass
                    if _do_top:
                        _r = extract_from_room(CLICK_X, _top_y, skip_titles=processed_this_cycle)
                        if (_r and _r.get("room_name") and not _r.get("_duplicate")
                                and not _r.get("_no_change") and not _r.get("_yielded")):
                            _rn = _r["room_name"]
                            if _rn in selected_names:
                                print(f"  [맨위우선] {_rn} 선처리", flush=True)
                                _process_room_result(_r, CLICK_X, _top_y, **process_deps)
                                processed_this_cycle.add(_rn)
                                _record_room(_rn, "processed")
                except Exception as _te:
                    print(f"  [맨위우선] 예외(무시): {_te}", flush=True)
                finally:
                    _klock.release("monitor")

            for p_i, page_idx in enumerate(PAGES, 1):
                if overlay.should_stop or _stop_requested():
                    break
                # 워크 답장 우선 요청 → 남은 페이지 양보 (사이클 종료 후 재대기)
                if _klock.is_requested():
                    print("[MONITOR] 워크 답장 우선 — 남은 페이지 양보", flush=True)
                    break

                overlay.set_status(f"페이지 {p_i}/{n_pages} (p{page_idx})")
                total_scroll = page_idx * PAGE_SCROLL
                print(f"  [페이지 {page_idx}] 스크롤 위치 -{total_scroll} — 맨 아래 행 반복 처리", flush=True)

                try:
                    # ── 캡처-선분석: 빨간 뱃지(안읽음) 있는 행만 클릭 ──
                    # 기존엔 9행 고정좌표를 전부 더블클릭하며 빈 행 헛클릭 → 안읽음 방
                    # 처리 지연. 이제 페이지를 캡처해 뱃지 y만 추출, 그 행만 클릭한다.
                    # 뱃지 0개면 페이지 즉시 스킵. NENOVA_BADGE_SCAN=0 으로 옛 방식 폴백.
                    _badge_mode = _os.environ.get("NENOVA_BADGE_SCAN", "1") != "0"
                    rows_desc = None
                    if _badge_mode:
                        try:
                            _force_kakao_main_foreground_inline()
                            _scroll_to_page(window, page_idx)
                            from PIL import ImageGrab as _IG
                            from core.badge_monitor import detect_unread_badge_rows
                            _bl, _bt2, _br, _bb = window.room_list_bbox()
                            _cap = captures_dir / f"_badge_p{page_idx}.png"
                            _IG.grab(bbox=(_bl, _bt2, _br, _bb)).save(_cap)
                            _badge_ys = detect_unread_badge_rows(_cap)
                            # 이미지 내 y → 화면 절대 y (룸리스트 top 기준)
                            rows_desc = [_bt2 + _y for _y in _badge_ys]
                            print(f"  [페이지 {page_idx}] 뱃지 {len(rows_desc)}개 감지 → 그 행만 처리", flush=True)
                            if not rows_desc:
                                print(f"  [페이지 {page_idx}] 안읽음 뱃지 0 → 페이지 스킵", flush=True)
                                continue
                        except Exception as _be:
                            print(f"  [페이지 {page_idx}] 뱃지스캔 실패({_be}) → 고정행 폴백", flush=True)
                            rows_desc = None

                    if rows_desc is None:
                        # 폴백: 9행 고정 좌표 (옛 방식)
                        _row_y_off = get_room_list_click_y_offset()
                        row_ys = [_tp + 35 + _row_y_off + i * SWEEP_ROW_HEIGHT for i in range(ROWS_PER_PAGE)]
                        if _row_y_off > 0:
                            row_ys = [_tp + 35] + row_ys
                        rows_desc = row_ys  # 위→아래

                    # 연속 미열림 카운터 — N회 연속이면 페이지 나머지 스킵 (빈 행 낭비 제거)
                    consecutive_misses = 0
                    MAX_CONSECUTIVE_MISSES = 3
                    # 연속 중복(이번 사이클 이미 처리한 방을 또 클릭) — 안읽음이 짧을 때
                    # 같은 방들이 19페이지 내내 반복 등장하는 헛작업 방지.
                    consecutive_dups = 0
                    MAX_CONSECUTIVE_DUPS = 4

                    for iter_idx, row_y in enumerate(rows_desc, 1):
                        if overlay.should_stop or _stop_requested():
                            break

                        overlay.set_status(f"p{p_i}/{n_pages} 행{iter_idx}/{ROWS_PER_PAGE} (y={row_y})")

                        # 카톡 제어 락 획득 — 워크 답장이 우선 요청이면 양보(이 행/페이지 스킵)
                        if not _klock.acquire("monitor", timeout=20, respect_request=True):
                            print(f"     [p{page_idx} r{iter_idx}] 워크 답장 우선 — 행 양보", flush=True)
                            break

                        try:
                            # 매 행마다: 메인창이 닫혀(트레이) 있으면 켜고 활성화.
                            # 활성화 실패 시 좌표 맹목 클릭 금지(엉뚱한 창 클릭 사고 방지) → 보류.
                            if not _force_kakao_main_foreground_inline():
                                consecutive_misses += 1
                                print(f"     [p{page_idx} r{iter_idx}] 카톡 메인창 비활성/닫힘 → 클릭 보류 "
                                      f"(연속 {consecutive_misses})", flush=True)
                                if consecutive_misses >= MAX_CONSECUTIVE_MISSES:
                                    print(f"     [p{page_idx}] 메인창 계속 비활성 → 페이지 나머지 스킵", flush=True)
                                    break
                                time.sleep(1.0)
                                continue
                            # 매 행마다 재스크롤 + 해당 행 클릭
                            _scroll_to_page(window, page_idx)

                            # 뱃지 모드: 방 처리 후 리스트가 바뀌어(읽음→방 이동) 저장된
                            # 절대 y가 무효가 되므로, 클릭 직전 재캡처로 '현재 맨 위 뱃지'를
                            # 다시 찾아 그 y로 클릭한다. (없으면 이 페이지 안읽음 소진 → break)
                            if _badge_mode:
                                try:
                                    from PIL import ImageGrab as _IG2
                                    from core.badge_monitor import detect_unread_badge_rows as _dub
                                    _bl2, _bt3, _br2, _bb2 = window.room_list_bbox()
                                    _cap2 = captures_dir / f"_badge_p{page_idx}_r{iter_idx}.png"
                                    _IG2.grab(bbox=(_bl2, _bt3, _br2, _bb2)).save(_cap2)
                                    _yy = _dub(_cap2)
                                    if not _yy:
                                        print(f"     [p{page_idx}] 남은 안읽음 뱃지 0 → 페이지 종료", flush=True)
                                        break
                                    row_y = _bt3 + _yy[0]  # 현재 맨 위 뱃지 행
                                except Exception:
                                    pass  # 실패 시 원래 row_y 사용

                            t0 = time.time()
                            result = extract_from_room(CLICK_X, row_y, skip_titles=processed_this_cycle)
                            time.sleep(DELAY)

                            from core.run_analyzer import log_issue as _log_issue
                            if not result:
                                _log_issue("chat_didnt_open", cycle=cycle, page=page_idx, row=iter_idx,
                                           context={"click_xy": [CLICK_X, row_y]})
                                consecutive_misses += 1
                                cycle_misses += 1
                                print(f"     [p{page_idx} r{iter_idx}] y={row_y} 분리창 미열림 → 스킵 (연속 {consecutive_misses})", flush=True)
                                if consecutive_misses >= MAX_CONSECUTIVE_MISSES:
                                    print(f"     [p{page_idx}] {MAX_CONSECUTIVE_MISSES}회 연속 미열림 → 페이지 나머지 스킵", flush=True)
                                    break
                                continue
                            # 결과가 있으면 연속 카운터 리셋 (방이 실제로 열렸다는 신호)
                            consecutive_misses = 0
                            cycle_opened += 1

                            # 워크 답장 우선 양보 (저장 전 감지) → 즉시 락 풀고 답장 처리
                            if result.get("_yielded"):
                                print(f"     [p{page_idx} r{iter_idx}] 워크 답장 우선 — 사이클 양보", flush=True)
                                break

                            if result.get("_duplicate"):
                                _log_issue("duplicate_skip", cycle=cycle, page=page_idx, row=iter_idx,
                                           room=result.get("room_name"))
                                consecutive_dups += 1
                                print(f"     [p{page_idx} r{iter_idx}] 이미 처리됨: {result.get('room_name','')[:20]} "
                                      f"(연속 dup {consecutive_dups})", flush=True)
                                if consecutive_dups >= MAX_CONSECUTIVE_DUPS:
                                    print(f"     [p{page_idx}] {MAX_CONSECUTIVE_DUPS}회 연속 이미처리 → "
                                          f"안읽음 리스트 소진 — 페이지 나머지 스킵", flush=True)
                                    break
                                continue
                            # 중복 아니면 카운터 리셋
                            consecutive_dups = 0

                            if result.get("_no_change"):
                                room_name = result["room_name"]
                                processed_this_cycle.add(room_name)
                                cycle_nochange.append(room_name)
                                _record_room(room_name, "no_change")
                                _log_issue("no_change_repeat", cycle=cycle, page=page_idx, row=iter_idx,
                                           room=room_name)
                                print(f"     [p{page_idx} r{iter_idx}] {room_name[:20]} 변경 없음", flush=True)
                                continue

                            room_name = result["room_name"]
                            if room_name not in selected_names:
                                # 새 초대 그룹방 자동 채택 (정확한 win32 제목 기준 → 다음부터 미러링)
                                _ad = "skip"
                                if cycle_adopted < MAX_ADOPT_PER_CYCLE:
                                    _ad = _auto_adopt_group_room(room_name)
                                if _ad == "adopted":
                                    cycle_adopted += 1
                                    selected_names.add(room_name)
                                elif _ad == "mapped":
                                    selected_names.add(room_name)
                                else:
                                    cycle_skipped.append(room_name)
                                    _record_room(room_name, "not_target")
                                    _log_issue("not_target_room", cycle=cycle, page=page_idx, row=iter_idx,
                                               room=room_name)
                                    print(f"     [p{page_idx} r{iter_idx}] {room_name} - 감시 대상 아님", flush=True)
                                    continue

                            cycle_found += 1
                            from core.kakaowork_router import count_photo_messages
                            n_photos = count_photo_messages(result.get("delta", ""))
                            eta = ROOM_ETA_BASE + n_photos * ROOM_ETA_PHOTO
                            print(f"     [p{page_idx} r{iter_idx}] {room_name} - 신규 (사진 {n_photos}장 / 예상 {eta}초)", flush=True)
                            overlay.set_status(f"p{p_i}/{n_pages} r{iter_idx} {room_name[:10]} ({eta}s)")

                            # 방 처리 직전 정지 체크 (extract 동안 들어온 정지 요청 반영)
                            if _stop_requested():
                                print("[MONITOR] 정지 요청 감지 — 방 처리 중단", flush=True)
                                break

                            _process_room_result(result, CLICK_X, row_y, **process_deps)
                            processed_this_cycle.add(room_name)
                            _record_room(room_name, "processed")
                            elapsed = int(time.time() - t0)
                            print(f"     → 완료 (실제 {elapsed}초)", flush=True)
                            time.sleep(DELAY)
                        except Exception as e:
                            print(f"     [p{page_idx} r{iter_idx}] 에러: {e}", flush=True)
                            cleanup_popups()
                            time.sleep(DELAY)
                            continue
                        finally:
                            # 방 처리 끝(성공/스킵/에러 무관) → 카톡 락 해제 → 답장 차례
                            _klock.release("monitor")

                except Exception as e:
                    error_detail = f"페이지 {page_idx} 에러\n{traceback.format_exc()}"
                    report_issue(f"페이지 {page_idx} 에러", error_detail)

            # 사이클 완료 후 맨 위로
            try:
                scroll_room_list_to_top(window)
            except Exception:
                pass

            _print_cycle_summary(
                cycle,
                int(time.time() - cycle_start_ts),
                cycle_found,
                cycle_nochange,
                cycle_skipped,
            )

            # ── 서킷브레이커: 연속 '완전 실패'(방이 하나도 안 열림) 사이클 → 자동 정지 + 경보 ──
            # idle(새 메시지 없음)과 구분: idle 은 방이 열리되 중복/무변경이라 cycle_opened>0.
            # 완전 실패는 클릭해도 분리창이 안 떠서 cycle_opened==0 & 미열림 누적 → 야간 폭주 패턴.
            if cycle_opened == 0 and cycle_misses >= DEAD_CYCLE_MIN_MISSES:
                consecutive_dead_cycles += 1
                print(f"[서킷] 완전 실패 사이클 {consecutive_dead_cycles}/{MAX_DEAD_CYCLES} "
                      f"(방 미열림 {cycle_misses}건, 열림 0)", flush=True)
            else:
                consecutive_dead_cycles = 0
            if consecutive_dead_cycles >= MAX_DEAD_CYCLES:
                alert = (f"🛑 감시 자동 중단 — 카톡 방 미열림 폭주 차단\n"
                         f"{consecutive_dead_cycles}사이클 연속으로 방이 하나도 안 열렸습니다 "
                         f"(직전 사이클 미열림 {cycle_misses}건).\n"
                         f"원인 후보: 카톡 로그아웃 / 화면잠금 / 카톡 창 최소화·이동.\n"
                         f"확인 후 다시 시작하세요.")
                print(alert, flush=True)
                try:
                    report_issue("감시 자동 중단 (방 미열림 폭주 차단)", alert)
                except Exception as _e:
                    print(f"[서킷] 경보 전송 실패(무시): {_e}", flush=True)
                break

            # 이슈 분석 요약 출력 + learning.md append
            try:
                from core.run_analyzer import summarize_cycle, append_learning_md
                issue_summary = summarize_cycle(cycle)
                print(issue_summary, flush=True)
                append_learning_md(cycle, issue_summary)
            except Exception as e:
                print(f"[ANALYZER] 요약 실패: {e}", flush=True)

            # ── 관리자 피드백 주기 체크 ──
            if cycle % 10 == 0:
                try:
                    learned = process_admin_feedback()
                    if learned:
                        print(f"[LEARN] 관리자 수정 {learned}건 → 패턴 업데이트")
                except Exception as e:
                    print(f"[LEARN] 피드백 체크 실패: {e}")

            # ── STALLED 체인 경보 (매 5사이클마다) ──
            if cycle % 5 == 0:
                try:
                    from core.pipeline_tracker import tracker
                    stalled = tracker.get_stalled(hours=4)
                    if stalled:
                        print(f"\n[STALLED] {len(stalled)}개 업무 4시간+ 미완결:", flush=True)
                        lines = ["⚠️ 미완결 업무 경보 (4시간+ 무반응):"]
                        for ch in stalled[:10]:
                            line = (f"  • {ch['chain_id']} "
                                    f"[{ch.get('trigger_event','?')}] "
                                    f"{ch.get('trigger_room','?')} "
                                    f"by {ch.get('trigger_sender','?')} "
                                    f"({ch.get('stalled_hours','?')}h 경과)")
                            print(line, flush=True)
                            lines.append(line)
                        # 이슈방 전송 — 비블로킹 (업무 경보가 미러링을 멈추면 안 됨).
                        # report_issue 의 블로킹 팝업은 '기술 에러' 전용으로 둔다.
                        try:
                            from core.issue_reporter import send_issue_to_kakaowork
                            send_issue_to_kakaowork("미완결 업무 경보", "\n".join(lines))
                        except Exception as _e:
                            print(f"[STALLED] 이슈방 전송 실패 (무시): {_e}")
                except Exception as e:
                    print(f"[STALLED] 체크 실패: {e}")

            # ── 새 방 자동 채택 — 2경로 역할 분리 (P1 중복 정리) ──
            #  (1) 실시간: _auto_adopt_group_room — 모니터가 안읽음 방을 열 때 정확한
            #      win32 제목으로 즉시 채택. OCR 변형 없음. 주력 경로(항상 동작).
            #  (2) 주기 백업: 아래 5시간 Vision 전체스캔 — 안읽음에 안 뜬(이미 읽은)
            #      신규 그룹방까지 훑는 보강. 단 Vision/OCR 변형 사고 위험이 있어
            #      기본 OFF. 필요 시 NENOVA_NEWROOM_SCAN=1 로 활성화.
            #  ensure_mirror_for_rooms 가 멱등(매핑 있으면 skip)이라 둘이 같은 방을
            #  잡아도 중복 생성은 없음.
            _newroom_scan_on = _os.environ.get("NENOVA_NEWROOM_SCAN") == "1"
            if _newroom_scan_on and time.time() - last_newroom_scan_ts >= NEWROOM_SCAN_INTERVAL:
                last_newroom_scan_ts = time.time()
                print("[NEWROOM] 5시간 주기 새 방 스캔 (전체 탭, Claude Vision)", flush=True)
                _got = _klock.acquire("monitor", timeout=30)
                try:
                    from core import room_sync as _rs
                    _res = _rs.adopt_new_rooms(window, auto_create=True)
                    print(f"[NEWROOM] 신규 {_res.get('new',0)} / 채택 {len(_res.get('adopted',[]))} / "
                          f"생성 {_res.get('created',0)} / 검토필요 {len(_res.get('review_external',[]))}", flush=True)
                    for _n in _res.get("adopted", []):
                        selected_names.add(_n)
                except Exception as _e:
                    print(f"[NEWROOM] 스캔 실패: {_e}", flush=True)
                finally:
                    if _got:
                        _klock.release("monitor")

            try:
                cleanup_popups()
                focus_kakaotalk()
            except Exception as e:
                # 카톡이 일시적으로 사라진 상황 (분리창 닫는 도중 등)
                # cycle을 죽이지 말고 다음 사이클에서 재시도
                print(f"[CYCLE-END] 카톡 포커스 일시 실패 ({e}) - 다음 사이클 재시도")
            time.sleep(POLL_INTERVAL)

    except KeyboardInterrupt:
        print("\n[MONITOR] Ctrl+C 감지. 감시 종료.")

    # 공통 종료 처리 (중지버튼/Ctrl+C 모두)
    try:
        overlay.stop()
    except Exception:
        pass

    # 사용자가 🛑 정지(또는 _STOP)로 멈춘 경우엔 무거운 후처리(LLM 프레임분석/회고)를
    # 건너뛰어 즉시·크레딧 없이 종료. (Ctrl+C/자연 종료는 기존대로 후처리 수행)
    stopped_by_user = False
    try:
        from core.stop_button import is_stop_requested as _isr
        stopped_by_user = _isr()
    except Exception:
        pass

    if with_recorder:
        try:
            from core.learning_recorder import get_recorder, set_recorder
            r = get_recorder()
            if r:
                r.stop_and_save()
            set_recorder(None)
        except Exception as e:
            print(f"[MONITOR] recorder stop err: {e}")

    if stopped_by_user:
        print("[MONITOR] 사용자 정지 — 후처리(프레임분석/회고) 생략하고 즉시 종료", flush=True)
        return 0

    # 실패 프레임 자동 분석 (스텝별 최신 캡처 → Claude로 원인 한 줄 요약)
    try:
        from core.failed_frame_analyzer import analyze_recent
        print("\n[MONITOR] 세션 실패 프레임 분석 중...")
        analyze_recent(within_seconds=7200)
    except Exception as e:
        print(f"[MONITOR] 실패 분석 err: {e}")
    # 회고 (reflection): 통계 + Claude 권장 fix
    try:
        from core.reflection import reflect_and_write_report
        reflect_and_write_report()
    except Exception as e:
        print(f"[REFLECT] 회고 err: {e}")
    return 0


def cmd_monitor_agentic() -> int:
    """완전 Agentic 감시 모드.
    UI 자동화 100% Claude Computer Use.
    결정론은 hash 가드(idle) + 파일 처리/Bot API만.
    """
    import time
    from core.window_detector import find_kakaotalk_window
    from core.window_manager import focus_kakaotalk
    from core.message_extractor import (
        KAKAO_SAVE_DIR, read_and_process_saved_file,
    )
    from core.gsheet_sync import classify_and_log_delta
    from core.kakaowork_router import send_messages_individually
    from core.agentic_monitor import (
        _compute_room_list_hash, agentic_collect_unread_rooms,
    )

    print("[AGENTIC-MONITOR] 완전 Agentic 감시 모드 시작")
    print(f"          폴링 간격: {5}초 / UI 액션 100% Claude Computer Use")

    try:
        window = focus_kakaotalk()
    except Exception as e:
        print(f"[ERROR] 카톡 활성화 실패: {e}")
        return 1

    print(f"          창: ({window.left},{window.top}) {window.width}x{window.height}\n")

    last_hash = ""
    cycle = 0
    try:
        while True:
            cycle += 1
            try:
                window = focus_kakaotalk()
            except Exception:
                time.sleep(5)
                continue

            cur_hash = _compute_room_list_hash(window)
            if cur_hash and cur_hash == last_hash:
                if cycle % 12 == 0:
                    print(f"[{cycle}] idle (변화 없음)", flush=True)
                time.sleep(5)
                continue
            if cur_hash:
                last_hash = cur_hash

            print(f"[{cycle}] 변화 감지 → Agentic 수집 시작", flush=True)
            new_files = agentic_collect_unread_rooms(window, KAKAO_SAVE_DIR)

            for fpath in new_files:
                try:
                    result = read_and_process_saved_file(fpath)
                    if not result:
                        continue
                    room_name = result["room_name"]
                    delta = result["delta"]
                    print(f"     → {room_name}: 신규 {len(delta)}자", flush=True)
                    try:
                        send_messages_individually(room_name, delta)
                    except Exception as e:
                        print(f"     → 워크 전송 실패: {e}", flush=True)
                    try:
                        classify_and_log_delta(room_name, delta, result["timestamp"])
                    except Exception as e:
                        print(f"     → 시트 기록 실패: {e}", flush=True)
                except Exception as e:
                    print(f"     [ERROR] {fpath.name}: {e}", flush=True)

            time.sleep(5)
    except KeyboardInterrupt:
        print("\n[AGENTIC-MONITOR] Ctrl+C 종료")
        try:
            from core.failed_frame_analyzer import analyze_recent
            analyze_recent(within_seconds=7200)
        except Exception:
            pass
        try:
            from core.reflection import reflect_and_write_report
            reflect_and_write_report()
        except Exception:
            pass
        return 0


def cmd_run() -> int:
    """원트리거: 전체 파이프라인 1회 실행"""
    from run_pipeline import run_all
    return run_all()


def cmd_learn() -> int:
    """학습 모드: 전체 파이프라인을 영상+이벤트로 녹화 → 앵커 후보 자동 추출"""
    from datetime import datetime
    from core.learning_recorder import LearningRecorder, set_recorder
    from run_pipeline import run_all

    session = f"learn_{datetime.now():%Y%m%d_%H%M%S}"
    rec = LearningRecorder(session)
    set_recorder(rec)
    rec.start()
    try:
        rc = run_all()
    finally:
        out = rec.stop_and_save()
        set_recorder(None)
        print(f"\n[LEARN] 완료 → {out}")
        print(f"  다음 단계: python main.py anchors  (앵커 후보 확인 GUI)")
    return rc


def cmd_anchors() -> int:
    """학습으로 추출된 앵커 후보를 관리자가 확인/승인 → data/anchors/ 로 확정"""
    from core.anchor_picker_gui import run_picker
    return run_picker()


def cmd_auto_anchor(argv: list[str]) -> int:
    """학습 세션 후보를 자동으로 클러스터링해서 앵커 승인."""
    from core.anchor_auto_approver import auto_approve

    commit = "--commit" in argv
    overwrite = "--overwrite" in argv
    min_count = 3
    for i, a in enumerate(argv):
        if a == "--min-count" and i + 1 < len(argv):
            try:
                min_count = int(argv[i + 1])
            except ValueError:
                pass
    print(f"[AUTO-ANCHOR] commit={commit} min_count={min_count}")
    r = auto_approve(min_count=min_count, commit=commit, overwrite=overwrite)
    print(f"  승인 {len(r['approved'])}건 / 기각 {len(r['rejected'])}건 / 스킵 {len(r['skipped'])}건")
    for step, cnt, src in r["approved"]:
        print(f"   + {step}  (n={cnt})")
    return 0


def cmd_metrics(argv: list[str]) -> int:
    """스텝별 성공률/재시도 메트릭 표시."""
    from core.metrics_dashboard import show_cli
    gui = "--gui" in argv
    if gui:
        from core.metrics_dashboard import show_gui
        show_gui()
    else:
        show_cli()
    return 0


def cmd_unlock(argv: list[str]) -> int:
    """스텝 락 해제 → 다음 실행부터 앵커 검증 재개."""
    from core.traced_actions import unlock_step, load_metrics

    if len(argv) < 3:
        print("Usage: python main.py unlock <step_name>")
        print("       python main.py unlock --all")
        return 1

    if "--all" in argv:
        m = load_metrics()
        n = 0
        for step in list(m.keys()):
            if unlock_step(step):
                n += 1
        print(f"[UNLOCK] {n}개 스텝 락 해제")
        return 0

    step = argv[2]
    if unlock_step(step):
        print(f"[UNLOCK] '{step}' 락 해제됨")
        return 0
    print(f"[UNLOCK] '{step}' 찾을 수 없음")
    return 1


def cmd_calibrate(argv: list[str]) -> int:
    """
    전체 학습 사이클 1회 실행:
      1. learn (녹화 + 파이프라인 1회)
      2. auto-anchor --commit (누적 후보 자동 승인)
      3. metrics (결과 리포트)

    반복 돌리면 앵커가 점진적으로 안정화되고, 성공 20회 연속 스텝은 lock.
    """
    print("[CALIBRATE] 학습 사이클 1회 시작")
    print("  1/3 learn - 전체 파이프라인 녹화")
    rc = cmd_learn()
    if rc != 0:
        print(f"  learn 실패 (rc={rc}) - 중단")
        return rc

    print("  2/3 auto-anchor - 누적 후보 자동 승인")
    cmd_auto_anchor(["cmd", "auto-anchor", "--commit"])

    print("  3/3 metrics - 결과 리포트")
    cmd_metrics(["cmd", "metrics"])

    print("\n[CALIBRATE] 완료. 필요 시 python main.py calibrate 를 반복 실행.")
    return 0


def cmd_cleanup_mirrors(argv: list[str]) -> int:
    """카카오워크 미러 방 중복 청소. --dry-run/--ui 플래그 지원."""
    from core.mirror_cleanup import cleanup_duplicates

    dry_run = "--dry-run" in argv
    use_ui = "--ui" in argv

    print("[CLEANUP] 중복 미러 방 청소 시작")
    print(f"          모드: dry_run={dry_run}, use_ui={use_ui}")
    result = cleanup_duplicates(dry_run=dry_run, use_ui=use_ui)
    # 중복이 있었는데 1개도 정리 못했으면 실패로 간주
    if not dry_run and result["duplicates_found"] > 0 and result["renamed"] == 0:
        return 1
    return 0


def cmd_rename_nv(argv: list[str]) -> int:
    """미러 방 이름을 'NV{NN}:원본이름' 으로 일괄 변경. --dry-run 지원."""
    from core.mirror_cleanup import apply_nv_naming

    dry_run = "--dry-run" in argv
    print(f"[NV-RENAME] 시작 (dry_run={dry_run})")
    result = apply_nv_naming(dry_run=dry_run)
    if result["failed"]:
        return 1
    return 0


def cmd_strip_mirror(argv: list[str]) -> int:
    """미러 방의 '[미러] X' prefix 를 제거해 'X' 원본 이름으로 변경. --dry-run 지원."""
    from core.mirror_cleanup import strip_mirror_prefixes

    dry_run = "--dry-run" in argv
    print(f"[STRIP-MIRROR] 시작 (dry_run={dry_run})")
    result = strip_mirror_prefixes(dry_run=dry_run)
    if result["failed"]:
        return 1
    return 0


def cmd_rename_via_app(argv: list[str]) -> int:
    """워크 앱 UI 자동화로 채팅방 이름 변경. 사용:
       main.py rename-via-app <conv_id> <new_name> [--dry-run]
       main.py rename-via-app --all [--dry-run]   # 37개 미러방 일괄
    """
    from core.kakaowork_app import rename_room_via_app
    from core.mirror_cleanup import fetch_all_bot_conversations, DELETE_MARK

    dry_run = "--dry-run" in argv
    if "--all" in argv:
        # 37개 정상 미러방 일괄 처리
        # mapping.json 의 키(원본 이름) 를 새 이름으로 사용
        from core.kakaowork_router import _load_room_mapping
        mapping = _load_room_mapping()
        targets = [(cid, name) for name, cid in mapping.items()]
        print(f"[RENAME-APP] 일괄 — {len(targets)}개 방, dry_run={dry_run}")
        for i, (cid, name) in enumerate(targets, start=1):
            print(f"\n  [{i}/{len(targets)}] {name} ({cid})")
            try:
                rename_room_via_app(str(cid), name, dry_run=dry_run)
            except Exception as e:
                print(f"    [ERROR] {e}")
        return 0

    if len(argv) < 4:
        print("사용: main.py rename-via-app <conv_id> <new_name> [--dry-run]")
        print("     main.py rename-via-app --all [--dry-run]")
        return 1
    conv_id = argv[2]
    new_name = argv[3]
    print(f"[RENAME-APP] conv={conv_id}, new_name='{new_name}', dry_run={dry_run}")
    try:
        ok = rename_room_via_app(conv_id, new_name, dry_run=dry_run)
        return 0 if ok else 1
    except Exception as e:
        print(f"[ERROR] {e}")
        return 1


def cmd_invite_member(argv: list[str]) -> int:
    """미러 방에 멤버 일괄 초대. 사용:
       main.py invite-member <user_id> [<user_id> ...] [--dry-run] [--only <conv_id>]
       --only <conv_id>: 시험용 단일 방만 초대
    """
    from core.mirror_cleanup import (
        invite_users_to_mirrors,
        invite_users_to_conv,
        fetch_all_bot_conversations,
    )

    dry_run = "--dry-run" in argv
    only_idx = argv.index("--only") if "--only" in argv else -1
    only_cid = argv[only_idx + 1] if only_idx >= 0 and only_idx + 1 < len(argv) else None
    # --only <cid> 자리의 cid 와 dry-run 같은 옵션은 user_id 후보에서 제외
    skip_indices = set()
    if only_idx >= 0:
        skip_indices.add(only_idx)
        skip_indices.add(only_idx + 1)
    user_ids = [
        a for i, a in enumerate(argv[2:], start=2)
        if a.isdigit() and i not in skip_indices
    ]
    if not user_ids:
        print("[ERROR] 사용: main.py invite-member <user_id> [<user_id>] [--dry-run] [--only <conv_id>]")
        return 1

    if only_cid:
        # 시험용: 단일 conv 만 초대
        print(f"[INVITE] 시험 — conv={only_cid}, users={user_ids}, dry_run={dry_run}")
        if dry_run:
            print("  (dry-run — 실제 호출 없음)")
            return 0
        ok, detail = invite_users_to_conv(only_cid, user_ids)
        print(f"  결과: ok={ok}, detail={detail}")
        return 0 if ok else 1

    # 미러방만 대상으로 좁히기 (--mirrors-only 또는 --prefix 968666)
    id_prefix = None
    if "--mirrors-only" in argv:
        id_prefix = "968666"
    elif "--prefix" in argv:
        pidx = argv.index("--prefix")
        if pidx + 1 < len(argv):
            id_prefix = argv[pidx + 1]

    print(f"[INVITE] 일괄 — users={user_ids}, dry_run={dry_run}, id_prefix={id_prefix}")
    result = invite_users_to_mirrors(user_ids, dry_run=dry_run, id_prefix=id_prefix)
    if result["failed"]:
        return 1
    return 0


def cmd_backfill(argv: list[str]) -> int:
    """전체 톡방 백필 — 각 미러방에 해당 카톡방 '전체 대화'를 1회 분할 전송.

    - 대상: room_mapping.json 의 매핑된 방 전체
    - 오늘 작업한 방(last_content mtime == 오늘)은 제외
    - 탭 전환 없이 검색(Ctrl+F)으로 각 방을 연다 (검색은 전체 채팅 대상)
    - Ctrl+S 로 전체 대화 저장 → 파일 내용을 2800자씩 분할해 미러방에 전송
    - 전송한 방은 last_content 갱신 → 재실행 시 '오늘 작업'으로 스킵(멱등)

    플래그: --dry-run (대상/제외 목록만 출력, 전송 안 함)
    """
    import json as _json
    import os as _os
    import time as _time
    from datetime import datetime as _dt, date as _date

    # 우하단 상태 오버레이의 '중지' 버튼을 자동화가 실수로 눌러 os._exit 되는
    # 버그 방지 — 백필 동안 오버레이를 stub 으로 강제 (monitor 와 동일).
    _os.environ["NENOVA_NO_OVERLAY"] = "1"

    dry_run = "--dry-run" in argv

    from core.window_manager import focus_kakaotalk
    from core import kakao_win32 as kw
    from core.kakao_win32 import clear_chat_search
    from core.message_extractor import (
        save_chat_with_ctrl_s, close_chat_room,
        LAST_CONTENT_DIR, _safe_filename, _save_last_content,
    )
    from core.kakaowork_router import _send_single, parse_delta_to_messages
    import win32gui

    mapping_path = ROOT / "data" / "room_mapping.json"
    if not mapping_path.exists():
        print("[BACKFILL] room_mapping.json 없음")
        return 1
    mapping = _json.loads(mapping_path.read_text(encoding="utf-8"))
    today = _date.today()

    def _worked_today(name: str) -> bool:
        p = LAST_CONTENT_DIR / f"{_safe_filename(name)}.txt"
        try:
            if p.exists():
                return _dt.fromtimestamp(p.stat().st_mtime).date() == today
        except Exception:
            pass
        return False

    all_rooms = list(mapping.keys())
    # 명시 타깃 파일이 있으면 그 목록을 사용 (오늘작업 제외 무시) — 재백필/특정방 지정용
    targets_file = ROOT / "data" / "_backfill_targets.json"
    if targets_file.exists():
        try:
            explicit = _json.loads(targets_file.read_text(encoding="utf-8"))
            targets = [r for r in explicit if r in mapping]
            skipped = []
            print(f"[BACKFILL] 명시 타깃 파일 사용 ({targets_file.name}): {len(targets)}개")
        except Exception as e:
            print(f"[BACKFILL] 타깃 파일 로드 실패({e}) → 자동 모드")
            targets = [r for r in all_rooms if not _worked_today(r)]
            skipped = [r for r in all_rooms if _worked_today(r)]
    else:
        targets = [r for r in all_rooms if not _worked_today(r)]
        skipped = [r for r in all_rooms if _worked_today(r)]

    print(f"\n{'='*60}")
    print(f"[BACKFILL] 매핑 {len(all_rooms)}개 / 대상 {len(targets)}개 / 오늘작업 제외 {len(skipped)}개")
    print(f"{'='*60}")
    for r in skipped:
        print(f"  [제외-오늘] {r}")
    for r in targets:
        print(f"  [대상] {r}")
    print(f"{'='*60}\n")

    if dry_run:
        print("[BACKFILL] --dry-run: 실제 전송 안 함. 위 '대상' 방들이 백필됩니다.")
        return 0

    if not targets:
        print("[BACKFILL] 대상 없음 (모두 오늘 작업됨)")
        return 0

    try:
        focus_kakaotalk()
    except Exception as e:
        print(f"[BACKFILL] 카톡 활성화 실패: {e}")
        return 1

    from core import kakao_lock as _klock
    done = 0
    for name in targets:
        conv_id = str(mapping[name])
        got = _klock.acquire("monitor", timeout=120)
        try:
            clear_chat_search()
            res = kw.search_and_open_room(name)
            hwnd = kw.find_chat_window(name)
            if hwnd is None:
                oh = res.get("hwnd")
                if oh and win32gui.IsWindow(oh) and (win32gui.GetWindowText(oh) or "") == name:
                    hwnd = oh
            if hwnd is None:
                print(f"  [BACKFILL] {name}: 정확한 분리창 못 엶 → 스킵", flush=True)
                continue
            saved = save_chat_with_ctrl_s(room_name=name, chat_hwnd=hwnd)
            close_chat_room(room_title=name)
            if not saved or not saved.exists():
                print(f"  [BACKFILL] {name}: 저장 실패 → 스킵", flush=True)
                continue
            content = saved.read_text(encoding="utf-8", errors="ignore").strip()
            if not content:
                print(f"  [BACKFILL] {name}: 빈 내용 → 스킵", flush=True)
                continue
            # 대화를 메시지 1건씩 분할 (날짜선/시스템/연속줄 처리) → 개별 전송
            msgs = parse_delta_to_messages(content)
            if not msgs:
                print(f"  [BACKFILL] {name}: 파싱된 메시지 0건 → 스킵", flush=True)
                continue
            print(f"  [BACKFILL] {name}: {len(content)}자 → 메시지 {len(msgs)}건 개별 전송", flush=True)

            # 멱등 원장: 이미 워크에 보낸 동일 메시지는 재전송 안 함 (재백필 안전)
            from core.sent_ledger import SentLedger as _SentLedger
            _bf_ledger = _SentLedger(name)

            def _send_verified(text: str) -> bool:
                """전송 성공(success=True) 할 때까지 재시도. 실패는 카운트 안 함.
                _send_single 은 429 시 30s backoff(False 반환), ConnectionError 시 내부
                재시도 → 그래도 False 면 여기서 대기 후 재시도(대량 RemoteDisconnected 대응)."""
                for attempt in range(6):
                    try:
                        if _send_single(conv_id, text):
                            return True
                    except Exception:
                        pass
                    _time.sleep(5.0)  # rate-limit(30s)/연결오류 회복 대기 (누적 30s)
                return False

            _send_verified(f"📦 [백필] {name} 전체 대화 {len(msgs)}건")
            _time.sleep(0.4)
            sent = miss = skipped = 0
            for m in msgs:
                body = (m.get("content") or "").strip()
                if not body:
                    continue
                # 멱등 dedup: 이미 워크에 보낸 동일 메시지면 스킵
                _h = _bf_ledger.hash_msg(m)
                if _bf_ledger.seen(_h):
                    skipped += 1
                    continue
                line = f"[{m.get('sender','')}] [{m.get('time','')}] {body}"
                if _send_verified(line):
                    sent += 1
                    _bf_ledger.add(_h)
                else:
                    miss += 1
                    print(f"  [BACKFILL] {name}: 1건 전송 실패(재시도 소진)", flush=True)
                _time.sleep(0.4)
            try:
                _bf_ledger.flush()
            except Exception:
                pass
            # 전부 성공했을 때만 '오늘 작업'으로 기록 (일부 실패 시 재실행에서 다시 시도)
            if miss == 0:
                _save_last_content(name, content)
            done += 1
            print(f"  [BACKFILL] {name}: 완료 (성공 {sent} / 중복스킵 {skipped} / 실패 {miss})", flush=True)
        except Exception as e:
            print(f"  [BACKFILL] {name}: 에러 {e}", flush=True)
            try:
                close_chat_room(room_title=name)
            except Exception:
                pass
        finally:
            if got:
                _klock.release("monitor")
        _time.sleep(1.0)

    print(f"\n[BACKFILL] 완료: {done}/{len(targets)}개 방 백필")
    return 0


def cmd_reply_buttons(argv: list[str]) -> int:
    """모든 워크 미러방에 [📤 카톡 답장] 버튼을 1개씩 송신 (상시 답장 진입점).

    Bot API(messages.send + button block)만 사용 — 화면 자동화 없음, monitor 와 무관.
    버튼 클릭 → reactive Request URL → 모달 → Callback → 카톡 송신.
    플래그: --dry-run (대상 목록만)
    """
    import json as _json
    import time as _time
    from core.kakaowork_router import send_reply_button

    dry = "--dry-run" in argv
    mapping_path = ROOT / "data" / "room_mapping.json"
    if not mapping_path.exists():
        print("[REPLY-BTN] room_mapping.json 없음")
        return 1
    mapping = _json.loads(mapping_path.read_text(encoding="utf-8"))

    print(f"[REPLY-BTN] {len(mapping)}개 미러방에 답장 버튼 송신"
          + (" (dry-run)" if dry else ""))
    if dry:
        for n in mapping:
            print(f"  [대상] {n}")
        return 0

    ok = fail = 0
    for name in mapping:
        try:
            r = send_reply_button(name)
        except Exception as e:
            r = False
            print(f"  ❌ {name}: {e}", flush=True)
        if r:
            ok += 1
            print(f"  ✅ {name}", flush=True)
        else:
            fail += 1
            print(f"  ❌ {name} (송신 실패)", flush=True)
        _time.sleep(0.4)
    print(f"[REPLY-BTN] 완료: 성공 {ok} / 실패 {fail}")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        return cmd_monitor()

    # 글로벌 플래그
    with_recorder = "--learn" in argv

    cmd = argv[1].lower()
    if cmd == "scan":
        return cmd_scan()
    elif cmd == "select":
        return cmd_select()
    elif cmd == "mirror":
        return cmd_mirror()
    elif cmd == "run":
        return cmd_run()
    elif cmd == "monitor":
        return cmd_monitor(with_recorder=with_recorder)
    elif cmd == "backfill":
        return cmd_backfill(argv)
    elif cmd in ("reply-buttons", "reply_buttons"):
        return cmd_reply_buttons(argv)
    elif cmd == "learn":
        return cmd_learn()
    elif cmd == "anchors":
        return cmd_anchors()
    elif cmd in ("cleanup-mirrors", "cleanup_mirrors", "cleanup"):
        return cmd_cleanup_mirrors(argv)
    elif cmd in ("rename-nv", "rename_nv"):
        return cmd_rename_nv(argv)
    elif cmd in ("strip-mirror", "strip_mirror"):
        return cmd_strip_mirror(argv)
    elif cmd in ("invite-member", "invite_member"):
        return cmd_invite_member(argv)
    elif cmd in ("rename-via-app", "rename_via_app"):
        return cmd_rename_via_app(argv)
    elif cmd in ("sync-mapping", "sync_mapping"):
        from core.mirror_cleanup import sync_room_mapping
        dry = "--dry-run" in argv
        print(f"[SYNC-MAPPING] 시작 (dry_run={dry})")
        result = sync_room_mapping(dry_run=dry)
        return 0 if not result["unmatched"] else 0  # unmatched 도 fatal 아님
    elif cmd in ("work-bridge", "work_bridge"):
        # 워크→카톡 자동 양방향 데몬 (Vision 룸리스트 델타 → 카톡 포워딩).
        # --dry-run : 송신 안 함(감지/필터만 로그)
        # --once    : 1사이클만
        # --interval N : 사이클 간격(초). 기본 20
        # --v2      : 본문읽기 방식(행클릭 열어 대화창 본문 → 워크 신규만)
        from core import work_bridge as _wb
        dry = "--dry-run" in argv
        once = "--once" in argv
        v2 = "--v2" in argv
        interval = 20
        if "--interval" in argv:
            i = argv.index("--interval")
            if i + 1 < len(argv):
                try:
                    interval = int(argv[i + 1])
                except ValueError:
                    pass
        return _wb.daemon(interval_sec=interval, once=once, dry_run=dry, v2=v2)
    elif cmd in ("adopt-new-rooms", "adopt_new_rooms", "adopt"):
        # 카톡 신규 초대방 자동 채택 → 워크 미러 자동 생성 + 등록 (그룹방만)
        from core import room_sync
        try:
            from core.window_manager import lock_kakaotalk_window
            lock_kakaotalk_window()
        except Exception:
            pass
        # 안전: 기본은 dry-run(보고만). 실제 생성은 --create 명시 필요.
        # (OCR 잡음으로 junk 방 대량생성 사고 방지)
        auto = "--create" in argv
        if not auto:
            print("[ADOPT] dry-run 모드 — 실제 생성 안 함. 생성하려면 --create 추가.")
        res = room_sync.adopt_new_rooms(auto_create=auto)
        print(f"[ADOPT] 결과: {res}")
        return 0
    elif cmd in ("auto-anchor", "auto_anchor"):
        return cmd_auto_anchor(argv)
    elif cmd == "metrics":
        return cmd_metrics(argv)
    elif cmd == "unlock":
        return cmd_unlock(argv)
    elif cmd == "calibrate":
        return cmd_calibrate(argv)
    elif cmd == "monitor-agentic":
        return cmd_monitor_agentic()
    elif cmd in ("learn-uploads", "learn_uploads", "upload-stats"):
        # 업로드 실패 ledger 요약 (기본 최근 24시간, 옵션: N시간 또는 'all')
        try:
            from core.upload_telemetry import render_text
        except Exception as e:
            print(f"[ERROR] upload_telemetry 로드 실패: {e}")
            return 1
        hours: int | None = 24
        if len(argv) > 2:
            a = argv[2].lower()
            if a == "all":
                hours = None
            else:
                try:
                    hours = int(a)
                except ValueError:
                    print(f"[ERROR] 인자는 정수 시간 또는 'all': {a}")
                    return 1
        print(render_text(hours))
        return 0
    elif cmd == "recover":
        # 수동 회복: 사용자가 친구 추가/광고 등 떴을 때 즉시 호출
        try:
            from core.computer_use_recovery import recover
            situation = " ".join(argv[2:]) if len(argv) > 2 else (
                "카카오톡 화면에 자동화를 막는 창(친구 추가/광고/팝업 등)이 떠있을 수 있음. "
                "확인 후 닫고 카카오톡 메인창을 활성화해줘."
            )
            ok = recover(situation)
            return 0 if ok else 1
        except Exception as e:
            print(f"[RECOVER] 실패: {e}")
            return 1
    else:
        print(f"[ERROR] 알 수 없는 명령: {cmd}")
        print(__doc__)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
