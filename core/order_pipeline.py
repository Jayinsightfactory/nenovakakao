"""
카톡 메시지 → ERP 주문 자동 등록 파이프라인 (Phase 3 스켈레톤).

흐름:
    delta 텍스트 (수집된 신규 카톡 메시지)
        ↓ parse_delta_to_messages — 메시지 단위 분리
        ↓ parse_message — event_type/sequence/product/variety/quantity/supplier 추출
        ↓ resolve_keys — 거래처명/품목명 → custKey/prodKey (마스터 매칭)
        ↓ stage_order — ErpBridge.add_order 호출 준비
        ↓ (미승인 대기열 or 자동 커밋) — data/pending_orders.json

주의:
- 본 모듈은 "드라이런" 기본. 실제 커밋은 auto_commit=True 또는
  approve_pending_orders 호출 시.
- 키 매칭 실패 시 pending_orders.json의 resolution='pending'으로 남기고
  관리자 검토 후 수동 해결.

관리자 워크플로:
    # 1. 카톡 수집 + 파싱 + 스테이징 (드라이런)
    python -c "from core.order_pipeline import process_delta_to_orders as p; \\
               import json; print(json.dumps(p('...'), ensure_ascii=False, indent=2))"

    # 2. pending_orders.json 검토 (매칭 결과 확인)
    # 3. 확정 승인 (실제 API 커밋)
    python -c "from core.order_pipeline import commit_pending_orders; commit_pending_orders()"
"""
from __future__ import annotations

import json
import time
from datetime import date
from pathlib import Path
from typing import Any

from core.kakaowork_router import parse_delta_to_messages
from core.gsheet_sync import parse_message
from core.erp_bridge import get_bridge

DATA_DIR = Path(__file__).parent.parent / "data"
PENDING_FILE = DATA_DIR / "pending_orders.json"
MASTER_CACHE = DATA_DIR / "erp_master_cache.json"
MASTER_TTL_SEC = 3600  # 1시간


# ─── 마스터 캐시 (거래처/품목) ───

