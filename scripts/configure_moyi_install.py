"""Safely create or update the local MOYI connector environment."""
from __future__ import annotations

import argparse
import getpass
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"


def _read_lines() -> list[str]:
    if not ENV_FILE.exists():
        return ["# Managed by the MOYI Kakao installer. Do not commit this file."]
    return ENV_FILE.read_text(encoding="utf-8-sig").splitlines()


def _values(lines: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in lines:
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key.strip()] = value.strip()
    return result


def _upsert(lines: list[str], updates: dict[str, str]) -> list[str]:
    pending = dict(updates)
    output: list[str] = []
    for line in lines:
        if "=" not in line or line.lstrip().startswith("#"):
            output.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in pending:
            output.append(f"{key}={pending.pop(key)}")
        else:
            output.append(line)
    if output and output[-1] != "":
        output.append("")
    output.extend(f"{key}={value}" for key, value in pending.items())
    return output


def configure(server: str, workspace_id: str, secret: str) -> None:
    lines = _read_lines()
    current = _values(lines)
    anthropic_key = current.get("ANTHROPIC_API_KEY", "")
    if anthropic_key:
        print("[config] Preserving the existing Anthropic API key.")
    else:
        anthropic_key = getpass.getpass("Paste Anthropic API key: ").strip()
        if not anthropic_key:
            raise SystemExit("[ERROR] Anthropic API key is required for room discovery.")

    # Preserve the user's exact room allowlist. Discovery remains fail-closed;
    # installers must never report every Kakao room automatically.
    updates = {
        "MOYI_BRIDGE_SECRET": current.get("MOYI_BRIDGE_SECRET") or secret,
        "MOYI_SERVER": server.rstrip("/"),
        "MOYI_API_BASE": "",
        "MOYI_WORKSPACE_ID": workspace_id,
        "MOYI_AGENT_ID": current.get("MOYI_AGENT_ID") or "nenova-owner-pc",
        "MOYI_AUTO_DISCOVER_ROOMS": "0",
        "MOYI_ROOM_SCAN_INTERVAL_SEC": current.get("MOYI_ROOM_SCAN_INTERVAL_SEC") or "900",
        "MOYI_INBOUND_ENABLED": "1",
        "MOYI_WORKER_POLL_SEC": current.get("MOYI_WORKER_POLL_SEC") or "5",
        "MOYI_MONITOR_POLL_SEC": current.get("MOYI_MONITOR_POLL_SEC") or "5",
        "ANTHROPIC_API_KEY": anthropic_key,
    }
    temporary = ENV_FILE.with_suffix(".tmp")
    temporary.write_text("\n".join(_upsert(lines, updates)).rstrip() + "\n", encoding="utf-8")
    os.replace(temporary, ENV_FILE)
    print("[config] Configuration updated without printing or replacing existing secrets.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", required=True)
    parser.add_argument("--workspace-id", required=True)
    args = parser.parse_args()
    secret = os.getenv("MOYI_INSTALL_SECRET", "")
    if not secret:
        raise SystemExit("[ERROR] Installer secret was not provided.")
    configure(args.server, args.workspace_id, secret)


if __name__ == "__main__":
    main()
