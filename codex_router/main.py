"""Codex Router — local proxy converting Responses API to Chat Completions API."""

import argparse
import atexit
import json
import logging
import signal
import threading
import webbrowser
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from codex_router.codex_config import restore_codex
from codex_router.config import ProxyConfig, load_config
from codex_router.config_manager import ConfigManager
from codex_router.errors import register_error_handlers
from codex_router.response_store import ResponseStore
from codex_router.router import create_router
from codex_router.stats import RequestStats
from codex_router.version import __version__
from codex_router.token_db import TokenDB

logger = logging.getLogger(__name__)

_backup = None
_config: ProxyConfig | None = None
_config_path: Path | None = None


class UnicodeJSONResponse(JSONResponse):
    def render(self, content: Any) -> bytes:
        return json.dumps(
            content,
            ensure_ascii=False,
            allow_nan=False,
            indent=None,
            separators=(",", ":"),
        ).encode("utf-8")


def create_app(config: ProxyConfig | None = None, config_path: Path | None = None) -> FastAPI:
    """Create and configure the FastAPI application."""
    global _config, _config_path
    if config is not None:
        _config = config
        _config_path = config_path
    elif _config is None:
        _config, _config_path = load_config()
    config = _config

    app = FastAPI(title="Codex Router", version=__version__, lifespan=_lifespan, default_response_class=UnicodeJSONResponse, websocket_max_size=10 * 1024 * 1024)

    assert _config_path is not None
    cm = ConfigManager(config, _config_path)
    app.state.config = config
    app.state.config_manager = cm
    app.state.response_store = ResponseStore()
    db_path = _config_path.parent / "token_usage.db"
    token_db = TokenDB(db_path)
    app.state.token_db = token_db
    app.state.request_stats = RequestStats(token_db=token_db)

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
    logger.info("Codex Router v%s started", __version__)
    logger.info("Shared httpx.AsyncClient created (timeout=%ss)", config.upstream.timeout)
    yield
    await app.state.http_client.aclose()
    logger.info("Shared httpx.AsyncClient closed")
    token_db: TokenDB | None = getattr(app.state, "token_db", None)
    if token_db:
        token_db.close()
        logger.info("TokenDB closed")


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


def _get_active_preset(config: ProxyConfig):
    """Get the active preset from config."""
    if not config.active_preset or not config.presets:
        return None
    for p in config.presets:
        if p.name == config.active_preset:
            return p
    return None


def main():
    global _backup, _config, _config_path

    parser = argparse.ArgumentParser(description="Codex Router")
    parser.add_argument("--version", action="version", version=f"codex-router {__version__}")
    parser.add_argument("port", nargs="?", type=int, help="Port to listen on (overrides config)")
    args = parser.parse_args()

    config, config_path = load_config()
    if args.port is not None:
        config.server.port = args.port
    _config = config
    _config_path = config_path

    logging.basicConfig(
        level=getattr(logging, config.server.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    logger.info("Codex Router v%s starting...", __version__)

    if config.codex.auto_configure:
        from codex_router.codex_config import backup_codex, configure_codex

        _backup = backup_codex(config)
        active_preset = _get_active_preset(config)
        models_path = configure_codex(config, active_preset)
        if models_path:
            _backup.models_path = models_path

        atexit.register(_restore_on_exit)
        signal.signal(signal.SIGTERM, _signal_handler)
        signal.signal(signal.SIGINT, _signal_handler)

    # Open admin UI in browser after a short delay
    admin_url = f"http://{config.server.host}:{config.server.port}/admin/"

    def _open_browser():
        threading.Event().wait(1.5)
        webbrowser.open(admin_url)

    threading.Thread(target=_open_browser, daemon=True).start()

    uvicorn.run(
        "codex_router.main:create_app",
        factory=True,
        host=config.server.host,
        port=config.server.port,
        log_level=config.server.log_level.lower(),
    )


if __name__ == "__main__":
    main()
