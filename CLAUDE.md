# 네노바 AI 에이전트 v2.1 — 마스터 지침서

> 이 파일은 Claude Code가 매 세션마다 자동으로 읽습니다. 세션이 바뀌어도 컨텍스트가 유지됩니다.

## 🚨 절대 원칙 (모든 작업 시작 전 반드시 인지)

1. **방별 차별화 분석 — 유형 그룹화 절대 금지**
   - 14개+ 카톡 방마다 **고유 설정** 으로 분석 (`data/room_analysis_config.json`)
   - "INTERNAL_BACKBONE / SUPPLIER_CHANNEL / PARTNER_CHANNEL" 같은 유형 묶음 사용 금지
   - 분류 규칙 / 키워드 사전 / 발신자 매핑 / 거래처 매칭 모두 **방마다 다르게**
   - 메모리 [room_specific_analysis_design.md] + [project_scope_correction.md] 참조

2. **현재 단계: 분석 신뢰도 향상 — WEB(nenovaweb/ERP) 쓰기 절대 금지**
   - 자동 입력 (`add_order` / `distribute_outgoing` / `set_start_stock` / `create_shipment_detail`) 금지
   - 읽기 (마스터 조회, 매칭 검증) 만 허용
   - 분석/분류 정확도가 충분히 올라간 후 사용자 명시 컨펌 받고 쓰기 시작

3. **봇 API 송신 — 1:1 DM 정상 복구 (2026-05-11)**
   - **이전 진단 ("2026-05-07~ 봇 push 차단") 은 오진**. 실제 원인: `ADMIN_USER_ID = 11826656`
     이 워크스페이스에서 빠진 stale 계정이라 `messages.send` 가 success 반환해도
     "no such user" 에게 갔던 것. 진짜 admin user_id 는 **11854018** (Nenovabusiness1@gmail.com).
   - 정정 후 BOT_A(`b80694c0`, 네노바 주문 알림봇), BOT_B(`1a70022c`, 카톡복사봇) 둘 다 1:1 DM
     송신 100% 정상 (사용자 클라이언트에서 직접 확인 완료).
   - **그룹/미러방 송신은 별도 검증 필요** — BOT_A 가 100+ 방에 들어가 있으나, 미러방
     실시청 정상 수신 여부는 아직 미검증. 미러 트랙 재개 전 그룹 1개에 테스트 송신 → 클라이언트 확인 필수.
   - find_by_email 은 **대소문자 구분** — `Nenovabusiness1@gmail.com` (대문자 N) 정확히 입력 필요.
   - 신규 봇 추가/멤버 변경 시 항상 `users.list` 로 실워크스페이스 멤버 재확인 (`kakaowork_users.json`
     stale 방지). 2026-05-11 기준 실멤버 3명: 11798470(임재용), 11798493(강현우), 11854018(네노바).

4. **`conversations.list` 는 100개에서 잘린다 — alive/dead 판정에 절대 쓰지 말 것 (2026-06-04 규명)**
   - 봇이 100+ 방에 들어가 있으면 `conversations.list` 가 cursor 페이징을 해도 **100개만 반환**(API 캡).
     목록에 없는 conv 도 **`messages.send` 는 정상 성공** → 살아있음. 가짜 id 만 `conversation_not_found`.
   - 따라서 "목록에 없으면 죽음" 은 **거짓**. 미러 생성/재생성 같은 파괴적 결정에 list 멤버십을 쓰면
     멀쩡한 방을 중복 재생성 → **과거 유령방 85개의 진짜 근본원인**. (2026-06-04 동일 실수로 9개 오생성:
     `982521*` — 비어있고 매핑 미포함, `[중복삭제]` 표시 후 cleanup-mirrors --ui 로 정리 대상.)
   - **수정 완료**: `ensure_mirror_for_rooms` 는 이제 매핑된 방을 무조건 신뢰(재생성 금지), 신규만 생성.
     `verify_mirror_room*` 는 false-negative 경고 달림. 진짜 죽은 conv 재매핑은 `messages.send` 프로브로만.
   - **방 정체는 이름(카톡 OCR명·워크명)이 아니라 미러링된 실제 내용으로 판단**(`collected_data.jsonl`).
     2명짜리 이름-stub 미러(예: '수입방' 973772195788587)는 고아 — 실내용은 다른 방('네노바 수입/영업/현장')에.

