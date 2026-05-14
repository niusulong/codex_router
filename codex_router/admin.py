"""Admin API routes and Web UI for Codex Router management panel."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from codex_router.config import PresetConfig, ProxyConfig
from codex_router.config_manager import ConfigManager

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
                "api_format": config.upstream.api_format,
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
        if "api_format" in body:
            config.upstream.api_format = body["api_format"]

        await cm.save_config()
        return {"ok": True, "message": "配置已更新"}

    # ── Logs ──

    @router.get("/api/logs", dependencies=[Depends(_local_only)])
    async def get_logs(request: Request):
        cm: ConfigManager = request.app.state.config_manager
        return cm.get_logs()

    return router
