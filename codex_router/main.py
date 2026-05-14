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
