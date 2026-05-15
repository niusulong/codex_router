"""Convert Chat Completions API response to Responses API response."""

from __future__ import annotations

import time
from typing import Any

from codex_router.converters.common import (
    CALL_ID_PREFIX,
    FUNCTION_CALL_ID_PREFIX,
    MESSAGE_ID_PREFIX,
    RESPONSE_ID_PREFIX,
    STATUS_MAP,
    convert_usage,
    gen_id,
)


def convert_response(cc_resp: dict[str, Any], model: str) -> dict[str, Any]:
    """Convert a Chat Completions response dict to a Responses API response dict."""
    resp_id = gen_id(RESPONSE_ID_PREFIX)
    created_ts = int(time.time())

    choice = cc_resp.get("choices", [{}])[0] if cc_resp.get("choices") else {}
    message = choice.get("message", {})
    finish_reason = choice.get("finish_reason", "stop")

    output: list[dict[str, Any]] = []

    # Text content -> message output item
    content = message.get("content")
    if content:
        output.append({
            "type": "message",
            "id": gen_id(MESSAGE_ID_PREFIX),
            "role": "assistant",
            "content": [{"type": "output_text", "text": content, "annotations": []}],
            "status": "completed",
        })

    # Tool calls -> function_call output items
    tool_calls = message.get("tool_calls")
    if tool_calls:
        for tc in tool_calls:
            tc_id = tc.get("id", gen_id(CALL_ID_PREFIX))
            output.append({
                "type": "function_call",
                "id": gen_id(FUNCTION_CALL_ID_PREFIX),
                "call_id": tc_id,
                "name": tc.get("function", {}).get("name", ""),
                "arguments": tc.get("function", {}).get("arguments", ""),
                "status": "completed",
            })

    # Status mapping
    status = STATUS_MAP.get(finish_reason, "completed")

    # Usage mapping
    usage = cc_resp.get("usage", {})
    resp_usage = convert_usage(usage) if usage else {}

    result: dict[str, Any] = {
        "id": resp_id,
        "object": "response",
        "created_at": created_ts,
        "model": model,
        "status": status,
        "output": output,
        "output_text": content or "",
        "usage": resp_usage,
        "metadata": {},
    }

    if finish_reason == "length":
        result["incomplete_details"] = {"reason": "max_output_tokens"}

    return result
