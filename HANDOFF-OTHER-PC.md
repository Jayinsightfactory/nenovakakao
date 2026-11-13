# 다른 PC / Claude Code 작업 인수인계

기준: 2026-11-13. 현재 로컬 소스와 실제 유지보수 기록을 정리한 공개 배포본이다. 과거 문서보다 이 문서와 docs/CURRENT-DESIGN.md를 우선한다.

## 다른 PC에서 시작

Windows, Python 3.12, Git, 카카오톡 PC, 필요한 브라우저 및 Claude Code를 준비한다.

```powershell
git clone -b docs/portable-handoff-20261113 https://github.com/Jayinsightfactory/nenovakakao.git
cd nenovakakao
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
claude
```

Claude에게 다음과 같이 지시한다:

> AGENTS.md, CLAUDE.md, HANDOFF-OTHER-PC.md, docs/CURRENT-DESIGN.md를 읽고 현재 상태를 확인해. 먼저 읽기 전용으로 조사하고 코드 수정은 진행하되, 카톡 발송·전산 저장·매크로 시작은 구체적인 내 지시 범위에서만 수행해. 운영 PC와 동시에 워커를 실행하지 마.

Claude Code는 개발 지시 도구다. 매크로의 구독 AI 분석 구현은 core/subscription_ai.py의 Codex CLI다. Claude 로그인만으로 이 분석이 동작하지 않는다. 실행 PC에서 Codex CLI도 별도 설치·로그인해야 한다. 유료 API로 자동 전환하지 않는다. Jev는 선택적 비교 실험이며 실제 호출에 별도 인증이 필요하다.

## 로컬 설정과 운영 상태 이관

Git clone만으로 기존 처리 상태는 복원되지 않는다. .env의 키/주소/저장경로를 PC에 맞춰 설정한다. 네노바 자동인증, 브라우저 확장·네이티브호스트, Windows 계정에 묶인 인증은 새 PC에서 다시 구성한다. 비밀값은 채팅이나 Git에 적지 않는다.

개발만 다른 PC에서 할 경우 운영 data를 옮기거나 워커를 시작할 필요가 없다. 실제 실행 PC를 바꿀 때는 기존 PC를 먼저 정지하고 data 전체를 암호화된 비공개 경로로 이관한다. 특히 요청상태, 승인, 등록영수증, 전송이력, 일일보고, 차수설정, 정지기록, 체크포인트를 함께 보존한다. 기존 Git 추적 data는 초기 시드일 뿐 최신 운영 기록이 아니다. 빈 상태로 과거 카톡을 재수집하면 중복 처리 위험이 있다.

현재 PC에서 마지막 확인한 표식은 paused이며 정지 이벤트는 floating stop button이다. 이 업로드는 시작 승인이 아니다. 새 PC에서 현재 프로세스와 최신 정지 기록을 다시 확인한다.

시작을 명시 승인받은 경우, 등록/전송 이력 대조 및 설정 확인 후 공식 경로 `.\.venv\Scripts\python.exe scripts/start_supervised.py --seconds 180`을 사용한다. 이 명령은 실제 작업을 시작한다. 단일 워커와 초기 사이클을 확인한다. 관찰 시간 종료와 초기 전체 사이클 완료는 다르다.

## 공개 저장소 범위

소스·관련 테스트·설계 및 익명화한 사용 경과를 공유한다. 원본 카톡, 업체별 원장, 인증자료, 화면 캡처, 전체 유지보수 로그, 실행 영수증은 새로 업로드하지 않는다. 공개본만으로 기존 건의 등록 성공을 판정하지 않는다.
