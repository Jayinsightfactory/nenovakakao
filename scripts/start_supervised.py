"""Start the existing worker and observe multiple cycles; never replay jobs."""
import argparse
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.moyi_control import emergency_stop, is_paused, set_paused, worker_processes
from moyi_console import start_worker_if_needed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--seconds', type=int, default=180)
    options = parser.parse_args()
    duration = max(45, min(options.seconds, 3600))
    logs = [ROOT / 'data' / name for name in (
        'moyi_events.jsonl', 'keyword_forward_events.jsonl',
        'moyi_control_events.jsonl', 'agent_runtime_events.jsonl',
        'moyi_worker_stderr.log')]
    offsets = {p: p.stat().st_size if p.exists() else 0 for p in logs}
    stages = 0
    agents = set()
    error = None
    try:
        set_paused(False)
        start_worker_if_needed()
        print('START_REQUESTED', flush=True)
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            time.sleep(1)
            if is_paused():
                error = 'pause_requested'
                break
            if not list(worker_processes()):
                error = 'worker_exited'
                break
            for path in logs:
                if not path.exists():
                    continue
                with path.open('rb') as stream:
                    stream.seek(offsets[path])
                    data = stream.read()
                if not data:
                    continue
                if path.suffix == '.log':
                    error = 'worker_stderr'
                    break
                # Consume only complete records; preserve a partial write.
                end = data.rfind(b'\n') + 1
                offsets[path] += end
                for line in data[:end].splitlines():
                    row = json.loads(line)
                    state = row.get('state', row.get('status', ''))
                    if state == 'stage_timing':
                        stages += 1
                    if row.get('agent') and row.get('outcome') in {'ok', 'error'}:
                        stages += 1
                        if row['outcome'] == 'ok': agents.add(row['agent'])
                    if row.get('agent') and row.get('outcome') == 'error':
                        error = row.get('agent') + '_agent_failed'
                        break
                    if (state in {'확인 필요', '결과 불명', 'unknown_result',
                                  'pending_unavailable', 'error_notice_unknown'}
                            or state.endswith('_failed') or state.endswith('_error')):
                        error = state
                        break
                if error:
                    break
            if error:
                break
    except BaseException:
        emergency_stop('supervised_start_exception')
        raise
    from core.workflow_settings import config as workflow_config
    required = {'approval', 'sales', 'defect_responses', 'defect'}
    if workflow_config()['order_processing_enabled']:
        required.add('order')
    if not error and not required.issubset(agents):
        error = 'startup_incomplete'
    if error:
        emergency_stop('supervised_start:' + error)
    result = {'running': bool(list(worker_processes())), 'paused': is_paused(),
              'error_state': error, 'observed_stage_count': stages,
              'observed_agents': sorted(agents), 'observed_seconds': duration,
              'at': time.time()}
    (ROOT / 'data' / 'supervised_start_result.json').write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 1 if error else 0


if __name__ == '__main__':
    raise SystemExit(main())
