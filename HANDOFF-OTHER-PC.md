# 다른 PC 환경 동기화 · 작업 이관 문서

**작성 기준일:** 2026-04-11  
**원본 작업 PC:** Windows 10 (`C:\Users\USER`)  
**프로젝트 루트:** `C:\Users\USER\nenova_agent`

이 문서는 **터미널(Claude Code 세션)에서 수행한 작업**, **로컬 설정**, **기획·아키텍처 요약**, **다른 PC에서 빠짐없이 맞추기 위한 체크리스트**를 한곳에 모은 것입니다. 비밀값(키·토큰·비밀번호)은 적지 않으며, **파일 이름·환경 변수 이름**만 기술합니다.

---

## 1. 문서 목적

- 동일 업무(카카오톡 수집 → 카카오워크 미러 → 구글시트 3계층·보고 → 알림)를 **다른 Windows PC**에서 재현할 때 필요한 항목을 누락 없이 나열한다.
- 터미널에서 겪은 **실수·이슈·해결 경로**를 기록해 반복을 막는다.

---

## 2. 이 PC에서 터미널(Claude Code) 기준으로 진행된 작업 요약

### 2.1 Claude Code / CLI

- **잘못된 입력:** `claude--dangerously-skip-permissions` (하이픈 연속) → PowerShell이 **명령을 인식하지 못함** (`CommandNotFoundException`).
- **올바른 형태:** `claude --dangerously-skip-permissions` — **`claude`와 `--` 사이 공백** 필수.
- 세션 하단에 **Anthropic CLI 업데이트** 안내가 있었음: `winget upgrade ...` (메시지에 `Anthropi…`로 잘림). 다른 PC에서도 CLI 버전을 맞출 때 참고.

### 2.2 네노바 에이전트(`nenova_agent`) 관련 작업 (세션 로그 기준)

| 구분 | 내용 |
|------|------|
| 사진 수집 | `drawer_handler.py` — 그리드 3열 순회, `[사진]` 개수만큼 다운로드, 단계별 캡처 확인 |
| 업무방 구성 | 기존 14개 → **15개** 업무방 재구성. 비활성 8개 제거, 신규 9개 반영(견적방, 한국방역, 네노바&선울, 3.미우신라방, 발번호및입고수량확인방, 네노바현장팀, 주님방, 영업지원팀, 백상 등). 카카오워크 미러방 **15개 매핑** |
| 파이프라인 | 7단계: 수입/입고 → 검수/불량 → 재고관리 → 발주/영업 → 출고/분배 → 현장 → 시스템. 주요 인력·품목·거래처는 `data/pipeline_config.json`에 정의 |
| 구글시트 3계층 | **L1** 이벤트로그, **L2** 비즈니스이벤트(파싱), **L3** 의사결정추적. `core/gsheet_sync.py`에서 시트 탭 생성·배치 기록. 실데이터 약 26건 기록 테스트 언급됨 |
| 보고·알림 | 시트에 **파이프라인보고서** 탭 생성(약 52행). Incoming Webhook이 `success: False`인 경우 **Bot API**로 관리자 DM 재전송 성공 기록 |
| 슬라이드 | `core/slide_report.py` 추가. **Google Slides API** 미활성/프로젝트 불일치/서비스 계정 **Drive 용량 0MB** 등으로 자동 생성 실패 → **시트 보고서 탭 + 카카오워크 링크 전송**으로 우회 완료 |

### 2.3 사용된 Python 인터프리터 (이 PC)

- 경로: `C:\Users\USER\AppData\Local\Programs\Python\Python312\python.exe` (**Python 3.12**)
- 세션에서 `ast.parse`로 `gsheet_sync.py` 문법 검사, `gsheet_sync` 모듈 직접 실행·임포트 테스트 등에 사용.

---

## 3. 프로젝트 목표·아키텍처 (기획 요약)

자세한 마스터 지침은 저장소 루트의 `CLAUDE.md`에 있음. 요지만 정리한다.

