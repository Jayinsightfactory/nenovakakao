"""Cross-process controls shared by the MOYI console and worker."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAUSE_FILE = ROOT / "data" / "moyi_worker.pause"


def is_paused() -> bool:
    return PAUSE_FILE.exists()


def set_paused(paused: bool) -> None:
    PAUSE_FILE.parent.mkdir(parents=True, exist_ok=True)
    if paused:
        temporary = PAUSE_FILE.with_suffix(".tmp")
        temporary.write_text("paused\n", encoding="utf-8")
        temporary.replace(PAUSE_FILE)
    else:
        PAUSE_FILE.unlink(missing_ok=True)

