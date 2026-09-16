"""User-selected collection/approval loop and lower-frequency background work."""
import json
from pathlib import Path

CONFIG = Path(__file__).resolve().parents[1] / 'data' / 'workflow_config.json'


def config():
    defaults = {'primary_interval_sec': 5, 'order_interval_sec': 60,
                'background_interval_sec': 1800, 'order_review_only': False,
                'order_processing_enabled': True}
    if CONFIG.exists():
        defaults.update(json.loads(CONFIG.read_text(encoding='utf-8')))
    return defaults


def register_agents(coordinator, sales, approval, order):
    # Both top operations are due together on every primary cycle.
    coordinator.add('approval', 0, 0, approval)
    coordinator.add('sales', 10, 0, sales)
    if config()['order_processing_enabled']:
        coordinator.add('order', 30, config()['order_interval_sec'], order)
