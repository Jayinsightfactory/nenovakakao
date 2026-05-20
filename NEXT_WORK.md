# 다음 세션 이어갈 작업 (2026-05-20 종료 시점)

## 🎉 양방향 실시간 미러링 완성

```
카톡 → 워크:  monitor (5/7 사진+링크 방식) → 미러방에 "메시지 + [📤 카톡 답장] 버튼"
워크 → 카톡:  미러방 버튼 클릭 → 모달 → reactive callback → kakao_win32 → 카톡 송신
```

end-to-end 검증 완료 (2026-05-20): 미러방 버튼 클릭 → 모달 입력 → 카톡 "주광 담당" 방 송신 OK.

## 최근 커밋
```
d49fab5 송신 안정화 (RemoteDisconnected retry) + 상시 가동 .bat
660295b monitor 미러 송신에 [📤 카톡 답장] 버튼 자동 첨부
53a9787 양방향 미러링 — 워크 → 카톡 역방향 챗봇 (reactive)
9f1bee8 win32 child window 직접 자동화 (kakao-mcp 채택)
```

## 핵심 컴포넌트

| 파일 | 역할 |
|---|---|
| `core/kakao_win32.py` | 카톡 PC win32 직접 자동화 (kakao-mcp 채택). search_and_open_room / read_chat_messages / send_message_to_room |
| `core/kakaowork_reactive.py` | Flask 서버. 워크 버튼 → 모달 → 카톡 송신 (reactive) |
| `core/kakaowork_router.py` | 봇 API 송신. send_reply_button (버튼 첨부) + _send_single (retry/backoff) |
| `main.py` (cmd_monitor) | 카톡 → 워크 monitor (5/7 사진+링크 방식, drawer 파일명 unique fix) |
| `run_nenova_realtime.bat` | 상시 가동 (monitor + Flask + tunnel) |

## 상시 운영 방법

```bat
REM 더블클릭 또는 작업 스케줄러(로그온 시)
run_nenova_realtime.bat
```
3 개 프로세스 가동:
1. reactive Flask (:5000) — 워크 → 카톡
2. cloudflare tunnel — public URL
3. monitor — 카톡 → 워크

**봇 대시보드 등록 URL** (secret = `data/reactive_secret.txt`):
```
Request URL : https://<tunnel>/<secret>/request_modal
Callback URL: https://<tunnel>/<secret>/callback
```
tunnel URL 은 `data/_cloudflared.log` 에서 확인.

## 🚨 다음 세션 우선 작업

### 1. 고정 URL (재등록 불필요)
현재 cloudflare quick tunnel = 재시작마다 URL 변경 → 봇 재등록 필요. 해결:
- **cloudflare named tunnel** (권장): cloudflare 계정(무료) + 도메인 등록 → 고정 subdomain
  - 도메인 없으면 cloudflare 에서 .com 구입 ($9/년) 또는 무료 도메인
  - `cloudflared tunnel login` → `tunnel create nenova` → DNS route → config.yml
- 또는 PC 상시 켜둠 + tunnel 안 끄면 URL 유지 (재부팅 시만 재등록)

### 2. monitor 안정화 추가 검증
- ✅ 사진 저장 파일명 unique (덮어쓰기 다이얼로그 멈춤 해결)
- ✅ 송신 RemoteDisconnected retry/backoff
- ⏳ 실가동 장시간 테스트 — 사진 다운로드 대량 시 안정성
- ⏳ 큰 delta (160018자 등) 첫 사이클 baseline 처리 (현재는 전체 송신 시도)

### 3. send_reply_button / send_image_block 도 retry 적용
- 현재 _send_single 만 retry. send_to_mirror_room / send_reply_button / send_image_block
  의 직접 requests.post 도 retry helper 로 통일하면 더 견고.

## 검증된 기술 메모

**카톡 PC win32 구조** (kakao-mcp):
```
EVA_Window_Dblclk (메인/분리창)
├─ EVA_Window (ChatRoomListView) → Edit (Ctrl+F 검색)
├─ RICHEDIT50W (메시지 입력)
└─ EVA_VH_ListControl_Dblclk (메시지 리스트 → Ctrl+A+Ctrl+C 추출)
```

**카카오워크 봇 한계**:
- 일반 메시지 수신 불가 (조회 API 없음, webhook 은 reactive 만)
- 양방향 = 봇이 button block 먼저 송신 → 사용자 클릭 → 모달 → callback
- 멘션/명령어로 봇 소환 불가

## 안전장치 (계승)
- 봇 `b80694c0`, ADMIN `11854018` 변경 금지
- 카톡창 (50, 50, 900, 900) — (0,0) 은 fail-safe 모서리
- 동일 에러 2회 → 정지, 5분 룰
- 화면 자동화 신규 시 win32 (kakao_win32) 우선, 좌표/캡쳐는 fallback
