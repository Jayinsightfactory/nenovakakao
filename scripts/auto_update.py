"""자동 최신화 켜기·끄기·상태 확인 (운영 PC 에서 실행).

    python scripts/auto_update.py status   # 지금 버전, 설정, 최근 기록
    python scripts/auto_update.py on       # 켜기 (배포 가지 release 를 10분마다 확인)
    python scripts/auto_update.py off      # 끄기
    python scripts/auto_update.py check    # 받아 올 새 버전이 있는지만 확인 (아무것도 바꾸지 않음)

매크로를 시작·정지하지 않고 카톡을 보내지 않는다. 설정은 data/update_config.json (Git 제외).
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import self_update  # noqa: E402


def main(argv):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    command = argv[1] if len(argv) > 1 else 'status'
    if command in ('on', 'off'):
        value = self_update.config()
        value['enabled'] = command == 'on'
        self_update.CONFIG.parent.mkdir(parents=True, exist_ok=True)
        self_update.CONFIG.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
        print('자동 최신화:', '켜짐' if value['enabled'] else '꺼짐',
              '(실행 중인 매크로는 다음 확인 주기부터 적용)')
    elif command == 'check':
        plan = self_update.check()
        print('새 버전 있음: %s → %s (파일 %d개)' % (plan['from'][:7], plan['to'][:7], plan['files'])
              if plan else '받아 올 새 버전 없음(또는 안전 조건 미충족 — 아래 기록 확인)')
    elif command != 'status':
        print(__doc__)
        return 1
    cfg = self_update.config()
    print('지금 버전:', self_update.current_version())
    print('설정: 사용=%s 배포가지=%s 확인주기=%s초' % (cfg['enabled'], cfg['ref'], cfg['interval_sec']))
    if self_update.LOG.exists():
        print('최근 기록:')
        for line in self_update.LOG.read_text(encoding='utf-8').splitlines()[-5:]:
            print('  ', line)
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