## 🎯 프로젝트 목표

카카오톡의 여러 방에서 들어오는 주문/변경 메시지 + 사진/파일을 자동 수집 → 카카오워크 미러 방에 실시간 전송 (텍스트 + 이미지) → 최종적으로 nenovaweb.com에 자동 입력.

## 🏗 아키텍처 (2026-04-10 확정)

### 읽기 (카카오톡 → 로컬)
- 화면 자동화: `pyautogui` (공식 API 없음)
- 텍스트: Ctrl+S → `C:\Users\USER\Downloads\카톡대화데이터\`에 txt 저장
- 사진/파일: Ctrl+K → 서랍 → 사진 탭 → 다운로드 (미구현 — 다음 세션)

### 쓰기 (로컬 → 카카오워크)
- **텍스트**: Bot API `messages.send` (작동 중)
- **이미지/파일**: Bot API에 업로드 엔드포인트 없음 → **카카오워크 앱 화면 자동화** (미구현 — 다음 세션)
- **단일 알림 폴백**: Incoming Webhook → 관리자전용톡방

### 미러링 구조
```
카카오톡 "수입방"        →  카카오워크 "[미러] 수입방"
카카오톡 "영업방팀..."    →  카카오워크 "[미러] 영업방팀..."
... (15개 방 매핑 완료 → data/room_mapping.json)
```

### 실행 명령
```bash
PYTHON="C:/Users/USER/AppData/Local/Programs/Python/Python312/python.exe"
"$PYTHON" main.py                       # 감시 모드 (뱃지 감지 → 저장 → 미러 전송)
"$PYTHON" main.py scan                  # 방 리스트 전체 스캔
"$PYTHON" main.py select                # 감시 방 GUI 선택
"$PYTHON" main.py mirror                # 카카오워크 미러 방 생성
"$PYTHON" main.py cleanup-mirrors --dry-run   # 중복 미러 방 탐지 (읽기 전용)
"$PYTHON" main.py cleanup-mirrors             # "[중복삭제] X" 리네이밍 + mapping 정리
"$PYTHON" main.py cleanup-mirrors --ui        # + 카카오워크 앱에서 나가기 UI 자동화

# ── 미러방 이름/멤버 정리 (2026-05-06 추가) ──
"$PYTHON" main.py strip-mirror --dry-run      # "[미러] X" → "X" 일괄 리네이밍 계획
"$PYTHON" main.py strip-mirror                # 실행 (멱등, [중복삭제] 보호)
"$PYTHON" main.py invite-member <user_id> [...] --mirrors-only [--dry-run]
                                              # 봇 그룹채널에 사람 멤버 일괄 초대
                                              # --mirrors-only: id prefix 968666* 만 (37개 미러방)
"$PYTHON" main.py sync-mapping --dry-run      # room_mapping.json 갱신 계획 (이름→conv_id 재매칭)
"$PYTHON" main.py sync-mapping                # 실행 (.bak 자동 백업)
"$PYTHON" main.py rename-nv [--dry-run]       # 미러방을 'NV{NN}:원본이름' 으로 (현재 미사용)

