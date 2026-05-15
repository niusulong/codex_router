"""Admin API routes and Web UI for Codex Router management panel."""

from __future__ import annotations

import json as _json
import logging
import platform
import sys
from io import BytesIO
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

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
                "api_format": config.upstream.api_format,
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
        if "api_format" in body:
            config.upstream.api_format = body["api_format"]

        await cm.save_config()
        return {"ok": True, "message": "配置已更新"}

    # ── Logs ──

    @router.get("/api/logs", dependencies=[Depends(_local_only)])
    async def get_logs(request: Request):
        cm: ConfigManager = request.app.state.config_manager
        return cm.get_logs()

    # ── Stats ──

    @router.get("/api/stats", dependencies=[Depends(_local_only)])
    async def get_stats(request: Request):
        from codex_router.stats import RequestStats
        stats: RequestStats | None = getattr(request.app.state, "request_stats", None)
        if stats is None:
            return {"uptime_seconds": 0, "total_requests": 0, "success_count": 0, "fail_count": 0, "active_connections": 0, "avg_latency_ms": 0, "last_request_at": None}
        return stats.get_summary()

    @router.get("/api/stats/requests", dependencies=[Depends(_local_only)])
    async def get_stats_requests(request: Request):
        from codex_router.stats import RequestStats
        stats: RequestStats | None = getattr(request.app.state, "request_stats", None)
        if stats is None:
            return []
        return stats.get_recent(100)

    # ── Export / Import ──

    @router.get("/api/stats/export", dependencies=[Depends(_local_only)])
    async def export_config(request: Request):
        cm: ConfigManager = request.app.state.config_manager
        presets = [p.model_dump() for p in cm.list_presets()]
        data = {
            "presets": presets,
            "active_preset": cm.active_preset_name,
            "ignored_builtin_tools": cm.config.ignored_builtin_tools,
            "timeout": cm.config.upstream.timeout,
            "api_format": cm.config.upstream.api_format,
        }
        content = _json.dumps(data, indent=2, ensure_ascii=False)
        buf = BytesIO(content.encode("utf-8"))
        return StreamingResponse(
            buf,
            media_type="application/json",
            headers={"Content-Disposition": "attachment; filename=codex_router_config.json"},
        )

    @router.post("/api/stats/import", dependencies=[Depends(_local_only)])
    async def import_config(request: Request):
        cm: ConfigManager = request.app.state.config_manager
        body = await request.json()
        presets_data = body.get("presets", [])
        imported = 0
        skipped = 0
        for p_data in presets_data:
            try:
                preset = PresetConfig(**p_data)
            except Exception:
                skipped += 1
                continue
            if preset.name in [p.name for p in cm.list_presets()]:
                skipped += 1
                continue
            await cm.add_preset(preset)
            imported += 1

        if "ignored_builtin_tools" in body:
            cm.config.ignored_builtin_tools = body["ignored_builtin_tools"]
        if "timeout" in body:
            cm.config.upstream.timeout = float(body["timeout"])
        if "api_format" in body:
            cm.config.upstream.api_format = body["api_format"]
        await cm.save_config()

        return {"ok": True, "message": f"导入完成: {imported} 个预设已添加, {skipped} 个已跳过", "imported": imported, "skipped": skipped}

    # ── Restore Codex ──

    @router.post("/api/codex/restore", dependencies=[Depends(_local_only)])
    async def restore_codex_config(request: Request):
        from codex_router.main import _backup, _config
        if _backup is not None and _config is not None:
            try:
                from codex_router.codex_config import restore_codex
                restore_codex(_config, _backup)
                return {"ok": True, "message": "Codex CLI 原始配置已恢复"}
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"恢复失败: {e}")
        return {"ok": True, "message": "无备份需要恢复（未启用自动配置或已恢复）"}

    # ── System Info ──

    @router.get("/api/system", dependencies=[Depends(_local_only)])
    async def get_system_info(request: Request):
        import os
        try:
            import psutil
            mem = psutil.virtual_memory()
            mem_info = {"total_mb": round(mem.total / 1024 / 1024), "used_mb": round(mem.used / 1024 / 1024), "percent": mem.percent}
        except ImportError:
            mem_info = None

        try:
            from codex_router import __version__
            version = __version__
        except Exception:
            version = "dev"

        return {
            "python_version": sys.version.split()[0],
            "router_version": version,
            "platform": platform.system(),
            "platform_release": platform.release(),
            "memory": mem_info,
        }

    # ── Token Usage ──

    @router.get("/api/token/summary", dependencies=[Depends(_local_only)])
    async def get_token_summary(request: Request):
        from codex_router.token_db import TokenDB
        token_db: TokenDB | None = getattr(request.app.state, "token_db", None)
        if token_db is None:
            return {"totals": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "request_count": 0}, "by_preset": []}
        return {"totals": token_db.get_total(), "by_preset": token_db.get_by_preset()}

    @router.get("/api/token/timeseries", dependencies=[Depends(_local_only)])
    async def get_token_timeseries(request: Request, period: str = "daily", days: int = 30):
        from codex_router.token_db import TokenDB
        token_db: TokenDB | None = getattr(request.app.state, "token_db", None)
        if token_db is None:
            return []
        if period == "weekly":
            return token_db.get_weekly(weeks=days)
        elif period == "monthly":
            return token_db.get_monthly(months=days)
        else:
            return token_db.get_daily(days=days)

    @router.get("/api/token/hourly", dependencies=[Depends(_local_only)])
    async def get_token_hourly(request: Request, date: str | None = None):
        from codex_router.token_db import TokenDB
        token_db: TokenDB | None = getattr(request.app.state, "token_db", None)
        if token_db is None:
            return []
        return token_db.get_hourly_curve(date)

    return router
