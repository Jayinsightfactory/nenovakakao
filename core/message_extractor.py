"""
Phase 1.5: Ctrl+S 저장 자동화 + 델타(신규 내용만) 추출

뱃지가 감지된 방을 클릭 → Ctrl+S → 저장 → txt 읽기 →
이전 내용과 비교하여 신규 라인만 추출 → ESC
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path

import pyautogui

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env", override=True)


def _safe_filename(name: str) -> str:
    """Windows 파일명 안전화: 금지 문자 치환 + 길이 제한."""
    safe = re.sub(r'[<>:"/\\|?*\n\r\t]', '_', name or "")
    safe = safe.strip(" .")
    return safe[:80] or "unknown"

# Ctrl+S 저장 경로 (카톡 기본: Documents/카카오톡 받은 파일)
KAKAO_SAVE_DIR = Path(os.getenv(
    "KAKAO_SAVE_DIR",
    "C:/Users/USER/Documents/카카오톡 받은 파일",
))

# 이전 처리 해시 저장
DATA_DIR = Path(__file__).parent.parent / "data"
USAGE_STATS = DATA_DIR / "usage_stats.json"
COLLECTED_DATA = DATA_DIR / "collected_data.jsonl"
# 방별 마지막 내용 저장 (델타 비교용)
LAST_CONTENT_DIR = DATA_DIR / "last_content"


def _load_usage_stats() -> dict:
    """처리 이력 로드"""
    if USAGE_STATS.exists():
        with open(USAGE_STATS, encoding="utf-8") as f:
            return json.load(f)
    return {"processed_hashes": []}


def _save_usage_stats(stats: dict):
    """처리 이력 저장"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(USAGE_STATS, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)


# ─── 해시 중복 차단 ───
# 구버전: stats["processed_hashes"] = [hash, hash, ...] (flat list, 1000 cap, 활동량
#         높은 한 방이 다른 방의 해시를 밀어내는 문제 있음)
# 신버전: stats["hashes_by_room"][room] = [hash, ...] (방별 100 cap, 균형 보장)
# 마이그레이션: 조회는 양쪽 모두, 신규 기록은 신형만.

_HASH_PER_ROOM_CAP = 100


def _hash_already_seen(stats: dict, room_name: str, content_hash: str) -> bool:
    by_room = stats.get("hashes_by_room", {})
    if content_hash in by_room.get(room_name, []):
        return True
    # 구형 flat list 도 조회 (마이그레이션 기간 false negative 방지)
    if content_hash in stats.get("processed_hashes", []):
        return True
    return False


def _remember_hash(stats: dict, room_name: str, content_hash: str) -> None:
    by_room = stats.setdefault("hashes_by_room", {})
    bucket = by_room.setdefault(room_name, [])
    bucket.append(content_hash)
    if len(bucket) > _HASH_PER_ROOM_CAP:
        del bucket[: len(bucket) - _HASH_PER_ROOM_CAP]


def _get_last_content(room_name: str) -> str:
    """방의 마지막 저장 내용을 가져온다 (방 이름 기반 파일명)."""
    LAST_CONTENT_DIR.mkdir(parents=True, exist_ok=True)
    safe = _safe_filename(room_name)
    path = LAST_CONTENT_DIR / f"{safe}.txt"
    if path.exists():
        return path.read_text(encoding="utf-8", errors="ignore")
    # 하위호환: 기존 MD5 해시 경로 → 마이그레이션
    legacy = LAST_CONTENT_DIR / f"{hashlib.md5(room_name.encode()).hexdigest()}.txt"
    if legacy.exists():
        content = legacy.read_text(encoding="utf-8", errors="ignore")
        try:
            path.write_text(content, encoding="utf-8")
            legacy.unlink()
        except Exception:
            pass
        return content
    return ""


