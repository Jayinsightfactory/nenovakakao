# 다음 세션 이어갈 작업 (2026-05-13 종료 시점)

## 현재 상태 (마지막 commit `62b8221`)

오늘 큰 진척 2 가지 + 추천 자료 1 건:

### 1) 안전 인프라 구축 — 자가 진단·자동 회복·정지 버튼
- `core/side_effect_detector.py` — 상태 캡쳐 + 진단 + ESC 회복 + 룰북 자동 진화
- `core/safe_actions.py` — `safe_click / safe_paste / safe_hotkey / safe_press`
- `core/stop_button.py` — 우상단 빨간 [🛑 즉시 정지] 창 (별도 스레드 tkinter)
- `data/automation_rules.yaml` — forbidden_coords / forbidden_sequences / known_dialogs
- `tools/test_side_effect_detector.py` — 마우스 미사용 정적 자가 테스트 9/9 통과
- `tools/preview_stop_button.py` — 정지 버튼 미리보기

오늘 사고 (검색바 paste → 친구추가 팝업) 와 어제 사고 (Ctrl+F 가정 실패) 모두 룰북에 등록.
신규 자동화 도구가 같은 좌표·시퀀스 시도하면 즉시 `ForbiddenAction` 으로 차단됨.

### 2) 미러방 ↔ 카톡 방 1:1 매칭 검증 — 완료
- `tools/verify_room_mapping_v2.py` 실행 결과 (`data/mapping_verify_report_v2.json`)
  - ✅ 정확 일치: **12/23**
  - 🟡 fuzzy 매칭: 2 (`네노바&선울`, `네노바현장팀` — 카톡에서 떠났을 가능성)
  - ❌ 미검증: 9 (수입방, 영업방팀..., 현장단체방, 견적방, 한국방역,
                  3.미우신라방, 발번호및 입고수량확인방, 영업지원팀, 백상)
- 카톡 채팅 리스트에 있는 방 43 개 중 mapping 에 없는 방 28 개 (대부분 1:1 채팅 + 거래처 단체방)

### 3) 정정 추천 자료
- `tools/recommend_mapping_fixes.py` 실행 결과 (`data/mapping_recommendations.json`)
- 11 정정 대상 (9 미발견 + 2 fuzzy) 에 대해 detected_rooms 중 fuzzy 점수 top-4 후보
- 점수 25 이하면 사실상 매칭 후보 없음 = 카톡에서 떠난 방

---

## 🚨 다음 세션 우선 작업

### 우선순위 1 — 매핑 정정 적용
관리자가 `data/mapping_recommendations.json` 보고 각 mapping key 처리 결정:

| mapping key | 추천 액션 (사용자 결정 필요) |
|---|---|
| `수입방` | mapping 에서 제거 OR `네노바 수입(불량 공유방)` 으로 매핑? |
| `영업방팀 발주 및 추가 재고확인` | mapping 제거? |
| `현장단체방` | mapping 제거? (`현장 추가취소방` 과는 다른 방) |
| `견적방` | mapping 제거? |
| `한국방역` | mapping 제거? |
| `3.미우신라방` | mapping 제거? |
| `발번호및 입고수량확인방` | mapping 제거? |
| `영업지원팀` | mapping 제거? (`네노바 영업` 과 다름) |
| `백상` | mapping 제거? |
| `네노바&선울` | mapping 제거 OR `네노바` 1:1 로 통합? |
| `네노바현장팀` | mapping 제거 OR `네노바 영업/현장` (별도 conv_id) 으로 흡수? |

→ **`tools/apply_mapping_fixes.py` 신규 작성 필요** (인터랙티브 선택 도구)
   - 각 key 마다 [1] 그대로 [2] 후보 적용 [3] mapping 에서 제거 선택
   - 기존 backup (`room_mapping.json.bak.YYYYMMDD_HHMMSS`) 자동 생성

### 우선순위 2 — 새 방 등록
카톡 리스트 28 개 mapping 미등록 방 중 미러링 필요한 거 있으면:
- `data/mapping_verify_report_v2.json` 의 `extra_rooms_in_chatlist_not_in_mapping` 확인
- `main.py mirror` 로 NV{NN} 미러방 일괄 생성 + mapping 추가

### 우선순위 3 — monitor 본 운영 재가동
매핑 정정 후:
- `python main.py monitor` 본 운영
- safe_actions 기반 자동화 도구가 차단 룰북 + 정지 버튼 + 부작용 진단을 모두 거침
- 첫 30 분은 관리자 옆에서 직접 감시 권장

---

## 즉시 실행 가능한 명령어

```bash
PYTHON="C:/Users/USER/AppData/Local/Programs/Python/Python312/python.exe"

# 1) 정지 버튼 미리보기 (마우스 미사용)
"$PYTHON" tools/preview_stop_button.py

# 2) 정적 자가 테스트 (마우스 미사용)
"$PYTHON" tools/test_side_effect_detector.py

# 3) 매핑 검증 재실행 (Phase A — 안전 영역 클릭만)
"$PYTHON" tools/verify_room_mapping_v2.py

# 4) 매핑 정정 추천 (보고서 재계산)
"$PYTHON" tools/recommend_mapping_fixes.py

# 5) 매핑 검증 결과 보기
cat data/mapping_verify_report_v2.json | python -m json.tool | head -80
cat data/mapping_recommendations.json | python -m json.tool

# 6) (다음 세션) 매핑 정정 도구 (작성 필요)
# "$PYTHON" tools/apply_mapping_fixes.py
```

---

## 안전장치 (자동 적용됨, 변경 금지)

1. **자동화 도구는 모두 `safe_actions` 사용**. raw `pyautogui.click()` 직접 호출 금지.
2. **카톡 좌표 신규 추가 전 화면 캡쳐로 검증 1 회 필수**. paste/Enter 가
   통합검색·친구추가 같은 부작용을 일으킬 수 있음 (2026-05-13 사고 참고).
3. **신규 도구 첫 실행 시 정지 버튼 띄움**. `start_stop_button()` 호출 + 종료 시 `stop_button_close()`.
4. **forbidden_coords / forbidden_sequences 는 자동 진화**. critical/high 부작용
   발생 시 좌표 ±40px 영역이 자동으로 `data/automation_rules.yaml` 에 추가됨.

---

## 어제 (5/12) 작업 (계승)

- ✅ 봇 1:1 DM 송신 (ADMIN 11854018)
- ✅ 미러방 23 개 생성
- ✅ 캡쳐 미러 패턴
- ✅ 화면 정체 워치독
- ✅ FailSafe graceful exit

## 주의사항 (계승)

- 봇 `b80694c0` (네노바 주문 알림봇), ADMIN `11854018` 변경 금지
- 카톡창 위치 `(50, 50, 900, 900)` 변경 금지
- 동일 에러 2회 발생 시 즉시 정지 + 수정 정책 유지
- 5분 룰: 결과 안 나오면 자동 중단 + 로그 분석
- PowerShell 도구 사용 자제 — Bash + Python subprocess 만 사용
- 사진 다운로드 흐름 (서랍/묶음저장) 완전 우회 — 캡쳐 미러 패턴만 사용
