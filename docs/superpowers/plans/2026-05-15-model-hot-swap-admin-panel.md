# 模型热切换与 Web 管理面板 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 Codex Router 添加运行时模型预设热切换能力和 Web 管理面板。

**Architecture:** 架构重构——router/ws_handler 从闭包捕获 config 改为 `app.state.config` 动态读取。新增 ConfigManager 单例管理预设 CRUD、热切换、持久化和 Codex 同步。Admin API + 独立 HTML 模板文件提供 Web UI。

**Tech Stack:** FastAPI, Pydantic v2, httpx, PyYAML, tomlkit, Pico CSS (CDN)

**Spec:** `docs/superpowers/specs/2026-05-15-model-hot-swap-admin-panel-design.md`

---

## File Structure

| 文件 | 类型 | 职责 |
|------|------|------|
| `codex_router/config.py` | 修改 | 新增 PresetConfig，ProxyConfig 加 presets/active_preset/save_to_file，load_config 返回 path |
| `codex_router/config_manager.py` | 新增 | 预设 CRUD、热切换、持久化、Codex 同步、操作日志 |
| `codex_router/admin.py` | 新增 | 管理 API 路由 + Web UI HTML 加载 |
| `codex_router/static/admin.html` | 新增 | Web UI 独立 HTML（Pico CSS + 原生 JS） |
| `codex_router/router.py` | 修改 | 闭包→app.state 动态读取 + 逐请求 timeout |
| `codex_router/ws_handler.py` | 修改 | 去掉 config 参数，动态读取 + 逐请求 timeout |
| `codex_router/main.py` | 修改 | 初始化 ConfigManager、注册 admin 路由 |

---

### Task 1: 扩展 config.py — PresetConfig + ProxyConfig 扩展

**Files:**
- Modify: `codex_router/config.py`

- [ ] **Step 1: 添加 PresetConfig 模型和 ProxyConfig 新字段**

在 `config.py` 顶部添加 `from pydantic import BaseModel` (pydantic_settings 已导入 pydantic 但需要显式导入 BaseModel)，然后在 `UpstreamConfig` 类之前添加 `PresetConfig`，在 `ProxyConfig` 中添加新字段。

```python
# config.py — 在文件顶部 import 区域添加 BaseModel
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings

# 在 UpstreamConfig 类定义之前，添加 PresetConfig
class PresetConfig(BaseModel):
    """模型预设配置"""
    name: str
    base_url: str
    api_key: str
    model: str
    timeout: float = 120.0
    created_at: Optional[float] = None
```

在 `ProxyConfig` 类中，在 `model_override` 字段之后添加：

```python
    presets: list[PresetConfig] = Field(
        default_factory=list,
        description="Model preset configurations",
    )
    active_preset: Optional[str] = Field(
        default=None,
        description="Name of the currently active preset",
    )
```

- [ ] **Step 2: 修改 load_config 返回 path**

将 `load_config` 的返回类型从 `ProxyConfig` 改为 `tuple[ProxyConfig, Path | None]`，同时处理 presets 字段的加载。

```python
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
        for key in ("passthrough_api_key", "ignored_builtin_tools", "model_override"):
            if key in data and data[key] is not None:
                init_values[key] = data[key]
        # 新增: presets 和 active_preset
        if "presets" in data and data["presets"] is not None:
            init_values["presets"] = data["presets"]
        if "active_preset" in data and data["active_preset"] is not None:
            init_values["active_preset"] = data["active_preset"]

    return ProxyConfig(**init_values), path
```

- [ ] **Step 3: 添加 save_to_file 方法到 ProxyConfig**

在 `ProxyConfig` 类中添加方法：

```python
    def to_yaml_dict(self) -> dict:
        """Serialize to a YAML-friendly dict."""
        from codex_router.codex_config import _atomic_write
        d = {}
        d["upstream"] = {
            "base_url": self.upstream.base_url,
            "api_key": self.upstream.api_key,
            "timeout": self.upstream.timeout,
        }
        d["server"] = {
            "host": self.server.host,
            "port": self.server.port,
            "log_level": self.server.log_level,
        }
        if self.passthrough_api_key:
            d["passthrough_api_key"] = self.passthrough_api_key
        if self.ignored_builtin_tools:
            d["ignored_builtin_tools"] = self.ignored_builtin_tools
        if self.model_override:
            d["model_override"] = self.model_override
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
        import yaml as _yaml
        from codex_router.codex_config import _atomic_write
        d = self.to_yaml_dict()
        content = _yaml.dump(d, default_flow_style=False, allow_unicode=True, sort_keys=False)
        _atomic_write(path, content)
```

- [ ] **Step 4: 验证 config.py 语法正确**