def _load_master_cache() -> dict:
    if not MASTER_CACHE.exists():
        return {}
    try:
        return json.loads(MASTER_CACHE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_master_cache(cache: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    MASTER_CACHE.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _get_masters(force: bool = False) -> dict:
    """
    {
        'customers': {name: custKey, ...},
        'products':  {name: prodKey, ...},
        'fetched_ts': float,
    }
    TTL 지나면 ERP에서 재조회.
    """
    cache = _load_master_cache()
    age = time.time() - cache.get("fetched_ts", 0)
    if not force and cache and age < MASTER_TTL_SEC:
        return cache

    bridge = get_bridge()
    try:
        customers = bridge.get_customers()
        products = bridge.get_products()
    except Exception as e:
        print(f"[MASTER] ERP 조회 실패: {e} - 기존 캐시 사용", flush=True)
        return cache if cache else {"customers": {}, "products": {}, "fetched_ts": 0}

    cust_map: dict[str, int] = {}
    for c in customers or []:
        name = (c.get("custNm") or c.get("name") or "").strip()
        key = c.get("custKey") or c.get("id")
        if name and key is not None:
            cust_map[name] = int(key)

    prod_map: dict[str, int] = {}
    for p in products or []:
        name = (p.get("prodNm") or p.get("name") or "").strip()
        key = p.get("prodKey") or p.get("id")
        if name and key is not None:
            prod_map[name] = int(key)

    data = {
        "customers": cust_map,
        "products": prod_map,
        "fetched_ts": time.time(),
    }
    _save_master_cache(data)
    print(
        f"[MASTER] 갱신: 거래처 {len(cust_map)}건, 품목 {len(prod_map)}건",
        flush=True,
    )
    return data


def _find_key(name: str, name_map: dict[str, int]) -> tuple[int | None, str]:
    """
    이름 매칭: 완전일치 → 공백제거 일치 → 부분포함.

    Returns:
        (key, matched_name) — 찾지 못하면 (None, "")
    """
    if not name or not name_map:
        return None, ""
    if name in name_map:
        return name_map[name], name

    # 공백 제거 매칭
    norm = name.replace(" ", "")
    for k, v in name_map.items():
        if k.replace(" ", "") == norm:
            return v, k

    # 부분 포함 (파싱된 이름이 짧은 경우)
    for k, v in name_map.items():
        if name in k or k in name:
            return v, k
    return None, ""


# ─── 스테이징 (pending_orders.json) ───

def _load_pending() -> list[dict]:
    if not PENDING_FILE.exists():
        return []
    try:
        d = json.loads(PENDING_FILE.read_text(encoding="utf-8"))
        return d if isinstance(d, list) else []
    except Exception:
        return []


def _save_pending(items: list[dict]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PENDING_FILE.write_text(
        json.dumps(items, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ─── 메인 파이프라인 ───

def stage_order_from_parsed(
    parsed: dict, *, room_name: str, raw_text: str,
) -> dict:
    """
    parse_message 결과 → ERP 주문 스테이징.

    Returns:
        {
            'status': 'ready' | 'missing_key' | 'skipped',
            'reason': str,
            'payload': {...},       # add_order()에 바로 넘길 수 있는 dict
            'raw': str,             # 원문
            'parsed': dict,         # parse_message 반환
            'resolved': {           # 매칭 결과
                'cust_key': int|None, 'cust_matched': str,
                'prod_key': int|None, 'prod_matched': str,
            },
            'room_name': str,
            'ts': float,
        }
    """
    result = {
        "status": "skipped",
        "reason": "",
        "payload": {},
        "raw": raw_text,
        "parsed": parsed,
        "resolved": {},
        "room_name": room_name,
        "ts": time.time(),
    }

    # ORDER_CHANGE (추가/취소)만 처리
    if parsed.get("event_type") != "ORDER_CHANGE":
        result["reason"] = f"event_type={parsed.get('event_type')} - 주문 아님"
        return result

    # 필수 필드 확인
    qty_str = parsed.get("quantity", "")
    if not qty_str:
        result["reason"] = "수량 누락"
        return result

    try:
        qty = int(qty_str)
    except ValueError:
        result["reason"] = f"수량 파싱 실패: {qty_str}"
        return result

    direction = parsed.get("direction", "+")
    signed_qty = qty if direction == "+" else -qty

    week = parsed.get("sequence", "") or ""
    if not week:
        result["reason"] = "차수 누락"
        return result

    # 마스터 매칭
    masters = _get_masters()
    cust_key, cust_matched = _find_key(
        parsed.get("supplier", ""), masters.get("customers", {}),
    )
    prod_name = parsed.get("product", "")
    if parsed.get("variety"):
        prod_name += parsed["variety"]  # "카네이션" + "연그린"
    prod_key, prod_matched = _find_key(prod_name, masters.get("products", {}))

    result["resolved"] = {
        "cust_key": cust_key, "cust_matched": cust_matched,
        "prod_key": prod_key, "prod_matched": prod_matched,
    }

    if cust_key is None or prod_key is None:
        result["status"] = "missing_key"
        missing = []
        if cust_key is None:
            missing.append(f"거래처 '{parsed.get('supplier', '')}'")
        if prod_key is None:
            missing.append(f"품목 '{prod_name}'")
        result["reason"] = " + ".join(missing) + " 매칭 실패"
        return result

    result["payload"] = {
        "cust_key": cust_key,
        "prod_key": prod_key,
        "week": week,
        "qty": signed_qty,
        "unit": parsed.get("unit") or "단",
    }
    result["status"] = "ready"
    return result


def process_delta_to_orders(
    delta: str,
    room_name: str,
    *,
    auto_commit: bool = False,
) -> dict:
    """
    delta → 메시지 분할 → 주문 파싱 → 마스터 매칭 → 스테이징.

    Args:
        delta: 카톡 신규 델타
        room_name: 발생 방 이름
        auto_commit: True면 'ready' 상태의 주문을 즉시 ERP에 POST
                     (기본 False — 관리자 검토 후 commit_pending_orders 호출)

    Returns:
        {'ready': int, 'missing_key': int, 'skipped': int,
         'committed': int, 'items': [...]}
    """
    stats = {"ready": 0, "missing_key": 0, "skipped": 0, "committed": 0}
    items: list[dict] = []

    messages = parse_delta_to_messages(delta)
    for msg in messages:
        raw = f"[{msg['sender']}] [{msg['time']}] {msg['content']}"
        parsed = parse_message(msg["content"], room_name)
        staged = stage_order_from_parsed(
            parsed, room_name=room_name, raw_text=raw,
        )
        items.append(staged)
        stats[staged["status"]] = stats.get(staged["status"], 0) + 1

        if auto_commit and staged["status"] == "ready":
            try:
                bridge = get_bridge()
                resp = bridge.add_order(**staged["payload"])
                if resp and resp.get("success"):
                    staged["status"] = "committed"
                    staged["api_response"] = resp
                    stats["committed"] += 1
                    stats["ready"] -= 1
                else:
                    staged["api_response"] = resp
                    staged["status"] = "commit_failed"
            except Exception as e:
                staged["status"] = "commit_failed"
                staged["reason"] = f"{staged.get('reason', '')} | API error: {e}"

    # 기존 pending + 신규 항목 누적 (status != committed만)
    if not auto_commit:
        existing = _load_pending()
        pending_new = [x for x in items if x["status"] in ("ready", "missing_key")]
        existing.extend(pending_new)
        _save_pending(existing)

    stats["items"] = items
    return stats


def commit_pending_orders(*, only_ready: bool = True) -> dict:
    """
    pending_orders.json에 쌓인 주문 중 status='ready' 항목을 실제 ERP에 POST.

    Returns:
        {'committed': int, 'failed': int, 'skipped': int}
    """
    pending = _load_pending()
    if not pending:
        return {"committed": 0, "failed": 0, "skipped": 0}

    bridge = get_bridge()
    committed = failed = skipped = 0
    remaining: list[dict] = []

    for item in pending:
        if item.get("status") != "ready":
            if only_ready:
                remaining.append(item)
                skipped += 1
                continue
        try:
            resp = bridge.add_order(**item["payload"])
            if resp and resp.get("success"):
                committed += 1
                item["status"] = "committed"
                item["api_response"] = resp
                # committed는 제거 (remaining에 추가하지 않음)
            else:
                failed += 1
                item["api_response"] = resp
                item["status"] = "commit_failed"
                remaining.append(item)
        except Exception as e:
            failed += 1
            item["reason"] = f"{item.get('reason', '')} | API error: {e}"
            item["status"] = "commit_failed"
            remaining.append(item)

    _save_pending(remaining)
    return {"committed": committed, "failed": failed, "skipped": skipped}


def clear_pending(*, keep_failed: bool = True) -> int:
    """pending 정리. keep_failed=True면 commit_failed는 남김."""
    pending = _load_pending()
    if keep_failed:
        kept = [x for x in pending if x.get("status") == "commit_failed"]
    else:
        kept = []
    removed = len(pending) - len(kept)
    _save_pending(kept)
    return removed


if __name__ == "__main__":
    # 간단 self-test (드라이런)
    sample = """[임재용] [15:14] 15-1차 카네이션변경사항 주광 연그린 1단 추가
[전동민] [15:20] 검역증 합격 확인"""
    r = process_delta_to_orders(sample, room_name="수입방", auto_commit=False)
    print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
