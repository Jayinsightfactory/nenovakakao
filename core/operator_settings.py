"""One persistent exact Kakao room name for reports and operator approvals."""
import json
from pathlib import Path
from core.atomic_json import save

CONFIG = Path(__file__).resolve().parents[1] / 'data' / 'operator_config.json'
LEGACY_NAME = '임재용대리'


def validate_name(value):
    name = str(value).strip()
    if not name or len(name) > 80 or any(ord(c) < 32 for c in name):
        raise ValueError('카카오톡 1:1 대화방 이름을 1~80자로 입력해주세요.')
    return name


def operator_name():
    if not CONFIG.exists():
        return LEGACY_NAME
    return validate_name(json.loads(CONFIG.read_text(encoding='utf-8'))['kakao_name'])


def configure(value):
    name = validate_name(value)
    from core.moyi_control import set_paused
    set_paused(True)
    save(CONFIG, {'kakao_name': name})
    return name