def _save_last_content(room_name: str, content: str):
    """방의 현재 전체 내용을 저장 (다음 비교용, 방 이름 기반)."""
    LAST_CONTENT_DIR.mkdir(parents=True, exist_ok=True)
    safe = _safe_filename(room_name)
    (LAST_CONTENT_DIR / f"{safe}.txt").write_text(content, encoding="utf-8")


def extract_delta(old_content: str, new_content: str) -> str:
    """
    이전 내용과 새 내용을 비교하여 신규 라인만 추출.

    카카오톡 Ctrl+S 파일은 시간순 누적이므로,
    이전 마지막 라인 이후의 내용이 신규.
    """
    if not old_content.strip():
        return new_content  # 최초 수집 시 전체 반환

    old_lines = old_content.strip().splitlines()
    new_lines = new_content.strip().splitlines()

    if not new_lines:
        return ""

    # 이전 내용의 마지막 몇 줄로 매칭 포인트 찾기
    # (정확한 매칭을 위해 마지막 3줄 사용)
    match_lines = old_lines[-3:] if len(old_lines) >= 3 else old_lines

    # 새 내용에서 매칭 포인트를 찾는다
    match_target = "\n".join(match_lines)
    for i in range(len(new_lines) - len(match_lines), -1, -1):
        candidate = "\n".join(new_lines[i:i + len(match_lines)])
        if candidate == match_target:
            # 매칭 지점 이후가 신규
            delta_lines = new_lines[i + len(match_lines):]
            if delta_lines:
                return "\n".join(delta_lines)
            return ""  # 변경 없음

    # 매칭 실패 시 (대화가 크게 달라진 경우) 전체를 신규로 간주
    return new_content


def _get_latest_saved_file() -> Path | None:
    """카카오톡 저장 폴더에서 가장 최근 txt 파일 찾기"""
    pattern = str(KAKAO_SAVE_DIR / "**" / "*.txt")
    files = glob.glob(pattern, recursive=True)
    if not files:
        return None
    return Path(max(files, key=os.path.getmtime))


def _status(msg: str) -> None:
    """오버레이 상태 업데이트 (실패 무시)."""
    try:
        from core.status_overlay import get_overlay
        get_overlay().set_status(msg)
    except Exception:
        pass


def _force_kakao_main_foreground() -> None:
    """카톡 메인창 강제 포커스 탈취 (Claude/다른 창의 포커스 강탈 대응)."""
    try:
        import win32gui
        from core.window_manager import force_foreground
        hwnds: list[int] = []
        def _f(h, lst):
            if win32gui.IsWindowVisible(h) and win32gui.GetWindowText(h) == "카카오톡":
                lst.append(h)
        win32gui.EnumWindows(_f, hwnds)
        if hwnds:
            force_foreground(hwnds[0])
    except Exception:
        pass


def close_all_chat_separators() -> int:
    """현재 열린 모든 카톡 분리창 전부 WM_CLOSE. 반환: 닫은 개수.

    사이클 시작 시 호출하여 깨끗한 상태로 시작.
    """
    try:
        import win32con
        import win32gui
        titles = _list_chat_room_titles()
        closed = 0
        for t in titles:
            try:
                hwnd = _find_hwnd_by_title(t)
                if hwnd:
                    win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
                    closed += 1
            except Exception:
                continue
        if closed:
            time.sleep(0.5)
        return closed
    except Exception:
        return 0


