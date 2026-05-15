"""Convert Anthropic Messages API response to Responses API response."""

from __future__ import annotations

import time
from typing import Any

from codex_router.converters.common import (
    CALL_ID_PREFIX,
    FUNCTION_CALL_ID_PREFIX,
    MESSAGE_ID_PREFIX,
    RESPONSE_ID_PREFIX,
    gen_id,
)


def convert_anthropic_response(data: dict[str, Any], model: str) -> dict[str, Any]:
    """Convert an Anthropic Messages response to a Responses API response."""
    resp_id = gen_id(RESPONSE_ID_PREFIX)
    created_ts = int(time.time())

    text = ""
    output: list[dict[str, Any]] = []

    for block in data.get("content", []):
        block_type = block.get("type", "")

        if block_type == "text":
            block_text = block.get("text", "")
            text += block_text

        elif block_type == "tool_use":
            output.append({
                "type": "function_call",
                "id": gen_id(FUNCTION_CALL_ID_PREFIX),
                "call_id": block.get("id", gen_id(CALL_ID_PREFIX)),
                "name": block.get("name", ""),
                "arguments": _serialize_input(block.get("input", {})),
                "status": "completed",
            })

    if text:
        output.insert(0, {
            "type": "message",
            "id": gen_id(MESSAGE_ID_PREFIX),
            "role": "assistant",
            "content": [{"type": "output_text", "text": text, "annotations": []}],
            "status": "completed",
        })

    stop_reason = data.get("stop_reason", "end_turn")
    status = "completed" if stop_reason in ("end_turn", "stop") else "incomplete"

    usage_data = data.get("usage", {})

    result: dict[str, Any] = {
        "id": resp_id,
        "object": "response",
        "created_at": created_ts,
        "model": model,
        "status": status,
        "output": output,
        "output_text": text,
        "usage": {
            "input_tokens": usage_data.get("input_tokens", 0),
            "output_tokens": usage_data.get("output_tokens", 0),
            "total_tokens": usage_data.get("input_tokens", 0) + usage_data.get("output_tokens", 0),
        },
        "metadata": {},
    }

    if stop_reason == "max_tokens":
        result["incomplete_details"] = {"reason": "max_output_tokens"}

    return result


def _serialize_input(data: Any) -> str:
    """Serialize Anthropic tool input to a JSON string for Responses API arguments."""
    if isinstance(data, str):
        return data
    import json
    try:
        return json.dumps(data, ensure_ascii=False)
    except (TypeError, ValueError):
        return "{}"
