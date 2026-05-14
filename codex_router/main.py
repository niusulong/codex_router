"""FastAPI application and entry point."""

from __future__ import annotations

import logging
import sys

import uvicorn
from fastapi import FastAPI

from codex_router.codex_config import backup_codex_auth, configure_codex, restore_codex
from codex_router.config import load_config
from codex_router.errors import register_error_handlers
from codex_router.router import create_router

_auth_backup: dict | None = None


def create_app() -> FastAPI:
    config = load_config()
    app = FastAPI(title="codex_router", version="0.1.0")
    app.state.config = config
    register_error_handlers(app)
    app.include_router(create_router(config))
    return app


def main() -> None:
    global _auth_backup

    config = load_config()
    logging.basicConfig(
        level=getattr(logging, config.server.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # Configure Codex CLI if enabled
    if config.codex.auto_configure:
        _auth_backup = backup_codex_auth(config)
        configure_codex(config)

    logger = logging.getLogger("codex_router")
    logger.info(
        "Proxy starting: %s:%s -> %s (model=%s)",
        config.server.host,
        config.server.port,
        config.upstream.base_url,
        config.model_override or "passthrough",
    )

    try:
        uvicorn.run(
            "codex_router.main:create_app",
            factory=True,
            host=config.server.host,
            port=config.server.port,
            log_level=config.server.log_level,
        )
    except KeyboardInterrupt:
        logger.info("Shutting down")
    finally:
        # Restore Codex auth on exit
        if config.codex.auto_configure and _auth_backup is not None:
            restore_codex(config, _auth_backup)
            logger.info("Codex CLI configuration restored")


if __name__ == "__main__":
    main()