"""
ERP Bridge — nenova_agent ↔ nenova-erp-ui API 연결 레이어.

모든 ERP 통신을 한 곳에서 관리:
- 인증 (JWT 토큰 자동 갱신)
- 인사이트 적재 (POST /api/agent/intelligence)
- 이슈 등록/조회 (POST/GET /api/agent/issues)
- 백업 기록 (POST /api/agent/backup)
- 마스터 조회 (GET /api/master)

사용:
    bridge = ERPBridge()
    bridge.push_intelligence(per_room_data)
    bridge.report_issue("수입방", "검역차감 5건 이상", severity="critical")
    bridge.log_pipeline_run(run_id, status="completed", ...)
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env", override=True)

ERP_BASE = os.getenv("NENOVAWEB_URL", "https://nenovaweb.com")
ERP_USER = os.getenv("NENOVAWEB_USERNAME", "admin")
ERP_PASS = os.getenv("NENOVAWEB_PASSWORD", "1234")


class ERPBridge:
    """ERP API 연결 브릿지 (세션 + 토큰 자동 관리)."""

    def __init__(self, base_url: str = ERP_BASE):
        self.base = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.timeout = 15
        self._token: str | None = None
        self._token_ts: float = 0

    # ─── 인증 ───
    def _ensure_auth(self) -> bool:
        """토큰이 없거나 7시간 경과하면 자동 로그인."""
        if self._token and (time.time() - self._token_ts < 7 * 3600):
            return True
        try:
            r = self.session.post(
                f"{self.base}/api/auth/login",
                json={"userId": ERP_USER, "password": ERP_PASS},
            )
            if r.status_code == 200:
                data = r.json()
                self._token = data.get("token")
                self._token_ts = time.time()
                self.session.headers["Cookie"] = f"nenovaToken={self._token}"
                return True
            print(f"[ERPBridge] 로그인 실패: {r.status_code}", flush=True)
            return False
        except Exception as e:
            print(f"[ERPBridge] 연결 실패: {e}", flush=True)
            return False

    def _post(self, path: str, body: dict) -> dict | None:
        if not self._ensure_auth():
            return None
        try:
            r = self.session.post(f"{self.base}{path}", json=body)
            return r.json()
        except Exception as e:
            print(f"[ERPBridge] POST {path} 실패: {e}", flush=True)
            return None

    def _get(self, path: str, params: dict | None = None) -> dict | None:
        if not self._ensure_auth():
            return None
        try:
            r = self.session.get(f"{self.base}{path}", params=params)
            return r.json()
        except Exception as e:
            print(f"[ERPBridge] GET {path} 실패: {e}", flush=True)
            return None

    # ─── 인사이트 적재 ───
    def push_intelligence(self, per_room_data: dict, pipeline_run_id: str = "") -> dict | None:
        """방별 분석 결과를 ERP DB에 적재."""
        return self._post("/api/agent/intelligence", {
            **per_room_data,
            "pipeline_run_id": pipeline_run_id,
        })

    # ─── 이슈 컨트롤 ───
    def report_issue(self, room_name: str, title: str,
                     issue_type: str = "alert", severity: str = "warning",
                     detail: Any = None, pipeline_run_id: str = "") -> dict | None:
        """이슈 등록."""
        return self._post("/api/agent/issues", {
            "room_name": room_name,
            "issue_type": issue_type,
            "severity": severity,
            "title": title,
            "detail": detail,
            "pipeline_run_id": pipeline_run_id,
        })

    def get_open_issues(self, limit: int = 100) -> list:
        """미해결 이슈 조회."""
        r = self._get("/api/agent/issues", {"status": "open", "limit": limit})
        return r.get("data", []) if r else []

    def resolve_issue(self, issue_id: int, resolved_by: str = "agent") -> dict | None:
        """이슈 해결 처리."""
        if not self._ensure_auth():
            return None
        try:
            r = self.session.patch(f"{self.base}/api/agent/issues",
                                   json={"id": issue_id, "status": "resolved", "resolved_by": resolved_by})
            return r.json()
        except Exception as e:
            print(f"[ERPBridge] PATCH issues 실패: {e}", flush=True)
            return None

    # ─── 백업 컨트롤 ───
    def log_pipeline_start(self, run_id: str) -> dict | None:
        """파이프라인 실행 시작 기록."""
        return self._post("/api/agent/backup", {
            "run_id": run_id,
            "status": "running",
        })

    def log_pipeline_end(self, run_id: str, status: str = "completed",
                         rooms_scanned: int = 0, rooms_saved: int = 0,
                         messages_sent: int = 0, issues_found: int = 0,
                         duration_sec: float = 0, summary: dict = None,
                         error_log: str = "") -> dict | None:
        """파이프라인 실행 완료 기록."""
        return self._post("/api/agent/backup", {
            "run_id": run_id,
            "status": status,
            "rooms_scanned": rooms_scanned,
            "rooms_saved": rooms_saved,
            "messages_sent": messages_sent,
            "issues_found": issues_found,
            "duration_sec": duration_sec,
            "summary": summary or {},
            "error_log": error_log,
        })

    def get_pipeline_history(self, limit: int = 50) -> list:
        """파이프라인 실행 이력 조회."""
        r = self._get("/api/agent/backup", {"limit": limit})
        return r.get("data", []) if r else []

    # ─── 이미지 업로드 (public URL 생성) ───
    def upload_image(self, file_path, room_name: str = "", pipeline_run_id: str = "") -> str | None:
        """
        이미지를 ERP DB에 업로드 → 공개 URL 반환.
        워크의 Block Kit 이미지 첨부용.
        """
        from pathlib import Path
        p = Path(file_path)
        if not p.exists():
            return None
        if not self._ensure_auth():
            return None
        try:
            import mimetypes
            mime = mimetypes.guess_type(str(p))[0] or "image/jpeg"
            with open(p, "rb") as f:
                files = {"image": (p.name, f, mime)}
                data = {"room_name": room_name, "pipeline_run_id": pipeline_run_id}
                r = self.session.post(
                    f"{self.base}/api/agent/image-upload",
                    files=files,
                    data=data,
                    timeout=60,
                )
            result = r.json()
            if result.get("success"):
                return result.get("url")
            print(f"[ERPBridge] 이미지 업로드 실패: {result}", flush=True)
            return None
        except Exception as e:
            print(f"[ERPBridge] 이미지 업로드 예외: {e}", flush=True)
            return None

    # ─── 마스터 조회 (기존 API 활용) ───
    def get_customers(self) -> list:
        r = self._get("/api/master", {"entity": "customers"})
        return r.get("data", []) if r else []

    def get_products(self) -> list:
        r = self._get("/api/master", {"entity": "products"})
        return r.get("data", []) if r else []

    def search_customer(self, keyword: str) -> list:
        r = self._get("/api/customers/search", {"q": keyword})
        return r.get("data", []) if r else []

    def search_product(self, keyword: str) -> list:
        r = self._get("/api/products/search", {"q": keyword})
        return r.get("data", []) if r else []

    # ─── 주문등록 / 출고 / 재고 (Phase 3 카톡→ERP 자동화) ───
    # 참고: CLAUDE.md "nenovaweb.com API 구조" 섹션
    # POST /api/shipment/stock-status   { action, custKey, prodKey, week, qty, unit }
    # PATCH /api/shipment/stock-status  { custKey, prodKey, week, outQty }
    # PUT   /api/shipment/stock-status  { prodKey, week, stock }
    # POST /api/shipment/distribute     { week, year, custKey, prodKey, outQty, cost }

    def add_order(
        self,
        *,
        cust_key: int,
        prod_key: int,
        week: str,
        qty: int | float,
        unit: str = "단",
    ) -> dict | None:
        """
        주문등록 (카톡 → ERP 자동화의 핵심).

        예) "15-1차 카네이션변경사항 주광 연그린 1추가"
            → parse → add_order(cust_key=..., prod_key=..., week="15-1", qty=1, unit="단")
        """
        return self._post("/api/shipment/stock-status", {
            "action": "addOrder",
            "custKey": cust_key,
            "prodKey": prod_key,
            "week": week,
            "qty": qty,
            "unit": unit,
        })

    def distribute_outgoing(
        self,
        *,
        cust_key: int,
        prod_key: int,
        week: str,
        out_qty: int | float,
    ) -> dict | None:
        """출고 분배 업데이트 (PATCH)."""
        if not self._ensure_auth():
            return None
        try:
            r = self.session.patch(
                f"{self.base}/api/shipment/stock-status",
                json={
                    "custKey": cust_key,
                    "prodKey": prod_key,
                    "week": week,
                    "outQty": out_qty,
                },
            )
            return r.json()
        except Exception as e:
            print(f"[ERPBridge] PATCH distribute 실패: {e}", flush=True)
            return None

    def set_start_stock(
        self, *, prod_key: int, week: str, stock: int | float,
    ) -> dict | None:
        """시작재고 설정 (PUT)."""
        if not self._ensure_auth():
            return None
        try:
            r = self.session.put(
                f"{self.base}/api/shipment/stock-status",
                json={"prodKey": prod_key, "week": week, "stock": stock},
            )
            return r.json()
        except Exception as e:
            print(f"[ERPBridge] PUT set_start_stock 실패: {e}", flush=True)
            return None

    def create_shipment_detail(
        self,
        *,
        week: str,
        year: int,
        cust_key: int,
        prod_key: int,
        out_qty: int | float,
        cost: int | float,
    ) -> dict | None:
        """출고 상세 생성 (거래처 x 품목 단가 기록)."""
        return self._post("/api/shipment/distribute", {
            "week": week, "year": year,
            "custKey": cust_key, "prodKey": prod_key,
            "outQty": out_qty, "cost": cost,
        })

    # ─── PeriodDay: 날짜 → 차수 매핑 (주문등록의 week 필드 생성용) ───
    def date_to_week(self, date_str: str) -> str | None:
        """
        '2026-04-10' → '15-1' 같은 차수 문자열.
        PeriodDay 마스터 테이블 기반. 실패 시 None.
        """
        r = self._get("/api/master/period-day", {"date": date_str})
        if not r or not r.get("data"):
            return None
        return r["data"].get("week")


# ─── 싱글톤 ───
_bridge: ERPBridge | None = None


def get_bridge() -> ERPBridge:
    global _bridge
    if _bridge is None:
        _bridge = ERPBridge()
    return _bridge