def _list_chat_room_titles() -> set[str]:
    """현재 visible한 카톡 채팅 분리창 제목 전부 수집 (selected_rooms.json과 매칭)."""
    try:
        import json
        import win32gui
        from pathlib import Path

        sel_file = Path(__file__).parent.parent / "data" / "selected_rooms.json"
        names: set[str] = set()
        if sel_file.exists():
            for r in json.loads(sel_file.read_text(encoding="utf-8")):
                names.add(r["name"] if isinstance(r, dict) else r)

        # selected_rooms 안에 없는 다른 방 이름도 감지 위해 느슨한 기준으로 폴백
        results: set[str] = set()

        def _f(h, _):
            if not win32gui.IsWindowVisible(h):
                return
            t = win32gui.GetWindowText(h)
            if not t:
                return
            if t == "카카오톡":
                return
            # 보호 대상 (카톡 분리창이 아닌 다른 앱/시스템 창)
            EXCLUDED = [
                "Claude", "액션 로그", "상태", "Chrome", "Edge", "Firefox",
                "Terminal", "cmd.exe", "VS Code", "Visual Studio",
                "Program Manager",  # Windows 바탕화면
                "Microsoft", "Windows",
                "Explorer", "Notepad", "MSN", "Cortana",
            ]
            if any(k in t for k in EXCLUDED):
                return
            # 저장 다이얼로그 제외
            if any(k in t for k in ["저장", "Save", "다른 이름", "확인", "열기", "Open"]):
                return
            # selected_rooms 매칭 or 적당한 크기
            r = win32gui.GetWindowRect(h)
            w, hh = r[2] - r[0], r[3] - r[1]
            if t in names or (300 <= w <= 800 and 400 <= hh <= 1000):
                results.add(t)

        win32gui.EnumWindows(_f, None)
        return results
    except Exception:
        return set()


def click_room(x: int, y: int) -> str | None:
    """방 클릭 → 새로 열린 분리창 감지 (포커스 무관, EnumWindows 기반)."""
    from core.traced_actions import mark

    _status(f"방 클릭 ({x},{y})")
    # 클릭 전 분리창 목록 스냅샷
    before = _list_chat_room_titles()

    _force_kakao_main_foreground()
    mark("click_room.single", "before", {"xy": [x, y]})
    pyautogui.click(x, y)
    time.sleep(0.3)
    mark("click_room.single", "after")

    _force_kakao_main_foreground()
    mark("click_room.double", "before")
    pyautogui.doubleClick(x, y)
    mark("click_room.double", "after")

    # 분리창 목록 변화 감지 — 최대 3초
    opened_title: str | None = None
    end_ts = time.time() + 3.0
    while time.time() < end_ts:
        after = _list_chat_room_titles()
        new_titles = after - before
        if new_titles:
            # 여러 개 새로 뜨면 일단 하나 선택
            opened_title = next(iter(new_titles))
            break
        time.sleep(0.1)

    # 새 창이 못 뜬 경우: 포커스 기반 폴백
    if not opened_title:
        try:
            import win32gui
            fg = win32gui.GetForegroundWindow()
            t = win32gui.GetWindowText(fg) if fg else ""
            # 시스템 창/다른 앱 전부 배제
            EXCLUDED = ("카카오톡", "Claude", "액션 로그", "상태", "Program Manager",
                        "Chrome", "Edge", "Terminal", "cmd.exe", "VS Code",
                        "Microsoft", "Windows", "Explorer")
            if t and not any(k in t for k in EXCLUDED):
                opened_title = t
        except Exception:
            pass

    if opened_title:
        mark("click_room.opened", "after", {"xy": [x, y], "title": opened_title[:40]})
        _status(f"분리창: {opened_title[:20]}")
    else:
        mark("click_room.opened", "fail", {"xy": [x, y]})
        _status(f"분리창 미감지 ({x},{y})")
    return opened_title


def _all_save_dirs() -> list[Path]:
    """카톡 저장 가능 경로 목록 (설정 + 기본값 + 관찰된 실제 경로)."""
    dirs = [
        KAKAO_SAVE_DIR,
        Path("C:/Users/USER/Documents/카카오톡 받은 파일"),
        Path("C:/Users/USER/Documents"),
        Path("C:/Users/USER/Downloads"),
    ]
    seen: set[Path] = set()
    out: list[Path] = []
    for d in dirs:
        if d in seen or not d.exists():
            continue
        seen.add(d)
        out.append(d)
    return out


