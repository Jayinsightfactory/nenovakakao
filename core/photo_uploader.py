"""
사진 파일 → nenovaweb.com 자체 호스팅 업로드 → 공개 URL 반환.

서버 규격 (관리자가 nenovaweb.com에 추가):
  POST /api/agent/photo-upload
    Header: Cookie: nenovaToken=<JWT>
    Body: multipart/form-data (file=이미지, room=옵션)
    Response: {"url": "https://nenovaweb.com/uploads/photos/..."}

  GET /uploads/photos/YYYY/MM/DD/<uuid>.jpg
    인증 없이 접근 가능해야 함 (카카오워크 Bot API가 사용).

사용:
  from core.photo_uploader import upload_to_nenovaweb, upload_many
  url = upload_to_nenovaweb(Path("photo.jpg"), room="수입방")
  urls = upload_many([p1, p2], room="수입방")
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env", override=True)

NENOVAWEB_URL = os.getenv("NENOVAWEB_URL", "https://nenovaweb.com").rstrip("/")
UPLOAD_ENDPOINT = f"{NENOVAWEB_URL}/api/agent/photo-upload"
_TIMEOUT = 30
_MAX_RETRIES = 3
_RETRY_DELAY = 2.0

# 업로드 결과 캐시 (같은 파일 중복 업로드 방지)
_cache: dict[str, str] = {}


def _cache_key(p: Path) -> str:
    try:
        st = p.stat()
        return f"{p.name}:{st.st_size}:{int(st.st_mtime)}"
    except Exception:
        return str(p)


def _get_session() -> Optional[requests.Session]:
    """ERPBridge의 인증된 세션 재사용."""
    try:
        from core.erp_bridge import ERPBridge
        bridge = ERPBridge()
        if not bridge._ensure_auth():
            print("  [NENOVAWEB] 로그인 실패", flush=True)
            return None
        return bridge.session
    except Exception as e:
        print(f"  [NENOVAWEB] ERPBridge import 실패: {e}", flush=True)
        return None


def upload_to_nenovaweb(file_path: Path, room: str = "") -> Optional[str]:
    """단일 이미지 업로드. 성공 시 공개 URL, 실패 시 None."""
    if not file_path.exists():
        print(f"  [NENOVAWEB] 파일 없음: {file_path}", flush=True)
        return None

    # 캐시 체크
    key = _cache_key(file_path)
    if key in _cache:
        return _cache[key]

    session = _get_session()
    if session is None:
        return None

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            with open(file_path, "rb") as f:
                files = {"file": (file_path.name, f, "image/jpeg")}
                data = {"room": room} if room else {}
                resp = session.post(
                    UPLOAD_ENDPOINT,
                    files=files,
                    data=data,
                    timeout=_TIMEOUT,
                )
            if resp.status_code == 200:
                j = resp.json()
                url = j.get("url")
                if url:
                    _cache[key] = url
                    print(f"  [NENOVAWEB] {file_path.name} -> {url}", flush=True)
                    return url
                print(f"  [NENOVAWEB] 응답에 url 없음: {j}", flush=True)
            elif resp.status_code == 401:
                print(f"  [NENOVAWEB] 401 인증 실패 - 재로그인 필요", flush=True)
                # 토큰 초기화 후 재시도
                try:
                    from core.erp_bridge import ERPBridge
                    b = ERPBridge()
                    b._token = None
                    b._ensure_auth()
                    session = b.session
                except Exception:
                    pass
                continue
            elif resp.status_code == 404:
                print(f"  [NENOVAWEB] 404 — {UPLOAD_ENDPOINT} 엔드포인트 미구현 "
                      f"(관리자가 서버에 추가 필요)", flush=True)
                return None
            else:
                print(f"  [NENOVAWEB] HTTP {resp.status_code}: {resp.text[:200]}", flush=True)
        except requests.exceptions.RequestException as e:
            print(f"  [NENOVAWEB] 요청 실패 (시도 {attempt}/{_MAX_RETRIES}): {e}", flush=True)
            if attempt < _MAX_RETRIES:
                time.sleep(_RETRY_DELAY)
                continue

    return None


def upload_many(file_paths: list[Path], room: str = "") -> list[Optional[str]]:
    """여러 파일 순차 업로드. 입력과 같은 순서, 실패는 None."""
    urls: list[Optional[str]] = []
    for p in file_paths:
        url = upload_to_nenovaweb(p, room=room)
        urls.append(url)
    return urls


def _get_client_id() -> bool:
    """send_delta_interleaved에서 체크용 (이름만 호환).
    nenovaweb는 ERP 자격으로 인증되므로 NENOVAWEB_USERNAME/PASSWORD 있으면 OK.
    """
    return bool(os.getenv("NENOVAWEB_USERNAME") and os.getenv("NENOVAWEB_PASSWORD"))


def check_credentials() -> bool:
    """엔드포인트 체크 — 가짜 GET으로 endpoint 존재 확인."""
    session = _get_session()
    if session is None:
        return False
    try:
        # HEAD 또는 GET으로 endpoint 존재 확인 (404 아니면 OK)
        resp = session.get(UPLOAD_ENDPOINT, timeout=10)
        if resp.status_code == 404:
            print(f"  [NENOVAWEB] 엔드포인트 미구현: {UPLOAD_ENDPOINT}", flush=True)
            return False
        # 405 Method Not Allowed도 엔드포인트는 존재하는 것
        print(f"  [NENOVAWEB] 엔드포인트 확인: {UPLOAD_ENDPOINT} (HTTP {resp.status_code})", flush=True)
        return True
    except Exception as e:
        print(f"  [NENOVAWEB] 체크 실패: {e}", flush=True)
        return False


if __name__ == "__main__":
    import sys
    print(f"nenovaweb 업로드 엔드포인트: {UPLOAD_ENDPOINT}")
    if not check_credentials():
        sys.exit(1)
    if len(sys.argv) > 1:
        p = Path(sys.argv[1])
        url = upload_to_nenovaweb(p, room="test")
        print(f"결과: {url}")
