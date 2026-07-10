# 네노바 AI 에이전트 v2.1 — 마스터 지침서

> 이 파일은 Claude Code가 매 세션마다 자동으로 읽습니다. 세션이 바뀌어도 컨텍스트가 유지됩니다.

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
"$PYTHON" main.py            # 감시 모드 (뱃지 감지 → 저장 → 미러 전송)
"$PYTHON" main.py scan       # 방 리스트 전체 스캔
"$PYTHON" main.py select     # 감시 방 GUI 선택
"$PYTHON" main.py mirror     # 카카오워크 미러 방 생성
"$PYTHON" main.py moyi-worker # MOYI→카톡 발송 워커 (KakaoOutbox 폴링→발송→ack, .env: MOYI_BRIDGE_SECRET)
```

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
│   ├── room_mapping.json        # 카톡방 → 워크 미러방 매핑 (14개)
│   ├── kakaowork_users.json     # 워크 멤버 목록 (5명)
│   ├── collected_data.jsonl     # 수집된 대화 누적
│   ├── usage_stats.json         # MD5 중복 차단
│   └── issue_room.json          # 워크 이슈전용방 conversation_id
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
- [ ] 실제 환경 테스트 (관리자 실행 검증 필요)

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

### Phase 2: 분류 엔진 (대기)
- 관리자가 collected_data.jsonl 검토 → classification_rules.yaml 작성
- AI가 분류 기준 임의로 세우지 말 것 (관리자 지시 명시)

### Phase 2.5: 구글시트 연동 (대기)
- 신규 델타 내용을 구글시트에 분류 기록
- 톡방별 내용 분석 → 분류 기준 자체 학습/디벨롭

### Phase 3: nenovaweb.com ERP 자동 입력 (대기)
- ~~Playwright 웹 자동화~~ → **API 직접 연동** (관리자 API 구조 제공 완료)
- 주문등록: POST /api/shipment/stock-status
- 거래처/품목 조회: GET /api/master
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
- 카카오워크 Bot: "네노바 주문 알림봇" (App Key in .env)
- 카카오워크 관리자: 임재용 (user_id: 11826656, dlaww584@gmail.com)
- 워크 멤버 5명: 임재용(2), 강현우, 김선희, 네노바

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
