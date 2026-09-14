"""Fail-closed MOYI/KakaoTalk bridge worker."""
from __future__ import annotations
import hashlib, json, os, struct, time
from pathlib import Path
from urllib.parse import urlparse
import pyautogui, requests, win32api
from dotenv import load_dotenv
from core.moyi_control import is_paused
from core.moyi_outbound import open_room_by_name
from core.safe_worker_room import open_unique_exact_room, close_room

ROOT = Path(__file__).resolve().parent.parent
JOURNAL = ROOT / "data" / "moyi_outbound_journal.jsonl"
EVENT_LOG = ROOT / "data" / "moyi_events.jsonl"
AGENT_LOG = ROOT / "data" / "agent_runtime_events.jsonl"
POLL_RETRY_SEC = 5
MAX_ATTACHMENT_BYTES = 50 * 1024 * 1024
SUPPRESSED_SYSTEM_TEXTS = ("말머리 설정 내역",)
ROOM_FAILURE_LIMIT = 3
ROOM_CIRCUIT_SEC = 1800


class RoomCircuitBreaker:
    """Quarantine only a repeatedly failing room; other rooms keep running."""
    def __init__(self):
        self.failures = {}
        self.blocked_until = {}

    def available(self, title, now=None):
        now = time.monotonic() if now is None else now
        until = self.blocked_until.get(title, 0)
        if until and now >= until:
            self.blocked_until.pop(title, None)
            self.failures[title] = 0
            return True
        return now >= until

    def succeeded(self, title):
        self.failures.pop(title, None)
        self.blocked_until.pop(title, None)

    def failed(self, title, now=None):
        now = time.monotonic() if now is None else now
        count = self.failures.get(title, 0) + 1
        self.failures[title] = count
        if count >= ROOM_FAILURE_LIMIT:
            self.blocked_until[title] = now + ROOM_CIRCUIT_SEC
            return True
        return False

def _inbound_schedule(rooms: list[dict]) -> list[dict]:
    """Alternate sales with every other room, without starving other rooms."""
    sales = [r for r in rooms if str(r.get('exact_title') or '').strip() == '영업방']
    others = [r for r in rooms if str(r.get('exact_title') or '').strip() != '영업방']
    imports = [r for r in others if str(r.get('exact_title') or '').strip() == '수입방']
    if len(sales) == 1 and len(imports) == 1:
        background = [r for r in others if r not in imports]
        return [r for other in background for r in (sales[0], imports[0], other)] if background else [sales[0], imports[0]]
    # Ambiguous bindings must not gain extra scheduling weight.
    if len(sales) != 1 or not others:
        return list(rooms)
    return [room for other in others for room in (sales[0], other)]


def _timed(stage, operation, *args, **kwargs):
    started = time.monotonic()
    outcome = 'ok'
    try:
        return operation(*args, **kwargs)
    except Exception:
        outcome = 'error'
        raise
    finally:
        elapsed = time.monotonic() - started
        _event(None, 'stage_timing', f'{stage}: {elapsed:.3f}s {outcome}')

def _is_suppressed_system_item(item: dict) -> bool:
    """Return True for MOYI system notices that should not reach KakaoTalk."""
    texts = [
        str(part.get("text") or "").strip()
        for part in item.get("parts") or []
        if part.get("type") == "text"
    ]
    return any(marker in text for marker in SUPPRESSED_SYSTEM_TEXTS for text in texts)

