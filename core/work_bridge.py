"""
워크→카톡 자동 양방향 브릿지 (Vision 룸리스트 델타 기반).

설계:
  1. 주기적(N초)으로 KW 룸리스트 캡처 → Claude Opus 추출
  2. 직전 상태와 diff → preview 가 변경된 방 = 새 메시지 도착
  3. 그 메시지가 "우리 봇이 워크로 보낸 메시지"면 skip(무한루프 방지)
  4. 매핑된 카톡 방 이름으로 해석 → kakao_win32.send_message_to_room 으로 카톡 송신
  5. 카톡 락(_kakao_lock) 우선 요청 → 모니터/답장서버와 자동 조정

상태 파일:
  data/work_vision_state.json   — 직전 사이클 룸리스트 (delta 비교용)
  data/work_sent_recent.json    — 우리가 워크로 보낸 최근 메시지 (방당 최대 N건)

CLI:
  python main.py work-bridge                 # 데몬 (interval 20s, 실제 송신)
  python main.py work-bridge --dry-run       # 송신 안 함, 감지/필터링만 로그
  python main.py work-bridge --once          # 1사이클만 (테스트)
  python main.py work-bridge --interval 30   # 30초마다
"""
from __future__ import annotations

import os
import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SENT_RECENT = DATA / "work_sent_recent.json"
VISION_STATE = DATA / "work_vision_state.json"
WORK_SENT_LEDGER = DATA / "work_bridge_sent_ledger.json"  # v2: 카톡으로 중계한 메시지 해시
COLLECTED = DATA / "collected_data.jsonl"  # 카톡→워크 미러 수집기록(원본 카톡 내용) — W→K 에코차단 대조용
MAX_PER_ROOM = 40  # 방당 최근 N건 보관 (loop 필터링)
SENT_TTL_SEC = 7200  # 2시간 — 그 이후 entry 만료 (메모리/판별 단순화)


# ─────────────────────────────────────────────────────────
# v2: 카톡으로 이미 중계한 메시지 ledger (재전송 방지)
# ─────────────────────────────────────────────────────────
import hashlib as _hashlib


def _v2_msg_key(kakao_room: str, m: dict) -> str:
    s = f"{kakao_room}|{m.get('sender','')}|{m.get('time','')}|{(m.get('content','') or '')[:80]}"
    return _hashlib.md5(s.encode("utf-8", errors="ignore")).hexdigest()


def _v2_load_ledger() -> set:
    try:
        return set(json.loads(WORK_SENT_LEDGER.read_text(encoding="utf-8")))
    except Exception:
        return set()


