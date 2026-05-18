"""ConfigManager: runtime preset management, hot-swap, persistence, Codex sync."""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from codex_router.codex_config import configure_codex
from codex_router.config import PresetConfig, ProxyConfig

logger = logging.getLogger(__name__)


@dataclass
class VerifyResult:
    ok: bool
    message: str
    latency_ms: float


@dataclass
class LogEntry:
    timestamp: float
    action: str
    detail: str


class ConfigManager:
    """Runtime configuration manager: presets, hot-swap, persistence, Codex sync."""

    def __init__(self, config: ProxyConfig, config_path: Path | None):
        self._config = config
        self._config_path = config_path
        self._presets: dict[str, PresetConfig] = {}
        self._active_preset: str | None = None
        self._logs: deque[LogEntry] = deque(maxlen=50)
        self._init_presets()

    @property
    def config(self) -> ProxyConfig:
        return self._config

    @property
    def active_preset_name(self) -> str | None:
        return self._active_preset

    # ── Initialization ──

    def _init_presets(self) -> None:
        if self._config.presets:
            for p in self._config.presets:
                self._presets[p.name] = p
            self._active_preset = self._config.active_preset
        else:
            preset = PresetConfig(
                name="default",
                base_url=self._config.upstream.base_url,
                api_key=self._config.upstream.api_key,
                model="default",
                timeout=self._config.upstream.timeout,
                api_format=self._config.upstream.api_format,
                created_at=time.time(),
            )
            self._presets["default"] = preset
            self._active_preset = "default"
            self._config.presets = [preset]
            self._config.active_preset = "default"

    # ── Preset CRUD ──

    def list_presets(self) -> list[PresetConfig]:
        return list(self._presets.values())

    def get_preset(self, name: str) -> PresetConfig | None:
        return self._presets.get(name)

    async def add_preset(self, preset: PresetConfig) -> None:
        if preset.name in self._presets:
            raise ValueError(f"预设名 {preset.name} 已存在")
        if preset.created_at is None:
            preset.created_at = time.time()
        self._presets[preset.name] = preset
        self._config.presets = list(self._presets.values())
        await self.save_config()
        self._add_log("添加预设", preset.name)
        logger.info("添加预设: %s", preset.name)

    async def update_preset(self, name: str, updates: dict[str, Any]) -> PresetConfig:
        preset = self._presets.get(name)
        if preset is None:
            raise KeyError(f"预设 {name} 不存在")

        # API Key 保护: 空值或含 **** 的脱敏值不更新
        if "api_key" in updates:
            ak = updates["api_key"]
            if not ak or "****" in ak:
                del updates["api_key"]

        for k, v in updates.items():
            if k != "name" and hasattr(preset, k):
                setattr(preset, k, v)

        # 如果编辑的是活跃预设，立即生效
        if name == self._active_preset:
            await self._apply_preset_to_config(preset)

        self._config.presets = list(self._presets.values())
        await self.save_config()
        self._add_log("编辑预设", name)
        logger.info("编辑预设: %s", name)
        return preset

    async def delete_preset(self, name: str) -> None:
        if name not in self._presets:
            raise KeyError(f"预设 {name} 不存在")
        if name == self._active_preset:
            raise ValueError("不能删除当前活跃预设")
        del self._presets[name]
        self._config.presets = list(self._presets.values())
        await self.save_config()
        self._add_log("删除预设", name)
        logger.info("删除预设: %s", name)

    # ── Model Switch ──

    async def switch_model(self, name: str, model: str) -> None:
        preset = self._presets.get(name)
        if preset is None:
            raise KeyError(f"预设 {name} 不存在")
        if preset.models and model not in preset.models:
            raise ValueError(f"模型 {model} 不在预设 {name} 的可用模型列表中")

        old_model = preset.model
        preset.model = model
        self._config.presets = list(self._presets.values())

        if name == self._active_preset:
            await self._apply_preset_to_config(preset)
        else:
            await self.save_config()

        self._add_log("切换模型", f"{name}: {old_model} → {model}")
        logger.info("切换模型: %s: %s → %s", name, old_model, model)

    # ── Hot Swap ──

    async def activate_preset(self, name: str) -> None:
        preset = self._presets.get(name)
        if preset is None:
            raise KeyError(f"预设 {name} 不存在")

        old_name = self._active_preset
        self._active_preset = name
        self._config.active_preset = name
        await self._apply_preset_to_config(preset)
        self._add_log("切换预设", f"{old_name} → {name}")
        logger.info("切换预设: %s → %s", old_name, name)

    async def _apply_preset_to_config(self, preset: PresetConfig) -> None:
        """Apply preset values to runtime config + Codex CLI + persist."""
        self._config.upstream.base_url = preset.base_url
        self._config.upstream.api_key = preset.api_key
        self._config.upstream.timeout = preset.timeout
        self._config.upstream.api_format = getattr(preset, 'api_format', 'openai')

        if self._config.codex.auto_configure:
            try:
                models_path = configure_codex(self._config, preset)
                from codex_router.main import _backup
                if _backup and models_path:
                    _backup.models_path = models_path
            except Exception:
                logger.exception("Codex CLI 配置同步失败")

        await self.save_config()

    # ── Verify ──

    async def verify_preset(self, name: str) -> VerifyResult:
        preset = self._presets.get(name)
        if preset is None:
            raise KeyError(f"预设 {name} 不存在")

        url = f"{preset.base_url.rstrip('/')}/chat/completions"
        headers = {"Authorization": f"Bearer {preset.api_key}", "Content-Type": "application/json"}
        body = {
            "model": preset.model,
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 1,
            "stream": False,
        }

        start = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, json=body, headers=headers)
            latency = (time.monotonic() - start) * 1000
            if resp.status_code == 200:
                self._add_log("验证预设", f"{name} 连接成功 ({latency:.0f}ms)")
                return VerifyResult(True, "连接验证成功", latency)
            else:
                msg = f"HTTP {resp.status_code}: {resp.text[:200]}"
                self._add_log("验证预设", f"{name} 连接失败 ({msg})")
                return VerifyResult(False, f"连接验证失败: {msg}", latency)
        except Exception as e:
            latency = (time.monotonic() - start) * 1000
            msg = str(e)
            self._add_log("验证预设", f"{name} 连接失败 ({msg})")
            return VerifyResult(False, f"连接验证失败: {msg}", latency)

    # ── Persistence ──

    async def save_config(self) -> None:
        if self._config_path is None:
            logger.warning("No config path, skipping save")
            return
        self._config.presets = list(self._presets.values())
        self._config.save_to_file(self._config_path)

    # ── Logs ──

    def _add_log(self, action: str, detail: str) -> None:
        self._logs.append(LogEntry(time.time(), action, detail))

    def get_logs(self, limit: int = 50) -> list[dict[str, Any]]:
        entries = list(self._logs)[-limit:]
        return [
            {"timestamp": e.timestamp, "action": e.action, "detail": e.detail}
            for e in entries
        ]