def _restore_safe_cursor() -> None:
    """Recover after failed UI automation without disabling the fail-safe."""
    width, height = pyautogui.size()
    win32api.SetCursorPos((max(1, width // 2), max(1, height // 2)))

def _config() -> tuple[str, str]:
    load_dotenv(ROOT / ".env")
    server = (os.getenv("MOYI_SERVER") or os.getenv("MOYI_API_BASE") or "").rstrip("/")
    secret = os.getenv("MOYI_BRIDGE_SECRET", "")
    if not server or not secret:
        raise RuntimeError("MOYI_SERVER와 MOYI_BRIDGE_SECRET가 필요합니다")
    return server, secret

def _headers(secret: str) -> dict[str, str]:
    return {"X-Company-Secret": secret}

def _retryable_request_error(exc: requests.RequestException) -> bool:
    response = getattr(exc, "response", None)
    return response is None or response.status_code == 429 or response.status_code >= 500

def _safe_request_error(exc: requests.RequestException) -> str:
    response = getattr(exc, "response", None)
    return f"HTTP {response.status_code}" if response is not None else type(exc).__name__


class PendingPoller:
    """Back off the failing endpoint, without blocking approvals or scanning."""
    def __init__(self):
        self.failures = 0
        self.next_at = 0.0

    def fetch(self, server, secret):
        if time.monotonic() < self.next_at: return []
        try:
            response = requests.get(f'{server}/kakao/agent/pending',
                headers=_headers(secret), params={'limit': 1}, timeout=20)
            response.raise_for_status()
            items = response.json().get('items', [])
        except requests.RequestException as exc:
            if not _retryable_request_error(exc):
                _event(None, 'pending_unavailable', _safe_request_error(exc))
                raise
            self.failures += 1
            delay = min(60, 5 * 2 ** min(self.failures - 1, 4))
            self.next_at = time.monotonic() + delay
            if self.failures == 1:
                _event(None, 'pending_unavailable', _safe_request_error(exc))
            _event(None, 'pending_retry_scheduled', f'failures={self.failures}; delay={delay}s')
            return []
        if self.failures:
            _event(None, 'pending_recovered', f'조회 복구; preceding_failures={self.failures}')
            from core.error_notifications import report
            report('server_recovered')
        self.failures = 0
        self.next_at = time.monotonic() + 5
        return items

def _journal_key(item: dict) -> str:
    return str(item.get("delivery_key") or hashlib.sha256(f"{item.get('room_binding_id')}:{item.get('id')}".encode()).hexdigest())

def _append_journal(item: dict, part_id: str, result: str, text: str = "") -> None:
    JOURNAL.parent.mkdir(parents=True, exist_ok=True)
    with JOURNAL.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"delivery_key": _journal_key(item), "outbox_id": item.get("id"), "part_id": part_id, "result": result, "content_hash": hashlib.sha256(text.strip().encode()).hexdigest() if text else "", "at": time.time()}) + "\n")

def _event(item: dict | None, state: str, detail: str = "") -> None:
    EVENT_LOG.parent.mkdir(parents=True, exist_ok=True)
    record = {"at": time.time(), "state": state, "detail": detail}
    if item:
        record.update({"outbox_id": item.get("id"), "delivery_key": item.get("delivery_key"), "room": item.get("external_room_id"), "part_id": item.get("current_part_id")})
    with EVENT_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    if state.endswith('_failed') or state in ('unknown_result', 'failed_not_sent', 'archive_deferred', 'pending_unavailable'):
        from core.error_notifications import notify
        context = {'stage': state, 'cause': detail,
                   'automatic_action': '결과를 확정하지 못한 작업은 완료 처리하거나 자동 재전송하지 않음'}
        if item:
            context.update(source_room=item.get('external_room_id', ''),
                           target_room=item.get('external_room_id', ''))
        elif state == 'inbound_room_failed' and ':' in detail:
            room, cause = detail.split(':', 1)
            context.update(source_room=room.strip(), cause=cause.strip())
        notify(state, (item or {}).get('id', ''), context)
    elif state == 'sent':
        from core.error_notifications import report
        report('outbound_sent', (item or {}).get('id', ''))

def _load_journal() -> dict[tuple[str, str], str]:
    sent = {}
    if not JOURNAL.exists(): return sent
    for line in JOURNAL.read_text(encoding="utf-8", errors="ignore").splitlines():
        try:
            row = json.loads(line)
            sent[(_journal_key(row), str(row.get("part_id") or ""))] = row.get("result", "")
        except json.JSONDecodeError:
            continue
    return sent

def _assert_room(hwnd: int, title: str) -> None:
    import win32gui
    if win32gui.GetForegroundWindow() != hwnd or win32gui.GetWindowText(hwnd) != title:
        raise RuntimeError("전송 직전 카카오톡 방 포커스/제목이 변경되었습니다")

def _send_text(text: str) -> None:
    pyperclip = __import__("pyperclip")
    pyperclip.copy(text)
    pyautogui.hotkey("ctrl", "v")
    pyautogui.press("enter")
    time.sleep(0.4)

