# SOLUTION_LINK — nenovakakao (네노바 통합 솔루션의 카톡 수집·전달 계층)

> 마스터 문서: PC의 `C:\Users\USER\NENOVA_SOLUTION_AUDIT.md`
> Cursor 작업 시 이 파일 + `CLAUDE.md` + `HANDOFF-OTHER-PC.md`를 컨텍스트에 포함할 것.

## 이 repo의 역할
카카오톡 15개 방의 주문/변경/불량/사진을 자동 수집 → 카카오워크 미러 + 구글시트 3계층 기록 (완성도 ~65%).
**솔루션 내 위치: 외부(고객 카톡)에서 들어오는 모든 신호의 입구.** Phase 3(ERP 자동입력)가 완성돼야 솔루션의 핵심 가치가 닫힌다.

## 연결 지점

| 상대 | 방향 | 인터페이스 | 상태 |
|---|---|---|---|
| 카카오톡 PC | IN | pyautogui (뱃지 스캔, Ctrl+S, Ctrl+K 서랍) | ✅ (사진 모듈은 실환경 미검증) |
| 카카오워크 | OUT | Bot API 미러 방 15개 + 이미지 업로드 | ✅ |
| 구글시트 | OUT | gsheet_sync.py — 이벤트로그/비즈니스이벤트/의사결정 3계층 | ✅ |
| **nenova-erp-ui** | OUT(계획) | `POST/PATCH /api/shipment/stock-status`, `GET /api/master` — **Phase 3 자동입력** | ❌ 0%, 최우선 |
| **talkhub** | OUT(계획) | 파싱 이벤트 → 컨펌요청 카드 (사람 승인 후 ERP 반영) | ❌ 기획 확정 |
| nenova-erp-ui admin/workflow | OUT | 구글시트 경유 (서비스계정 chagam) | ✅ |

## 이 repo에서 작업할 때의 목적 (우선순위)
1. **P1: Phase 3 구현** — 파싱(차수/품목/수량/거래처) → custKey/prodKey 매칭 → ERP API 호출. 매칭은 nenova-erp-ui의 parse-paste 학습 자산(475개 매핑) 재사용. **자동 반영 전 talkhub 컨펌(또는 시트 승인 컬럼)을 거치는 휴먼게이트 권장.**
2. **P1: 사진 다운로드/업로드 실환경 검증** (CLAUDE.md 113행).
3. **P2: 보안** — ADMIN_USER_ID 하드코딩 → 환경변수화, 서비스계정 키 rotate.

## 작업 규칙
- 이 시스템은 화면 자동화(pyautogui) 의존 — 좌표/딜레이 변경 시 반드시 실제 카톡 창에서 검증.
- usage_stats.json(MD5 중복차단)과 last_content/ 델타 로직을 깨뜨리지 말 것 (중복 전송 = 미러 방 스팸).
- 키/토큰은 .env에만. 코드/문서에 이메일·ID 하드코딩 금지.