Run: `python -c "from codex_router.config import ProxyConfig, PresetConfig, load_config; print('OK')"`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add codex_router/config.py
git commit -m "feat: extend ProxyConfig with PresetConfig, presets, active_preset, save_to_file"
```

---

### Task 2: 创建 config_manager.py

**Files:**
- Create: `codex_router/config_manager.py`

- [ ] **Step 1: 创建 ConfigManager 完整实现**

```python
"""ConfigManager: runtime preset management, hot-swap, persistence, Codex sync."""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field
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
                model=self._config.model_override or "",
                timeout=self._config.upstream.timeout,
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

    # ── Hot Swap ──

    async def activate_preset(self, name: str) -> None:
        preset = self._presets.get(name)
        if preset is None:
            raise KeyError(f"预设 {name} 不存在")

        old_name = self._active_preset
        await self._apply_preset_to_config(preset)
        self._active_preset = name
        self._config.active_preset = name
        self._add_log("切换预设", f"{old_name} → {name}")
        logger.info("切换预设: %s → %s", old_name, name)

    async def _apply_preset_to_config(self, preset: PresetConfig) -> None:
        """Apply preset values to runtime config + Codex CLI + persist."""
        self._config.upstream.base_url = preset.base_url
        self._config.upstream.api_key = preset.api_key
        self._config.upstream.timeout = preset.timeout
        self._config.model_override = preset.model

        if self._config.codex.auto_configure:
            try:
                configure_codex(self._config)
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
```

- [ ] **Step 2: 验证语法正确**

Run: `python -c "from codex_router.config_manager import ConfigManager; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add codex_router/config_manager.py
git commit -m "feat: add ConfigManager with presets CRUD, hot-swap, verify, persistence"
```

---

### Task 3: 重构 router.py — 动态 config + 逐请求 timeout

**Files:**
- Modify: `codex_router/router.py`

- [ ] **Step 1: 修改 create_router 签名和内部 config 读取**

将整个 `router.py` 替换为：

```python
"""FastAPI route handler for /v1/responses (HTTP + WebSocket)."""

from __future__ import annotations

import logging
from typing import Any, AsyncGenerator

from fastapi import APIRouter, HTTPException, Request, WebSocket
from fastapi.responses import StreamingResponse

from codex_router.config import ProxyConfig
from codex_router.converters.request import convert_request
from codex_router.converters.response import convert_response
from codex_router.converters.streaming import convert_stream, sse_event
from codex_router.errors import UpstreamError
from codex_router.models import ResponsesRequest
from codex_router.ws_handler import build_upstream_headers, build_upstream_url, handle_websocket

logger = logging.getLogger(__name__)


def create_router() -> APIRouter:
    router = APIRouter()

    @router.post("/v1/responses")
    async def create_response(request: Request):
        config: ProxyConfig = request.app.state.config

        try:
            body = await request.json()
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}") from e

        try:
            resp_req = ResponsesRequest(**body)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid request: {e}") from e

        api_key = config.upstream.api_key
        if not api_key and config.passthrough_api_key:
            auth_header = request.headers.get("authorization", "")
            if auth_header.startswith("Bearer "):
                api_key = auth_header[7:]

        if not api_key:
            raise HTTPException(status_code=401, detail="No API key available")

        cc_req = convert_request(resp_req, config)
        headers = build_upstream_headers(api_key)
        upstream_url = build_upstream_url(config)
        client: Any = request.app.state.http_client
        timeout = config.upstream.timeout

        if resp_req.stream:
            return await _handle_streaming(client, upstream_url, headers, cc_req, resp_req.model, timeout)
        else:
            return await _handle_non_streaming(client, upstream_url, headers, cc_req, resp_req.model, timeout)

    @router.websocket("/v1/responses")
    async def ws_responses(ws: WebSocket):
        await handle_websocket(ws)

    return router


async def _handle_non_streaming(
    client: Any,
    upstream_url: str,
    headers: dict[str, str],
    cc_req: dict[str, Any],
    model: str,
    timeout: float,
) -> dict[str, Any]:
    try:
        resp = await client.post(upstream_url, json=cc_req, headers=headers, timeout=timeout)
    except Exception as e:
        raise UpstreamError(f"Upstream request failed: {e}") from e

    if resp.status_code != 200:
        logger.error("Upstream error %d: %s", resp.status_code, resp.text[:2000])
        raise UpstreamError(f"Upstream returned {resp.status_code}")

    return convert_response(resp.json(), model)


async def _handle_streaming(
    client: Any,
    upstream_url: str,
    headers: dict[str, str],
    cc_req: dict[str, Any],
    model: str,
    timeout: float,
) -> StreamingResponse:
    async def event_generator() -> AsyncGenerator[str, None]:
        async with client.stream("POST", upstream_url, json=cc_req, headers=headers, timeout=timeout) as resp:
            if resp.status_code != 200:
                error_body = await resp.aread()
                decoded = error_body.decode(errors="replace")
                logger.error("Upstream streaming error %d: %s", resp.status_code, decoded[:2000])
                yield sse_event("response.failed", {
                    "type": "response.failed",
                    "response": {
                        "status": "failed",
                        "error": {"message": f"Upstream returned {resp.status_code}"},
                    },
                })
                return

            async for event in convert_stream(resp.aiter_lines(), model):
                yield event

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
```

关键变更点：
- `create_router(config)` → `create_router()`
- `create_response` 内 `config = request.app.state.config`
- `ws_responses` 内直接调 `handle_websocket(ws)` 不传 config
- `_handle_non_streaming` 和 `_handle_streaming` 新增 `timeout: float` 参数
- `client.post(..., timeout=timeout)` 和 `client.stream(..., timeout=timeout)` 逐请求传入

- [ ] **Step 2: 验证语法正确**

Run: `python -c "from codex_router.router import create_router; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add codex_router/router.py
git commit -m "refactor: router reads config from app.state, per-request timeout"
```

---

### Task 4: 重构 ws_handler.py — 动态 config + 逐请求 timeout

**Files:**
- Modify: `codex_router/ws_handler.py`

- [ ] **Step 1: 修改 handle_websocket 和 _handle_response_create**

将 `ws_handler.py` 替换为：

```python
"""WebSocket handler for /v1/responses."""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import urlparse