def _snapshot_txt_files() -> set[str]:
    """모든 저장 경로의 KakaoTalk_*.txt 파일 스냅샷 (루트 한 레벨만)."""
    result: set[str] = set()
    for d in _all_save_dirs():
        if d == Path("C:/Users/USER/Documents") or d == Path("C:/Users/USER/Downloads"):
            # 루트는 무한 재귀 방지 - KakaoTalk_*.txt 패턴만 1레벨
            for p in d.glob("KakaoTalk_*.txt"):
                result.add(str(p))
        else:
            for p in d.rglob("KakaoTalk_*.txt"):
                result.add(str(p))
    return result


def save_chat_with_ctrl_s(room_name: str | None = None, chat_hwnd: int | None = None) -> Path | None:
    """
    현재 열린 채팅방에서 Ctrl+S → 저장 다이얼로그 → 절대경로 강제 입력 → Enter.
    KAKAO_SAVE_DIR로 강제 저장 (OneDrive 등 카톡 기본 경로 회피).

    파일명 규칙:
      - room_name 지정 → "{방이름}_{ms}.txt"
      - 미지정 → "kakao_{ms}.txt"
    어느 쪽이든 ms 타임스탬프로 유일성 확보 → 덮어쓰기 팝업 방지.

    학습 포인트: ctrl_s.{triggered,path_forced,dialog_enter,overwrite_y,file_found}
    """
    from core.traced_actions import mark
    from core.safety_guard import (
        SafetyAbort, safe_hotkey, safe_press, safe_paste,
        pre_action_guard, _matches, get_foreground_title,
    )

    # 안전 가드: 카톡 창 활성 상태 확인 + 위험 팝업 자동 처치
    # 실패 시 강제 재활성화 후 1회 재시도
    try:
        if not pre_action_guard("kakaotalk"):
            try:
                from core.window_manager import focus_kakaotalk as _focus
                _focus()
                time.sleep(0.5)
            except Exception:
                pass
            if not pre_action_guard("kakaotalk"):
                mark("ctrl_s.triggered", "fail", {"reason": "safety guard: 카톡 비활성 (재활성화 실패)"})
                return None
    except Exception as e:
        print(f"  [SAFE] 가드 호출 에러: {e}", flush=True)
        return None

    before_files = _snapshot_txt_files()

    # 저장 폴더 보장
    KAKAO_SAVE_DIR.mkdir(parents=True, exist_ok=True)
    ts_ms = int(time.time() * 1000)
    stem = _safe_filename(room_name) if room_name else "kakao"
    target_path = KAKAO_SAVE_DIR / f"{stem}_{ts_ms}.txt"

    try:
        # Ctrl+S 직전: chat_hwnd가 실제 foreground가 될 때까지 재시도
        _status("분리창 강제 포커스")
        if chat_hwnd:
            try:
                import win32gui
                from core.window_manager import force_foreground
                for _ in range(8):  # 최대 8회 × 0.15s = 1.2s
                    force_foreground(chat_hwnd)
                    time.sleep(0.15)
                    if win32gui.GetForegroundWindow() == chat_hwnd:
                        break
            except Exception:
                pass

        # Ctrl+S — 분리창이 foreground 상태에서 원시 pyautogui로
        _status("Ctrl+S 저장 트리거")
        mark("ctrl_s.triggered", "before")
        pyautogui.hotkey("ctrl", "s")
        mark("ctrl_s.triggered", "after")

        # ⚠️ 중요: 카톡에서 Ctrl+A = "친구 추가" 단축키!
        # 저장 다이얼로그가 실제로 뜬 것을 확인한 후에만 후속 액션 실행.
        # 최대 5초 폴링.
        dialog_ready = False
        for _ in range(25):  # 25 × 0.2s = 5s
            time.sleep(0.2)
            fg_title = get_foreground_title()
            if any(k in fg_title for k in ["저장", "Save", "다른 이름", "이름"]):
                dialog_ready = True
                _status(f"다이얼로그 감지: {fg_title[:20]}")
                break

        if not dialog_ready:
            mark("ctrl_s.triggered", "fail", {"reason": "dialog not appeared"})
            _status("다이얼로그 미감지 → 스킵")
            return None

        # 저장 다이얼로그 hwnd 찾아서 강제 포커스 — 로그창/Program Manager가
        # 포커스 뺏더라도 다이얼로그로 재탈취해서 Ctrl+V / Enter를 안전하게 전달.
        import win32gui
        def _find_save_dialog() -> int | None:
            results: list[int] = []
            def _f(h, lst):
                if not win32gui.IsWindowVisible(h):
                    return
                t = win32gui.GetWindowText(h)
                if any(k in t for k in ["저장", "Save", "다른 이름"]):
                    lst.append(h)
            win32gui.EnumWindows(_f, results)
            return results[0] if results else None

        from core.window_manager import force_foreground
        dialog_hwnd = _find_save_dialog()

        # paste 전 강제 포커스 + 원시 pyautogui (safety guard 우회)
        _status(f"경로 paste: {target_path.name[:20]}")
        mark("ctrl_s.path_forced", "before", {"target": str(target_path)})
        if dialog_hwnd:
            force_foreground(dialog_hwnd)
            time.sleep(0.15)
        try:
            import pyperclip
            pyperclip.copy(str(target_path))
        except Exception:
            pass
        pyautogui.hotkey("ctrl", "v")
        time.sleep(0.3)
        mark("ctrl_s.path_forced", "after")

        # Enter — 다시 다이얼로그 강제 포커스 후
        _status("저장 Enter")
        mark("ctrl_s.dialog_enter", "before")
        if dialog_hwnd:
            force_foreground(dialog_hwnd)
            time.sleep(0.15)
        pyautogui.press("enter")
        time.sleep(2.0)
        mark("ctrl_s.dialog_enter", "after")

        # 덮어쓰기 팝업 → Y (기본은 '아니요')
        ft = get_foreground_title()
        if any(k in ft for k in ["확인", "있습니다", "바꾸", "Replace"]):
            mark("ctrl_s.overwrite_y", "before", {"title": ft})
            safe_press("y", expected="any")
            time.sleep(1.0)
            mark("ctrl_s.overwrite_y", "after")

        # "대화내보내기 완료되었습니다" 확인 팝업 닫기 (Enter 또는 ESC)
        # 카톡이 저장 성공 후 출력하는 confirmation popup — 닫지 않으면 후속 작업 차단
        time.sleep(1.0)
        for _ in range(3):
            ft2 = get_foreground_title()
            if any(k in ft2 for k in ["완료", "내보내기", "완료되었", "저장되었"]):
                try:
                    pyautogui.press("enter")
                    time.sleep(0.3)
                except Exception:
                    pass
            else:
                break

    except SafetyAbort as e:
        mark("ctrl_s.aborted", "fail", {"reason": str(e)})
        print(f"  [SAFE-ABORT] save_chat_with_ctrl_s: {e} → 스킵 (agentic 비활성화)", flush=True)
        try:
            import pyautogui as _pa
            _pa.press("escape")
        except Exception:
            pass
        # agentic fallback 제거 — 10~30초 공회전 방지. 다음 사이클에서 재시도.
        return None

    # 의도한 경로에 파일 생성됐는지 우선 확인
    if target_path.exists():
        mark("ctrl_s.file_found", "after", {"path": str(target_path), "forced": True})
        return target_path

    # fallback: 새로 생성된 파일 (다른 경로일 수도)
    after_files = _snapshot_txt_files()
    new_files = after_files - before_files
    if new_files:
        path = Path(max(new_files, key=os.path.getmtime))
        mark("ctrl_s.file_found", "after", {"path": str(path), "forced": False})
        return path

    fallback = _get_latest_saved_file()
    if fallback:
        mark("ctrl_s.file_found", "after", {"path": str(fallback), "fallback": True})
    else:
        mark("ctrl_s.file_found", "fail")
    return fallback


