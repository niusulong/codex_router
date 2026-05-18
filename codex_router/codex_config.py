"""Codex CLI configuration file management (auth.json + config.toml)."""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import tomlkit

from codex_router.config import PresetConfig, ProxyConfig

logger = logging.getLogger(__name__)


@dataclass
class CodexBackup:
    """Holds backup data for Codex CLI config files."""
    auth_json: str | None = None
    config_toml: str | None = None
    auth_path: Path | None = None
    config_path: Path | None = None
    models_path: Path | None = field(default=None, init=False)


def _codex_dir(config: ProxyConfig) -> Path:
    """Get the Codex CLI config directory."""
    if config.codex.config_dir:
        return Path(config.codex.config_dir)
    return Path.home() / ".codex"


def _atomic_write(path: Path, content: str, mode: int = 0o644) -> None:
    """Write content to a file atomically using temp file + rename."""
    dir_path = path.parent
    dir_path.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(dir_path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.chmod(tmp_path, mode)
        os.replace(tmp_path, str(path))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def backup_codex(config: ProxyConfig) -> CodexBackup:
    """Backup Codex CLI config files (auth.json + config.toml).

    Stores backup both in memory and on disk (.bak files) for crash recovery.
    """
    codex_dir = _codex_dir(config)
    backup = CodexBackup()

    # Backup auth.json
    auth_path = codex_dir / "auth.json"
    backup.auth_path = auth_path
    if auth_path.exists():
        backup.auth_json = auth_path.read_text(encoding="utf-8")
        # Disk backup for crash recovery
        bak_path = codex_dir / "auth.json.bak"
        shutil.copy2(str(auth_path), str(bak_path))
        logger.info("Backed up auth.json (memory + disk)")

    # Backup config.toml
    config_path = codex_dir / "config.toml"
    backup.config_path = config_path
    if config_path.exists():
        backup.config_toml = config_path.read_text(encoding="utf-8")
        # Disk backup for crash recovery
        bak_path = codex_dir / "config.toml.bak"
        shutil.copy2(str(config_path), str(bak_path))
        logger.info("Backed up config.toml (memory + disk)")

    return backup


def configure_codex(config: ProxyConfig, preset: PresetConfig | None = None) -> Path | None:
    """Write proxy configuration into Codex CLI config files.

    Args:
        config: Proxy configuration.
        preset: The active preset to use for model/catalog. If None, falls back to config defaults.

    Returns the path to generated models.json, or None.
    """
    codex_dir = _codex_dir(config)
    codex_dir.mkdir(parents=True, exist_ok=True)

    # Write auth.json
    auth_path = codex_dir / "auth.json"
    auth_data: dict = {}
    if auth_path.exists():
        try:
            auth_data = json.loads(auth_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            logger.warning("Could not parse existing auth.json, overwriting")

    api_key = config.upstream.api_key or ""
    auth_data["OPENAI_API_KEY"] = api_key
    _atomic_write(auth_path, json.dumps(auth_data, indent=2) + "\n", mode=0o600)
    logger.info("Wrote auth.json with API key")

    # Generate model catalog JSON for Codex /model command
    catalog_path = None
    model_name = preset.model if preset else "default"
    if preset and preset.models:
        catalog = _build_model_catalog(preset.models)
        catalog_path = codex_dir / "models.json"
        _atomic_write(catalog_path, json.dumps(catalog, indent=2, ensure_ascii=False) + "\n")
        logger.info("Wrote model catalog with %d models", len(preset.models))

    # Patch config.toml using tomlkit for safe structured modification
    config_path = codex_dir / "config.toml"
    if config_path.exists():
        try:
            toml_content = config_path.read_text(encoding="utf-8")
            doc = tomlkit.parse(toml_content)
        except Exception:
            logger.warning("Could not parse existing config.toml, creating new")
            doc = tomlkit.document()
    else:
        doc = tomlkit.document()

    proxy_url = f"http://{config.server.host}:{config.server.port}/v1"
    doc["openai_base_url"] = proxy_url

    doc["model"] = model_name

    if catalog_path:
        doc["model_catalog_json"] = str(catalog_path.resolve())

    _atomic_write(config_path, tomlkit.dumps(doc))
    logger.info(
        "Patched config.toml: openai_base_url=%s, model=%s, model_catalog_json=%s",
        proxy_url,
        model_name,
        catalog_path.resolve() if catalog_path else None,
    )
    return catalog_path


def _build_model_catalog(model_ids: list[str]) -> dict:
    """Build a Codex-compatible model catalog JSON with safe defaults."""
    _DEFAULT_REASONING_LEVELS = [
        {"effort": "low", "description": "Fast responses with lighter reasoning"},
        {"effort": "medium", "description": "Balances speed and reasoning depth"},
        {"effort": "high", "description": "Greater reasoning depth for complex problems"},
    ]
    models = []
    for idx, model_id in enumerate(model_ids):
        models.append({
            "slug": model_id,
            "display_name": model_id,
            "description": f"Model {model_id}",
            "priority": idx,
            "visibility": "list",
            "minimal_client_version": "0.0.1",
            "supported_in_api": True,
            "default_reasoning_level": "medium",
            "supported_reasoning_levels": _DEFAULT_REASONING_LEVELS,
            "reasoning_summary_format": "none",
            "default_reasoning_summary": "none",
            "supports_reasoning_summaries": False,
            "context_window": 128000,
            "max_context_window": 128000,
            "auto_compact_token_limit": None,
            "truncation_policy": {"mode": "tokens", "limit": 10000},
            "prefer_websockets": False,
            "support_verbosity": False,
            "default_verbosity": "medium",
            "apply_patch_tool_type": "freeform",
            "web_search_tool_type": "text",
            "input_modalities": ["text"],
            "supports_image_detail_original": False,
            "supports_parallel_tool_calls": False,
            "shell_type": "shell_command",
            "supports_search_tool": False,
            "experimental_supported_tools": [],
            "available_in_plans": [],
            "service_tiers": [],
            "additional_speed_tiers": [],
            "base_instructions": "",
            "upgrade": None,
            "availability_nux": None,
            "model_messages": None,
        })
    return {"models": models}


def restore_codex(config: ProxyConfig, backup: CodexBackup) -> None:
    """Restore Codex CLI config files from backup."""
    codex_dir = _codex_dir(config)

    # Restore auth.json
    if backup.auth_json is not None and backup.auth_path is not None:
        _atomic_write(backup.auth_path, backup.auth_json)
        logger.info("Restored auth.json")
    elif backup.auth_path and backup.auth_path.exists():
        # Try disk backup if memory backup is missing
        bak_path = codex_dir / "auth.json.bak"
        if bak_path.exists():
            shutil.copy2(str(bak_path), str(backup.auth_path))
            logger.info("Restored auth.json from disk backup")

    # Restore config.toml
    if backup.config_toml is not None and backup.config_path is not None:
        _atomic_write(backup.config_path, backup.config_toml)
        logger.info("Restored config.toml")
    elif backup.config_path and backup.config_path.exists():
        bak_path = codex_dir / "config.toml.bak"
        if bak_path.exists():
            shutil.copy2(str(bak_path), str(backup.config_path))
            logger.info("Restored config.toml from disk backup")

    # Clean up generated models.json
    if backup.models_path and backup.models_path.exists():
        backup.models_path.unlink()
        logger.info("Removed generated models.json")

    # Clean up disk backups
    for bak_name in ("auth.json.bak", "config.toml.bak"):
        bak_path = codex_dir / bak_name
        if bak_path.exists():
            bak_path.unlink()