from fastapi import WebSocket, WebSocketDisconnect

from codex_router.config import ProxyConfig
from codex_router.converters.request import convert_request
from codex_router.converters.streaming import convert_stream_events
from codex_router.models import ResponsesRequest

logger = logging.getLogger(__name__)

_ALLOWED_HOSTNAMES = {"localhost", "127.0.0.1"}


def _is_origin_allowed(origin: str | None) -> bool:
    """Check if the Origin header is from a trusted local source."""
    if origin is None:
        return True
    try:
        parsed = urlparse(origin)
        return parsed.hostname in _ALLOWED_HOSTNAMES
    except Exception:
        return False


def build_upstream_headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


def build_upstream_url(config: ProxyConfig) -> str:
    return f"{config.upstream.base_url.rstrip('/')}/chat/completions"


async def handle_websocket(ws: WebSocket):
    origin = ws.headers.get("origin")
    if not _is_origin_allowed(origin):
        logger.warning("Rejecting WebSocket from untrusted origin: %s", origin)
        await ws.close(code=4403, reason="Origin not allowed")
        return

    await ws.accept()

    try:
        while True:
            raw = await ws.receive_text()
            try:
                event = json.loads(raw)
            except json.JSONDecodeError as e:
                logger.warning("Invalid JSON from WebSocket: %s", e)
                await ws.send_json({"type": "error", "message": f"Invalid JSON: {e}"})
                continue

            # 每次事件循环获取最新配置
            config: ProxyConfig = ws.app.state.config
            event_type = event.get("type", "")
            if event_type == "response.create":
                await _handle_response_create(ws, event, config)
            elif event_type == "response.cancel":
                logger.info("Received response.cancel (not implemented)")
            else:
                logger.warning("Unknown WebSocket event type: %s", event_type)

    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected")
    except Exception:
        logger.exception("WebSocket error")
        try:
            await ws.close(code=1011, reason="Internal server error")
        except Exception:
            pass


async def _handle_response_create(ws: WebSocket, event: dict[str, Any], config: ProxyConfig):
    params = {k: v for k, v in event.items() if k != "type"}

    try:
        resp_req = ResponsesRequest(**params)
    except Exception as e:
        await ws.send_json({"type": "error", "message": f"Invalid request: {e}"})
        return

    api_key = config.upstream.api_key
    if not api_key:
        await ws.send_json({"type": "error", "message": "No API key configured"})
        return

    cc_req = convert_request(resp_req, config)
    cc_req["stream"] = True

    headers = build_upstream_headers(api_key)
    upstream_url = build_upstream_url(config)
    client: Any = ws.app.state.http_client
    timeout = config.upstream.timeout

    try:
        async with client.stream("POST", upstream_url, json=cc_req, headers=headers, timeout=timeout) as resp:
            if resp.status_code != 200:
                error_body = await resp.aread()
                decoded = error_body.decode(errors="replace")
                logger.error("Upstream WS error %d: %s", resp.status_code, decoded[:2000])
                await ws.send_json({
                    "type": "response.failed",
                    "response": {
                        "status": "failed",
                        "error": {"message": f"Upstream returned {resp.status_code}"},
                    },
                })
                return

            async for event_type, data in convert_stream_events(resp.aiter_lines(), resp_req.model):
                await ws.send_json(data)
    except Exception:
        logger.exception("Upstream request failed in WebSocket")
        try:
            await ws.send_json({
                "type": "response.failed",
                "response": {
                    "status": "failed",
                    "error": {"message": "Upstream request failed"},
                },
            })
        except Exception:
            pass
```

关键变更点：
- `handle_websocket(ws, config)` → `handle_websocket(ws)`
- while 循环内每次 `config = ws.app.state.config`
- `_handle_response_create` 接收由调用方传入的最新 config，不再覆盖
- `client.stream(..., timeout=timeout)` 逐请求传入

- [ ] **Step 2: 验证语法正确**

Run: `python -c "from codex_router.ws_handler import handle_websocket; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add codex_router/ws_handler.py
git commit -m "refactor: ws_handler reads config from app.state per-event, per-request timeout"
```

---

### Task 5: 重构 main.py — 集成 ConfigManager 和 Admin 路由

**Files:**
- Modify: `codex_router/main.py`

- [ ] **Step 1: 修改 create_app 和 main**

```python
"""Codex Router — local proxy converting Responses API to Chat Completions API."""

