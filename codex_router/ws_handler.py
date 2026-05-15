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
from codex_router.response_store import ResponseStore

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

    # Resolve previous_response_id → expand conversation history
    store: ResponseStore = ws.app.state.response_store
    params = store.resolve(params)

    try:
        resp_req = ResponsesRequest(**params)
    except Exception as e:
        await ws.send_json({"type": "error", "message": f"Invalid request: {e}"})
        return

    input_items = resp_req.input

    api_key = config.upstream.api_key
    if not api_key:
        await ws.send_json({"type": "error", "message": "No API key configured"})
        return

    is_anthropic = config.upstream.api_format == "anthropic"
    if is_anthropic:
        from codex_router.converters.anthropic_request import convert_anthropic_request
        cc_req = convert_anthropic_request(resp_req, config)
        cc_req["stream"] = True
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        upstream_url = f"{config.upstream.base_url.rstrip('/')}/v1/messages"
    else:
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

            if is_anthropic:
                from codex_router.converters.anthropic_streaming import convert_anthropic_stream_events
                stream_events = convert_anthropic_stream_events(resp.aiter_lines(), resp_req.model)
            else:
                stream_events = convert_stream_events(resp.aiter_lines(), resp_req.model)

            response_id = None
            output: list[dict[str, Any]] = []
            async for event_type, data in stream_events:
                await ws.send_json(data)
                if event_type == "response.completed":
                    resp_data = data.get("response", {})
                    response_id = resp_data.get("id")
                    output = resp_data.get("output", [])

        if response_id:
            store.store(response_id, input_items, output)
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