def _safe_attachment_name(name: str) -> str:
    return Path(name or "attachment.bin").name or "attachment.bin"

def _download_attachment(server: str, part: dict) -> Path:
    url = str(part.get("url") or "")
    parsed, expected = urlparse(url), urlparse(server)
    if parsed.scheme != "https" or parsed.netloc != expected.netloc:
        raise RuntimeError("not_sent: attachment URL is outside the MOYI server")
    target_dir = ROOT / "data" / "moyi_attachment_cache" / _journal_key(part)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / _safe_attachment_name(str(part.get("name") or "attachment.bin"))
    total = 0
    try:
        with requests.get(url, stream=True, timeout=60) as response:
            response.raise_for_status()
            with target.open("wb") as output:
                for chunk in response.iter_content(1024 * 256):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > MAX_ATTACHMENT_BYTES:
                        raise RuntimeError("not_sent: attachment exceeds 50MB")
                    output.write(chunk)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return target

def _copy_file_to_clipboard(path: Path) -> None:
    import win32clipboard
    payload = struct.pack("IiiII", 20, 0, 0, 0, 1) + (str(path.resolve()) + "\0\0").encode("utf-16le")
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32clipboard.CF_HDROP, payload)
    finally:
        win32clipboard.CloseClipboard()

def _send_attachment(path: Path) -> None:
    _copy_file_to_clipboard(path)
    pyautogui.hotkey("ctrl", "v")
    time.sleep(1.2)
    pyautogui.press("enter")
    time.sleep(1.0)

def process_item(server: str, secret: str, item: dict) -> None:
    if _is_suppressed_system_item(item):
        _event(item, "suppressed", "MOYI system notice: thread-head settings")
        requests.post(
            f"{server}/kakao/agent/ack/{item['id']}",
            headers=_headers(secret),
            json={
                "ok": True,
                "final": True,
                "outcome": "sent",
                "lease_token": item.get("lease_token"),
                "completed_part_ids": [
                    str(part.get("part_id"))
                    for part in item.get("parts") or []
                    if part.get("part_id")
                ],
            },
            timeout=20,
        ).raise_for_status()
        return
    title, binding = str(item.get("external_room_id") or "").strip(), str(item.get("room_binding_id") or "").strip()
    if not title or not binding:
        raise RuntimeError("방 제목 또는 room_binding_id가 없습니다")
    open_room_by_name(title)
    hwnd = open_unique_exact_room(title)
    verify = requests.post(
        f"{server}/kakao/agent/verify-room", headers=_headers(secret),
        json={"room_binding_id": binding, "exact_title": title, "match_count": 1},
        timeout=20,
    )
    verify.raise_for_status()
    _event(item, "room_verified", title)
    completed = set(item.get("completed_part_ids") or [])
    journal = _load_journal()
    try:
        for part in sorted(item.get("parts") or [], key=lambda p: p.get("sequence", 0)):
            part_id = str(part.get("part_id") or "")
            if not part_id or part_id in completed:
                continue
            previous = journal.get((_journal_key(item), part_id))
            if previous in ("sent", "unknown_result"):
                completed.add(part_id)
                _event(item, "journal_hold", f"part={part_id}, previous={previous}")
                continue
            _assert_room(hwnd, title)
            _event(item, "paste_started", f"part={part_id}")
            if part.get("type") == "text":
                _send_text(str(part.get("text") or ""))
                hash_text = str(part.get("text") or "")
            elif part.get("type") in ("image", "file"):
                downloaded = _download_attachment(server, {**item, **part})
                _send_attachment(downloaded)
                hash_text = ""
            else:
                raise RuntimeError(f"not_sent: 지원하지 않는 part type: {part.get('type')}")
            _event(item, "enter_pressed", f"part={part_id}")
            completed.add(part_id)
            _append_journal(item, part_id, "sent", hash_text)
            _event(item, "sent", f"part={part_id}")
            response = requests.post(f"{server}/kakao/agent/ack/{item['id']}", headers=_headers(secret), json={"ok": True, "lease_token": item.get("lease_token"), "completed_part_ids": sorted(completed), "current_part_id": part_id}, timeout=20)
            response.raise_for_status()
        requests.post(f"{server}/kakao/agent/ack/{item['id']}", headers=_headers(secret), json={"ok": True, "final": True, "outcome": "sent", "lease_token": item.get("lease_token"), "completed_part_ids": sorted(completed)}, timeout=20).raise_for_status()
    finally:
        close_room(hwnd)

