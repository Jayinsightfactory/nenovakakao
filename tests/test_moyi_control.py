from pathlib import Path

import core.moyi_control as control


def test_pause_marker_round_trip(tmp_path: Path, monkeypatch):
    marker = tmp_path / "worker.pause"
    monkeypatch.setattr(control, "PAUSE_FILE", marker)

    assert control.is_paused() is False
    control.set_paused(True)
    assert control.is_paused() is True
    assert marker.read_text(encoding="utf-8") == "paused\n"

    control.set_paused(False)
    assert control.is_paused() is False

