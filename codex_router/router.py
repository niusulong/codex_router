"""FastAPI route handler for /v1/responses."""

from __future__ import annotations

import logging
from typing import Any, AsyncGenerator

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from codex_router.config import ProxyConfig
from codex_router.converters.request import convert_request
from codex_router.converters.response import convert_response
from codex_router.converters.streaming import convert_stream, sse_event
from codex_router.errors import UpstreamError
from codex_router.models import ResponsesRequest

logger = logging.getLogger(__name__)


def create_router(config: ProxyConfig) -> APIRouter:
    router = APIRouter()

    @router.post("/v1/responses")
    async def create_response(request: Request):
        # 1. Parse request body
        try:
            body = await request.json()
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}") from e

        try:
            resp_req = ResponsesRequest(**body)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid request: {e}") from e

        # 2. Determine API key
        api_key = config.upstream.api_key
        if not api_key and config.passthrough_api_key:
            auth_header = request.headers.get("authorization", "")
            if auth_header.startswith("Bearer "):
                api_key = auth_header[7:]

        if not api_key:
            raise HTTPException(status_code=401, detail="No API key available")

        # 3. Convert request
        cc_req = convert_request(resp_req, config)

        # 4. Build upstream request
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        upstream_url = f"{config.upstream.base_url.rstrip('/')}/chat/completions"

        # 5. Streaming vs non-streaming
        if resp_req.stream:
            return await _handle_streaming(upstream_url, headers, cc_req, config, resp_req.model)
        else:
            return await _handle_non_streaming(upstream_url, headers, cc_req, config, resp_req.model)

    return router


async def _handle_non_streaming(
    upstream_url: str,
    headers: dict[str, str],
    cc_req: dict[str, Any],
    config: ProxyConfig,
    model: str,
) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=config.upstream.timeout) as client:
        try:
            resp = await client.post(upstream_url, json=cc_req, headers=headers)
        except httpx.RequestError as e:
            raise UpstreamError(f"Upstream request failed: {e}") from e

    if resp.status_code != 200:
        raise UpstreamError(f"Upstream returned {resp.status_code}: {resp.text}")

    return convert_response(resp.json(), model)


async def _handle_streaming(
    upstream_url: str,
    headers: dict[str, str],
    cc_req: dict[str, Any],
    config: ProxyConfig,
    model: str,
) -> StreamingResponse:
    async def event_generator() -> AsyncGenerator[str, None]:
        async with httpx.AsyncClient(timeout=config.upstream.timeout) as client:
            async with client.stream("POST", upstream_url, json=cc_req, headers=headers) as resp:
                if resp.status_code != 200:
                    error_body = await resp.aread()
                    yield sse_event("response.failed", {
                        "type": "response.failed",
                        "response": {
                            "status": "failed",
                            "error": {"message": error_body.decode(errors="replace")},
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
