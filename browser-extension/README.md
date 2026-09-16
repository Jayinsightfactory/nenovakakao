# 네노바 로그인 세션 연결

1. Chrome 주소창에서 chrome://extensions 를 연다.
2. 개발자 모드를 켜고 **압축해제된 확장 프로그램을 로드합니다**를 누른다.
3. `C:\Users\USER\Downloads\nenovakakao\browser-extension` 폴더를 선택한다.
4. 로그인된 네노바 영업수입불량차감 탭을 새로고침한다. 저장하지 않은 편집 내용이 있으면 먼저 보존한다.
5. 확장 아이콘 표시 ON을 확인한다. OFF이면 아이콘을 눌러 다시 연결한다. TAB은 대상 페이지가 아직 연결되지 않은 상태다.

예상 확장 ID: pgbmfhegpkdifgkojfmdjonneoppdlji

권한: nenovaweb.com 페이지 접근 및 nativeMessaging. 실제 콘텐츠 스크립트는 /sales/defect-deductions 페이지에서만 실행한다. cookies 권한이나 전체 사이트 접근 권한은 없다. 로그인 쿠키·비밀번호를 읽거나 로컬 파일로 저장하지 않는다. 현재 웹 계정의 서버 권한이 적용된다.

담당자 승인 기록·버전·원문 작성자·저장 내용이 맞는 요청만 네이티브 호스트가 전달한다. 영업입력 저장 및 재매칭/조회만 허용한다. 견적서 일괄등록·삭제는 허용하지 않는다. 확장이 없으면 승인 상태를 보존한다. 로그인 만료는 웹에서 정상 로그인해야 한다.

로컬 설치: `python scripts/install_browser_session_bridge.py` (이 PC에는 등록 완료).
HKCU\Software\Google\Chrome\NativeMessagingHosts\com.nenovakakao.sales_session 에 자기 확장 ID 하나만 허용하는 호스트 매니페스트를 등록한다. TCP 수신 포트는 열지 않는다.

확장 로드 후 실제 브라우저 연결과 승인된 첫 저장의 재조회 검증이 필요하다. 로컬 테스트 통과는 실제 저장 완료를 뜻하지 않는다.
