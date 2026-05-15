"""Custom exceptions and FastAPI exception handlers."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class UpstreamError(Exception):
    """Error communicating with the upstream API."""

    def __init__(self, message: str, status_code: int = 502):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def register_error_handlers(app: FastAPI) -> None:
    """Register exception handlers on the FastAPI app."""

    @app.exception_handler(UpstreamError)
    async def upstream_error_handler(request, exc: UpstreamError):
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "type": "upstream_error",
                    "message": exc.message,
                }
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request, exc: RequestValidationError):
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "type": "invalid_request_error",
                    "message": str(exc),
                }
            },
        )
