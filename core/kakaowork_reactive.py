"""
카카오워크 봇 반응형(Reactive) 서버 — 양방향 미러링 (워크 → 카톡).

흐름:
  1. monitor 가 미러방에 메시지 + [📤 카톡 답장] 버튼 송신
     (kakaowork_router.send_to_mirror_room 의 button block)
  2. 사용자가 버튼 클릭 → 카카오워크가 Request URL (POST /<secret>/request_modal)
     → 우리가 모달 JSON 응답 (답장 텍스트 입력 필드)
  3. 사용자 모달 입력 + 제출 → 카카오워크가 Callback URL (POST /<secret>/callback)
     → 우리가 core.kakao_win32.send_message_to_room 으로 카톡 원본 방에 송신 → HTTP 200

보안:
  URL path 에 random secret token (data/reactive_secret.txt). 카카오워크는 요청 서명
  안 하므로 secret path 로 무단 호출 차단.

검증된 명세 (docs.kakaoi.ai/kakao_work/webapireference/reactive):
  request_modal 수신:
    {type:"request_modal", value:"room=수입방", message:{conversation_id,...}, react_user_id}
  모달 응답:
    {view:{title, accept, decline, value, blocks:[{type:"input", name, ...}]}}
  submission 수신:
    {type:"submission", actions:{reply_text:"..."}, value:"room=수입방", message:{...}}

실행:
  python -m core.kakaowork_reactive            # 포트 5000
  python -m core.kakaowork_reactive --port 8080
"""
from __future__ import annotations

import json
import queue
import secrets
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SECRET_PATH = ROOT / "data" / "reactive_secret.txt"
LOG_PATH = ROOT / "data" / "reactive_log.jsonl"
MAPPING_PATH = ROOT / "data" / "room_mapping.json"


def _get_secret() -> str:
    """random secret token (없으면 생성)."""
    if SECRET_PATH.exists():
        s = SECRET_PATH.read_text(encoding="utf-8").strip()
        if s:
            return s
    s = secrets.token_urlsafe(24)
    SECRET_PATH.parent.mkdir(parents=True, exist_ok=True)
    SECRET_PATH.write_text(s, encoding="utf-8")
    return s


def _log(event: dict) -> None:
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": round(time.time(), 2), **event}, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _parse_room_from_value(value: str) -> str | None:
    """modal/button value → 카톡 방 이름.

    'cid=<미러conv_id>' → reverse 매핑으로 방 이름 (짧고 안전 — 권장)
    'room=<방이름>'     → 그대로 (구버전 버튼 호환)
    """
    if not value:
        return None
    if value.startswith("cid="):
        return _conv_id_to_room(value[len("cid="):])
    if value.startswith("room="):
        return value[len("room="):]
    return value or None


