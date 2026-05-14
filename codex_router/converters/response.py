"""Convert Chat Completions API response to Responses API response."""

from __future__ import annotations

import time
import uuid
from typing import Any


def convert_response(cc_resp: dict[str, Any], model: str) -> dict[str, Any]:
    """Convert a Chat Completions response dict to a Responses API response dict."""
    resp_id = "resp_" + uuid.uuid4().hex[:24]
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
            "id": "msg_" + uuid.uuid4().hex[:24],
            "role": "assistant",
            "content": [{"type": "output_text", "text": content, "annotations": []}],
            "status": "completed",
        })

    # Tool calls -> function_call output items
    tool_calls = message.get("tool_calls")
    if tool_calls:
        for tc in tool_calls:
            tc_id = tc.get("id", "call_" + uuid.uuid4().hex[:24])
            output.append({
                "type": "function_call",
                "id": "fc_" + uuid.uuid4().hex[:24],
                "call_id": tc_id,
                "name": tc.get("function", {}).get("name", ""),
                "arguments": tc.get("function", {}).get("arguments", ""),
                "status": "completed",
            })

    # Status mapping
    status_map = {
        "stop": "completed",
        "length": "incomplete",
        "tool_calls": "completed",
        "content_filter": "incomplete",
    }
    status = status_map.get(finish_reason, "completed")

    # Usage mapping
    usage = cc_resp.get("usage", {})
    resp_usage: dict[str, Any] = {}
    if usage:
        resp_usage = {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
        }
        details_in = usage.get("prompt_tokens_details")
        details_out = usage.get("completion_tokens_details")
        if details_in:
            resp_usage["input_tokens_details"] = {"cached_tokens": details_in.get("cached_tokens", 0)}
        if details_out:
            resp_usage["output_tokens_details"] = {"reasoning_tokens": details_out.get("reasoning_tokens", 0)}

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
