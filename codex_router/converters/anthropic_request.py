"""Convert Responses API request to Anthropic Messages API request."""

from __future__ import annotations

import logging
from typing import Any

from codex_router.config import ProxyConfig
from codex_router.models import (
    InputFunctionCallItem,
    InputFunctionCallOutputItem,
    InputImageContent,
    InputItem,
    InputMessageItem,
    InputTextContent,
    ResponsesRequest,
)

logger = logging.getLogger(__name__)


def convert_anthropic_request(req: ResponsesRequest, config: ProxyConfig) -> dict[str, Any]:
    """Convert a Responses API request to an Anthropic Messages API request."""
    messages = _convert_input(req.input)

    anthropic_req: dict[str, Any] = {
        "model": config.model_override or req.model,
        "messages": messages,
        "max_tokens": req.max_output_tokens if req.max_output_tokens is not None else 4096,
    }

    if req.instructions:
        anthropic_req["system"] = req.instructions

    if req.temperature is not None:
        anthropic_req["temperature"] = req.temperature

    if req.top_p is not None:
        anthropic_req["top_p"] = req.top_p

    if req.stream is not None:
        anthropic_req["stream"] = req.stream

    return anthropic_req


def _convert_input(
    input_data: str | list[InputItem],
) -> list[dict[str, Any]]:
    """Convert the `input` field to an Anthropic messages array."""
    if isinstance(input_data, str):
        return [{"role": "user", "content": input_data}]

    messages: list[dict[str, Any]] = []
    for item in input_data:
        if isinstance(item, InputMessageItem):
            msg = _convert_message_item(item)
            messages.append(msg)
        elif isinstance(item, InputFunctionCallItem):
            tc = {
                "type": "tool_use",
                "id": item.call_id,
                "name": item.name,
                "input": item.arguments,
            }
            messages.append({"role": "assistant", "content": [tc]})
        elif isinstance(item, InputFunctionCallOutputItem):
            messages.append({
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": item.call_id,
                    "content": item.output,
                }],
            })
        else:
            logger.warning("Unknown input item type: %s", type(item).__name__)

    return messages


def _convert_message_item(item: InputMessageItem) -> dict[str, Any]:
    """Convert an InputMessageItem to an Anthropic message dict."""
    role = item.role
    # Anthropic uses "user" or "assistant", map developer/system to user
    if role in ("developer", "system"):
        role = "user"

    if isinstance(item.content, str):
        return {"role": role, "content": item.content}

    parts: list[dict[str, Any]] = []
    for part in item.content:
        if isinstance(part, InputTextContent):
            parts.append({"type": "text", "text": part.text})
        elif isinstance(part, InputImageContent):
            img: dict[str, Any] = {}
            if part.image_url:
                img = {"type": "image", "source": {"type": "url", "url": part.image_url}}
            elif part.file_id:
                logger.warning("file_id not supported for Anthropic image input")
                continue
            if part.detail:
                img["detail"] = part.detail
            parts.append(img)
        else:
            logger.warning("Unknown content part type: %s", type(part).__name__)

    return {"role": role, "content": parts}