import atexit
import logging
import signal

import httpx
import uvicorn
from fastapi import FastAPI

from codex_router.codex_config import restore_codex
from codex_router.config import ProxyConfig, load_config
from codex_router.config_manager import ConfigManager
from codex_router.errors import register_error_handlers
from codex_router.router import create_router

logger = logging.getLogger(__name__)

_backup = None
_config = None
_config_path = None


def create_app(config: ProxyConfig | None = None, config_path=None) -> FastAPI:
    """Create and configure the FastAPI application."""
    global _config, _config_path
    if config is not None:
        _config = config
        _config_path = config_path
    elif _config is None:
        _config, _config_path = load_config()
    config = _config

    app = FastAPI(title="Codex Router", lifespan=_lifespan, websocket_max_size=10 * 1024 * 1024)

    cm = ConfigManager(config, _config_path)
    app.state.config = config
    app.state.config_manager = cm

    register_error_handlers(app)
    app.include_router(create_router())

    from codex_router.admin import create_admin_router
    app.include_router(create_admin_router())

    return app


async def _lifespan(app: FastAPI):
    """Manage application lifespan: create shared client on startup, close on shutdown."""
    config: ProxyConfig = app.state.config
    app.state.http_client = httpx.AsyncClient(
        timeout=config.upstream.timeout,
        limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
    )
    logger.info("Shared httpx.AsyncClient created (timeout=%ss)", config.upstream.timeout)
    yield
    await app.state.http_client.aclose()
    logger.info("Shared httpx.AsyncClient closed")


def _restore_on_exit():
    """Restore Codex CLI config on process exit."""
    global _backup, _config
    if _backup is not None and _config is not None:
        try:
            restore_codex(_config, _backup)
            logger.info("Codex CLI config restored (atexit)")
        except Exception:
            logger.exception("Failed to restore Codex CLI config on exit")


def _signal_handler(signum, frame):
    """Handle termination signals."""
    _restore_on_exit()
    raise SystemExit(128 + signum)


