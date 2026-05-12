# 다음 세션 이어갈 작업 (2026-05-12 종료 시점)

## 현재 상태 (마지막 commit `006854a`)

오늘 4개 commit 완료:
```
006854a monitor: FailSafeException 정상 종료 흐름 처리
c1bc31e 화면 정체 워치독 + 완료 다이얼로그 자식텍스트 매칭
3e515d5 monitor 흐름 캡쳐 미러 패턴 통합 — 사진 다운로드 우회
a979815 ADMIN user_id 정정 + 미러방 23개 확장 + 채팅창 캡쳐 미러 패턴
```

작동 검증 완료:
- ✅ 봇 1:1 DM 송신 정상 (ADMIN 11854018)
- ✅ 미러방 23개 (BOT_A `b80694c0`) 생성
- ✅ 캡쳐 미러 패턴 (사진 다운로드 우회)
- ✅ 미리보기+링크 분리 송신
- ✅ 화면 정체 워치독 (120초 stall → 자동 정지)
- ✅ 완료 다이얼로그 자식 텍스트 매칭
- ✅ FailSafe graceful exit
- ✅ monitor 한 사이클 실증 (네노바 수입/영업/현장 624자 + 캡쳐 ✅)

## 🚨 남은 작업: 미러방 ↔ 카톡 방 1:1 매칭 검증

**문제:** mapping 23개 키 vs 카톡 실제 방 이름이 정확히 1:1 매칭 안 됨.

- 알려진 불일치 1건: `조현욱, 박성빈, 김원영차장` (mapping) vs `조현욱, 박성빈, 김원영차장, 변진형과장` (카톡 실제)
- mapping 의 1-15 (기존 미러방, 안 읽음 탭에 없음) 검증 미완
- monitor 가 "감시 대상 아님" 으로 스킵해서 해당 방 메시지가 미러방에 영영 안 감

**시도한 도구:** `tools/verify_room_mapping.py`

**결과:** 모든 23개 검색에서 분리창 title 이 `Program Manager` 로 잡힘 (= Windows 데스크탑 자체).
즉:
- Ctrl+F 검색이 카톡 분리창을 띄우지 않음 (또는 분리창 enum 코드가 카톡 분리창 못 찾음)
- 카톡 검색이 좌측 패널 내부 매칭만 하고 분리창 열지 않을 가능성
- `_get_visible_separate_windows` 의 excluded 리스트에 "Program Manager" 추가 + 다른 enum 방식 시도 필요

## 다음 세션 우선 작업 (우선순위 순)

### 1. 1:1 매칭 검증 — 다른 방식 시도
- `tools/verify_room_mapping.py` 의 분리창 감지 로직 수정
- 또는 Ctrl+F → Enter 후 카톡 메인 창의 채팅 영역 헤더(상단 방 이름) OCR
- 또는 카톡 좌측 채팅 리스트 (전체 탭) 스크롤하면서 Vision OCR 으로 모든 방 이름 추출

### 2. 불일치 mapping 수정
검증 통과 후:
- `data/room_mapping.json` 키 정정
- 미러방 conversation_name 도 카톡 실제 이름과 동일하게 rename
  - Bot API 미러방 rename endpoint 확인 (없으면 GUI 자동화 `main.py rename-via-app`)

### 3. monitor 본 운영 (재가동)
1:1 매칭 보장 후:
- `python main.py monitor` 본 운영
- 워치독 + graceful exit + 캡쳐 미러 패턴 검증된 상태

## 즉시 실행 가능한 명령어

```bash
PYTHON="C:/Users/USER/AppData/Local/Programs/Python/Python312/python.exe"

# 1) 상태 확인
git log --oneline -10
cat NEXT_WORK.md

# 2) 1:1 매칭 보고서 확인 (이전 실패 결과)
cat data/mapping_verify_report.json

# 3) 매칭 검증 재시도 (코드 수정 후)
"$PYTHON" tools/verify_room_mapping.py

# 4) monitor 본 운영 (매칭 통과 후)
"$PYTHON" main.py monitor

# 5) 23개 미러방 일괄 ping (송신 가능 여부 빠른 확인)
"$PYTHON" -c "
import os, json, requests
from pathlib import Path
from dotenv import load_dotenv
load_dotenv('.env')
H = {'Authorization': f\"Bearer {os.getenv('KAKAOWORK_BOT_TOKEN')}\", 'Content-Type': 'application/json'}
mapping = json.loads(Path('data/room_mapping.json').read_text(encoding='utf-8'))
for n, cid in mapping.items():
    r = requests.post('https://api.kakaowork.com/v1/messages.send', headers=H,
        json={'conversation_id': cid, 'text': f'[ping] {n}'}, timeout=10).json()
    print(f\"{'OK' if r.get('success') else 'FAIL'} {n}\")
"
```

## 주의사항 (계승)

- 봇 `b80694c0` (네노바 주문 알림봇), ADMIN `11854018` 변경 금지
- 카톡창 위치 `(50, 50, 900, 900)` 변경 금지 (fail-safe 회피)
- 동일 에러 2회 발생 시 즉시 정지 + 수정 정책 유지
- 5분 룰: 결과 안 나오면 자동 중단 + 로그 분석
- PowerShell 도구 사용 자제 — Bash + Python subprocess (STARTF_USESHOWWINDOW) 만 사용
- 사진 다운로드 흐름 (서랍/묶음저장) 완전 우회 — 캡쳐 미러 패턴만 사용
