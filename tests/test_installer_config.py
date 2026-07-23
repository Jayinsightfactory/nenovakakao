from pathlib import Path
from unittest.mock import patch

import scripts.configure_moyi_install as installer


def test_config_preserves_secrets_and_allowlist(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text(
        "MOYI_BRIDGE_SECRET=existing-secret\n"
        "ANTHROPIC_API_KEY=existing-key\n"
        "MOYI_ROOM_ALLOWLIST=수입방,현장방\n"
        "MOYI_AUTO_DISCOVER_ROOMS=1\n",
        encoding="utf-8",
    )
    with patch.object(installer, "ENV_FILE", env), patch.object(installer.getpass, "getpass") as prompt:
        installer.configure("https://api.example.test/", "workspace-1", "new-secret")
    text = env.read_text(encoding="utf-8")
    assert "MOYI_BRIDGE_SECRET=existing-secret" in text
    assert "ANTHROPIC_API_KEY=existing-key" in text
    assert "MOYI_ROOM_ALLOWLIST=수입방,현장방" in text
    assert "MOYI_AUTO_DISCOVER_ROOMS=0" in text
    assert "MOYI_SERVER=https://api.example.test" in text
    prompt.assert_not_called()


def test_config_prompts_only_when_anthropic_key_is_missing(tmp_path: Path):
    env = tmp_path / ".env"
    with patch.object(installer, "ENV_FILE", env), patch.object(
        installer.getpass, "getpass", return_value="new-api-key"
    ):
        installer.configure("https://api.example.test", "workspace-1", "company-secret")
    text = env.read_text(encoding="utf-8")
    assert "MOYI_BRIDGE_SECRET=company-secret" in text
    assert "ANTHROPIC_API_KEY=new-api-key" in text