# ── 100% 자기학습 루프 (반복 실행 시 스텝 자동 안정화) ──
"$PYTHON" main.py learn                       # 파이프라인 1회를 전체 화면 녹화 + 이벤트
"$PYTHON" main.py auto-anchor --commit        # 누적 후보를 클러스터링해 앵커 자동 확정
"$PYTHON" main.py metrics                     # 스텝별 성공률/재시도 메트릭 (CLI)
"$PYTHON" main.py metrics --gui               # tkinter 대시보드 (3초 새로고침)
"$PYTHON" main.py unlock <step>               # 특정 스텝 락 해제 → 재학습
"$PYTHON" main.py unlock --all                # 전 스텝 락 해제
"$PYTHON" main.py calibrate                   # learn → auto-anchor → metrics 1사이클
```

### 학습 루프 동작 방식

1. `learn` 실행 시 `LearningRecorder`가 10fps 전체 화면 녹화 + 각 자동화 스텝의
   `mark(step, "before|after|fail")` 이벤트 수집
2. 종료 시 `after` 프레임을 `data/anchor_candidates/<session>/<step>__*.png`로 추출
3. `auto-anchor`가 여러 세션의 후보를 pHash로 클러스터링 → 3회 이상 동일 프레임이
   나타난 스텝을 `data/anchors/<step>.png`로 자동 승인
4. 다음 실행부터는 `scoped_step`·`run_step`이 확정 앵커로 검증 → 실패 시 재시도
5. 20회 연속 성공 스텝은 자동 락 (검증 생략, 성능 우선). 실패 시 락 해제 + 재학습
6. 실패 프레임은 `data/anchor_candidates/failed/<step>/<ts>.png`로 축적되어
   다음 iteration에서 새 후보로 승격 가능

## 📏 하네스 운영 원칙 (반드시 준수)

1. **선(先)기획 후(後)행**: 모든 개발/수정 전 방향 제안 → 관리자 컨펌 → 실행.
2. **하네스 역제안**: 더 나은 대안이 있으면 반드시 "Option B" 역제안.
3. **자가 수정 루프**: 테스트는 에이전트가 직접 실행, 로그 분석 후 수정 반복.
4. **모든 에러 투명 보고**: 예외를 숨기지 않고 관리자 톡방에 보고.
5. **반복 작업 금지**: `data/usage_stats.json`에 MD5 해시 영구 저장.
6. **안전 우선**: 마우스/키보드 제어 전 주의, 모서리 Fail-safe.

## 🔐 보안 규칙

- ❌ 평문 비밀번호/API키를 채팅창에 요청하거나 저장하지 말 것
- ❌ 메모리, 파일, Git에 시크릿 저장 금지
- ✅ 모든 시크릿은 `.env` 파일에만, 사용자가 직접 입력

## 📁 폴더 구조

```
nenova_agent/
├── CLAUDE.md                    # 이 파일
├── .env                         # 시크릿 (사용자 직접 작성)
├── .gitignore
├── requirements.txt
├── run.bat                      # Windows 런처
├── main.py                      # 진입점 (scan/select/mirror/monitor)
├── capture_all_pages.py         # 방 리스트 스크롤 캡처 유틸
├── core/
│   ├── window_detector.py       # 카톡 창 감지 + 채팅탭 전환 + 스크롤
│   ├── window_manager.py        # 창 생명주기 관리 (정리/활성화/복귀)
│   ├── room_scanner.py          # Claude/Gemini Vision OCR
│   ├── room_selector_gui.py     # 방 선택 체크박스 GUI
│   ├── badge_monitor.py         # 빨간 뱃지 픽셀 스캔 감지
│   ├── message_extractor.py     # Ctrl+S 자동화 + MD5 중복차단
│   ├── drawer_handler.py        # 카톡 서랍 사진 다운로드 자동화
│   ├── kakaowork_notifier.py    # Webhook 단일 방 알림
│   ├── kakaowork_router.py      # Bot API 다중 방 라우팅 + 미러 방 생성
│   ├── kakaowork_app.py         # 워크 앱 화면 자동화 (이미지 업로드)
│   ├── status_overlay.py        # 우하단 상태 표시등 (빨간불 깜빡임)
│   └── issue_reporter.py        # 이슈 팝업 + 워크 이슈방 전송 + 일시정지
├── data/
│   ├── rooms_detected.json      # 스캔된 방 리스트 (14개)
│   ├── selected_rooms.json      # 감시 대상 방
│   ├── room_mapping.json        # 카톡방 → 워크 미러방 매핑 (15개, 968666* 시리즈)
│   ├── room_mapping.json.bak    # sync-mapping 자동 백업
│   ├── kakaowork_users.json     # 워크 멤버 목록 (5명)
│   ├── collected_data.jsonl     # 수집된 대화 누적
│   ├── usage_stats.json         # MD5 중복 차단
│   ├── issue_room.json          # 워크 이슈전용방 conversation_id
│   ├── mirror_cleanup_log.json  # 중복 청소 이력
│   ├── strip_mirror_log.json    # [미러] prefix 제거 이력
│   ├── invite_member_log.json   # 멤버 초대 이력
│   └── sync_mapping_log.json    # mapping 동기화 이력
├── captures/
│   ├── pages/                   # 방 리스트 스크롤 캡처
│   ├── drawer.png               # 카톡 서랍 캡처 (사진 다운로드용 좌표 참조)
│   └── drawer_photos.png        # 카톡 서랍 사진 탭 캡처
└── logs/
```

## 📋 개발 단계

### Phase 1: 텍스트 수집 + 미러링 ✅ 완료
- [x] 1.0 프로젝트 구조 + CLAUDE.md
- [x] 1.1 카톡 창 감지 (0,0 500x900) + 채팅탭 자동 전환 (27,115)
- [x] 1.2 방 리스트 OCR (Claude Code 직접 분석 — Gemini 할당량 부족)
- [x] 1.3 방 선택 GUI (tkinter 체크박스 + 수정/추가/삭제)
- [x] 1.4 빨간 뱃지 픽셀 스캔 감지 (R>180, G<100, B<100)
- [x] 1.5 Ctrl+S 저장 자동화 (더블클릭→Ctrl+S→Enter→Enter→읽기→ESC)
- [x] 1.6 collected_data.jsonl 누적 + MD5 중복차단
- [x] 1.7 카카오워크 전송 (Webhook + Bot API 다중 방 라우팅)
- [x] 미러 방 15개 자동 생성 (Bot API conversations.open) — 2026-04-11 업데이트

### Phase 1.5: 이미지/파일 수집 + 전송 ⏳ 진행 중
- [x] 카톡 Ctrl+K 서랍 자동화 (사진 다운로드) → drawer_handler.py
- [x] 텍스트에서 "[사진]" 타임라인 감지 → 사진 있는 경우만 서랍 열기 → main.py 통합
- [x] 카카오워크 앱 화면 자동화 (이미지 업로드) ✅ 검증 완료
- [x] 카카오워크 NV 미러 방에 이미지 첨부 전송 ✅ 검증 완료
- [x] 창 생명주기 관리 → window_manager.py (cleanup/focus/return)
- [x] 상태 표시등 → status_overlay.py (우하단 빨간불 깜빡임)
- [x] 이슈 보고 시스템 → issue_reporter.py (팝업 + 워크 이슈방 + 일시정지)
- [x] 여러 사진 순회 다운로드 (그리드 3열 순회 + 캡처 확인)
- [x] **UIA 기반 드로어 오프너 (2026-04-22)** → `core/drawer_uia.py`
      pywinauto 로 ≡/서랍/사진탭을 접근성 이름으로 `.invoke()` (픽셀 무관).
      기존 `drawer_handler.open_drawer` 픽셀 경로는 폴백으로 유지.
      블로킹 팝업 ("100% 완료되었습니다" 등) 사전 제거 내장.
- [x] **Vision-first + hover-first 하이브리드 (2026-04-22)** → `core/drawer_handler.py`
      E2E 검증 완료: 드로어 열기 + 사진 다운로드 100% 작동.
      - ≡ 버튼: Vision OCR 우선 (conf 0.95+) → 하드코딩 폴백
      - "채팅방 서랍": Vision OCR 좌표 → hover 2초 (서브메뉴 유도) → click 2.5초 폴백
      - "사진/동영상": Vision OCR → click
      - 서랍 사진 다운로드: layout 체크박스 (다량) / 더블클릭 묶음저장 (소량) 자동 폴백
      - Vision 이모지 출력 UnicodeEncodeError 방지
- [x] 실제 환경 테스트 완료 — PHOTO_*.png 파일 저장 확인

#### 사진 자동화 확정 파이프라인 (2026-04-22, E2E 검증)
```
main.py  (monitor loop)
  └─ _process_room_result
      ├─ _has_existing_separate() ← 기존 분리창 있으면 재클릭 스킵 (토글 방지)
      ├─ click_room (없을 때만)
      ├─ 분리창 hwnd 감지 (visible+minimized 모두, iconic 복원)
      └─ extract_photos_from_room (drawer_layout_auto.py)

