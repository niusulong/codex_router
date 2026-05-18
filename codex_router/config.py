"""Configuration loader supporting config.yaml and env vars."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings


class PresetConfig(BaseModel):
    """模型预设配置"""
    name: str
    base_url: str
    api_key: str
    model: str
    models: list[str] = Field(default_factory=list, description="可用模型列表")
    timeout: float = 120.0
    api_format: str = "openai"
    created_at: Optional[float] = None


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
    api_format: str = Field(
        default="openai",
        description="Upstream API format: openai or anthropic",
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
        default=False,
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
    presets: list[PresetConfig] = Field(
        default_factory=list,
        description="Model preset configurations",
    )
    active_preset: Optional[str] = Field(
        default=None,
        description="Name of the currently active preset",
    )

    model_config = {"env_prefix": "CODEX_ROUTER_", "env_nested_delimiter": "__"}

    @property
    def active_model(self) -> str:
        """Get the model from the currently active preset."""
        if self.active_preset:
            for p in self.presets:
                if p.name == self.active_preset:
                    return p.model
        return "default"

    def to_yaml_dict(self) -> dict:
        """Serialize to a YAML-friendly dict."""
        d = {}
        d["upstream"] = {
            "base_url": self.upstream.base_url,
            "api_key": self.upstream.api_key,
            "timeout": self.upstream.timeout,
        }
        if self.upstream.api_format != "openai":
            d["upstream"]["api_format"] = self.upstream.api_format
        d["server"] = {
            "host": self.server.host,
            "port": self.server.port,
            "log_level": self.server.log_level,
        }
        if self.passthrough_api_key:
            d["passthrough_api_key"] = self.passthrough_api_key
        if self.ignored_builtin_tools:
            d["ignored_builtin_tools"] = self.ignored_builtin_tools
        d["codex"] = {"auto_configure": self.codex.auto_configure}
        if self.codex.config_dir:
            d["codex"]["config_dir"] = self.codex.config_dir
        if self.presets:
            d["presets"] = [p.model_dump() for p in self.presets]
        if self.active_preset:
            d["active_preset"] = self.active_preset
        return d

    def save_to_file(self, path: Path) -> None:
        """Serialize to YAML and write atomically (temp + rename)."""
        from codex_router.codex_config import _atomic_write
        d = self.to_yaml_dict()
        content = yaml.dump(d, default_flow_style=False, allow_unicode=True, sort_keys=False)
        _atomic_write(path, content)


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


def load_config(config_path: str | None = None) -> tuple[ProxyConfig, Path | None]:
    """Load config from config.yaml, with env var overrides.

    Priority: env vars > config.yaml > code defaults.
    Only passes non-None YAML values so env vars can still override.
    Returns (ProxyConfig, resolved config file path or None).
    """
    if config_path:
        path = Path(config_path)
    else:
        path = _find_config_path()

    init_values: dict = {}

    if path and path.exists():
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        if "upstream" in data and data["upstream"]:
            init_values["upstream"] = {k: v for k, v in data["upstream"].items() if v is not None}
        if "server" in data and data["server"]:
            init_values["server"] = {k: v for k, v in data["server"].items() if v is not None}
        if "codex" in data and data["codex"]:
            init_values["codex"] = {k: v for k, v in data["codex"].items() if v is not None}
        for key in ("passthrough_api_key", "ignored_builtin_tools"):
            if key in data and data[key] is not None:
                init_values[key] = data[key]
        # model_override is no longer used; silently ignored if present in config
        if "presets" in data and data["presets"] is not None:
            init_values["presets"] = data["presets"]
        if "active_preset" in data and data["active_preset"] is not None:
            init_values["active_preset"] = data["active_preset"]

    return ProxyConfig(**init_values), path
