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
DELETE_ENDPOINT = f"{NENOVAWEB_URL}/api/agent/photo-delete"
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


def _resize_if_too_large(file_path: Path, max_bytes: int = 5_000_000,
                         max_dim: int = 2048) -> Path:
    """파일이 너무 크면 리사이즈한 임시 파일 경로 반환. 아니면 원본 반환.
    nenovaweb HTTP 413 방지. max: 5MB 또는 2048px 긴변.
    """
    try:
        size = file_path.stat().st_size
        if size <= max_bytes:
            return file_path
        from PIL import Image
        img = Image.open(file_path)
        w, h = img.size
        scale = max_dim / max(w, h)
        if scale >= 1.0 and size <= max_bytes * 2:
            return file_path
        scale = min(scale, 1.0)
        nw, nh = int(w * scale), int(h * scale)
        img = img.resize((nw, nh), Image.LANCZOS)
        resized_path = file_path.parent / f".resized_{file_path.name}"
        # JPEG 로 저장 (크기 추가 감소)
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        img.save(resized_path, "JPEG", quality=85, optimize=True)
        new_size = resized_path.stat().st_size
        print(f"  [NENOVAWEB] 리사이즈 {w}x{h}/{size//1024}KB → {nw}x{nh}/{new_size//1024}KB", flush=True)
        return resized_path
    except Exception as e:
        print(f"  [NENOVAWEB] 리사이즈 실패 (원본 그대로): {e}", flush=True)
        return file_path


def upload_to_nenovaweb(file_path: Path, room: str = "") -> Optional[str]:
    """단일 이미지 업로드. 성공 시 공개 URL, 실패 시 None."""
    if not file_path.exists():
        print(f"  [NENOVAWEB] 파일 없음: {file_path}", flush=True)
        return None

    # 캐시 체크
    key = _cache_key(file_path)
    if key in _cache:
        print(f"  [NENOVAWEB] {file_path.name} → 캐시 히트 {_cache[key]}", flush=True)
        return _cache[key]

    session = _get_session()
    if session is None:
        print(f"  [NENOVAWEB] {file_path.name} → 세션 없음 (자격증명 미설정)", flush=True)
        return None

    # 크기 체크 + 필요 시 리사이즈
    upload_path = _resize_if_too_large(file_path)
    up_size = upload_path.stat().st_size if upload_path.exists() else 0
    print(f"  [NENOVAWEB] {file_path.name} 업로드 시작 ({up_size//1024}KB, attempt 1/{_MAX_RETRIES})", flush=True)

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            with open(upload_path, "rb") as f:
                files = {"file": (file_path.name, f, "image/jpeg")}
                data = {"room": room} if room else {}
                resp = session.post(
                    UPLOAD_ENDPOINT,
                    files=files,
                    data=data,
                    timeout=_TIMEOUT,
                )
            if resp.status_code == 200:
                try:
                    j = resp.json()
                except Exception as e:
                    print(f"  [NENOVAWEB] 200 but JSON 파싱 실패: {e} / body={resp.text[:200]}", flush=True)
                    return None
                url = j.get("url")
                if url:
                    _cache[key] = url
                    print(f"  [NENOVAWEB] {file_path.name} -> {url}", flush=True)
                    return url
                print(f"  [NENOVAWEB] {file_path.name} 응답에 url 없음: {j}", flush=True)
                return None
            elif resp.status_code == 401:
                print(f"  [NENOVAWEB] {file_path.name} 401 인증 실패 - 재로그인 (시도 {attempt}/{_MAX_RETRIES})", flush=True)
                try:
                    from core.erp_bridge import ERPBridge
                    b = ERPBridge()
                    b._token = None
                    b._ensure_auth()
                    session = b.session
                except Exception as e:
                    print(f"  [NENOVAWEB] 재로그인 실패: {e}", flush=True)
                continue
            elif resp.status_code == 404:
                print(f"  [NENOVAWEB] {file_path.name} 404 — {UPLOAD_ENDPOINT} 엔드포인트 미구현", flush=True)
                return None
            elif resp.status_code == 413:
                print(f"  [NENOVAWEB] {file_path.name} 413 크기 초과 ({up_size//1024}KB) — 리사이즈도 부족. 스킵", flush=True)
                return None
            else:
                print(f"  [NENOVAWEB] {file_path.name} HTTP {resp.status_code}: {resp.text[:200]}", flush=True)
        except requests.exceptions.RequestException as e:
            print(f"  [NENOVAWEB] {file_path.name} 요청 실패 (시도 {attempt}/{_MAX_RETRIES}): {e}", flush=True)
            if attempt < _MAX_RETRIES:
                time.sleep(_RETRY_DELAY)
                continue
        except Exception as e:
            print(f"  [NENOVAWEB] {file_path.name} 예외 (시도 {attempt}/{_MAX_RETRIES}): {type(e).__name__}: {e}", flush=True)
            if attempt < _MAX_RETRIES:
                time.sleep(_RETRY_DELAY)
                continue

    print(f"  [NENOVAWEB] {file_path.name} 업로드 최종 실패 (모든 {_MAX_RETRIES} 시도)", flush=True)
    return None


def upload_many(file_paths: list[Path], room: str = "") -> list[Optional[str]]:
    """여러 파일 순차 업로드. 입력과 같은 순서, 실패는 None."""
    urls: list[Optional[str]] = []
    total = len(file_paths)
    print(f"  [NENOVAWEB] 일괄 업로드 시작: {total}장 (room={room})", flush=True)
    for i, p in enumerate(file_paths, 1):
        print(f"  [NENOVAWEB] [{i}/{total}] {p.name}", flush=True)
        url = upload_to_nenovaweb(p, room=room)
        urls.append(url)
    ok = sum(1 for u in urls if u)
    print(f"  [NENOVAWEB] 일괄 업로드 완료: {ok}/{total} 성공", flush=True)
    return urls


def delete_from_nenovaweb(url: str) -> bool:
    """워크 전송 성공 후 nenovaweb 서버의 업로드 파일 삭제 (용량 관리).

    Args:
        url: upload_to_nenovaweb 가 반환한 공개 URL
             (예: https://nenovaweb.com/uploads/photos/2026/04/22/xxx.png)
    Returns: True = 삭제 성공 / False = 실패 (서버 측 엔드포인트 미구현 등)
    """
    if not url:
        return False
    session = _get_session()
    if session is None:
        return False
    try:
        # DELETE API 로 파일 경로 전달
        resp = session.post(
            DELETE_ENDPOINT,
            json={"url": url},
            timeout=_TIMEOUT,
        )
        if resp.status_code in (200, 204):
            print(f"  [NENOVAWEB] 삭제: {url.rsplit('/', 1)[-1]}", flush=True)
            # 캐시에서도 제거
            for k, v in list(_cache.items()):
                if v == url:
                    del _cache[k]
            return True
        elif resp.status_code == 404:
            print(f"  [NENOVAWEB] 삭제 엔드포인트 미구현: {DELETE_ENDPOINT}", flush=True)
        else:
            print(f"  [NENOVAWEB] 삭제 실패 HTTP {resp.status_code}: {resp.text[:100]}", flush=True)
    except Exception as e:
        print(f"  [NENOVAWEB] 삭제 예외: {e}", flush=True)
    return False


def delete_many(urls: list[str]) -> int:
    """여러 URL 순차 삭제. 성공 개수 반환."""
    n = 0
    for u in urls:
        if u and delete_from_nenovaweb(u):
            n += 1
    return n


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
