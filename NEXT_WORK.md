# 다음 세션 이어갈 작업 (2026-05-19 종료 시점)

## 🎉 큰 돌파 — kakao-mcp win32 직접 자동화 채택

**커밋 `9f1bee8`** — 8 회 monitor-agentic 실패 후 GitHub 검색으로 발견:
[kronenz/kakaotalk-mcp](https://github.com/kronenz/kakaotalk-mcp) (MIT) 의 win32 child window
직접 접근 기법을 우리 프로젝트에 채택. 좌표·OCR·Computer Use 전부 폐기.

**핵심 변경**:
- `core/kakao_win32.py` 신규 — win32 child window 직접 자동화 (300+ 줄)
- `main.py monitor-win32` 신규 진입점 — 떠있는 분리창 polling → 봇 미러 송신
- 첫 시험 운영 (`cycle 1`): "네노바 + 청화원예" 1633자 추출 + 미러방 송신 OK
- `forbidden_sequences ctrl_f_for_room_lookup` 제거 (어제 가정 틀렸음)

**카톡 PC win32 child window 구조 (검증 완료)**:
```
카카오톡 (메인 hwnd, EVA_Window_Dblclk)
└─ EVA_Window (ChatRoomListView)
   └─ Edit (채팅탭 검색 입력 ← Ctrl+F 로 활성)

[방 이름] 분리창 (별도 hwnd, EVA_Window_Dblclk)
├─ RICHEDIT50W            ← 메시지 입력
└─ EVA_VH_ListControl_Dblclk  ← 메시지 리스트 (Ctrl+A + Ctrl+C 로 추출)
```

---

## 🚨 다음 세션 우선 작업

### 우선순위 1 — monitor-win32 본 운영
이미 검증된 흐름. 카톡 PC + 카카오워크 다 실행 + 안 읽은 방 분리창 띄움 → 시작:
```bash
PYTHON="C:/Users/USER/AppData/Local/Programs/Python/Python312/python.exe"
"$PYTHON" main.py monitor-win32              # 폴링 5초 (기본)
"$PYTHON" main.py monitor-win32 --interval 10   # 폴링 10초
```

**사용 흐름**:
1. 카톡 안 읽음 탭에서 처리할 방을 더블클릭으로 분리창 띄움
2. monitor-win32 가 그 분리창에서 자동으로 텍스트 추출 → 봇 미러방 송신
3. 새 안 읽은 방 → 추가 더블클릭

**중지**: 우상단 [🛑 즉시 정지] 버튼 또는 Ctrl+C

### 우선순위 2 — 자동 방 진입 추가 (반자동 → 완전 자동)
현재는 사용자가 분리창 미리 더블클릭 필요. 완전 자동화:
- monitor-win32 에 `--auto-open` 옵션 추가
- mapping 27 방 중 안 읽은 방 자동 검색/진입
- 안 읽은 방 식별: 카톡 안 읽음 탭 픽셀 스캔 (기존 badge_monitor) OR
  카톡 chat list child window enum (안 읽음 표시 검색)

### 우선순위 3 — 사진 처리 (download_recent_images 통합)
`core/kakao_win32.download_recent_images()` 가 카톡 cache 디렉토리에서 직접 복사:
- `LOCALAPPDATA/Kakao/KakaoTalk/users/{sha1hash}/chat_data/cli_http_v2/`
- 서랍 자동화 / 사진 다운로드 흐름 완전 폐기
- monitor 가 텍스트 처리 시 그 사이 cache 신규 파일 → 카카오워크 송신

### 우선순위 4 — 델타 추출 정밀화
현재는 `raw.startswith(prev)` 단순 prefix 비교. 카톡 채팅창 스크롤이나 메시지
삭제 시 prefix 매칭 깨짐. 더 robust 한 델타:
- 마지막 N 라인 hash 비교
- 또는 timestamp 기반 (메시지 형식: `[발신자] [오전/오후 HH:MM] 본문`)

---

## 즉시 실행 가능한 명령어

```bash
PYTHON="C:/Users/USER/AppData/Local/Programs/Python/Python312/python.exe"

# 1) win32 자가 테스트 (read-only)
"$PYTHON" tools/test_kakao_win32.py             # Step 1 (안전)
"$PYTHON" tools/test_kakao_win32.py --step2     # + child window 트리
"$PYTHON" tools/test_kakao_win32.py --step3     # + 메시지 추출 (Ctrl+A/C)
"$PYTHON" tools/test_kakao_win32.py --step4 주광 담당   # + 자동 방 진입

# 2) monitor-win32 본 운영
"$PYTHON" main.py monitor-win32

# 3) 매핑 / 미러방 / 검증
cat data/room_mapping.json | python -m json.tool | head -30   # 현재 27 keys
"$PYTHON" tools/scan_all_chat_rooms.py        # 카톡 전체 방 OCR 스캔 (참고용)
"$PYTHON" tools/recommend_mapping_fixes.py    # mapping 정정 추천
```

---

## 폐기 / 보존 정책

**폐기 (사용 X, 코드 보존)**:
- `monitor-agentic` (Claude Computer Use) — 8 회 시도 모두 실패, 비용 ~$8 소진
- 화면 OCR 기반 매핑 검증 (`verify_room_mapping_v2/v3`) — 참고용만
- 서랍 자동화 (`drawer_handler`, `drawer_layout_auto`) — 캐시 디렉토리 직접 접근으로 대체
- pyautogui 좌표 클릭 기반 monitor — kakao_win32 win32 API 로 대체

**현역**:
- `core/kakao_win32.py` ★ 핵심
- `main.py monitor-win32` ★ 본 운영
- `core/kakaowork_router.py` (봇 API) — 미러방 송신 계속 사용
- `core/safe_actions.py` / `stop_button.py` / `stall_detector.py` — 다른 자동화 도구 안전
- `data/room_mapping.json` (27 keys) — 검증 완료
- `data/automation_rules.yaml` — Ctrl+F 룰 제거, forbidden_coords 검색바만 유지

---

## 안전장치 (계승)

- 봇 `b80694c0` (네노바 주문 알림봇), ADMIN `11854018` 변경 금지
- 카톡창 위치 `(50, 50, 900, 900)` 변경 금지 (monitor-win32 시작 시 lock)
- 동일 에러 2회 발생 시 즉시 정지 + 수정 정책 유지
- 5분 룰: 결과 안 나오면 자동 중단 + 로그 분석
- 신규 자동화 도구 작성 시: 우선 win32 API (kakao_win32 패턴) 시도. 좌표/캡쳐는 fallback.