extract_photos_from_chat_via_layout
  ├─ [1] 서랍 열기
  │    ├─ open_drawer_uia (UIA 경로 — 카톡 DirectUI 라 실패 확실)
  │    └─ drawer_handler.open_drawer (픽셀+Vision)
  │         ├─ fix_chat_window_position → (910, 50, 600, 800) + TOPMOST
  │         ├─ dismiss_blocking_dialogs (선제 완료팝업 정리)
  │         ├─ Vision-first: ≡ 버튼 좌표 OCR (conf 0.95+) → 하드코딩 폴백
  │         ├─ ≡ 클릭 (pyautogui) → 팝업(EVA_Menu, 225x324) 감지
  │         └─ _try_uia_inner_nav (팝업 내 네비게이션)
  │               ├─ "채팅방 서랍" UIA invoke (MenuItems 없음 → Vision 폴백)
  │               ├─ Vision → hover 2초 → 서브메뉴 자동 출현
  │               │  (서브메뉴 미출현 시 click 2.5초 폴백)
  │               └─ "사진/동영상" Vision 클릭 → 서랍 창 (panel)
  │
  ├─ [2] 사진 다운로드 (이중 전략)
  │    ├─ download_n_from_drawer("photo", N) ← layout 3x5 그리드 체크박스 방식
  │    │  (다량 사진 방에 효과적)
  │    └─ 0건이면 → drawer_handler.download_photos_from_drawer ← 더블클릭 묶음저장
  │       (소량 사진 / 단일 사진 방에 효과적)
  │         ├─ 그리드 셀 더블클릭 → 뷰어 열림
  │         ├─ ↓ 버튼 → "묶음사진 전체저장"
  │         ├─ "다른 이름으로 저장" → Enter
  │         └─ 덮어쓰기 확인 팝업 → Y
  │
  └─ [3] 파일 리네임: PHOTO_<방이름>__<timestamp>_<idx>.<ext>

