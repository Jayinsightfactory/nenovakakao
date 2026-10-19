# AGENTS.md — nenovakakao (카톡 브릿지·일일 보고) — Codex/Claude 공용

먼저 읽기: `CLAUDE.md`. 전역 규칙은 `~/.codex/AGENTS.md`.

- `send_report.py`: 매일 09:00 예약작업 `NenovaKakaoReport`가 자가수정 보고를 카톡방 `임재용대리`로 보냄(pyautogui+클립보드, 발송 후 클립보드 복원). 중복 방지 `~/.orbit/kakao-report-sent.txt`.
- 카톡 발송은 사장님 PC 화면을 점유하므로 테스트 발송은 `--force` 없이 금지. 업무방 9개만 수집, 1:1은 동의자만.
- 노출된 키는 rotate 대상. 크로스저장소 curl 진단은 한글 POST가 깨지므로 Node fetch로.