def run() -> int:
    server, secret = _config()
    pending_poller = PendingPoller()
    room_breaker = RoomCircuitBreaker()
    from core.moyi_inbound import poll_once as poll_inbound_once
    from core.agent_runtime import AgentCoordinator
    from core.error_notifications import report
    inbound_interval = max(15, int(os.getenv("MOYI_INBOUND_SCAN_SEC", "30")))
    from core.workflow_settings import config as workflow_config, register_agents
    workflow = workflow_config()
    background_interval = workflow['background_interval_sec']
    next_report_at = next_outbound_at = next_primary_at = 0.0
    next_inbound_at = 0.0
    inbound_room_index = 0
    pause_announced = False
    next_archive_at = 0.0
    from core import import_order, keyword_approval, keyword_forward, order_services
    from core.moyi_inbound import export_exact_room, _load_state, _save_state

    def mark_rescan(title):
        response = requests.get(f'{server}/kakao/agent/rooms', headers=_headers(secret), timeout=20)
        response.raise_for_status()
        state = _load_state()
        pending = set(state.get('_needs_rescan', []))
        for room in response.json().get('items', []):
            if room.get('exact_title') == title:
                pending.add(str(room['room_binding_id']))
        state['_needs_rescan'] = sorted(pending)
        _save_state(state)

    def sales_agent():
        if is_paused(): return None
        result = _timed('agent:sales', poll_inbound_once, server, secret,
                        only_title='영업방', defer_archive=True, max_events=5)
        if result['sent'] or result['initialized']:
            report('inbound_processed')
        from core.error_notifications import resolve_all
        resolve_all('inbound_room_failed')
        return result

    def approval_agent():
        if is_paused(): return None
        result = _timed('agent:approval', keyword_approval.poll, export_exact_room,
                        keyword_forward.send_exact, is_paused, mark_rescan)
        from core.error_notifications import poll as poll_notices
        _timed('approval:receipts', poll_notices, export_exact_room,
               keyword_forward.send_exact, is_paused, receipts_only=True)
        from core.error_notifications import resolve_all
        resolve_all('approval_check_failed')
        return result

    def order_agent():
        if is_paused(): return None
        result = _timed('agent:import_collection', poll_inbound_once, server, secret,
                        only_title='수입방', defer_archive=True, max_events=5)
        from core.order_review import start_sync
        from core.order_analysis_queue import start as start_analysis
        start_analysis()
        start_sync()
        if workflow_config()['order_review_only']:
            return result
        return _timed('agent:order', import_order.poll, export_exact_room,
                      keyword_forward.send_exact, order_services.master,
                      order_services.register_bulk, is_paused)

    error_states = {'sales': 'inbound_room_failed', 'approval': 'approval_check_failed',
                    'order': 'import_order_check_failed'}
    def agent_error(agent, exc):
        _event(None, error_states[agent.name], str(exc)[:200])
        _restore_safe_cursor()

    coordinator = AgentCoordinator(AGENT_LOG, clock=time.monotonic,
                                   wall_clock=time.time, on_error=agent_error)
    register_agents(coordinator, sales_agent, approval_agent, order_agent)

    def priority_poll():
        nonlocal next_primary_at
        if not is_paused() and time.monotonic() >= next_primary_at:
            # Interval is measured from the start, not added to UI processing.
            next_primary_at = time.monotonic() + workflow['primary_interval_sec']
            coordinator.run_due()
    print("[MOYI] Kakao connector worker started (fail-closed)")
    report('worker_started')
    print("[MOYI] agents: sales then approvals/receipts, import review, 30-minute background")
    while True:
        if is_paused():
            if not pause_announced:
                print("[MOYI] connector paused from operations console")
                _event(None, "paused", "operations console")
                pause_announced = True
            time.sleep(1)
            continue
        if pause_announced:
            print("[MOYI] connector resumed from operations console")
            _event(None, "resumed", "operations console")
            pause_announced = False
        priority_poll()
        if not is_paused() and time.monotonic() >= next_report_at:
            next_report_at = time.monotonic() + background_interval
            from core import error_notifications, keyword_forward
            from core.moyi_inbound import export_exact_room
            try:
                error_notifications.poll(export_exact_room,
                    lambda room, text: keyword_forward.send_exact(room, text, require_forward_enabled=False), is_paused)
            except Exception as exc:
                from core.moyi_control import audit, OperationPaused
                if isinstance(exc, OperationPaused):
                    audit('notice_paused', '사용자 일시정지; 알림 처리 기록 유지')
                else:
                    audit('error_notice_poll_failed', '오류 알림 처리 실패 · 프로그램 확인 필요')
        outbound_items = []
        if not is_paused() and time.monotonic() >= next_outbound_at:
            next_outbound_at = time.monotonic() + background_interval
            outbound_items = pending_poller.fetch(server, secret)
        for item in outbound_items:
            _event(item, "leased", "server queue lease acquired")
            try:
                _timed('outbound', process_item, server, secret, item)
            except Exception as exc:
                detail = str(exc)
                state = "failed_not_sent" if detail.startswith("not_sent:") or "방 제목" in detail or "exact room" in detail else "unknown_result"
                print(f"[MOYI] {state} {item.get('id')}: {detail}")
                _event(item, state, detail)
                try:
                    requests.post(f"{server}/kakao/agent/ack/{item['id']}", headers=_headers(secret), json={"ok": False, "outcome": "unknown_result", "lease_token": item.get("lease_token"), "error": str(exc)[:500]}, timeout=20).raise_for_status()
                except requests.RequestException as ack_exc:
                    print(f"[MOYI] failure ack temporarily unavailable ({_safe_request_error(ack_exc)})")
        priority_poll()
        if not is_paused() and time.monotonic() >= next_inbound_at:
            rooms = []
            try:
                rooms_response = requests.get(
                    f"{server}/kakao/agent/rooms", headers=_headers(secret), timeout=20
                )
                rooms_response.raise_for_status()
                from core.error_notifications import resolve_all
                resolve_all('inbound_scan_failed')
                rooms = [
                    room for room in rooms_response.json().get("items", [])
                    if str(room.get("exact_title") or "").strip()
                    and str(room.get("exact_title") or "").strip() not in ('영업방', '수입방')
                ]
                if rooms and not is_paused():
                    for room in _inbound_schedule(rooms):
                        if is_paused(): break
                        priority_poll()
                        title = str(room.get("exact_title") or "").strip()
                        if room_breaker.available(title):
                            try:
                                result = _timed('inbound:' + title, poll_inbound_once, server, secret, only_title=title, defer_archive=True, max_events=5)
                                if result["sent"] or result["initialized"]:
                                    report('inbound_processed')
                                    print(f"[MOYI] inbound {title}: {result['sent']} sent, {result['initialized']} initialized")
                                room_breaker.succeeded(title)
                            except Exception as room_exc:
                                print(f"[MOYI] inbound room failed ({title}): {room_exc}")
                                _event(None, "inbound_room_failed", f"{title}: {str(room_exc)[:400]}")
                                if room_breaker.failed(title):
                                    _event(None, 'room_circuit_open', f'{title}: 3회 연속 실패; 30분 격리')
                                _restore_safe_cursor()

            except Exception as exc:
                print(f"[MOYI] inbound scan failed: {exc}")
                _event(None, "inbound_scan_failed", str(exc)[:500])
            # Bounded room chunks yield back to replies before the next room.
            room_count = len(rooms) if rooms else 1
            next_inbound_at = time.monotonic() + background_interval
        priority_poll()
        if not is_paused() and time.monotonic() >= next_archive_at:
            try:
                from core.mindmap_sink import flush_pending
                archived = _timed('archive', flush_pending, batch_size=50, timeout=10)
                if archived: report('archive_completed')
            except Exception as exc:
                _event(None, 'archive_deferred', type(exc).__name__)
            next_archive_at = time.monotonic() + background_interval
        time.sleep(1)