환경 스위치:
  NENOVA_DRAWER_FORCE_PIXEL=1  # UIA 완전 스킵 (비상용)
  NENOVA_DRAWER_DEBUG=1        # captures/uia_*.txt 에 트리 덤프

진단 도구:
  "$PYTHON" tools/probe_kakao_uia.py         # UIA 트리 덤프 (5초 카운트)
  "$PYTHON" tools/diagnose_hamburger_click.py # ≡ 클릭 3방식 비교
  "$PYTHON" tools/test_drawer_e2e.py          # 서랍 E2E 검증 (필요 시)
```

#### 실전 테스트 절차 (관리자)

```bash
PYTHON="C:/Users/USER/AppData/Local/Programs/Python/Python312/python.exe"

# 1. 오프라인 회귀 점검 (앱 없이도 OK)
"$PYTHON" diagnostic.py

# 2. 중복 미러 방 현황 확인 (읽기 전용)
"$PYTHON" main.py cleanup-mirrors --dry-run

# 3. 중복 리네이밍 실행 (API만, 방은 아직 존재)
"$PYTHON" main.py cleanup-mirrors
#    → data/room_mapping.json 정리됨
#    → "[미러] X" 중복이 "[중복삭제] X"로 이름 변경됨
#    → 관리자가 카카오워크 앱에서 수동 확인 가능

# 4. (선택) UI 자동 나가기 — 카카오워크 앱 실행 필요
"$PYTHON" main.py cleanup-mirrors --ui

# 5. 라이브 진단 (카톡/워크 실행 중)
"$PYTHON" diagnostic.py --live

