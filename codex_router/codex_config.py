"""Manage Codex CLI configuration (auth.json)."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from codex_router.config import ProxyConfig

logger = logging.getLogger(__name__)


def get_codex_config_dir(config: ProxyConfig) -> Path:
    """Get the Codex CLI config directory."""
    if config.codex.config_dir:
        return Path(config.codex.config_dir)
    return Path.home() / ".codex"


def read_auth_json(config_dir: Path) -> dict[str, Any]:
    """Read the existing auth.json, return empty dict if not found."""
    auth_path = config_dir / "auth.json"
    if auth_path.exists():
        with open(auth_path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def write_auth_json(config_dir: Path, auth: dict[str, Any]) -> None:
    """Write auth.json."""
    config_dir.mkdir(parents=True, exist_ok=True)
    auth_path = config_dir / "auth.json"
    with open(auth_path, "w", encoding="utf-8") as f:
        json.dump(auth, f, indent=2)
    logger.info("Updated %s", auth_path)


def configure_codex(config: ProxyConfig) -> None:
    """Configure Codex CLI to point to this proxy."""
    config_dir = get_codex_config_dir(config)
    auth = read_auth_json(config_dir)

    proxy_base = f"http://{config.server.host}:{config.server.port}/v1"

    # Determine the API key to put in auth.json
    # Codex sends this key in Authorization header; our proxy will use it
    # or the upstream key depending on config
    api_key = config.upstream.api_key or auth.get("OPENAI_API_KEY", "")

    auth["OPENAI_API_KEY"] = api_key
    auth["OPENAI_BASE_URL"] = proxy_base

    write_auth_json(config_dir, auth)
    logger.info("Codex CLI configured: base_url=%s", proxy_base)


def restore_codex(config: ProxyConfig, backup: dict[str, Any] | None = None) -> None:
    """Restore Codex CLI auth.json to previous state."""
    config_dir = get_codex_config_dir(config)
    if backup is not None:
        write_auth_json(config_dir, backup)
    else:
        auth = read_auth_json(config_dir)
        auth.pop("OPENAI_BASE_URL", None)
        write_auth_json(config_dir, auth)
    logger.info("Codex CLI configuration restored")


def backup_codex_auth(config: ProxyConfig) -> dict[str, Any]:
    """Backup current Codex auth.json."""
    config_dir = get_codex_config_dir(config)
    return read_auth_json(config_dir)
