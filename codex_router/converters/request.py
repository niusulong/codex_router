"""Convert Responses API request to Chat Completions API request."""

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


def convert_request(req: ResponsesRequest, config: ProxyConfig) -> dict[str, Any]:
    """Convert a Responses API request body to a Chat Completions API request body."""
    messages = _convert_input(req.input)

    # Prepend instructions as system message
    if req.instructions:
        messages.insert(0, {"role": "system", "content": req.instructions})

    # Build the Chat Completions request
    cc_req: dict[str, Any] = {"model": config.model_override or req.model, "messages": messages}

    if req.tools is not None:
        cc_tools = _convert_tools(req.tools, config)
        if cc_tools:
            cc_req["tools"] = cc_tools

    if req.tool_choice is not None:
        cc_req["tool_choice"] = _convert_tool_choice(req.tool_choice)

    if req.max_output_tokens is not None:
        cc_req["max_tokens"] = req.max_output_tokens

    if req.temperature is not None:
        cc_req["temperature"] = req.temperature

    if req.top_p is not None:
        cc_req["top_p"] = req.top_p

    if req.stream is not None:
        cc_req["stream"] = req.stream

    if req.text is not None:
        rf = _convert_text_format(req.text)
        if rf:
            cc_req["response_format"] = rf

    return cc_req


def _convert_input(
    input_data: str | list[InputItem],
) -> list[dict[str, Any]]:
    """Convert the `input` field to a messages array."""
    if isinstance(input_data, str):
        return [{"role": "user", "content": input_data}]

    messages: list[dict[str, Any]] = []
    for item in input_data:
        if isinstance(item, InputMessageItem):
            msg = _convert_message_item(item)
            messages.append(msg)
        elif isinstance(item, InputFunctionCallItem):
            _flush_pending_tool_calls(messages)
            tc = {
                "id": item.call_id,
                "type": "function",
                "function": {"name": item.name, "arguments": item.arguments},
            }
            messages.append({"role": "assistant", "tool_calls": [tc]})
        elif isinstance(item, InputFunctionCallOutputItem):
            messages.append({
                "role": "tool",
                "tool_call_id": item.call_id,
                "content": item.output,
            })
        else:
            logger.warning("Unknown input item type: %s", type(item).__name__)

    return messages


def _convert_message_item(item: InputMessageItem) -> dict[str, Any]:
    """Convert an InputMessageItem to a Chat Completions message dict."""
    role = "system" if item.role == "developer" else item.role

    if isinstance(item.content, str):
        return {"role": role, "content": item.content}

    parts: list[dict[str, Any]] = []
    for part in item.content:
        if isinstance(part, InputTextContent):
            parts.append({"type": "text", "text": part.text})
        elif isinstance(part, InputImageContent):
            if part.file_id and (".." in part.file_id or "/" in part.file_id or "\\" in part.file_id):
                logger.warning("file_id contains path traversal characters, skipping: %s", part.file_id)
                continue
            img: dict[str, Any] = {"url": part.image_url or f"file://{part.file_id}"}
            if part.detail:
                img["detail"] = part.detail
            parts.append({"type": "image_url", "image_url": img})
        else:
            logger.warning("Unknown content part type: %s", type(part).__name__)

    return {"role": role, "content": parts}


def _flush_pending_tool_calls(messages: list[dict[str, Any]]) -> None:
    """Merge consecutive assistant messages with tool_calls into one."""
    if len(messages) >= 2:
        prev = messages[-1]
        prev2 = messages[-2]
        if (
            prev.get("role") == "assistant"
            and "tool_calls" in prev
            and prev2.get("role") == "assistant"
            and "tool_calls" in prev2
        ):
            prev2["tool_calls"].extend(prev["tool_calls"])
            messages.pop()


def _convert_tools(tools: list[Any], config: ProxyConfig) -> list[dict[str, Any]]:
    """Convert tools, filtering out unsupported built-in types.

    Supports both Responses API format (flat: {type, name, parameters})
    and Chat Completions format (nested: {type, function: {name, ...}}).
    """
    cc_tools: list[dict[str, Any]] = []
    for tool in tools:
        if isinstance(tool, dict):
            tool_type = tool.get("type", "function")
            if tool_type in config.ignored_builtin_tools:
                logger.info("Filtering out built-in tool: %s", tool_type)
                continue
            if tool_type == "function":
                # Responses API format: {type: "function", name: "...", parameters: {...}}
                # Chat Completions format: {type: "function", function: {name: "...", ...}}
                fn = tool.get("function")
                if fn and isinstance(fn, dict):
                    # Chat Completions format (nested)
                    cc_tool: dict[str, Any] = {"type": "function", "function": {"name": fn.get("name", "")}}
                    if "description" in fn:
                        cc_tool["function"]["description"] = fn["description"]
                    if "parameters" in fn:
                        cc_tool["function"]["parameters"] = fn["parameters"]
                    if "strict" in fn:
                        cc_tool["function"]["strict"] = fn["strict"]
                else:
                    # Responses API format (flat)
                    cc_tool = {"type": "function", "function": {"name": tool.get("name", "")}}
                    if "description" in tool:
                        cc_tool["function"]["description"] = tool["description"]
                    if "parameters" in tool:
                        cc_tool["function"]["parameters"] = tool["parameters"]
                    if "strict" in tool:
                        cc_tool["function"]["strict"] = tool["strict"]
                cc_tools.append(cc_tool)
            else:
                logger.warning("Unsupported tool type: %s", tool_type)
    return cc_tools


def _convert_tool_choice(tool_choice: str | dict[str, Any]) -> str | dict[str, Any]:
    """Convert tool_choice from Responses API to Chat Completions format."""
    if isinstance(tool_choice, str):
        return tool_choice
    if isinstance(tool_choice, dict) and tool_choice.get("type") == "function":
        return {"type": "function", "function": {"name": tool_choice["name"]}}
    return tool_choice


def _convert_text_format(text_format: Any) -> dict[str, Any] | None:
    """Convert text.format to response_format."""
    from pydantic import BaseModel

    if isinstance(text_format, BaseModel):
        text_format = text_format.model_dump(exclude_none=True)

    if isinstance(text_format, dict):
        fmt_type = text_format.get("type", "")
        if fmt_type == "json_schema" and text_format.get("json_schema"):
            return {"type": "json_schema", "json_schema": text_format["json_schema"]}
        if fmt_type == "json_object":
            return {"type": "json_object"}
        if fmt_type == "text":
            return None
    return None
