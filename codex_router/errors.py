"""Custom exceptions and error handlers."""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse


class ConversionError(Exception):
    """Raised when request/response conversion fails."""


class UpstreamError(Exception):
    """Raised when the upstream API returns an error."""


def register_error_handlers(app) -> None:
    """Register exception handlers on the FastAPI app."""

    @app.exception_handler(ConversionError)
    async def _conversion_error(request: Request, exc: ConversionError):
        return JSONResponse(
            status_code=400,
            content={"error": {"type": "invalid_request_error", "message": str(exc)}},
        )

    @app.exception_handler(UpstreamError)
    async def _upstream_error(request: Request, exc: UpstreamError):
        return JSONResponse(
            status_code=502,
            content={"error": {"type": "upstream_error", "message": str(exc)}},
        )