def _v2_save_ledger(s: set) -> None:
    try:
        DATA.mkdir(parents=True, exist_ok=True)
        keep = list(s)[-4000:]  # 무한증가 방지
        WORK_SENT_LEDGER.write_text(json.dumps(keep, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


# ─────────────────────────────────────────────────────────
# 1) 우리 봇이 워크로 보낸 메시지 기록 (loop 방지)
# ─────────────────────────────────────────────────────────

def append_sent(kakaotalk_room: str, text: str) -> None:
    """워크 미러방에 봇으로 보낸 메시지 1건 기록. 키 = 카톡 방 이름.

    호출자: kakaowork_router._send_single 직후 (텍스트), send_to_mirror_room,
    send_reply_button, kakaowork_reactive._post_send_confirmation 등.
    """
    if not kakaotalk_room or not text:
        return
    try:
        DATA.mkdir(parents=True, exist_ok=True)
        try:
            data = json.loads(SENT_RECENT.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        if not isinstance(data, dict):
            data = {}
        now = time.time()
        bucket = data.get(kakaotalk_room) or []
        bucket = [e for e in bucket if isinstance(e, dict) and now - (e.get("ts") or 0) < SENT_TTL_SEC]
        bucket.append({"text": text, "ts": now})
        if len(bucket) > MAX_PER_ROOM:
            bucket = bucket[-MAX_PER_ROOM:]
        data[kakaotalk_room] = bucket
        SENT_RECENT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


# 봇이 워크방에 남기는 시스템 메시지의 preview 시그니처.
# 이게 preview 로 잡히면 사용자 메시지가 아니므로 절대 카톡으로 보내지 않는다.
_BOT_SYSTEM_MARKERS = (
    "방으로 답장",        # send_reply_button: "💬 'X' 방으로 답장"
    "[카톡 미러]",         # send_to_mirror_room 헤더
    "📤 카톡으로 전송",     # reactive 전송 기록
    "📤 카톡 답장",         # 답장 버튼 라벨
    "✅ 카톡 반영 확인",    # 캡처 확인
    "⚠️ 전송됨",
    "💬 '",               # 답장 버튼 텍스트 시작
    "📦 [백필]",
    "[사진]",             # 모니터 미러 사진 헤더 "[발신자] [시각] [사진]"
    "다운로드 실패",       # 사진 다운로드 실패 fallback
)


import re as _re_sys

# 카톡으로 보내면 안 되는 '비-사용자' 메시지(시스템/UI/봇잔재) 패턴
_NON_USER_PATTERNS = (
    _re_sys.compile(r"^\d{4}년\s*\d{1,2}월\s*\d{1,2}일"),   # 날짜 구분선
    _re_sys.compile(r"여기까지\s*읽으셨"),                  # 읽음 표시
    _re_sys.compile(r"채팅방\s*이름을\s*변경"),             # 시스템: 이름변경
    _re_sys.compile(r"님(이|을)\s*.*?(들어왔|나갔|초대|입장|퇴장|변경)"),  # 입퇴장/초대/변경
    _re_sys.compile(r"^\[?카톡\s*답장\]?$"),                # 답장버튼 라벨
    _re_sys.compile(r"^https?://"),                          # URL 단독(봇 이미지 등)
    _re_sys.compile(r"^\[?(사진|동영상|이모티콘|음성메시지|보이스톡|페이스톡|파일|선물)\]?$"),  # 미디어 placeholder(미러 흔적, 본문 아님)
    _re_sys.compile(r"^\s*$"),                               # 빈 줄
)


def _is_non_user_message(text: str) -> bool:
    """날짜선/읽음표시/입퇴장/이름변경/답장버튼/URL 등 = 사용자 메시지 아님."""
    t = (text or "").strip()
    if not t or len(t) < 2:
        return True
    return any(p.search(t) for p in _NON_USER_PATTERNS)


def _looks_like_mirror_header(preview: str) -> bool:
    """모니터가 카톡→워크 미러할 때 쓰는 "[발신자] [시각] 내용" 형식이면 True.
    이 형식은 사람이 워크에서 직접 치는 답장이 아니라 봇 미러이므로 카톡으로 안 보냄.

    시각 대괄호는 반드시 닫혀야(]) 매치 — '[공지] [10:00 시작] ...' 같은 실제 사용자
    메시지를 미러로 오인해 삭제하던 false-positive 방지(code-review). 이름 40자까지.
    """
    import re as _re2
    p = (preview or "").strip()
    return bool(_re2.match(r"^\[[^\]]{1,40}\]\s*\[(?:오전|오후)?\s*\d{1,2}:\d{2}\]", p))


def _is_bot_system_preview(preview: str) -> bool:
    """preview 가 봇이 워크에 남긴 시스템 메시지면 True (무한 에코 차단)."""
    p = (preview or "").strip()
    if not p:
        return False
    return any(mk in p for mk in _BOT_SYSTEM_MARKERS)


# ─────────────────────────────────────────────────────────
# 미러 복사본 vs 워크 네이티브 구분 (발신자 화이트리스트)
# ─────────────────────────────────────────────────────────
_WORK_MEMBERS_CACHE: list | None = None


def _norm_name(s: str) -> str:
    s = (s or "").strip()
    for suf in ("님", "씨"):
        if s.endswith(suf):
            s = s[: -len(suf)]
    return s.replace(" ", "")


# 카카오워크는 '로그인 계정 본인'이 보낸 메시지를 화면 오른쪽에 이름표 없이 표시한다.
# Vision 은 이 오른쪽(본인) 말풍선의 발신자를 '나'(또는 '내가/본인/me')로 읽는다.
# 이 PC 워크앱은 워크 실멤버(네노바)로 로그인돼 있으므로 '나' = 네이티브(직접 친 글) → 카톡 전송 대상.
# (과거 버그: '나'가 멤버목록에 없어 _is_mirror_origin 이 전부 미러로 차단 → W→K 0건)
_SELF_SENDERS = {"나", "내가", "본인", "(나)", "me"}


def _is_self_sender(sender_norm: str) -> bool:
    """정규화된 발신자가 '로그인 계정 본인'(오른쪽 정렬 말풍선)인가."""
    if not sender_norm:
        return False
    return sender_norm in _SELF_SENDERS or sender_norm.lower() == "me"


def _self_display_name() -> str:
    """'나'(본인) 메시지를 카톡으로 보낼 때 붙일 작성자 표기. 기본 '네노바'(로그인 계정).
    환경변수 NENOVA_WORK_SELF_NAME 로 변경 가능."""
    return (os.environ.get("NENOVA_WORK_SELF_NAME") or "네노바").strip()


def _display_sender(raw) -> str:
    """카톡 전송 시 붙일 작성자 라벨. '나'/본인 표기는 실제 로그인 계정명으로 치환."""
    s = (raw or "").strip()
    if _is_self_sender(_norm_name(s)):
        return _self_display_name()
    return s


_WORK_MEMBERS_WARNED = False


def _work_member_names() -> list[str]:
    """data/kakaowork_users.json 의 워크 실멤버 이름(정규화) 목록.

    ⚠️ 성공(비어있지 않게 로드)했을 때만 캐시한다. 실패/빈값이면 캐시하지 않고
    빈 리스트 반환 → 다음 호출에서 재시도(일시적 I/O 오류가 필터를 영구 무력화하지 않게).
    """
    global _WORK_MEMBERS_CACHE
    if _WORK_MEMBERS_CACHE:
        return _WORK_MEMBERS_CACHE
    names: list[str] = []
    try:
        data = json.loads((DATA / "kakaowork_users.json").read_text(encoding="utf-8"))
        for u in data:
            for k in ("display_name", "name", "nickname"):
                v = _norm_name(u.get(k) or "")
                if v and v not in names:
                    names.append(v)
    except Exception:
        return []  # 미캐시 → 다음 호출 재시도
    if names:
        _WORK_MEMBERS_CACHE = names
    return names


# ─────────────────────────────────────────────────────────
# 봇/미러 복사본 판별 + 이모지반응 판별 (사용자 기준 2026-06-08)
#   · 워크 본문이 '['로 시작 → 봇 미러("[발신자][시각] 본문" / "[카톡 미러]…" / "[사진]…").
#     봇은 항상 대괄호 헤더로 올리므로 이게 가장 단순·견고한 봇 판별이다.
#   · 그 외엔 발신자 화이트리스트 없이 전부 카톡 전송(사람이 직접 친 네이티브로 봄).
#   · 단, 이모지/기호 반응만 있는 글은 안 보냄.
# (과거 발신자 화이트리스트 방식은 '나'·비멤버를 과차단 → W→K 0건 사고. 폐기.)
# ─────────────────────────────────────────────────────────
def _is_bot_or_mirror(m: dict, content: str | None = None) -> bool:
    """카톡으로 되보내면 안 되는 봇/미러 복사본이면 True.

    판별(사용자 기준):
      1) 본문이 여는 대괄호('[' 등)로 시작 → 봇이 올린 미러("[발신자][시각] 본문").
      2) 봇 시스템 마커(_BOT_SYSTEM_MARKERS) 포함.
      3) 발신자/본문에 봇 이름(알림봇/복사봇).
    사람이 워크에서 직접 친 글은 '['로 시작하지 않으므로 발신자와 무관하게 전송 대상.
    """
    c = (content if content is not None else (m.get("content") or "")).strip()
    if c[:1] in ("[", "【", "〔", "「", "［"):   # 반각/전각 여는 대괄호
        return True
    if _is_bot_system_preview(c):
        return True
    _bj = (_norm_name(m.get("sender", "")) + c).replace(" ", "")
    if any(t in _bj for t in ("알림봇", "복사봇", "주문알림봇")):
        return True
    return False


_TEXT_CHAR_RE = _re_sys.compile(r"[0-9A-Za-z가-힣ㄱ-ㅎㅏ-ㅣ一-鿿぀-ヿ]")


def _is_emoji_only(content: str) -> bool:
    """의미있는 텍스트(한글·영숫자·한자·가나)가 하나도 없고 이모지/기호/반응만이면 True.
    예: '👍😊😊🤩' → True(안 보냄). '수국 23-2차 시작재고' → False(보냄).
    빈 문자열도 True(보낼 내용 없음)."""
    s = (content or "").strip()
    if not s:
        return True
    return not _TEXT_CHAR_RE.search(s)


# ─────────────────────────────────────────────────────────
# 카톡-기원 내용 대조 (W→K 에코 차단의 핵심 — 사용자 기준 2026-06-08)
#   "카톡→워크로 미러한 내용을 워크→카톡으로 되보내면 안 된다."
#   collected_data.jsonl(카톡 원본 수집기록)의 '해당 카톡방' 최근 delta 안에 워크 본문이
#   들어있으면 = 카톡에서 온 미러 → 전송 안 함. 발신자 표기에 의존 안 해 견고
#   (Vision 이 미러 헤더 "[발신자][시각]"를 sender 로 쪼개 읽어도 내용으로 잡힌다).
# ─────────────────────────────────────────────────────────
_KAKAO_CONTENT_CACHE = {"mtime": None, "size": None, "by_room": {}}
_KAKAO_TAIL_BYTES = 3_000_000   # collected_data 끝에서 이만큼만 인덱싱(최근분)
_KAKAO_MIN_MATCH_LEN = 6        # 정규화 후 이 길이 미만이면 대조 안 함(짧은 글 오매칭 방지)
_MATCH_KEEP_RE = _re_sys.compile(r"[^0-9A-Za-z가-힣一-鿿]")  # 한글·영숫자·한자 외 전부 제거


def _norm_for_match(s: str) -> str:
    """대조용 정규화: 한글·영숫자·한자만 남기고 공백/기호/대괄호/이모지 제거 + 소문자.
    (OCR 띄어쓰기·구두점·헤더 대괄호 변동을 흡수)"""
    return _MATCH_KEEP_RE.sub("", (s or "")).lower()


def _build_kakao_index() -> dict:
    """collected_data.jsonl 끝부분을 읽어 {정규화방이름: [정규화delta, ...]} 인덱스.
    mtime/size 안 변하면 캐시 재사용."""
    global _KAKAO_CONTENT_CACHE
    try:
        st = COLLECTED.stat()
    except Exception:
        return {}
    if (_KAKAO_CONTENT_CACHE.get("mtime") == st.st_mtime
            and _KAKAO_CONTENT_CACHE.get("size") == st.st_size):
        return _KAKAO_CONTENT_CACHE["by_room"]
    by_room: dict = {}
    try:
        with open(COLLECTED, "rb") as f:
            if st.st_size > _KAKAO_TAIL_BYTES:
                f.seek(st.st_size - _KAKAO_TAIL_BYTES)
                f.readline()  # 잘린 첫 줄 버림
            raw = f.read()
        for line in raw.decode("utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            room = _norm_for_match(rec.get("room_name") or "")
            delta = _norm_for_match(rec.get("delta") or "")
            if room and delta:
                by_room.setdefault(room, []).append(delta)
    except Exception:
        return _KAKAO_CONTENT_CACHE.get("by_room", {})
    for r in by_room:                       # 방당 최근 400건만 (메모리 제한)
        if len(by_room[r]) > 400:
            by_room[r] = by_room[r][-400:]
    _KAKAO_CONTENT_CACHE = {"mtime": st.st_mtime, "size": st.st_size, "by_room": by_room}
    return by_room


def _is_kakao_origin(kakao_room: str, content: str) -> bool:
    """워크 본문이 '해당 카톡방에서 온(미러된) 내용'이면 True → 되보내면 에코.
    collected_data 의 그 방 최근 delta 안에 본문이 (정규화 후) 통째로/거의 들어있으면 카톡기원.
    """
    body = _norm_for_match(content)
    if len(body) < _KAKAO_MIN_MATCH_LEN:
        return False  # 너무 짧으면 대조 신뢰 불가 → 다른 필터/ledger 에 맡김
    deltas = _build_kakao_index().get(_norm_for_match(kakao_room or ""))
    if not deltas:
        return False
    from difflib import SequenceMatcher
    fuzzy_ok = len(body) >= 12
    need = int(len(body) * 0.85)
    for d in deltas:
        if body in d:                       # 정규화 후 통째 포함 = 카톡기원(대부분 여기서 잡힘)
            return True
        if fuzzy_ok and len(d) >= len(body):  # OCR 미세차이 허용(앵커 최장일치 85%+)
            if SequenceMatcher(None, body, d, autojunk=False).find_longest_match(
                    0, len(body), 0, len(d)).size >= need:
                return True
    return False


def _is_work_native_sender(m: dict) -> bool:
    """발신자가 '본인(나)' 또는 워크 실멤버면 True. (짧은 글의 보조 판별용 — 긴 글은 내용대조로 충분)"""
    s = _norm_name(m.get("sender", ""))
    if _is_self_sender(s):
        return True
    mem = _work_member_names()
    return bool(mem) and s in mem


def _is_our_message(kakaotalk_room: str, preview: str) -> bool:
    """preview 가 우리가 보낸 최근 메시지와 '접두사 일치'면 True (loop 차단).

    Vision 의 preview 는 KW UI 의 최근 메시지를 앞에서부터 잘라 보여주므로
    실제 봇 메시지 텍스트의 prefix(또는 그 반대)이어야 한다. 단순 substring 매칭은
    '답장'·'수입방' 같은 공통 단어가 사용자 메시지에 우연히 들어있을 때
    오스킵(=사용자 메시지 유실)을 일으켜 사용 금지. (code-review 2026-06-01)
    """
    if not preview or not kakaotalk_room:
        return False
    try:
        data = json.loads(SENT_RECENT.read_text(encoding="utf-8"))
    except Exception:
        return False
    bucket = data.get(kakaotalk_room) or []
    p = preview.strip()
    if not p:
        return False
    now = time.time()
    for ent in bucket:
        if not isinstance(ent, dict):
            continue
        if now - (ent.get("ts") or 0) >= SENT_TTL_SEC:
            continue
        t = (ent.get("text") or "").strip()
        if not t:
            continue
        # 접두사 일치 — 어느 한 쪽이 다른 쪽으로 시작하면 동일 메시지로 본다
        if t.startswith(p) or p.startswith(t):
            return True
        # "[발신자] [시각] 본문" 헤더 형식이면 본문 부분도 접두사로 비교
        if "] " in t:
            body = t.split("] ", 2)[-1].strip()
            if body and (body.startswith(p) or p.startswith(body)):
                return True
    return False


# ─────────────────────────────────────────────────────────
# 2) Vision 상태 저장/로드
# ─────────────────────────────────────────────────────────

def _load_state() -> list:
    try:
        return (json.loads(VISION_STATE.read_text(encoding="utf-8")) or {}).get("rooms", [])
    except Exception:
        return []


def _save_state(rows: list) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    VISION_STATE.write_text(
        json.dumps({"ts": time.time(), "rooms": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ─────────────────────────────────────────────────────────
# 3) 워크 방 이름 → 카톡 방 이름 매핑 해석
# ─────────────────────────────────────────────────────────

def _resolve_kakao_room(work_room: str, mapping: dict) -> str | None:
    """워크 방 이름(Vision OCR)을 room_mapping(카톡이름→conv_id) 키와 매칭.

    워크는 conv_id 로 관리되지만 화면엔 conv_id 가 안 보여 이름에 의존한다.
    Vision OCR 표기 차이("네노바 영업방" vs "네노바 영업", 공백/괄호)에 관용적으로:
      1) "[미러] " / "NV##:" prefix 제거
      2) 정확 일치
      3) 공백제거 일치
      4) 끝의 "방" 접미사 차이 무시
      5) fuzzy(SequenceMatcher) 0.88+ — 단 '유일하게' 1개만 통과할 때만(오송신 방지)
    """
    from difflib import SequenceMatcher
    name = (work_room or "").strip()
    for _pfx in ("[미러] ", "[미러]", "[미리] ", "[미리]"):  # [미리]=[미러] OCR 오독
        if name.startswith(_pfx):
            name = name[len(_pfx):].strip()
            break
    if ":" in name:
        head = name.split(":", 1)[0].strip()
        if head.upper().startswith("NV"):
            name = name.split(":", 1)[1].strip()

    if name in mapping:
        return name

    def _norm(s: str) -> str:
        s = s.replace(" ", "")
        # 끝 "방" 접미사 무시 (네노바 영업방 ↔ 네노바 영업)
        if s.endswith("방"):
            s = s[:-1]
        return s

    nn = _norm(name)
    # 2~3) 공백/방접미사 정규화 일치
    exact_norm = [k for k in mapping if _norm(k) == nn]
    if len(exact_norm) == 1:
        return exact_norm[0]
    if len(exact_norm) > 1:
        # 모호 → 가장 긴 공통(=정확) 우선, 그래도 여럿이면 첫 번째
        return sorted(exact_norm, key=len, reverse=True)[0]

    # 4) 음절 fuzzy — 유일하게 0.88+ 일 때만 (여럿이면 모호 → 미매칭)
    cands = [(k, SequenceMatcher(None, nn, _norm(k)).ratio()) for k in mapping]
    cands = [(k, r) for k, r in cands if r >= 0.88]
    if len(cands) == 1:
        return cands[0][0]

    # 5) 자모(NFD) fuzzy — 한글 자음/모음 단위 OCR 오독 관용(레↔네, 주짐↔주님, ·↔+ 등).
    #    음절 비교는 '레'≠'네'라 0점이지만 자모는 ᄅ↔ᄂ 1개 차이라 ~0.83 으로 잡힌다.
    #    오배송 방지: 0.70+ 후보 중 '유일' 하거나 '2등보다 0.10+ 앞선 압도적 1등' 일 때만 채택.
    import unicodedata as _ud
    def _jamo(s: str) -> str:
        return _ud.normalize("NFD", s)
    jnn = _jamo(nn)
    jc = sorted(((k, SequenceMatcher(None, jnn, _jamo(_norm(k))).ratio()) for k in mapping),
                key=lambda x: x[1], reverse=True)
    top = [c for c in jc if c[1] >= 0.70]
    if len(top) == 1:
        return top[0][0]
    if len(top) >= 2 and top[0][1] - top[1][1] >= 0.10:
        return top[0][0]
    return None


# ─────────────────────────────────────────────────────────
# 4) 1회 사이클 — 캡처/diff/필터/포워딩
# ─────────────────────────────────────────────────────────

def cycle_once(*, forward: bool = True, verbose: bool = True) -> dict:
    """1회 사이클. 반환 통계 dict.
    forward=False 면 dry-run (송신 안 함).
    """
    from core.work_vision_reader import read_room_list_state, diff_room_list
    from core.kakaowork_router import _load_room_mapping

    prev = _load_state()
    rows, cap = read_room_list_state()
    if not rows:
        return {"err": "no_rows"}
    diff = diff_room_list(prev, rows)
    mapping = _load_room_mapping()

    stats = {"rows": len(rows), "diff": len(diff), "forwarded": 0,
             "self_loop_skipped": 0, "unmapped_skipped": 0,
             "new_room_skipped": 0, "first_baseline": int(not prev)}

    # 첫 사이클(baseline) 은 diff 가 전부 new_room — 송신 안 함
    if not prev:
        if verbose:
            print(f"  [WORK→KK] baseline {len(rows)} 방 기록만 — 송신 없음", flush=True)
        _save_state(rows)
        return stats

    to_forward: list[tuple[str, str, str]] = []  # (kakaotalk_room, preview, work_room)
    for d in diff:
        kind = d.get("_kind")
        work_room = d.get("room", "")
        preview = (d.get("preview") or "").strip()
        if kind == "new_room":
            stats["new_room_skipped"] += 1
            if verbose:
                print(f"  [WORK→KK] new_room (skip): {work_room}", flush=True)
            continue
        # 빈 preview 차단 — Vision 이 내용을 못 읽었거나(사진/파일만) 빈 값일 때
        # 빈 메시지가 카톡으로 가는 것 방지. (preview 변경 감지됐어도 내용 없으면 스킵)
        if not preview or len(preview) < 2:
            stats["self_loop_skipped"] += 1
            if verbose:
                print(f"  [WORK→KK] 빈/짧은 preview skip: '{work_room}'", flush=True)
            continue
        # 봇 시스템 메시지 차단 — 우리(봇)가 워크방에 남기는 메시지가 preview 로
        # 잡혀 카톡으로 되쏘는 무한 에코 방지. 답장버튼/미러헤더/전송확인 등.
        # + "[발신자] [시각] ..." 모니터 미러 형식도 차단(사람 답장이 아님).
        if _is_bot_system_preview(preview) or _looks_like_mirror_header(preview):
            stats["self_loop_skipped"] += 1
            if verbose:
                print(f"  [WORK→KK] 봇/미러 메시지 skip: '{preview[:40]}'", flush=True)
            continue
        kk = _resolve_kakao_room(work_room, mapping)
        if not kk:
            stats["unmapped_skipped"] += 1
            if verbose:
                print(f"  [WORK→KK] unmapped (skip): '{work_room}'", flush=True)
            continue
        if _is_our_message(kk, preview):
            stats["self_loop_skipped"] += 1
            if verbose:
                print(f"  [WORK→KK] self-loop skip: {kk} '{preview[:40]}'", flush=True)
            continue
        to_forward.append((kk, preview, work_room))

    # 실제 포워딩: 락 한 번 잡고 일괄 송신
    if to_forward and forward:
        from core import kakao_lock as klock
        from core import kakao_win32 as kw
        klock.request()
        got = klock.acquire("work_bridge", timeout=30, respect_request=False)
        if not got:
            print(f"  [WORK→KK] 락 확보 실패 — {len(to_forward)}건 보류 (다음 사이클 재시도)", flush=True)
            klock.clear_request()
            # 상태 저장은 미루기 (다음 사이클에 같은 diff 다시 잡히게)
            return stats
        try:
            import win32gui as _w32
            # KakaoWork 를 캡처하며 TOPMOST 로 올렸으므로, 카톡 검색 전에 카톡 메인창을
            # 확실히 전면화한다. (안 하면 Ctrl+F 검색이 KW 창으로 가 방을 못 엶 — 6s 실패)
            try:
                from core.window_manager import ensure_main_window_foreground
                ensure_main_window_foreground()
                time.sleep(0.4)
            except Exception:
                pass
            for kk, preview, work_room in to_forward:
                if _stop_requested():
                    print("  [WORK→KK] data/_STOP 감지 — 송신 중단", flush=True)
                    break
                try:
                    # 카톡 분리창이 없으면 먼저 검색→열기 (답장서버와 동일 패턴).
                    # send_message_to_room 은 '이미 열린 분리창'만 찾으므로 선행 필수.
                    # 주의: search_and_open_room 이 success=False 여도 실제로는 잠시 뒤
                    #       분리창이 뜨는 경우가 있어(검색→창생성 지연), 반환값과 무관하게
                    #       최대 ~6초까지 재확인한다.
                    hwnd = kw.find_chat_window(kk)
                    if hwnd is None:
                        ores = kw.search_and_open_room(kk)
                        oh = ores.get("hwnd")
                        for _ in range(33):  # ~10s — 정확 제목 분리창 대기(모니터 경합 여유)
                            hwnd = kw.find_chat_window(kk)
                            if hwnd:
                                break
                            if oh and _w32.IsWindow(oh) and (_w32.GetWindowText(oh) or "") == kk:
                                hwnd = oh
                                break
                            time.sleep(0.3)
                        if hwnd is None:
                            print(f"  [WORK→KK] ❌ '{kk}' 정확한 분리창 못 엶(10s) — 스킵", flush=True)
                            continue
                    res = kw.send_message_to_room(kk, preview)
                    ok = res.get("success", False)
                    if ok:
                        stats["forwarded"] += 1
                        print(f"  [WORK→KK] ✅ '{kk}' ← '{preview[:60]}'", flush=True)
                        # 포워딩 후엔 우리가 워크에 그 텍스트를 다시 보내진 않지만,
                        # 모니터가 곧 카톡→워크로 다시 mirror 할 것. 그 mirror 가
                        # work_bridge 의 self-loop 필터에 잡혀야 함 → 이미 sent_ledger
                        # 와 work_sent_recent 양쪽이 모니터 송신 시 기록되므로 OK.
                    else:
                        print(f"  [WORK→KK] ❌ '{kk}' 송신실패: {res.get('error','')}", flush=True)
                except Exception as e:
                    print(f"  [WORK→KK] '{kk}' 예외: {type(e).__name__}: {e}", flush=True)
                time.sleep(0.5)
        finally:
            klock.release("work_bridge")
            klock.clear_request()
    elif to_forward and not forward:
        for kk, preview, _w in to_forward:
            print(f"  [WORK→KK] (dry) '{kk}' ← '{preview[:60]}'", flush=True)

    _save_state(rows)
    return stats


STOP_FILE = DATA / "_STOP"


def _stop_requested() -> bool:
    """공용 정지 파일(data/_STOP)이 있으면 True. 별도 프로세스에서도 정지 가능."""
    try:
        return STOP_FILE.exists()
    except Exception:
        return False


def cycle_once_v2(*, forward: bool = True, verbose: bool = True, max_rooms: int = 3) -> dict:
    """v2 사이클: 워크 룸목록 → 파란뱃지 방 → 행클릭 본문읽기 → 새 메시지만 카톡 송신.

    v1(미리보기)과 달리 '대화창 본문 전체'를 읽어, 봇/미러([발신자][시각])·이미중계분을
    제외한 '워크 네이티브 신규'만 카톡 원본방으로 전송. max_rooms: 사이클당 최대 처리 방수.
    """
    from core.work_vision_reader import (
        find_kakaowork_window, capture_region, open_work_room_by_row_and_read,
        read_room_list_state,
    )
    from core.badge_monitor import detect_blue_badge_rows
    from core.window_manager import (
        lock_kakaowork_window, lock_kakaotalk_window, get_pos_tuple, get_capture_region,
    )
    from core.kakaowork_router import _load_room_mapping

    stats = {"unread_rooms": 0, "opened": 0, "forwarded": 0,
             "self_loop_skipped": 0, "unmapped_skipped": 0}

    lock_kakaowork_window()
    lock_kakaotalk_window()
    time.sleep(0.3)
    h = find_kakaowork_window()
    if not h:
        return {"err": "no_kw"}

    # 룸목록 1회 캡처 → ① 파란뱃지 행 y ② Vision 으로 방이름+순서
    cap = DATA.parent / "captures" / f"_v2list_{int(time.time()*1000)}.png"
    if not capture_region(h, "kakaowork_roomlist", cap):
        return {"err": "capture_fail"}
    badge_ys = detect_blue_badge_rows(str(cap))
    try:
        cap.unlink()
    except Exception:
        pass
    stats["unread_rooms"] = len(badge_ys)
    if not badge_ys:
        if verbose:
            print("  [WORK→KK v2] 안읽음 방 없음", flush=True)
        return stats

    rows, _ = read_room_list_state()  # [{room, unread, ...}] 위→아래 순
    if not rows:
        return {"err": "roomlist_vision_fail"}

    wl, wt, ww, wh = get_pos_tuple("kakaowork_main")
    reg = get_capture_region("kakaowork_roomlist")
    rdy = reg.get("dy", 110) if reg else 110
    row_h = (get_capture_region("kakaowork_row_height") or 76) if False else 76

    mapping = _load_room_mapping()
    ledger = _v2_load_ledger()

    # 뱃지 y → 행 인덱스 → rows[인덱스] 방이름. (뱃지순서=룸목록순서 가정)
    for rank, by in enumerate(badge_ys[:max_rooms]):
        if _stop_requested():
            break
        # 뱃지 y 로 행 인덱스 추정
        idx = round((by - badge_ys[0]) / row_h) if len(badge_ys) > 1 else 0
        idx = max(0, min(idx, len(rows) - 1))
        work_room = rows[idx].get("room", "") if idx < len(rows) else ""
        kk = _resolve_kakao_room(work_room, mapping)
        if not kk:
            stats["unmapped_skipped"] += 1
            if verbose:
                print(f"  [WORK→KK v2] unmapped: '{work_room}' (행 {idx})", flush=True)
            continue

        row_abs_y = wt + rdy + by
        msgs = open_work_room_by_row_and_read(h, row_abs_y, max_msgs_tail=12)
        if not msgs:
            continue
        stats["opened"] += 1

        # 본문 → 워크 네이티브 신규만 추출(봇/미러/비사용자/이미중계 제외)
        to_send = []
        for m in msgs:
            content = (m.get("content") or "").strip()
            if _is_non_user_message(content):
                continue
            # ⚠️ content(본문) 만으로 판정한다. 과거엔 "[발신자] [시각] 내용" 을
            #    재조립한 line 에 _looks_like_mirror_header 를 걸었는데, 그 정규식은
            #    발신자+시각이 있는 '모든' 메시지에 매칭돼 워크 네이티브 신규까지
            #    전부 걸러 0건이 되는 버그였다. Vision 은 미러 메시지의 "[발신자][시각]"
            #    접두사를 sender/time 필드로 분리 추출하므로, 미러 식별은 content 가
            #    아니라 sender 화이트리스트/시간 워터마크로 해야 한다(아래 _is_mirror_origin).
            # 봇/미러 글('['로 시작 등)은 제외 — 그 외엔 발신자 무관 전부 전송(사용자 기준 2026-06-08)
            if _is_bot_or_mirror(m, content):
                continue
            if _is_emoji_only(content):   # 이모지/기호 반응만 있는 글은 안 보냄
                continue
            # ★ 에코 차단 핵심: 본문이 그 카톡방에서 온(미러된) 내용이면 되보내지 않음
            if _is_kakao_origin(kk, content):
                stats["kakao_origin_skipped"] = stats.get("kakao_origin_skipped", 0) + 1
                continue
            # 짧은 글은 내용대조 신뢰불가 → 팀(본인/멤버) 발신자만 허용(짧은 미러 에코 차단)
            if len(_norm_for_match(content)) < _KAKAO_MIN_MATCH_LEN and not _is_work_native_sender(m):
                stats["kakao_origin_skipped"] = stats.get("kakao_origin_skipped", 0) + 1
                continue
            key = _v2_msg_key(kk, m)
            if key in ledger:
                continue  # 이미 카톡으로 중계함
            _sender = _display_sender(m.get("sender"))
            _txt = f"[{_sender}] {content}" if _sender else content
            to_send.append((_txt, key))

        if not to_send:
            continue
        if verbose:
            print(f"  [WORK→KK v2] '{kk}' 워크신규 {len(to_send)}건", flush=True)

        if forward:
            from core import kakao_win32 as kw
            import win32gui as _w32
            # 카톡 방 열기(없으면 검색)
            hwnd = kw.find_chat_window(kk)
            if hwnd is None:
                ores = kw.search_and_open_room(kk)
                oh = ores.get("hwnd")
                for _ in range(33):
                    hwnd = kw.find_chat_window(kk)
                    if hwnd:
                        break
                    if oh and _w32.IsWindow(oh) and (_w32.GetWindowText(oh) or "") == kk:
                        hwnd = oh
                        break
                    time.sleep(0.3)
            if hwnd is None:
                print(f"  [WORK→KK v2] ❌ 카톡 '{kk}' 방 못 엶 — 스킵", flush=True)
                continue
            for content, key in to_send:
                try:
                    res = kw.send_message_to_room(kk, content)
                    if res.get("success"):
                        stats["forwarded"] += 1
                        ledger.add(key)
                        print(f"  [WORK→KK v2] ✅ '{kk}' ← '{content[:50]}'", flush=True)
                    else:
                        print(f"  [WORK→KK v2] ❌ '{kk}' 송신실패: {res.get('error','')}", flush=True)
                except Exception as e:
                    print(f"  [WORK→KK v2] '{kk}' 예외: {e}", flush=True)
                time.sleep(0.5)
        else:
            for content, key in to_send:
                print(f"  [WORK→KK v2] (dry) '{kk}' ← '{content[:50]}'", flush=True)
                ledger.add(key)  # dry 도 ledger 기록(중복로그 방지)

    _v2_save_ledger(ledger)
    return stats


def _forward_to_kakao(kk: str, to_send: list, ledger: set, stats: dict, verbose: bool) -> None:
    """to_send(=[(content,key),...]) 를 카톡 방 kk 로 일괄 송신. ledger/stats 갱신.

    카톡 입력(마우스/키보드)을 점유하므로 kakao_lock 을 잡고 송신한다(모니터/답장서버와
    동시 구동 시 입력 경합 방지). 락 확보 실패 시 송신 보류(이번 사이클 스킵).
    """
    from core import kakao_win32 as kw
    from core import kakao_lock as klock
    import win32gui as _w32

    klock.request()
    if not klock.acquire("work_bridge", timeout=30, respect_request=False):
        print(f"  [WORK→KK v3] 카톡 락 확보 실패 — {len(to_send)}건 보류(다음 사이클)", flush=True)
        klock.clear_request()
        return
    try:
        try:
            from core.window_manager import ensure_main_window_foreground
            ensure_main_window_foreground()
            time.sleep(0.4)
        except Exception:
            pass
        hwnd = kw.find_chat_window(kk)
        if hwnd is None:
            ores = kw.search_and_open_room(kk)
            oh = ores.get("hwnd")
            for _ in range(33):
                hwnd = kw.find_chat_window(kk)
                if hwnd:
                    break
                if oh and _w32.IsWindow(oh) and (_w32.GetWindowText(oh) or "") == kk:
                    hwnd = oh
                    break
                time.sleep(0.3)
        if hwnd is None:
            print(f"  [WORK→KK v3] ❌ 카톡 '{kk}' 정확한 분리창 못 엶 — 스킵", flush=True)
            return
        for content, key in to_send:
            if _stop_requested():
                print("  [WORK→KK v3] data/_STOP — 송신 중단", flush=True)
                break
            try:
                res = kw.send_message_to_room(kk, content)
                if res.get("success"):
                    stats["forwarded"] += 1
                    ledger.add(key)
                    # 에코 차단: 우리가 카톡으로 보낸 내용이 모니터에 의해 워크로 되미러될 때
                    # (sender=네노바 등 멤버로 보여) 다시 네이티브로 잡혀 무한송신되는 것 방지.
                    append_sent(kk, content)
                    print(f"  [WORK→KK v3] ✅ '{kk}' ← '{content[:50]}'", flush=True)
                else:
                    print(f"  [WORK→KK v3] ❌ '{kk}' 송신실패: {res.get('error','')}", flush=True)
            except Exception as e:
                print(f"  [WORK→KK v3] '{kk}' 예외: {type(e).__name__}: {e}", flush=True)
            time.sleep(0.5)
    finally:
        klock.release("work_bridge")
        klock.clear_request()


def cycle_once_v3(*, forward: bool = True, verbose: bool = True, max_rooms: int = 4) -> dict:
    """v3 사이클: 매 방마다 룸목록 재캡처 → 최상단 파란뱃지 클릭 → 방제목으로 방 식별
    → 본문읽기 → 워크 네이티브 신규만 카톡 송신.

    핵심(이전 인덱스기반 방식의 reorder 오차 해결):
      · 안읽음 방은 항상 목록 상단으로 올라온다 → '최상단 뱃지'만 처리.
      · detect_blue_badge_rows 는 순수 픽셀(Vision 아님) → 캡처→클릭 간격 ~300ms,
        그 사이 재정렬 거의 없음. 인덱스/행높이 추정에 의존하지 않음.
      · 클릭으로 연 방의 '제목'을 신뢰원천으로 삼아 카톡방 매핑(엉뚱한 방이면 매핑/제목으로 드러남).
      · 방을 읽으면 뱃지가 사라져 다음 방이 top 으로 → 반복하면 안읽음 소진.
      · 봇/미러 식별 _is_bot_or_mirror(본문 '['시작/봇표지) + 이모지반응 제외, 재전송 방지 ledger.
    """
    from core.work_vision_reader import (
        find_kakaowork_window, capture_region, open_work_room_verify_and_read,
        scroll_roomlist_to_top,
    )
    from core.badge_monitor import detect_blue_badge_rows
    from core.window_manager import (
        lock_kakaowork_window, lock_kakaotalk_window, get_pos_tuple, get_capture_region,
    )
    from core.kakaowork_router import _load_room_mapping

    stats = {"unread_top": 0, "opened": 0, "forwarded": 0,
             "unmapped_skipped": 0, "dup_title_break": 0, "self_loop_skipped": 0,
             "kakao_origin_skipped": 0}

    lock_kakaowork_window()
    lock_kakaotalk_window()
    time.sleep(0.3)
    h = find_kakaowork_window()
    if not h:
        return {"err": "no_kw"}

    wl, wt, ww, wh = get_pos_tuple("kakaowork_main")
    reg = get_capture_region("kakaowork_roomlist")
    rdy = reg.get("dy", 110) if reg else 110
    mapping = _load_room_mapping()
    ledger = _v2_load_ledger()
    seen_titles: set[str] = set()  # 한 사이클 내 같은 방 재오픈 무한루프 방지

    for i in range(max_rooms):
        if _stop_requested():
            break
        # 1) 매 방마다 룸목록 '맨 위로 스크롤' 후 재캡처 → 최상단 뱃지 (픽셀, 빠름)
        #    (안읽음 방은 최상단으로 올라오는데, 목록이 아래로 드리프트돼 있으면 고정
        #     캡처영역이 top 을 놓쳐 '안읽음 없음'으로 오판 → 반드시 top 으로 올림)
        scroll_roomlist_to_top(h)
        cap = ROOT / "captures" / f"_v3top_{int(time.time()*1000)}.png"
        if not capture_region(h, "kakaowork_roomlist", cap):
            break
        badge_ys = detect_blue_badge_rows(str(cap))
        try:
            cap.unlink()
        except Exception:
            pass
        if not badge_ys:
            if verbose and i == 0:
                print("  [WORK→KK v3] 안읽음 방 없음", flush=True)
            break
        stats["unread_top"] += 1

        # 2) 최상단 뱃지 y 클릭 → 제목+본문 (제목으로 식별)
        row_abs_y = wt + rdy + badge_ys[0]
        msgs, title, _ = open_work_room_verify_and_read(h, row_abs_y, None, max_msgs_tail=12)
        title = (title or "").strip()
        if not title or title in seen_titles:
            # 제목 못 읽음 / 같은 방 재오픈(뱃지 안 사라짐) → 더 진행하면 무한루프
            stats["dup_title_break"] += 1
            if verbose:
                print(f"  [WORK→KK v3] 제목 '{title}' 중복/공백 — 사이클 종료", flush=True)
            break
        seen_titles.add(title)
        stats["opened"] += 1

        kk = _resolve_kakao_room(title, mapping)
        if not kk:
            stats["unmapped_skipped"] += 1
            if verbose:
                print(f"  [WORK→KK v3] unmapped(제목 '{title}') — 스킵", flush=True)
            continue

        # 3) 본문 → 워크 네이티브 신규만
        to_send = []
        for m in msgs:
            content = (m.get("content") or "").strip()
            if _is_non_user_message(content):
                continue
            # 봇/미러 글('['로 시작 등)은 제외 — 그 외엔 발신자 무관 전부 전송(사용자 기준 2026-06-08)
            if _is_bot_or_mirror(m, content):
                continue
            # 이모지/기호 반응만 있는 글은 카톡으로 안 보냄
            if _is_emoji_only(content):
                continue
            # ★ 에코 차단 핵심: 본문이 그 카톡방에서 온(미러된) 내용이면 되보내지 않음
            if _is_kakao_origin(kk, content):
                stats["kakao_origin_skipped"] = stats.get("kakao_origin_skipped", 0) + 1
                continue
            # 짧은 글은 내용대조 신뢰불가 → 팀(본인/멤버) 발신자만 허용(짧은 미러 에코 차단)
            if len(_norm_for_match(content)) < _KAKAO_MIN_MATCH_LEN and not _is_work_native_sender(m):
                stats["kakao_origin_skipped"] = stats.get("kakao_origin_skipped", 0) + 1
                continue
            # 에코 차단: 우리가 직전에 카톡으로 보낸 내용이 워크로 되미러된 것이면 스킵
            if _is_our_message(kk, content):
                stats["self_loop_skipped"] += 1
                continue
            key = _v2_msg_key(kk, m)
            if key in ledger:
                continue
            # 카톡에 '작성자(워크멤버)'를 함께 표기해서 보냄: "[임재용] 본문" (누가 보냈는지)
            # '나'(본인) 메시지는 실제 로그인 계정명(기본 '네노바')으로 치환.
            _sender = _display_sender(m.get("sender"))
            _txt = f"[{_sender}] {content}" if _sender else content
            to_send.append((_txt, key))
        if not to_send:
            if verbose:
                print(f"  [WORK→KK v3] '{title}'→'{kk}': 워크네이티브 신규 없음", flush=True)
            continue
        if verbose:
            print(f"  [WORK→KK v3] '{title}'→'{kk}' 워크네이티브 신규 {len(to_send)}건", flush=True)
        if forward:
            _forward_to_kakao(kk, to_send, ledger, stats, verbose)
        else:
            # dry-run 은 미리보기 — ledger 를 건드리지 않는다(이후 실송신이 막히지 않게).
            for content, key in to_send:
                print(f"  [WORK→KK v3] (dry) '{kk}' ← '{content[:50]}'", flush=True)

    _v2_save_ledger(ledger)
    return stats


def daemon(*, interval_sec: int = 20, once: bool = False,
           dry_run: bool = False, v2: bool = False, v3: bool = False) -> int:
    """워크→카톡 브릿지 데몬. Ctrl+C 또는 data/_STOP 파일로 종료.
    v3=True → cycle_once_v3(단일캡처+제목검증, 권장), v2=True → cycle_once_v2."""
    print(f"[WORK→KK] 데몬 시작 interval={interval_sec}s dry={dry_run} once={once}", flush=True)
    if _stop_requested():
        print("[WORK→KK] data/_STOP 존재 — 시작 안 함(정지 상태). 모니터를 다시 시작하면 latch 가 해제됩니다.", flush=True)
        return 0
    cycle = 0
    while True:
        if _stop_requested():
            print("[WORK→KK] data/_STOP 감지 — 데몬 종료", flush=True)
            return 0
        cycle += 1
        try:
            tag = " v3" if v3 else (" v2" if v2 else "")
            print(f"\n[WORK→KK] === cycle {cycle}{tag} ===", flush=True)
            if v3:
                stats = cycle_once_v3(forward=not dry_run, verbose=True)
            elif v2:
                stats = cycle_once_v2(forward=not dry_run, verbose=True)
            else:
                stats = cycle_once(forward=not dry_run, verbose=True)
            print(f"[WORK→KK] cycle {cycle} stats: {stats}", flush=True)
        except KeyboardInterrupt:
            print("\n[WORK→KK] Ctrl+C — 종료", flush=True)
            return 0
        except Exception as e:
            print(f"[WORK→KK] cycle 예외: {type(e).__name__}: {e}", flush=True)
        if once:
            return 0
        # 정지 반응성 위해 interval 을 잘게 쪼개 _STOP 체크
        slept = 0.0
        while slept < interval_sec:
            if _stop_requested():
                print("[WORK→KK] data/_STOP 감지(대기중) — 데몬 종료", flush=True)
                return 0
            time.sleep(min(2.0, interval_sec - slept))
            slept += 2.0