def close_chat_room(room_title: str | None = None):
    """채팅방 닫기 — ESC + 분리창 hwnd 직접 WM_CLOSE.

    room_title을 지정하면 그 제목의 창을 찾아 명시적으로 닫음.
    (다음 doubleClick이 제대로 새 분리창을 열도록 보장)
    """
    try:
        pyautogui.press("escape")
        time.sleep(0.3)
    except Exception:
        pass

    # 해당 분리창을 hwnd로 직접 닫기
    if room_title:
        try:
            import win32con
            import win32gui
            hwnd = _find_hwnd_by_title(room_title)
            if hwnd:
                win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
                time.sleep(0.3)
        except Exception:
            pass


def _unlink_quiet(p: Path | None) -> None:
    """파일 삭제 실패는 조용히 무시."""
    try:
        if p and p.exists():
            p.unlink()
    except Exception as e:
        print(f"  [CLEANUP] 원본 삭제 실패 ({p.name}): {e}", flush=True)


def read_and_process_saved_file(file_path: Path) -> dict | None:
    """
    저장된 txt 파일을 읽고, 이전 대비 신규 내용만 추출.
    - 저장된 원본을 `KAKAO_SAVE_DIR/{방이름}.txt`로 표준화(이동/덮어쓰기)
    - 처리 완료(성공/실패 무관) 시 최종 경로의 파일은 즉시 삭제

    Returns:
        {"room_name": str, "content": str, "delta": str,
         "has_new": bool, "timestamp": str, "file_path": str}
        또는 None (파일 없음/비어있음/변경 없음)
    """
    if not file_path or not file_path.exists():
        return None

    current_path = file_path
    try:
        content = current_path.read_text(encoding="utf-8", errors="ignore")
        if not content.strip():
            return None

        # 방 이름 추출: 파일 내용 첫 줄 우선 (예: "수입방 임과 카카오톡 대화")
        room_name = current_path.stem
        first_line = content.strip().splitlines()[0] if content.strip() else ""
        if "카카오톡 대화" in first_line:
            parts = first_line.split("카카오톡 대화")[0].strip()
            for suffix in ["님과", "임과", "과"]:
                if parts.endswith(suffix):
                    parts = parts[:-len(suffix)].strip()
                    break
            if parts:
                room_name = parts
        elif "카카오톡" in room_name:
            parts = current_path.stem.split(" - ", 1)
            if len(parts) > 1:
                room_name = parts[1]

        # 저장 위치/이름 표준화: KAKAO_SAVE_DIR/{방이름}.txt로 이동 (덮어쓰기)
        try:
            KAKAO_SAVE_DIR.mkdir(parents=True, exist_ok=True)
            dst = KAKAO_SAVE_DIR / f"{_safe_filename(room_name)}.txt"
            if dst.resolve() != current_path.resolve():
                if dst.exists():
                    dst.unlink()
                current_path = Path(current_path.replace(dst))
        except Exception as e:
            print(f"  [RENAME] 표준화 실패 ({room_name}): {e}", flush=True)

        # MD5 해시로 전체 내용 변경 여부 확인
        content_hash = hashlib.md5(content.encode("utf-8")).hexdigest()
        stats = _load_usage_stats()

        # 중복 해시 확인 — 신형(per-room dict) + 구형(flat list) 양쪽 조회
        if _hash_already_seen(stats, room_name, content_hash):
            return None  # 완전히 동일한 내용 — 변경 없음

        # 이전 내용과 비교하여 델타(신규분만) 추출
        old_content = _get_last_content(room_name)
        delta = extract_delta(old_content, content)

        if not delta.strip():
            _remember_hash(stats, room_name, content_hash)
            _save_usage_stats(stats)
            return None

        _save_last_content(room_name, content)

        _remember_hash(stats, room_name, content_hash)
        _save_usage_stats(stats)

        result = {
            "room_name": room_name,
            "content": content,
            "delta": delta,
            "has_new": True,
            "timestamp": datetime.now().isoformat(),
            "file_path": str(current_path),
            "content_hash": content_hash,
        }

        DATA_DIR.mkdir(parents=True, exist_ok=True)
        record = {
            "room_name": room_name,
            "delta": delta,
            "timestamp": result["timestamp"],
            "content_hash": content_hash,
        }
        with open(COLLECTED_DATA, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

        return result
    finally:
        # 최종 경로의 txt는 처리 성공/실패와 관계없이 즉시 삭제 (누적 방지)
        _unlink_quiet(current_path)
        # 원본 경로가 다른 곳에 남아있으면 그것도 삭제
        if current_path.resolve() != file_path.resolve():
            _unlink_quiet(file_path)


def _detect_room_name_from_foreground() -> str | None:
    """클릭 직후 foreground 창 제목에서 방 이름 추출.

    - 분리창 모드: 제목 = 방 이름
    - 단일창 모드: 제목 = "카카오톡" (방 이름 모름 → None)
    """
    try:
        import win32gui
        fg = win32gui.GetForegroundWindow()
        if not fg:
            return None
        title = win32gui.GetWindowText(fg) or ""
        if not title or title == "카카오톡":
            return None
        return title.strip()
    except Exception:
        return None


def _find_hwnd_by_title(title: str) -> int | None:
    """제목이 정확히 일치하는 visible 창의 hwnd 반환."""
    try:
        import win32gui
        results: list[int] = []
        def _f(h, lst):
            if win32gui.IsWindowVisible(h) and win32gui.GetWindowText(h) == title:
                lst.append(h)
        win32gui.EnumWindows(_f, results)
        return results[0] if results else None
    except Exception:
        return None


def extract_from_room(
    x: int, y: int,
    room_name: str | None = None,
    *,
    skip_titles: set | None = None,
) -> dict | None:
    """
    방 클릭 → 분리창 강제 포커스 → Ctrl+S → 저장 → 읽기 → 닫기.

    Args:
        skip_titles: 이미 처리된 방 제목 set. 분리창 열리자마자 매칭되면
                     Ctrl+S 전에 즉시 스킵 (카톡 리스트 재정렬 대비).
    """
    opened_title = click_room(x, y)
    if not opened_title:
        _status(f"행 미열림 ({x},{y}) 스킵")
        return None

    # 중복 체크 — 이미 이번 사이클에서 처리한 방이면 Ctrl+S 전에 즉시 스킵
    if skip_titles and opened_title in skip_titles:
        _status(f"중복 스킵: {opened_title[:18]}")
        close_chat_room(room_title=opened_title)
        return {"room_name": opened_title, "delta": "", "has_new": False, "_duplicate": True}

    # 분리창 hwnd 찾기
    chat_hwnd = _find_hwnd_by_title(opened_title)

    if not room_name:
        room_name = opened_title

    saved_file = save_chat_with_ctrl_s(room_name=room_name, chat_hwnd=chat_hwnd)
    _status("파일 읽기 + 델타 추출")
    result = read_and_process_saved_file(saved_file)

    # delta 에 [사진] 있으면 분리창을 닫지 않음 — main.py 의 사진 다운로드 경로에서 재사용.
    # main.py 는 downstream 에서 창 닫기 책임짐 (close_chat_room 수동 호출).
    has_photos = False
    if result and isinstance(result, dict):
        delta_text = result.get("delta", "") or ""
        try:
            from core.kakaowork_router import count_photo_messages
            has_photos = count_photo_messages(delta_text) > 0
        except Exception:
            has_photos = "[사진]" in delta_text

    if has_photos:
        _status(f"[사진] 감지 → 분리창 유지: {opened_title[:18]}")
    else:
        _status(f"분리창 닫기: {opened_title[:18]}")
        close_chat_room(room_title=opened_title)

    # result가 None(변경 없음)이어도 opened_title 정보 반환 — main.py가 processed 추적 가능
    if result is None:
        # 사진 없으면 이미 위에서 닫았음. 사진 delta 없으므로 has_photos=False 가 확실.
        return {
            "room_name": opened_title,
            "delta": "",
            "has_new": False,
            "_no_change": True,
        }
    return result
