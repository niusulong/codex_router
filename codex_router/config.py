"""Configuration loader supporting config.yaml and env vars."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings


class UpstreamConfig(BaseSettings):
    base_url: str = Field(
        default="https://api.openai.com/v1",
        description="Base URL of the upstream Chat Completions-compatible API",
    )
    api_key: str = Field(
        default="",
        description="API key for the upstream service",
    )
    timeout: float = Field(
        default=120.0,
        description="Upstream request timeout in seconds",
    )

    model_config = {"env_prefix": "CODEX_ROUTER_UPSTREAM__"}


class ServerConfig(BaseSettings):
    host: str = Field(default="127.0.0.1")
    port: int = Field(default=8080)
    log_level: str = Field(default="info")

    model_config = {"env_prefix": "CODEX_ROUTER_SERVER__"}


class CodexConfig(BaseSettings):
    auto_configure: bool = Field(
        default=True,
        description="Auto-configure Codex CLI auth.json on startup",
    )
    config_dir: Optional[str] = Field(
        default=None,
        description="Codex config directory (default: ~/.codex)",
    )

    model_config = {"env_prefix": "CODEX_ROUTER_CODEX__"}


class ProxyConfig(BaseSettings):
    upstream: UpstreamConfig = Field(default_factory=UpstreamConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    codex: CodexConfig = Field(default_factory=CodexConfig)
    passthrough_api_key: bool = Field(
        default=True,
        description="If true, use the API key from the incoming request when upstream.api_key is empty",
    )
    ignored_builtin_tools: list[str] = Field(
        default_factory=lambda: [
            "web_search_preview",
            "web_search",
            "file_search",
            "code_interpreter",
            "mcp",
        ],
        description="Responses API built-in tool types to filter out",
    )
    model_override: Optional[str] = Field(
        default=None,
        description="If set, override the model name in all requests",
    )

    model_config = {"env_prefix": "CODEX_ROUTER_", "env_nested_delimiter": "__"}


def _find_config_path() -> Path | None:
    """Find config.yaml in current directory or script directory."""
    candidates = [
        Path.cwd() / "config.yaml",
        Path(__file__).parent.parent / "config.yaml",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


def load_config(config_path: str | None = None) -> ProxyConfig:
    """Load config from config.yaml, with env var overrides."""
    if config_path:
        path = Path(config_path)
    else:
        path = _find_config_path()

    init_values: dict = {}

    if path and path.exists():
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        # Flatten yaml into init_values for ProxyConfig
        if "upstream" in data:
            for k, v in data["upstream"].items():
                init_values.setdefault("upstream", {})[k] = v
        if "server" in data:
            for k, v in data["server"].items():
                init_values.setdefault("server", {})[k] = v
        if "codex" in data:
            for k, v in data["codex"].items():
                init_values.setdefault("codex", {})[k] = v
        for key in ("passthrough_api_key", "ignored_builtin_tools", "model_override"):
            if key in data:
                init_values[key] = data[key]

    return ProxyConfig(**init_values)