# 6. 실제 감시 모드
"$PYTHON" main.py
```

**검증된 카카오워크 이미지 업로드 방식 (2026-04-10):**
```
1. Bot API messages.send → 대상 NV방이 목록 맨 위로 올라옴
2. 카카오워크 앱 활성화 → 왼쪽 패널 첫 번째 방 클릭 (80, 60)
3. 채팅 입력란 클릭 (width//3, height-50) → 포커스 확보
4. Ctrl+T → Windows 파일 다이얼로그 열림
5. 파일 경로 클립보드 → Ctrl+V → Enter → 파일 선택
6. 전송 확인 팝업 → Enter → 전송 완료
```
- Ctrl+F 검색 방식은 실패 (검색 패널이 포커스 점유)
- Bot API → 첫 번째 방 클릭 방식이 안정적

**검증된 카톡 Ctrl+K 서랍 사진 다운로드 방식 (2026-04-10):**
```
사전조건: 채팅방 창이 열려있어야 함 (수입방 등)
1. win32gui.SetForegroundWindow(채팅방_hwnd) → 채팅방 활성화
2. Ctrl+K → 서랍 열림 (별도 창: "채팅방 서랍", 약 840x600)
3. 서랍 창 찾기: pygetwindow로 width>500, height>300, top<50, left>50
4. win32gui.SetForegroundWindow(서랍_hwnd) → 서랍 활성화
5. 사진/동영상 탭 클릭: (drawer.left+120, drawer.top+190)
6. 첫번째 사진 클릭: (drawer.left+150, drawer.top+280)
7. 다운로드 버튼 클릭: (drawer.left+width-25, drawer.top+height-25)
8. 2초 대기 → 파일 저장됨 (C:\Users\USER\Documents\카카오톡 받은 파일\)
9. ESC → 팝업 닫기
```
- 한 번 클릭으로 관련 사진 4장이 묶음 다운로드됨 (검증 완료)
- 서랍 위치는 매번 pygetwindow로 동적 감지 (고정값 아님)
- 반드시 win32gui.SetForegroundWindow로 활성화 후 클릭해야 함

**남은 작업:**
- 실제 환경 테스트 (관리자 실행 후 좌표/타이밍 미세조정)
- 여러 사진 순회 다운로드 (현재는 첫번째만 — Ctrl+A 전체선택 또는 순차 클릭 필요)

### Phase 2: 분류 엔진 ⏳ 초안 완료 — 관리자 규칙 검토 필요
- [x] 분류 규칙 YAML 외부화 → `data/classification_rules.yaml` (재기동 없이 mtime 감지 재로딩)
- [x] `core/gsheet_sync.parse_message` — 12개 event_type 기반 우선순위 분류
- [x] 차수·수량·거래처·품목 추출 (SEQ_PATTERN, QTY_PATTERN, pipeline_config)
- [x] `classify_and_log_delta` + `process_admin_feedback` (구글시트 학습 루프)
- [ ] 관리자가 `data/classification_rules.yaml` 키워드 보강 (실전 샘플 기반)
- [ ] 방별 고유 규칙 오버라이드 (memory의 room_specific_analysis_design.md)

### Phase 2.5: 구글시트 연동 ⏳ 진행 중
- [x] 이벤트로그/비즈니스이벤트/의사결정추적 3계층 스키마
- [x] 파이프라인단계 탭 구성 (`_ensure_worksheets`)
- [x] 관리자 수정 → 학습로그 피드백 루프
- [ ] 톡방별 내용 분석 → 분류 기준 자체 학습/디벨롭

### Phase 3: nenovaweb.com ERP 자동 입력 ⏳ 스켈레톤 완료
- [x] `core/erp_bridge.py` — JWT auth + 마스터 조회 + 이슈/백업 API
- [x] 주문등록 API wrapper: `add_order / distribute_outgoing / set_start_stock / create_shipment_detail`
- [x] 날짜 → 차수 변환 stub: `date_to_week` (PeriodDay 엔드포인트)
- [x] `core/order_pipeline.py` — delta → 메시지 → 파싱 → 마스터 매칭 → 스테이징
- [x] 마스터 캐시 (`data/erp_master_cache.json`, 1h TTL)
- [x] Pending 대기열 (`data/pending_orders.json`) — 관리자 검토 후 `commit_pending_orders()`
- [ ] 실제 ERP 연동 테스트 (관리자 계정으로 `add_order` dry-commit)
- [ ] 거래처/품목 매칭 정확도 튜닝 (현재 완전일치→공백제거→부분포함)
- [ ] 자동 커밋 정책 수립 (방별 / event_type별 화이트리스트)
- PeriodDay 테이블로 날짜↔차수 자동 변환

**nenovaweb.com API 구조 (2026-04-10 관리자 제공):**

nenova ERP v4.9 — API 직접 연동 가능 (Playwright 불필요)

핵심 API 엔드포인트:
```
# 쓰기 (카톡→ERP 자동화 대상)
POST   /api/shipment/stock-status   {action:'addOrder', custKey, prodKey, week, qty, unit}  ← 주문등록
PATCH  /api/shipment/stock-status   {custKey, prodKey, week, outQty}                        ← 출고분배
POST   /api/shipment/distribute     {week, year, custKey, prodKey, outQty, cost}             ← 출고상세
POST   /api/warehouse               ← 입고 등록
POST   /api/estimate                ← 견적서
PUT    /api/shipment/stock-status   {prodKey, week, stock}                                   ← 시작재고

# 읽기 (조회/검증용)
GET    /api/master?entity=customers  ← 거래처 677건
GET    /api/master?entity=products   ← 품목 3,082건
GET    /api/master/pricing-matrix    ← 업체별 단가 631,428건
GET    /api/orders                   ← 주문 조회
GET    /api/orders/history           ← 주문 변경내역 124,066건
GET    /api/stock                    ← 재고 현황
GET    /api/stats/*                  ← 매출 통계
```

마스터 코드:
```
Country(14), Flower(111), Farm(96), ProductSort(53), CodeInfo(47)
PeriodDay(14,610) ← 날짜↔차수 매핑 (차수 계산 핵심!)
```

**카톡 메시지 → ERP API 매핑:**
```
카톡: "15-1차 카네이션변경사항 주광 연그린 1추가"
  1. 파싱 → 차수=15-1, 품목=카네이션, 색상=연그린, 수량=+1
  2. GET /api/master?entity=products → prodKey 조회 (카네이션/연그린)
  3. GET /api/master?entity=customers → custKey 조회
  4. POST /api/shipment/stock-status
       {action:'addOrder', custKey, prodKey, week:'15-1', qty:1, unit:'송이'}
```

## 🔧 환경 정보

- Python: `C:\Users\USER\AppData\Local\Programs\Python\Python312\python.exe`
- 카톡 창: (0,0) 500x900, 채팅탭 아이콘 (27,115)
- 카톡 Ctrl+S 저장: `C:\Users\USER\Downloads\카톡대화데이터`
- 카카오워크 Bot: "네노바 주문 알림봇" (App Key in .env, prefix `b80694c0`)
  - 부 봇 "카톡복사봇" (`1a70022c`) 존재 — .env 는 알림봇으로 통일 (2026-05-11)
- 카카오워크 관리자: **네노바 (user_id: 11854018, Nenovabusiness1@gmail.com)**
  - find_by_email 은 **대소문자 구분** — 정확히 `Nenovabusiness1@gmail.com`
- 워크 멤버 (실제 3명, 2026-05-11 `users.list` 기준):
  - 11798470 임재용 (dlaww@naver.com)
  - 11798493 강현우 (floshw@naver.com)
  - 11854018 네노바 (Nenovabusiness1@gmail.com) ← ADMIN
- 이전 stale entry (`data/kakaowork_users.json` 의 11826656/11798575/11821285) 정리 완료

## .env 항목

```
ANTHROPIC_API_KEY=...           # Claude API (크레딧 필요)
GEMINI_API_KEY=...              # Gemini (무료 할당량 부족 — gemini-2.5-flash 사용)
KAKAOWORK_WEBHOOK_URL=...       # Incoming Webhook (관리자전용톡방)
KAKAOWORK_BOT_TOKEN=...         # Bot App Key (다중 방 라우팅)
NENOVAWEB_URL=https://nenovaweb.com
NENOVAWEB_USERNAME=...
NENOVAWEB_PASSWORD=...          # ⚠️ 변경 강력 권장 (admin/1234 위험)
KAKAO_SAVE_DIR=C:/Users/USER/Downloads/카톡대화데이터
```