- **목표:** 카카오톡 다방(주문·변경·사진 등) → 로컬 수집 → 카카오워크 미러 방 실시간 전송 → (확장) nenovaweb 등 후속 자동화.
- **읽기:** `pyautogui` 등 화면 자동화, Ctrl+S 저장 경로(`KAKAO_SAVE_DIR`), 서랍 사진(`drawer_handler.py`).
- **쓰기:** 텍스트는 **카카오워크 Bot API**(`KAKAOWORK_BOT_TOKEN`), 단일 알림은 **Incoming Webhook**(`KAKAOWORK_WEBHOOK_URL`).
- **미러:** `data/room_mapping.json` — 카톡 방 ↔ 워크 `[미러] …` 방 (15개 기준으로 갱신됨).
- **구글시트:** 서비스 계정 JSON + `GOOGLE_SHEET_URL`. 시트 내 다중 탭(이벤트로그, 비즈니스이벤트, 의사결정추적, 메시지분류, 패턴라이브러리, 파이프라인보고서 등)은 코드가 없으면 생성.

---

## 4. 다른 PC 선행 조건 (체크리스트)

### 4.1 OS·앱

- [ ] **Windows** (현재 코드는 Win 전용: `pywin32`, 창 좌표, 카카오톡·카카오워크 **데스크톱 앱** 가정).
- [ ] **카카오톡 PC版** 설치, 감시 대상 방·해상도·창 위치가 기존과 크게 다르면 `CLAUDE.md` / `core/window_detector.py` 좌표 재조정 필요.
- [ ] **카카오워크 PC 앱** (이미지 업로드 자동화 경로 사용 시).

### 4.2 Python

- [ ] **Python 3.12** 권장 (이 PC와 동일).
- [ ] `PATH`에 등록하거나, 아래 **경로를 새 PC 사용자명에 맞게** 수정:
  - `run.bat`의 `PYTHON=...`
  - 문서·기본값에 하드코딩된 `C:\Users\USER\...` (`.env`의 `KAKAO_SAVE_DIR`, `learning.py` 기본 경로 등).

### 4.3 프로젝트 복사

