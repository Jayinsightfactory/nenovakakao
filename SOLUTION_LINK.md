# SOLUTION_LINK — nenovakakao (네노바 통합 솔루션의 카톡 수집·전달 계층)

> 마스터 문서: PC의 `C:\Users\USER\NENOVA_SOLUTION_AUDIT.md`
> Cursor 작업 시 이 파일 + `CLAUDE.md` + `HANDOFF-OTHER-PC.md`를 컨텍스트에 포함할 것.

## 이 repo의 역할
**카카오톡 ↔ talkhub 양방향 브릿지** (+ ERP 자동입력). talkhub가 카카오톡을 대체하는 전환기 동안, 카톡에 남은 유저와 talkhub 유저를 잇는다 (완성도 ~65% — 수집은 완성, 브릿지 송수신은 미구현).
- **카톡 → talkhub**: 카톡 15개 방의 톡/사진을 수집(완성) → talkhub `/bridge/kakao` API로 게시 (미구현 — 현재는 카카오워크 미러로만 감)
- **talkhub → 카톡**: talkhub 방의 새 메시지를 받아 카톡 해당 방에 전송 (미구현 — 카톡 송신 모듈 신규: 방 포커스 → 입력 → Enter)

## 연결 지점

| 상대 | 방향 | 인터페이스 | 상태 |
|---|---|---|---|
| 카카오톡 PC | IN | pyautogui (뱃지 스캔, Ctrl+S, Ctrl+K 서랍) | ✅ (사진 모듈은 실환경 미검증) |
| 카카오톡 PC | OUT | **신규: 메시지 송신 모듈** (방 검색/포커스 → 입력 → Enter) | ❌ 신규 개발 |
| **talkhub** | OUT | 수집한 카톡 메시지 → `POST /bridge/kakao` (방 매핑 필요) | ❌ 최우선 |
| **talkhub** | IN | talkhub 아웃바운드 웹훅/폴링 → 카톡 전송 큐 (에코 루프 방지 필수) | ❌ 신규 개발 |
| 카카오워크 | OUT | Bot API 미러 방 15개 + 이미지 업로드 — talkhub 전환 후 축소/폐기 예정 | ✅ (레거시) |
| 구글시트 | OUT | gsheet_sync.py — 이벤트로그/비즈니스이벤트/의사결정 3계층 | ✅ |
| nenova-erp-ui | OUT(계획) | `POST/PATCH /api/shipment/stock-status` — Phase 3 자동입력 | ❌ |

## 이 repo에서 작업할 때의 목적 (우선순위)
1. **P1: 카톡 → talkhub 게시** — 기존 수집 루프의 출력에 talkhub `/bridge/kakao` 송신 추가 (카톡방↔talkhub방 매핑 json, 발신자/시각 메타 포함). 카카오워크 미러와 병행하다가 안정되면 미러 축소.
2. **P1: talkhub → 카톡 송신 모듈** — talkhub 웹훅 수신(또는 폴링) → 카톡 방 포커스 → 메시지 입력 전송. **에코 루프 방지**: 브릿지가 카톡에서 가져온 메시지는 다시 카톡으로 내보내지 않기 (메시지 출처 태그).
3. **P2: Phase 3 (ERP 자동입력)** — 파싱 → custKey/prodKey 매칭(parse-paste 학습 475개 재사용) → ERP API. 자동 반영 전 talkhub 컨펌 휴먼게이트 권장.
4. **P2: 사진 모듈 실환경 검증** (CLAUDE.md 113행), 보안(ADMIN_USER_ID 환경변수화, 키 rotate).

## 작업 규칙
- 이 시스템은 화면 자동화(pyautogui) 의존 — 좌표/딜레이 변경 시 반드시 실제 카톡 창에서 검증.
- usage_stats.json(MD5 중복차단)과 last_content/ 델타 로직을 깨뜨리지 말 것 (중복 전송 = 미러 방 스팸).
- 키/토큰은 .env에만. 코드/문서에 이메일·ID 하드코딩 금지.
