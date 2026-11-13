"""Resolve the configured open period; preserve the scope of existing writes."""
from datetime import date, timedelta


def upload_scope(row):
    from core import defect_approval as d
    config_path = d.ROOT.parent / 'defect_config.json'
    cfg = d.load(config_path) if config_path.exists() else {}
    if cfg.get('registration_scope_policy') == 'source_next':
        if row.get('status') in ('completed', 'write_unknown', 'resolved_existing'):
            if row.get('registration_scope'):
                return dict(row['registration_scope'])
            periods = {(int(r['orderYear']), int(r['orderWeek'])) for r in row.get('receipt') or []}
            if len(periods) == 1:
                year, week = periods.pop()
                return {'year': year, 'week': week}
            raise ValueError('기존 등록 결과의 원래 등록 차수 대조 필요')
        import re
        sequence = re.fullmatch(r'(\d{1,2})(?:-[123])?(?:차)?', str(row['extracted'].get('sequence', '')).strip())
        if not sequence or not 1 <= int(sequence[1]) <= 51:
            raise ValueError('원문 차수 또는 연말 업무 차수 이월 확인 필요')
        year = int(row['event']['timestamp'].split('년')[0])
        if not 2000 <= year <= 2100:
            raise ValueError('불량 원문 연도 확인 필요')
        return {'year': year, 'week': int(sequence[1]) + 1}
    if cfg.get('registration_scope_policy') in ('open_period', 'support_registration'):
        if row.get('status') in ('write_unknown', 'completed'):
            if row.get('registration_scope'):
                return dict(row['registration_scope'])
            receipts = row.get('receipt') or []
            periods = {(int(r['orderYear']), int(r['orderWeek'])) for r in receipts}
            if len(periods) == 1:
                year, week = periods.pop()
                return {'year': year, 'week': week}
            raise ValueError('기존 등록 결과의 원래 등록 차수 대조 필요')
        if cfg.get('registration_scope_policy') == 'support_registration':
            if not row.get('period_resolution') or not row.get('registration_scope'):
                raise ValueError('영업지원 등록 이력 기준 차수 검증 필요')
            return dict(row['registration_scope'])
        period = cfg.get('current_registration_period') or {}
        date.fromisocalendar(int(period.get('year', 0)), int(period.get('week', 0)), 1)
        # Business defect periods are independent of dashboard and ISO weeks.
        # The adapter separately checks the live support ledger before saving.
        if period.get('source') != 'user_confirmed_defect_period':
            raise ValueError('불량 등록 차수 명시 확인 필요: 대시보드·달력 차수 사용 금지')
        import re
        posted = re.match(r'(\d+)년\s*(\d+)월\s*(\d+)일', row['event']['timestamp'])
        if not posted or date(*map(int, posted.groups())) > date.today():
            raise ValueError('카톡 작성 시점 확인 필요')
        return {'year': period['year'], 'week': period['week']}
    year = int(row['event']['timestamp'].split('년')[0])
    week = int(row['extracted']['sequence'].split('-')[0])
    if not 2000 <= year <= 2100:
        raise ValueError('불량 원문 연도 확인 필요')
    following = (date.fromisocalendar(year, week, 1) + timedelta(days=7)).isocalendar()
    return {'year': following.year, 'week': following.week}
