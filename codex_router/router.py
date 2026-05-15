"""FastAPI route handler for /v1/responses (HTTP + WebSocket)."""

from __future__ import annotations

import json
import logging
import time as _time
from typing import Any, AsyncGenerator

from fastapi import APIRouter, HTTPException, Request, WebSocket
from fastapi.responses import StreamingResponse

from codex_router.config import ProxyConfig
from codex_router.converters.request import convert_request
from codex_router.converters.response import convert_response
from codex_router.converters.streaming import convert_stream, sse_event
from codex_router.converters.anthropic_request import convert_anthropic_request
from codex_router.converters.anthropic_response import convert_anthropic_response
from codex_router.converters.anthropic_streaming import convert_anthropic_stream
from codex_router.errors import UpstreamError
from codex_router.models import ResponsesRequest
from codex_router.response_store import ResponseStore
from codex_router.ws_handler import build_upstream_headers, build_upstream_url, handle_websocket

logger = logging.getLogger(__name__)


def create_router() -> APIRouter:
    router = APIRouter()

    @router.post("/v1/responses")
    async def create_response(request: Request):
        config: ProxyConfig = request.app.state.config
        stats = getattr(request.app.state, "request_stats", None)

        try:
            body = await request.json()
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}") from e

        store: ResponseStore = request.app.state.response_store
        body = store.resolve(body)

        try:
            resp_req = ResponsesRequest(**body)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid request: {e}") from e

        input_items = resp_req.input
        model = resp_req.model

        api_key = config.upstream.api_key
        if not api_key and config.passthrough_api_key:
            auth_header = request.headers.get("authorization", "")
            if auth_header.startswith("Bearer "):
                api_key = auth_header[7:]

        if not api_key:
            raise HTTPException(status_code=401, detail="No API key available")

        is_anthropic = config.upstream.api_format == "anthropic"
        if is_anthropic:
            cc_req = convert_anthropic_request(resp_req, config)
            headers = {
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            }
            upstream_url = f"{config.upstream.base_url.rstrip('/')}/v1/messages"
        else:
            cc_req = convert_request(resp_req, config)
            headers = build_upstream_headers(api_key)
            upstream_url = build_upstream_url(config)
        client: Any = request.app.state.http_client
        timeout = config.upstream.timeout

        t0 = _time.monotonic()
        try:
            if resp_req.stream:
                return await _handle_streaming(client, upstream_url, headers, cc_req, resp_req.model, timeout, is_anthropic, store, input_items, stats, t0)
            else:
                result = await _handle_non_streaming(client, upstream_url, headers, cc_req, resp_req.model, timeout, is_anthropic, store, input_items)
                if stats:
                    stats.record(model, 200, (_time.monotonic() - t0) * 1000, "http")
                return result
        except UpstreamError as exc:
            if stats:
                stats.record(model, 502, (_time.monotonic() - t0) * 1000, "http", str(exc))
            raise
        except HTTPException as exc:
            if stats:
                stats.record(model, exc.status_code, (_time.monotonic() - t0) * 1000, "http", exc.detail)
            raise

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
    is_anthropic: bool = False,
    store: ResponseStore | None = None,
    input_items: list | str | None = None,
) -> dict[str, Any]:
    try:
        resp = await client.post(upstream_url, json=cc_req, headers=headers, timeout=timeout)
    except Exception as e:
        raise UpstreamError(f"Upstream request failed: {e}") from e

    if resp.status_code != 200:
        logger.error("Upstream error %d: %s", resp.status_code, resp.text[:2000])
        raise UpstreamError(f"Upstream returned {resp.status_code}")

    if is_anthropic:
        result = convert_anthropic_response(resp.json(), model)
    else:
        result = convert_response(resp.json(), model)

    if store is not None and input_items is not None:
        store.store(result["id"], input_items, result.get("output", []))

    return result


async def _handle_streaming(
    client: Any,
    upstream_url: str,
    headers: dict[str, str],
    cc_req: dict[str, Any],
    model: str,
    timeout: float,
    is_anthropic: bool = False,
    store: ResponseStore | None = None,
    input_items: list | str | None = None,
    stats: Any = None,
    t0: float = 0,
) -> StreamingResponse:
    async def event_generator() -> AsyncGenerator[str, None]:
        response_id = None
        output: list[dict[str, Any]] = []
        stream_status = 200

        async with client.stream("POST", upstream_url, json=cc_req, headers=headers, timeout=timeout) as resp:
            if resp.status_code != 200:
                stream_status = resp.status_code
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
            else:
                if is_anthropic:
                    from codex_router.converters.anthropic_streaming import convert_anthropic_stream
                    stream = convert_anthropic_stream(resp.aiter_lines(), model)
                else:
                    stream = convert_stream(resp.aiter_lines(), model)

                async for sse_str in stream:
                    yield sse_str
                    if store is not None and sse_str.startswith("event: response.completed\n"):
                        for line in sse_str.split("\n"):
                            if line.startswith("data: "):
                                try:
                                    data = json.loads(line[6:])
                                    resp_data = data.get("response", {})
                                    response_id = resp_data.get("id")
                                    output = resp_data.get("output", [])
                                except (json.JSONDecodeError, KeyError):
                                    pass

        if stats:
            latency = (_time.monotonic() - t0) * 1000
            error_msg = None if stream_status == 200 else f"Upstream {stream_status}"
            stats.record(model, stream_status, latency, "http", error_msg)
        if store is not None and response_id and input_items is not None:
            store.store(response_id, input_items, output)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