- [ ] 폴더 전체 복사: `nenova_agent\` (또는 Git clone 후 동일 구조).
- [ ] **Git에 올리면 안 되는 것:** `.env`, `data/gsheet_credentials.json`, `data/collected_data.jsonl`(업무 데이터), 각종 `last_content` 등 — `.gitignore` 확인 후 필요 시 USB/암호화 채널로만 이전.

### 4.4 의존 패키지

`requirements.txt`에 명시된 항목 설치:

```bat
cd /d C:\Users\USER\nenova_agent
"C:\Users\USER\AppData\Local\Programs\Python\Python312\python.exe" -m pip install -r requirements.txt
```

또는:

```bat
run.bat install
```

**추가로 코드에서 쓰지만 `requirements.txt`에 없을 수 있는 패키지** (다른 PC에서 `ModuleNotFoundError` 나면 설치):

- `gspread` — `core/gsheet_sync.py`
- `google-api-python-client`, `google-auth` — `core/slide_report.py`, Google API 일반

권장 (한 번에):

```bat
python -m pip install gspread google-api-python-client google-auth
```

---

## 5. 환경 변수·자격증명 (이름만, 값은 각 PC에서 직접 설정)

### 5.1 `.env` (루트)

`.env.example`을 복사해 `.env`로 두고 채운다. 코드에서 추가로 읽는 항목은 예제에 없을 수 있으므로 아래를 **빠짐없이** 확인한다.

| 변수명 | 용도 |
|--------|------|
| `GEMINI_API_KEY` | Gemini API (`learning.py` 등) |
| `KAKAOWORK_WEBHOOK_URL` | Incoming Webhook 단일 방 알림 |
| `KAKAOWORK_BOT_TOKEN` | Bot API (`kakaowork_router`, `kakaowork_app`, `issue_reporter`) |
| `NENOVAWEB_URL`, `NENOVAWEB_USERNAME`, `NENOVAWEB_PASSWORD` | nenovaweb 로그인(해당 기능 사용 시) |
| `KAKAO_SAVE_DIR` | 카톡 Ctrl+S 저장 폴더 (**새 PC 경로로 변경**) |
| `GOOGLE_SHEET_URL` | 구글 스프레드시트 전체 URL (`gsheet_sync.py`) |
| `ANTHROPIC_API_KEY` | 방 스캔 OCR 등 `room_scanner.py` 경로 |
| `ADMIN_GOOGLE_EMAIL` | 슬라이드/공유 관련 기본값(`slide_report.py`에 폴백 있음) |

### 5.2 JSON 파일

| 경로 | 용도 |
|------|------|
| `data/gsheet_credentials.json` | Google 서비스 계정 키 (Sheets/Drive/Slides 권한은 GCP 콘솔·시트 공유에서 부여) |
| `data/room_mapping.json` | 카톡↔워크 미러 매핑 |
| `data/pipeline_config.json` | 파이프라인 단계·방·품목·거래처 |
| `data/selected_rooms.json` | 감시 대상 방 |
| `data/issue_room.json` | 이슈 전용 워크 대화 ID (사용 시) |

---

## 6. Google Cloud / 서비스 계정 — 터미널 세션에서 드러난 이슈

1. **API 활성화 프로젝트와 서비스 계정의 프로젝트가 다름**  
   - 로그에 숫자 프로젝트(`625445952881`)에서 Slides를 켠 것으로 보이나, 서비스 계정은 **`chagam` 프로젝트** (`chagam@chagam.iam.gserviceaccount.com`)에 묶여 있음.  
   - **반드시 서비스 계정이 속한 GCP 프로젝트**에서 Slides API(및 필요 시 Drive API)를 활성화할 것.

2. **Slides API**  
   - 미활성 시 `create_report` 류 호출에서 HTTP 오류 발생.

3. **서비스 계정 Drive 용량**  
   - 무료 한도로 **새 파일 생성 불가(0MB로 표시된 경우)** 가 있었음.  
   - **대안:** 관리자가 빈 **구글 슬라이드**를 만들고 `chagam@chagam.iam.gserviceaccount.com`에 **편집자** 공유 후, 해당 프레젠테이션 ID로만 내용 채우기.  
   - 또는 **시트의 파이프라인보고서 탭**만 사용하고 워크로 시트 링크 전송(세션에서 실제 성공).

4. **스프레드시트 공유**  
   - 서비스 계정 이메일에 스프레드시트 **편집 권한**이 있어야 `gspread` 읽기/쓰기·탭 생성이 됨.

---

## 7. 실행·검증 명령 (다른 PC)

`run.bat` 또는 동일한 `python.exe` 경로로:

| 목적 | 명령 |
|------|------|
| 감시(메인) | `run.bat` 또는 `python main.py` |
| 방 전체 스캔 | `run.bat scan` 또는 `python main.py scan` |
| 감시 방 GUI | `run.bat select` 또는 `python main.py select` |
| 미러 방 생성 | `python main.py mirror` (`CLAUDE.md` 참고) |

구글시트 모듈 단독 점검(예시):

```bat
"C:\...\Python312\python.exe" -c "import ast; ast.parse(open('core/gsheet_sync.py', encoding='utf-8').read()); print('Syntax OK')"
"C:\...\Python312\python.exe" core\gsheet_sync.py
```

(실제 서브커맨드는 `gsheet_sync.py` 하단 `if __name__` 블록 기준.)

---

## 8. 알려진 주의사항 (운영)

- **좌표·해상도:** 카톡/워크 UI는 PC마다 다름 — `CLAUDE.md`의 좌표는 **검증된 환경 기준**.
- **Webhook vs Bot API:** Webhook이 `success: False`여도 Bot API로 재시도하는 흐름이 세션에 있었음 — 토큰·권한을 새 PC에서도 동일하게 유지할 것.
- **인코딩:** Windows 콘솔에서 한글 로그가 깨져 보일 수 있음 — 실행 결과는 구글시트·워크 쪽으로 확인하는 것이 안전.
- **권한 모드:** Claude Code에서 `bypass permissions` 사용 시 파일/네트워크 접근 범위가 넓어짐 — 다른 PC 정책에 맞게 사용.

---

## 9. 동기화 시 꼭 챙길 파일 (요약)

**반드시 이전:** 소스 전체, `data/pipeline_config.json`, `data/room_mapping.json`, `data/selected_rooms.json`, 자격증명(JSON), `.env`(별도 보안 채널).

**선택/재생성 가능:** `usage_stats.json`, `last_content/*`, 일부 learning 캐시 — 업무 연속성 필요하면 함께 복사.

---

## 10. 문의·갱신

- 프로젝트 규칙·페이즈 상태는 **`CLAUDE.md`**가 최신 기준이다.
- 이 HANDOFF 문서는 **2026-04-11** 터미널 로그와 저장소 스냅샷을 기준으로 작성했다. 구조 변경 시 `requirements.txt`·`.env.example`과 함께 이 파일도 갱신하는 것을 권장한다.