def _conv_id_to_room(conv_id: str) -> str | None:
    """미러방 conv_id → 카톡 원본 방 이름 (mapping reverse lookup)."""
    try:
        mapping = json.loads(MAPPING_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None
    for name, cid in mapping.items():
        if str(cid) == str(conv_id):
            return name
    return None


# ─────────────────────────────────────────────
# 송신 큐 + 백그라운드 워커
#   콜백은 (방, 텍스트)를 큐에 넣고 즉시 200 반환 → 카카오워크 webhook 타임아웃
#   ("일시적으로 서버에 접속할 수 없습니다") 방지. 실제 카톡 송신은 워커 1개가
#   순서대로(직렬) 처리하며 monitor 와 락으로 조정한다.
# ─────────────────────────────────────────────
_send_q: "queue.Queue[tuple[str, str]]" = queue.Queue()
_worker_started = False
_worker_lock = threading.Lock()


def _post_send_confirmation(room: str, hwnd, reply_text: str) -> None:
    """카톡 전송 직후, 카톡 창을 캡처해 워크 미러방에 업로드 → '100% 반영' 시각 확인.

    순서(워크 미러방): 📤 전송내용 텍스트 → [카톡 창 캡처 이미지] → ✅ 반영확인.
    캡처는 봇 네이티브 업로드(send_photos_native)로 올린다. 모든 단계 예외 무시.
    """
    from core.kakaowork_router import send_to_mirror_room
    # 1) 전송 내용 텍스트 기록
    try:
        send_to_mirror_room(room, f"📤 카톡으로 전송: {reply_text}")
    except Exception as e:
        print(f"  [REACTIVE-WORKER] 워크 텍스트기록 실패(무시): {e}", flush=True)

    # 2) 카톡 창 캡처 → 워크 미러방 업로드
    out = None
    try:
        import win32gui
        from PIL import ImageGrab
        from core import kakao_win32 as kw
        from core.kakaowork_router import send_photos_native, _load_room_mapping

        h = hwnd
        if not h or not win32gui.IsWindow(h):
            h = kw.find_chat_window(room)
        if not h:
            print("  [REACTIVE-WORKER] 캡처: 카톡 창 못찾음 → 스킵", flush=True)
            return
        try:
            kw.bring_window_to_front(h)
        except Exception:
            pass
        time.sleep(0.6)
        l, t, r, b = win32gui.GetWindowRect(h)
        if (r - l) < 100 or (b - t) < 100:
            return
        img = ImageGrab.grab(bbox=(l, t, r, b))
        out = ROOT / "data" / f"_reply_capture_{int(time.time() * 1000)}.png"
        img.save(out)

        mapping = _load_room_mapping()
        conv_id = mapping.get(room)
        if not conv_id:
            nn = room.replace(" ", "")
            for k, v in mapping.items():
                if k.replace(" ", "") == nn:
                    conv_id = v
                    break
        posted = send_photos_native(str(conv_id), [out]) if conv_id else []
        msg = "✅ 카톡 반영 확인 (위 캡처)" if posted else "⚠️ 전송됨 — 캡처 업로드 실패"
        try:
            send_to_mirror_room(room, msg)
        except Exception:
            pass
        print(f"  [REACTIVE-WORKER] 전송 확인 캡처: {'업로드 OK' if posted else '업로드 실패'}", flush=True)
    except Exception as e:
        print(f"  [REACTIVE-WORKER] 캡처/업로드 예외(무시): {e}", flush=True)
    finally:
        try:
            if out is not None:
                out.unlink(missing_ok=True)
        except Exception:
            pass


def _process_send(room: str, reply_text: str) -> None:
    """실제 카톡 송신 (워커 스레드에서 호출). 락으로 monitor 와 조정."""
    from core import kakao_lock as _klock
    from core import kakao_win32 as kw

    _klock.request()  # monitor 에 우선 양보 신호
    got = _klock.acquire("reactive", timeout=180, respect_request=False)
    if not got:
        # monitor 가 180초 내내 안 놓음(비정상) → 충돌 방지 위해 송신 보류(스킵)
        print(f"  [REACTIVE-WORKER] 락 획득 실패(180s) — 송신 보류: {room!r}", flush=True)
        _log({"endpoint": "worker", "result": "lock_timeout", "room": room})
        _klock.clear_request()
        return
    try:
        import win32gui as _w32
        hwnd = kw.find_chat_window(room)
        if hwnd is None:
            res = kw.search_and_open_room(room)
            if not res.get("success"):
                print(f"  [REACTIVE-WORKER] 방 진입 실패: {res.get('error')}", flush=True)
                _log({"endpoint": "worker", "result": "open_failed", "room": room})
                return
            # 검색 후 '정확한 제목' 분리창이 뜰 때까지 잠깐 재확인 (지연/타이밍 대응).
            # 잘못된 방 송신 방지를 위해 '정확 일치' 분리창만 사용한다.
            for _ in range(8):  # ~2.4s
                hwnd = kw.find_chat_window(room)
                if hwnd:
                    break
                oh = res.get("hwnd")
                if oh and _w32.IsWindow(oh) and (_w32.GetWindowText(oh) or "") == room:
                    hwnd = oh
                    break
                time.sleep(0.3)
            if hwnd is None:
                print(f"  [REACTIVE-WORKER] 카톡 송신: FAIL 정확한 분리창 '{room}' 못 엶 "
                      f"(이름 모호/미생성) — 잘못된 방 송신 방지로 중단", flush=True)
                _log({"endpoint": "worker", "result": "exact_window_not_found", "room": room})
                return
        send_res = kw.send_message_to_room(room, reply_text)
        ok = send_res.get("success")
        print(f"  [REACTIVE-WORKER] 카톡 송신: {'OK' if ok else 'FAIL'} {send_res.get('error', '')}", flush=True)
        _log({"endpoint": "worker", "result": "sent" if ok else "send_failed",
              "room": room, "detail": send_res})

        # 워크 미러방에 답장 기록 + 카톡 창 캡처 업로드 (100% 전송 시각 확인)
        if ok:
            _post_send_confirmation(room, hwnd, reply_text)
    except Exception as e:
        print(f"  [REACTIVE-WORKER] 예외: {type(e).__name__}: {e}", flush=True)
        _log({"endpoint": "worker", "result": "exception", "error": str(e)})
    finally:
        _klock.release("reactive")
        _klock.clear_request()


def _send_worker() -> None:
    """큐에서 (방, 텍스트)를 꺼내 순서대로 송신."""
    while True:
        room, reply_text = _send_q.get()
        try:
            _process_send(room, reply_text)
        except Exception as e:
            print(f"  [REACTIVE-WORKER] 루프 예외: {e}", flush=True)
        finally:
            _send_q.task_done()


def _ensure_worker() -> None:
    global _worker_started
    with _worker_lock:
        if _worker_started:
            return
        t = threading.Thread(target=_send_worker, daemon=True, name="reactive-send-worker")
        t.start()
        _worker_started = True


def create_app():
    from flask import Flask, request, jsonify
    _ensure_worker()

    app = Flask(__name__)
    secret = _get_secret()

    @app.route("/", methods=["GET"])
    def health():
        return jsonify({"status": "ok", "service": "nenova kakaowork reactive"})

    @app.route(f"/{secret}/request_modal", methods=["POST"])
    def request_modal():
        data = request.get_json(force=True, silent=True) or {}
        _log({"endpoint": "request_modal", "data": data})
        value = data.get("value") or ""
        msg = data.get("message") or {}
        conv_id = msg.get("conversation_id")
        # 카톡 방 이름: value 우선 → conv_id reverse
        room = _parse_room_from_value(value) or _conv_id_to_room(conv_id) or "?"
        print(f"  [REACTIVE] request_modal: room={room!r} conv_id={conv_id}", flush=True)
        # 모달은 '짧고 안전한' 필드만 사용한다. 방 이름이 길거나 특수문자(쉼표/&/+//)
        # 가 있으면 카카오워크가 모달 응답을 거부해 "서버 오류"가 났음(방마다 다름).
        # → callback 식별자는 conv_id(짧은 숫자)로 전달, 제목에는 방 이름 제거,
        #   라벨의 방 이름은 짧게 잘라 안전화.
        modal_value = f"cid={conv_id}" if conv_id else f"room={room}"
        safe_room = (room or "?")[:18]
        return jsonify({
            "view": {
                "title": "카톡 답장",
                "accept": "보내기",
                "decline": "취소",
                "value": modal_value,  # callback 에서 받을 값 (cid=숫자)
                "blocks": [
                    {"type": "label", "text": f"'{safe_room}' 방으로 보낼 메시지", "markdown": False},
                    {
                        "type": "input",
                        "name": "reply_text",
                        "required": True,
                        "placeholder": "답장 내용을 입력하세요",
                    },
                ],
            }
        })

    @app.route(f"/{secret}/callback", methods=["POST"])
    def callback():
        data = request.get_json(force=True, silent=True) or {}
        _log({"endpoint": "callback", "data": data})
        actions = data.get("actions") or {}
        value = data.get("value") or ""
        reply_text = (actions.get("reply_text") or "").strip()
        room = _parse_room_from_value(value)
        if not room:
            msg = data.get("message") or {}
            room = _conv_id_to_room(msg.get("conversation_id"))
        print(f"  [REACTIVE] callback: room={room!r} text={reply_text[:40]!r}", flush=True)

        if not room or not reply_text:
            _log({"endpoint": "callback", "result": "skip", "room": room, "text_len": len(reply_text)})
            return ("", 200)  # 200 안 주면 카카오워크가 에러 표시

        # 큐에 적재하고 즉시 200 반환 → webhook 타임아웃("서버 접속 불가") 방지.
        # 실제 카톡 송신은 백그라운드 워커가 순서대로 처리 (monitor 와 락 조정).
        _send_q.put((room, reply_text))
        qsize = _send_q.qsize()
        print(f"  [REACTIVE] 큐 적재 → 즉시 200 (대기 {qsize}건): room={room!r} text={reply_text[:30]!r}", flush=True)
        _log({"endpoint": "callback", "result": "queued", "room": room, "qsize": qsize})
        return ("", 200)

    return app, secret


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    port = 5000
    if "--port" in sys.argv:
        i = sys.argv.index("--port")
        if i + 1 < len(sys.argv):
            try:
                port = int(sys.argv[i + 1])
            except ValueError:
                pass

    app, secret = create_app()
    print(f"[REACTIVE] 카카오워크 반응형 서버 시작 (포트 {port})")
    print(f"[REACTIVE] secret token: {secret}")
    print(f"[REACTIVE] 봇 대시보드 등록 URL:")
    print(f"           Request URL : https://<public>/{secret}/request_modal")
    print(f"           Callback URL: https://<public>/{secret}/callback")
    print(f"[REACTIVE] (public 은 localtunnel/ngrok URL 로 치환)")
    app.run(host="0.0.0.0", port=port, debug=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
