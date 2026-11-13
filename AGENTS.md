# AGENTS.md — nenovakakao (카톡 브릿지·일일 보고) — Codex/Claude 공용

먼저 읽기: `CLAUDE.md`. 전역 규칙은 `~/.codex/AGENTS.md`.

- `send_report.py`: 평일 09:10 예약작업 `NenovaKakaoReport`가 자가수정 보고를 카톡방 하나(`~/.orbit/kakao-report-room.txt` 첫 줄)로 보냄(pyautogui+클립보드, 발송 후 클립보드 복원). 방이 뜰 때까지 기다리고 못 열면 아무것도 보내지 않음(`open_room_verified`, 실패 화면 `~/.orbit/kakao-report-fail.png`). 중복 방지 `~/.orbit/kakao-report-sent.txt`, 일일 발송 한도 `data/send_limits.json`.
- 카톡 발송은 사장님 PC 화면을 점유하므로 테스트 발송은 `--force` 없이 금지. 업무방 9개만 수집, 1:1은 동의자만.
- 노출된 키는 rotate 대상. 크로스저장소 curl 진단은 한글 POST가 깨지므로 Node fetch로.
