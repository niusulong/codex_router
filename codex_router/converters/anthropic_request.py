"""Convert Responses API request to Anthropic Messages API request."""

from __future__ import annotations

import json
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
        "model": req.model,
        "messages": messages,
        "max_tokens": req.max_output_tokens if req.max_output_tokens is not None else 16384,
    }

    if req.instructions:
        anthropic_req["system"] = req.instructions

    if req.tools is not None:
        anthropic_tools = _convert_tools(req.tools, config)
        if anthropic_tools:
            anthropic_req["tools"] = anthropic_tools

    if req.tool_choice is not None:
        tc = _convert_tool_choice(req.tool_choice)
        if tc is not None:
            anthropic_req["tool_choice"] = tc

    if req.temperature is not None:
        anthropic_req["temperature"] = req.temperature

    if req.top_p is not None:
        anthropic_req["top_p"] = req.top_p

    if req.stream is not None:
        anthropic_req["stream"] = req.stream

    return anthropic_req


def _convert_tools(tools: list[Any], config: ProxyConfig) -> list[dict[str, Any]]:
    """Convert Responses API tools to Anthropic tool format."""
    anthropic_tools: list[dict[str, Any]] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        tool_type = tool.get("type", "function")
        if tool_type in config.ignored_builtin_tools:
            logger.debug("Filtering out built-in tool: %s", tool_type)
            continue
        if tool_type == "function":
            # Responses API flat format: {type: "function", name, parameters}
            fn = tool.get("function")
            if fn and isinstance(fn, dict):
                name = fn.get("name", "")
                at: dict[str, Any] = {"name": name}
                if "description" in fn:
                    at["description"] = fn["description"]
                if "parameters" in fn:
                    at["input_schema"] = fn["parameters"]
            else:
                name = tool.get("name", "")
                at = {"name": name}
                if "description" in tool:
                    at["description"] = tool["description"]
                if "parameters" in tool:
                    at["input_schema"] = tool["parameters"]
            anthropic_tools.append(at)
        else:
            logger.debug("Skipping non-function tool type: %s", tool_type)
    return anthropic_tools


def _convert_tool_choice(tool_choice: str | dict[str, Any]) -> str | dict[str, Any] | None:
    """Convert tool_choice from Responses API to Anthropic format."""
    if isinstance(tool_choice, str):
        if tool_choice == "auto":
            return {"type": "auto"}
        elif tool_choice == "none":
            return None
        elif tool_choice == "required":
            return {"type": "any"}
        return {"type": "auto"}
    if isinstance(tool_choice, dict):
        tc_type = tool_choice.get("type", "")
        if tc_type == "function":
            return {"type": "tool", "name": tool_choice.get("name", "")}
        if tc_type in ("auto", "any"):
            return {"type": tc_type}
    return {"type": "auto"}


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
            try:
                args_data = json.loads(item.arguments) if item.arguments else {}
            except (json.JSONDecodeError, TypeError):
                args_data = {}
            tc = {
                "type": "tool_use",
                "id": item.call_id,
                "name": item.name,
                "input": args_data,
            }
            # Merge into previous assistant message if possible
            if messages and messages[-1].get("role") == "assistant":
                prev = messages[-1]
                content = prev.get("content", [])
                if isinstance(content, str):
                    prev["content"] = [{"type": "text", "text": content}] if content else []
                prev.setdefault("content", []).append(tc)
                continue
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
