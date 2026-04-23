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

    room_name = result["room_name"]
    delta = result["delta"]
    # 작업 시작 배너
    print(f"\n  ┌─ 🔧 작업방: [{room_name}] 신규 {len(delta)}자 ─")

    # ── [사진] 감지 시 서랍에서 다운로드 ──
    # 실제 메시지 경계 기반 카운트 (substring 카운트 아님).
    # "사진 3장" 같은 묶음은 합산. 일반 텍스트에 "[사진]"이 끼어도 오탐 없음.
    from core.kakaowork_router import count_photo_messages
    downloaded_files = []
    photo_count = count_photo_messages(delta)
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
    )
    from core.badge_monitor import detect_badge_positions, badge_y_to_absolute
    from core.message_extractor import extract_from_room
    from core.kakaowork_router import send_to_mirror_room, send_delta_interleaved
    # upload_to_nv_room은 send_delta_interleaved 내부에서 lazy import
    # 레이아웃 기반 일괄 다운로드만 사용 (legacy 드로어 검증/좌측 스크롤 제거)
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

    # 상태 오버레이 시작
    overlay = get_overlay()

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

    captures_dir = ROOT / "captures"
    cycle = 0

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
    # 페이지 0 (맨 위) 부터 점점 아래로. 안읽음 방이 5개뿐이어도 p0 에서 바로 성공.
    # 이전 [19→0] 순서는 안읽음 방 적을 때 빈 영역 헛클릭 반복했음.
    PAGES = list(range(0, 20))  # [0, 1, ..., 19] — 맨 위부터

    try:
        while True:
            # ── 중지 버튼 체크 ──
            if overlay.should_stop:
                print("[MONITOR] 중지 버튼 클릭 -- 감시 종료")
                break

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
            ROOM_ETA_BASE = 5
            ROOM_ETA_PHOTO = 6
            n_pages = len(PAGES)
            DELAY = 0.7  # 모든 액션 간 고정 딜레이
            MAX_ITERATIONS_PER_PAGE = 15  # 같은 스크롤 위치에서 최대 시도 (중복 나오면 더 빨리 끝남)

            def _force_kakao_main_foreground_inline():
                """스크롤 리셋 없이 카톡 메인창만 foreground로."""
                try:
                    import win32gui as _w32
                    from core.window_manager import force_foreground as _ff
                    _hwnds: list = []
                    def _f(h, lst):
                        if _w32.IsWindowVisible(h) and _w32.GetWindowText(h) == "카카오톡":
                            lst.append(h)
                    _w32.EnumWindows(_f, _hwnds)
                    if _hwnds:
                        _ff(_hwnds[0])
                except Exception:
                    pass

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

            for p_i, page_idx in enumerate(PAGES, 1):
                if overlay.should_stop:
                    break

                overlay.set_status(f"페이지 {p_i}/{n_pages} (p{page_idx})")
                total_scroll = page_idx * PAGE_SCROLL
                print(f"  [페이지 {page_idx}] 스크롤 위치 -{total_scroll} — 맨 아래 행 반복 처리", flush=True)

                try:
                    # 9행 처리 — 아래(row 9, y=614) → 위(row 1, y=134)
                    # 매 행마다 리스트 리셋되므로 재스크롤 필수
                    row_ys = [_tp + 35 + i * SWEEP_ROW_HEIGHT for i in range(ROWS_PER_PAGE)]
                    # 위→아래 순회: 안읽음 방이 적을 때 상위 방부터 처리
                    # (이전: 하위부터 → 빈 영역 3회 스킵 → 실제 방 못 건드림)
                    rows_desc = row_ys  # 위→아래

                    # 연속 미열림 카운터 — N회 연속이면 페이지 나머지 스킵 (빈 행 낭비 제거)
                    consecutive_misses = 0
                    MAX_CONSECUTIVE_MISSES = 3

                    for iter_idx, row_y in enumerate(rows_desc, 1):
                        if overlay.should_stop:
                            break

                        overlay.set_status(f"p{p_i}/{n_pages} 행{iter_idx}/{ROWS_PER_PAGE} (y={row_y})")

                        # 매 행마다 재스크롤 + 해당 행 클릭
                        _force_kakao_main_foreground_inline()
                        _scroll_to_page(window, page_idx)

                        try:
                            t0 = time.time()
                            result = extract_from_room(CLICK_X, row_y, skip_titles=processed_this_cycle)
                            time.sleep(DELAY)

                            from core.run_analyzer import log_issue as _log_issue
                            if not result:
                                _log_issue("chat_didnt_open", cycle=cycle, page=page_idx, row=iter_idx,
                                           context={"click_xy": [CLICK_X, row_y]})
                                consecutive_misses += 1
                                print(f"     [p{page_idx} r{iter_idx}] y={row_y} 분리창 미열림 → 스킵 (연속 {consecutive_misses})", flush=True)
                                if consecutive_misses >= MAX_CONSECUTIVE_MISSES:
                                    print(f"     [p{page_idx}] {MAX_CONSECUTIVE_MISSES}회 연속 미열림 → 페이지 나머지 스킵", flush=True)
                                    break
                                continue
                            # 결과가 있으면 연속 카운터 리셋
                            consecutive_misses = 0

                            if result.get("_duplicate"):
                                _log_issue("duplicate_skip", cycle=cycle, page=page_idx, row=iter_idx,
                                           room=result.get("room_name"))
                                print(f"     [p{page_idx} r{iter_idx}] 이미 처리됨: {result.get('room_name','')[:20]}", flush=True)
                                continue

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
                        # 이슈방 전송
                        try:
                            report_issue("미완결 업무 경보", "\n".join(lines))
                        except Exception as _e:
                            print(f"[STALLED] 이슈방 전송 실패 (무시): {_e}")
                except Exception as e:
                    print(f"[STALLED] 체크 실패: {e}")

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
    if with_recorder:
        try:
            from core.learning_recorder import get_recorder, set_recorder
            r = get_recorder()
            if r:
                r.stop_and_save()
            set_recorder(None)
        except Exception as e:
            print(f"[MONITOR] recorder stop err: {e}")
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
    elif cmd == "learn":
        return cmd_learn()
    elif cmd == "anchors":
        return cmd_anchors()
    elif cmd in ("cleanup-mirrors", "cleanup_mirrors", "cleanup"):
        return cmd_cleanup_mirrors(argv)
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