def main():
    global _backup, _config, _config_path

    config, config_path = load_config()
    _config = config
    _config_path = config_path

    logging.basicConfig(
        level=getattr(logging, config.server.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if config.codex.auto_configure:
        from codex_router.codex_config import backup_codex, configure_codex

        _backup = backup_codex(config)
        configure_codex(config)

        atexit.register(_restore_on_exit)
        signal.signal(signal.SIGTERM, _signal_handler)
        signal.signal(signal.SIGINT, _signal_handler)

    uvicorn.run(
        "codex_router.main:create_app",
        factory=True,
        host=config.server.host,
        port=config.server.port,
        log_level=config.server.log_level.lower(),
    )


if __name__ == "__main__":
    main()
```

关键变更点：
- `load_config()` 返回 `(config, path)` 元组
- `create_app` 内创建 `ConfigManager` 并挂到 `app.state.config_manager`
- `app.state.config` 和 `cm.config` 指向同一个 ProxyConfig 对象
- 新增 `app.include_router(create_admin_router())`
- `create_router()` 不再传 config

- [ ] **Step 2: 验证语法正确（此时 admin 模块还不存在，会报错）**

先跳过此步，Task 6 创建 admin.py 后统一验证。

- [ ] **Step 3: Commit**

```bash
git add codex_router/main.py
git commit -m "refactor: main integrates ConfigManager, admin router, load_config returns path"
```

---

### Task 6: 创建 admin.py — 管理 API 路由

**Files:**
- Create: `codex_router/admin.py`

- [ ] **Step 1: 创建 admin.py**

```python
"""Admin API routes and Web UI for Codex Router management panel."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse

from codex_router.config_manager import ConfigManager
from codex_router.config import PresetConfig, ProxyConfig

logger = logging.getLogger(__name__)

_admin_html: str | None = None


def _load_admin_html() -> str:
    global _admin_html
    if _admin_html is None:
        html_path = Path(__file__).parent / "static" / "admin.html"
        if html_path.exists():
            _admin_html = html_path.read_text(encoding="utf-8")
        else:
            _admin_html = "<html><body><h1>Admin UI not found</h1></body></html>"
    return _admin_html


def _mask_key(key: str) -> str:
    if not key or len(key) <= 8:
        return "****"
    return key[:4] + "****" + key[-4:]


def _mask_preset(preset: PresetConfig) -> dict[str, Any]:
    d = preset.model_dump()
    d["api_key"] = _mask_key(d["api_key"])
    return d


async def _local_only(request: Request):
    if request.client.host not in ("127.0.0.1", "::1"):
        raise HTTPException(status_code=403, detail="管理接口仅限本地访问")


def create_admin_router() -> APIRouter:
    router = APIRouter(prefix="/admin")

    # ── Web UI ──

    @router.get("/", response_class=HTMLResponse)
    async def admin_page():
        return HTMLResponse(content=_load_admin_html())

    # ── Config ──

    @router.get("/api/config", dependencies=[Depends(_local_only)])
    async def get_config(request: Request):
        cm: ConfigManager = request.app.state.config_manager
        config: ProxyConfig = cm.config
        return {
            "active_preset": cm.active_preset_name,
            "upstream": {
                "base_url": config.upstream.base_url,
                "api_key": _mask_key(config.upstream.api_key),
                "timeout": config.upstream.timeout,
            },
            "model_override": config.model_override,
            "server": {
                "host": config.server.host,
                "port": config.server.port,
                "log_level": config.server.log_level,
            },
            "codex": {"auto_configure": config.codex.auto_configure},
            "ignored_builtin_tools": config.ignored_builtin_tools,
        }

    # ── Presets ──

    @router.get("/api/presets", dependencies=[Depends(_local_only)])
    async def list_presets(request: Request):
        cm: ConfigManager = request.app.state.config_manager
        return [_mask_preset(p) for p in cm.list_presets()]

    @router.post("/api/presets", dependencies=[Depends(_local_only)])
    async def add_preset(request: Request):
        cm: ConfigManager = request.app.state.config_manager
        body = await request.json()
        try:
            preset = PresetConfig(**body)
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))
        try:
            await cm.add_preset(preset)
        except ValueError as e:
            raise HTTPException(status_code=409, detail=str(e))
        return {"ok": True, "message": f"预设 {preset.name} 已添加"}

    @router.put("/api/presets/{name}", dependencies=[Depends(_local_only)])
    async def update_preset(name: str, request: Request):
        cm: ConfigManager = request.app.state.config_manager
        body = await request.json()
        try:
            updated = await cm.update_preset(name, body)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))
        return {"ok": True, "message": f"预设 {name} 已更新", "preset": _mask_preset(updated)}

    @router.delete("/api/presets/{name}", dependencies=[Depends(_local_only)])
    async def delete_preset(name: str, request: Request):
        cm: ConfigManager = request.app.state.config_manager
        try:
            await cm.delete_preset(name)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {"ok": True, "message": f"预设 {name} 已删除"}

    # ── Activate ──

    @router.post("/api/presets/{name}/activate", dependencies=[Depends(_local_only)])
    async def activate_preset(name: str, request: Request):
        cm: ConfigManager = request.app.state.config_manager
        try:
            await cm.activate_preset(name)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))
        return {
            "ok": True,
            "message": f"已切换到预设 {name}",
            "active_preset": name,
            "model": cm.config.model_override,
        }

    # ── Verify ──

    @router.post("/api/presets/{name}/verify", dependencies=[Depends(_local_only)])
    async def verify_preset(name: str, request: Request):
        cm: ConfigManager = request.app.state.config_manager
        try:
            result = await cm.verify_preset(name)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))
        return {"ok": result.ok, "message": result.message, "latency_ms": round(result.latency_ms)}

    # ── Settings ──

    @router.put("/api/settings", dependencies=[Depends(_local_only)])
    async def update_settings(request: Request):
        cm: ConfigManager = request.app.state.config_manager
        body = await request.json()
        config = cm.config

        if "ignored_builtin_tools" in body:
            config.ignored_builtin_tools = body["ignored_builtin_tools"]
        if "timeout" in body:
            config.upstream.timeout = float(body["timeout"])
        if "auto_configure" in body:
            config.codex.auto_configure = bool(body["auto_configure"])

        await cm.save_config()
        return {"ok": True, "message": "配置已更新"}

    # ── Logs ──

    @router.get("/api/logs", dependencies=[Depends(_local_only)])
    async def get_logs(request: Request):
        cm: ConfigManager = request.app.state.config_manager
        return cm.get_logs()

    return router
```

- [ ] **Step 2: 创建 static 目录**

Run: `mkdir -p codex_router/static`

- [ ] **Step 3: 创建 admin.html 占位（确保 import 不报错）**

```bash
echo '<!DOCTYPE html><html><body><h1>Admin Panel (placeholder)</h1></body></html>' > codex_router/static/admin.html
```

- [ ] **Step 4: 验证全部 import 正确**

Run: `python -c "from codex_router.main import create_app; print('OK')"`
Expected: `OK`

- [ ] **Step 5: 验证代理路由仍正常启动**

Run: `python -c "from codex_router.main import create_app; app = create_app(); print('Routes:', [r.path for r in app.routes])"`
Expected: 输出包含 `/v1/responses` 和 `/admin/` 路由

- [ ] **Step 6: Commit**

```bash
git add codex_router/admin.py codex_router/static/
git commit -m "feat: add admin API routes with preset CRUD, hot-swap, verify, settings, logs"
```

---

### Task 7: 创建 admin.html — Web 管理面板 UI

**Files:**
- Create: `codex_router/static/admin.html`

- [ ] **Step 1: 创建完整的 admin.html**

```html
<!DOCTYPE html>
<html lang="zh-CN" data-theme="light">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Codex Router 管理面板</title>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@picocss/pico@2/css/pico.min.css">
    <style>
        body { max-width: 900px; margin: 0 auto; padding: 1rem; }
        .header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 1.5rem; }
        .header h1 { margin: 0; font-size: 1.4rem; }
        .status-dot { display: inline-block; width: 10px; height: 10px; border-radius: 50%; background: #4caf50; margin-right: 6px; }
        .status-text { font-size: 0.85rem; color: #666; }
        .card { border: 1px solid #ddd; border-radius: 8px; padding: 1rem 1.25rem; margin-bottom: 1.25rem; }
        .card h2 { margin: 0 0 0.75rem 0; font-size: 1rem; color: #555; }
        .config-grid { display: grid; grid-template-columns: auto 1fr; gap: 0.35rem 1rem; font-size: 0.9rem; }
        .config-grid dt { color: #888; font-weight: normal; }
        .config-grid dd { margin: 0; font-weight: 500; }
        table { width: 100%; font-size: 0.85rem; }
        table th { font-size: 0.8rem; text-transform: uppercase; color: #888; }
        .active-badge { color: #4caf50; font-weight: bold; }
        .btn-sm { font-size: 0.8rem; padding: 0.2rem 0.6rem; margin-right: 0.3rem; }
        .inline-msg { font-size: 0.85rem; margin-top: 0.5rem; padding: 0.4rem 0.8rem; border-radius: 4px; display: none; }
        .inline-msg.success { display: block; background: #e8f5e9; color: #2e7d32; }
        .inline-msg.error { display: block; background: #fce4ec; color: #c62828; }
        details summary { cursor: pointer; font-size: 0.9rem; color: #555; }
        .log-entry { font-size: 0.8rem; color: #666; padding: 0.15rem 0; border-bottom: 1px solid #f0f0f0; }
        dialog { max-width: 480px; }
        dialog form { margin: 0; }
        .form-hint { font-size: 0.75rem; color: #999; margin-top: 0.2rem; }
    </style>
</head>
<body>
    <div class="header">
        <h1>Codex Router 管理面板</h1>
        <span class="status-text"><span class="status-dot"></span>运行中</span>
    </div>

    <!-- 当前配置 -->
    <div class="card">
        <h2>当前配置</h2>
        <dl class="config-grid" id="current-config">
            <dt>预设</dt><dd id="cfg-preset">-</dd>
            <dt>模型</dt><dd id="cfg-model">-</dd>
            <dt>上游</dt><dd id="cfg-upstream">-</dd>
            <dt>密钥</dt><dd id="cfg-key">-</dd>
            <dt>超时</dt><dd id="cfg-timeout">-</dd>
        </dl>
    </div>

    <!-- 模型预设 -->
    <div class="card">
        <h2>模型预设</h2>
        <div style="overflow-x:auto;">
            <table>
                <thead><tr><th></th><th>名称</th><th>上游地址</th><th>模型</th><th>操作</th></tr></thead>
                <tbody id="preset-list"></tbody>
            </table>
        </div>
        <div style="margin-top:0.75rem;">
            <button class="btn-sm secondary" onclick="openAddDialog()">+ 添加预设</button>
        </div>
        <div class="inline-msg" id="preset-msg"></div>
    </div>

    <!-- 通用配置 -->
    <div class="card">
        <h2>通用配置</h2>
        <div class="grid">
            <label>超时 (秒)
                <input type="number" id="set-timeout" min="10" max="600" step="10">
            </label>
            <label>自动配置 Codex
                <input type="checkbox" id="set-auto-configure">
            </label>
        </div>
        <label>过滤工具 <span style="font-size:0.75rem;color:#999">(逗号分隔)</span>
            <input type="text" id="set-tools">
        </label>
        <div style="margin-top:0.75rem;">
            <button class="btn-sm" onclick="saveSettings()">保存</button>
            <div class="inline-msg" id="settings-msg"></div>
        </div>
    </div>

    <!-- 操作日志 -->
    <div class="card">
        <details id="logs-details">
            <summary>操作日志</summary>
            <div id="log-list" style="margin-top:0.5rem;"></div>
        </details>
    </div>

    <!-- 添加/编辑模态框 -->
    <dialog id="preset-dialog">
        <article>
            <h3 id="dialog-title">添加预设</h3>
            <form id="preset-form" onsubmit="return false;">
                <label>名称 <input type="text" id="f-name" required></label>
                <label>上游地址 <input type="url" id="f-base-url" required placeholder="https://api.example.com/v1"></label>
                <label>API Key <input type="text" id="f-api-key">
                    <span class="form-hint">留空则保留原密钥</span>
                </label>
                <label>模型 <input type="text" id="f-model" required></label>
                <label>超时 (秒) <input type="number" id="f-timeout" value="120" min="10" max="600"></label>
                <div class="grid" style="margin-top:1rem;">
                    <button class="outline" type="button" onclick="closeDialog()">取消</button>
                    <button type="button" onclick="submitPreset()">保存</button>
                </div>
            </form>
        </article>
    </dialog>

<script>
const API = '/admin/api';
let _editingName = null;

async function api(path, opts = {}) {
    const res = await fetch(API + path, opts);
    return res.json();
}

function showMsg(id, text, ok) {
    const el = document.getElementById(id);
    el.textContent = text;
    el.className = 'inline-msg ' + (ok ? 'success' : 'error');
    setTimeout(() => { el.className = 'inline-msg'; }, 3000);
}

async function loadConfig() {
    const cfg = await api('/config');
    document.getElementById('cfg-preset').textContent = cfg.active_preset || '-';
    document.getElementById('cfg-model').textContent = cfg.model_override || '-';
    document.getElementById('cfg-upstream').textContent = cfg.upstream.base_url;
    document.getElementById('cfg-key').textContent = cfg.upstream.api_key;
    document.getElementById('cfg-timeout').textContent = cfg.upstream.timeout + 's';
    document.getElementById('set-timeout').value = cfg.upstream.timeout;
    document.getElementById('set-auto-configure').checked = cfg.codex.auto_configure;
    document.getElementById('set-tools').value = (cfg.ignored_builtin_tools || []).join(', ');
}

async function loadPresets() {
    const presets = await api('/presets');
    const cfg = await api('/config');
    const tbody = document.getElementById('preset-list');
    tbody.innerHTML = presets.map(p => {
        const active = p.name === cfg.active_preset;
        return `<tr>
            <td>${active ? '<span class="active-badge">★</span>' : ''}</td>
            <td>${esc(p.name)}</td>
            <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(p.base_url)}</td>
            <td>${esc(p.model)}</td>
            <td>
                ${active
                    ? `<button class="btn-sm outline" onclick="openEditDialog('${esc(p.name)}')">编辑</button>
                       <button class="btn-sm outline" onclick="deletePreset('${esc(p.name)}')">删除</button>`
                    : `<button class="btn-sm outline" onclick="verifyPreset('${esc(p.name)}')">验证</button>
                       <button class="btn-sm" onclick="activatePreset('${esc(p.name)}')">切换</button>`
                }
            </td>
        </tr>`;
    }).join('');
}

function esc(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }

async function activatePreset(name) {
    const vr = await api(`/presets/${encodeURIComponent(name)}/verify`, { method: 'POST' });
    const msg = vr.ok ? `验证成功 (${vr.latency_ms}ms)。确认切换到 ${name}?` : `验证失败: ${vr.message}。仍然切换到 ${name}?`;
    if (!confirm(msg)) return;
    const res = await api(`/presets/${encodeURIComponent(name)}/activate`, { method: 'POST' });
    showMsg('preset-msg', res.message, res.ok);
    loadConfig();
    loadPresets();
}

async function verifyPreset(name) {
    const res = await api(`/presets/${encodeURIComponent(name)}/verify`, { method: 'POST' });
    showMsg('preset-msg', `${name}: ${res.message} (${res.latency_ms}ms)`, res.ok);
}

async function deletePreset(name) {
    if (!confirm(`确认删除预设 ${name}?`)) return;
    const res = await api(`/presets/${encodeURIComponent(name)}`, { method: 'DELETE' });
    showMsg('preset-msg', res.message, res.ok);
    loadPresets();
}

function openAddDialog() {
    _editingName = null;
    document.getElementById('dialog-title').textContent = '添加预设';
    document.getElementById('f-name').value = '';
    document.getElementById('f-name').disabled = false;
    document.getElementById('f-base-url').value = '';
    document.getElementById('f-api-key').value = '';
    document.getElementById('f-model').value = '';
    document.getElementById('f-timeout').value = '120';
    document.getElementById('preset-dialog').showModal();
}

async function openEditDialog(name) {
    const presets = await api('/presets');
    const p = presets.find(x => x.name === name);
    if (!p) return;
    _editingName = name;
    document.getElementById('dialog-title').textContent = '编辑预设';
    document.getElementById('f-name').value = p.name;
    document.getElementById('f-name').disabled = true;
    document.getElementById('f-base-url').value = p.base_url;
    document.getElementById('f-api-key').value = '';
    document.getElementById('f-api-key').placeholder = p.api_key;
    document.getElementById('f-model').value = p.model;
    document.getElementById('f-timeout').value = p.timeout;
    document.getElementById('preset-dialog').showModal();
}

function closeDialog() {
    document.getElementById('preset-dialog').close();
}

async function submitPreset() {
    const data = {
        base_url: document.getElementById('f-base-url').value,
        api_key: document.getElementById('f-api-key').value,
        model: document.getElementById('f-model').value,
        timeout: parseFloat(document.getElementById('f-timeout').value) || 120,
    };
    if (_editingName) {
        const res = await api(`/presets/${encodeURIComponent(_editingName)}`, {
            method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)
        });
        showMsg('preset-msg', res.message, res.ok);
    } else {
        data.name = document.getElementById('f-name').value;
        const res = await api('/presets', {
            method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)
        });
        showMsg('preset-msg', res.message, res.ok);
    }
    closeDialog();
    loadConfig();
    loadPresets();
}

async function saveSettings() {
    const data = {
        timeout: parseFloat(document.getElementById('set-timeout').value),
        auto_configure: document.getElementById('set-auto-configure').checked,
        ignored_builtin_tools: document.getElementById('set-tools').value.split(',').map(s => s.trim()).filter(Boolean),
    };
    const res = await api('/settings', {
        method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)
    });
    showMsg('settings-msg', res.message, res.ok);
    loadConfig();
}

async function loadLogs() {
    const logs = await api('/logs');
    const el = document.getElementById('log-list');
    if (!logs.length) { el.innerHTML = '<div style="color:#999;font-size:0.8rem;">暂无操作日志</div>'; return; }
    el.innerHTML = logs.map(l => {
        const t = new Date(l.timestamp * 1000).toLocaleTimeString();
        return `<div class="log-entry">${t} ${esc(l.action)}: ${esc(l.detail)}</div>`;
    }).reverse().join('');
}

document.getElementById('logs-details').addEventListener('toggle', function() {
    if (this.open) loadLogs();
});

loadConfig();
loadPresets();
</script>
</body>
</html>
```

- [ ] **Step 2: 验证 HTML 加载正常**

Run: `python -c "from pathlib import Path; p = Path('codex_router/static/admin.html'); print(p.exists(), len(p.read_text()))"`
Expected: `True <正整数>`

- [ ] **Step 3: Commit**

```bash
git add codex_router/static/admin.html
git commit -m "feat: add admin.html Web UI with Pico CSS, preset management, hot-swap"
```

---

### Task 8: 更新 config.yaml.example

**Files:**
- Modify: `config.yaml.example`

- [ ] **Step 1: 在 config.yaml.example 末尾添加 presets 示例**

在文件末尾 `codex:` 段之后追加：

```yaml

# 模型预设（可选）：预定义多套上游配置，通过 Web 管理面板一键切换
# presets:
#   - name: "DeepSeek"
#     base_url: "https://api.deepseek.com/v1"
#     api_key: "sk-xxx"
#     model: "deepseek-chat"
#     timeout: 120
#   - name: "Qwen"
#     base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1"
#     api_key: "sk-yyy"
#     model: "qwen-plus"
#     timeout: 120
# active_preset: "DeepSeek"
```

- [ ] **Step 2: Commit**

```bash
git add config.yaml.example
git commit -m "docs: add presets example to config.yaml.example"
```

---

### Task 9: 端到端验证

- [ ] **Step 1: 启动 Router 并验证管理面板可访问**

准备一个测试用 config.yaml（使用真实 API key），然后：

```bash
cd D:/niusulong/AI/codex_router
python -m codex_router
```

在浏览器打开 `http://127.0.0.1:8080/admin/`，验证：
- 页面正常加载，有 Pico CSS 样式
- 当前配置区显示正确信息
- 预设列表显示 "default" 预设

- [ ] **Step 2: 验证 API 接口**

```bash
curl http://127.0.0.1:8080/admin/api/config
curl http://127.0.0.1:8080/admin/api/presets
```

验证返回 JSON 数据正确，API Key 已脱敏。

- [ ] **Step 3: 验证添加预设 + 切换**

通过 UI 添加一个测试预设，点击切换，验证：
- 当前配置区更新
- config.yaml 文件已更新
- `~/.codex/config.toml` 中 model 字段已更新
- `~/.codex/auth.json` 中 API key 已更新

- [ ] **Step 4: 验证代理功能正常**

使用 Codex CLI 发送请求，确认代理仍然正常工作（HTTP POST 和 WebSocket）。

- [ ] **Step 5: Final commit (如有修复)**

```bash
git add -A
git commit -m "fix: address issues found during e2e verification"
```

---

## Self-Review

**Spec coverage check:**

| Spec Section | Task |
|---|---|
| PresetConfig with created_at | Task 1 |
| ProxyConfig.presets/active_preset | Task 1 |
| load_config returns path | Task 1 |
| save_to_file | Task 1 |
| ConfigManager full interface | Task 2 |
| verify_preset | Task 2 |
| save_config with dict→list sync | Task 2 |
| Object reference guarantee | Task 5 (create_app sets both) |
| Operation logs deque + get_logs | Task 2 |
| Router dynamic config read | Task 3 |
| Per-request timeout | Task 3, Task 4 |
| WS handler dynamic config | Task 4 |
| main.py ConfigManager init | Task 5 |
| Admin API all endpoints | Task 6 |
| _local_only access control | Task 6 |
| API Key masking + edit protection | Task 6 (mask) + Task 2 (update_preset) |
| PUT /admin/api/settings | Task 6 |
| GET /admin/api/logs | Task 6 |
| admin.html with Pico CSS | Task 7 |
| config.yaml.example update | Task 8 |

**Placeholder scan:** No TBD, TODO, or "implement later" patterns found.

**Type consistency:**
- `PresetConfig` fields consistent across config.py, config_manager.py, admin.py
- `ConfigManager` interface matches between definition (Task 2) and usage (Task 6)
- `create_router()` no-param signature matches call in Task 5
- `handle_websocket(ws)` no-config signature matches call in Task 3
- `_handle_non_streaming` and `_handle_streaming` `timeout: float` param added consistently
