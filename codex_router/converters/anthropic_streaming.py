"""Convert Anthropic Messages SSE stream to Responses API SSE stream."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator

from codex_router.converters.common import (
    CALL_ID_PREFIX,
    FUNCTION_CALL_ID_PREFIX,
    MESSAGE_ID_PREFIX,
    RESPONSE_ID_PREFIX,
    gen_id,
)

logger = logging.getLogger(__name__)


@dataclass
class AnthropicStreamState:
    response_id: str = ""
    model: str = ""
    created_at: int = 0
    initialized: bool = False
    finalized: bool = False
    message_open: bool = False
    message_id: str = ""
    output_item_index: int = 0
    content_part_index: int = 0
    accumulated_text: str = ""
    output: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)
    # Tool call tracking
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    current_tool_index: int = -1


def _skeleton_response(state: AnthropicStreamState) -> dict[str, Any]:
    return {
        "id": state.response_id,
        "object": "response",
        "created_at": state.created_at,
        "model": state.model,
        "status": "in_progress",
        "output": [],
        "usage": {},
        "metadata": {},
    }


async def convert_anthropic_stream(
    upstream_lines: AsyncGenerator[str, None],
    model: str,
) -> AsyncGenerator[str, None]:
    """Consume Anthropic SSE lines, yield Responses API SSE event strings."""
    async for event_type, data in convert_anthropic_stream_events(upstream_lines, model):
        yield f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


async def convert_anthropic_stream_events(
    upstream_lines: AsyncGenerator[str, None],
    model: str,
) -> AsyncGenerator[tuple[str, dict[str, Any]], None]:
    """Consume Anthropic SSE lines, yield (event_type, data_dict) tuples.

    Used by WebSocket handler.
    """
    state = AnthropicStreamState(
        response_id=gen_id(RESPONSE_ID_PREFIX),
        model=model,
        created_at=int(time.time()),
    )

    current_event_type = ""
    pending_line: dict[str, Any] | None = None

    async for line in upstream_lines:
        stripped = line.strip()
        if not stripped:
            # Process the pending data line when we hit an empty line (event boundary)
            if pending_line is not None:
                async for event_type, event_data in _process_anthropic_event(
                    current_event_type, pending_line, state,
                ):
                    yield event_type, event_data
                pending_line = None
            continue

        if stripped.startswith("event: "):
            current_event_type = stripped[7:]
            continue

        if not stripped.startswith("data: "):
            continue

        try:
            data = json.loads(stripped[6:])
        except json.JSONDecodeError:
            continue

        pending_line = data

    # Process any remaining pending data
    if pending_line is not None:
        async for event_type, event_data in _process_anthropic_event(
            current_event_type, pending_line, state,
        ):
            yield event_type, event_data


async def _process_anthropic_event(
    event_type: str, data: dict[str, Any], state: AnthropicStreamState,
) -> AsyncGenerator[tuple[str, dict[str, Any]], None]:
    """Process a single Anthropic SSE event."""

    if event_type == "message_start":
        if not state.initialized:
            state.initialized = True
            msg = data.get("message", {})
            state.usage["input_tokens"] = msg.get("usage", {}).get("input_tokens", 0)
            yield "response.created", {
                "type": "response.created",
                "response": _skeleton_response(state),
            }
            yield "response.in_progress", {
                "type": "response.in_progress",
                "response": _skeleton_response(state),
            }

    elif event_type == "content_block_start":
        block = data.get("content_block", {})
        block_type = block.get("type", "text")
        block_index = data.get("index", 0)

        if block_type == "text":
            if not state.message_open:
                state.message_open = True
                state.message_id = gen_id(MESSAGE_ID_PREFIX)
                state.content_part_index = 0

                yield "response.output_item.added", {
                    "type": "response.output_item.added",
                    "output_index": state.output_item_index,
                    "item": {
                        "type": "message",
                        "id": state.message_id,
                        "role": "assistant",
                        "content": [],
                        "status": "in_progress",
                    },
                }
                yield "response.content_part.added", {
                    "type": "response.content_part.added",
                    "output_index": state.output_item_index,
                    "content_index": state.content_part_index,
                    "part": {"type": "output_text", "text": "", "annotations": []},
                }

        elif block_type == "tool_use":
            # Close any open text message first
            if state.message_open:
                async for et, ed in _close_text_message(state):
                    yield et, ed

            tool_id = block.get("id", gen_id(CALL_ID_PREFIX))
            tool_name = block.get("name", "")
            fc_id = gen_id(FUNCTION_CALL_ID_PREFIX)
            out_idx = state.output_item_index

            tc_entry = {
                "id": tool_id,
                "name": tool_name,
                "arguments": "",
                "_fc_id": fc_id,
                "_output_index": out_idx,
            }
            state.tool_calls.append(tc_entry)
            state.current_tool_index = len(state.tool_calls) - 1

            yield "response.output_item.added", {
                "type": "response.output_item.added",
                "output_index": out_idx,
                "item": {
                    "type": "function_call",
                    "id": fc_id,
                    "call_id": tool_id,
                    "name": tool_name,
                    "arguments": "",
                    "status": "in_progress",
                },
            }
            state.output_item_index += 1

    elif event_type == "content_block_delta":
        delta = data.get("delta", {})
        delta_type = delta.get("type", "")

        if delta_type == "text_delta":
            text_delta = delta.get("text", "")
            if text_delta and state.message_open:
                yield "response.output_text.delta", {
                    "type": "response.output_text.delta",
                    "output_index": state.output_item_index,
                    "content_index": state.content_part_index,
                    "delta": text_delta,
                }
                state.accumulated_text += text_delta

        elif delta_type == "input_json_delta":
            args_delta = delta.get("partial_json", "")
            if args_delta and state.current_tool_index >= 0:
                tc = state.tool_calls[state.current_tool_index]
                tc["arguments"] += args_delta
                yield "response.function_call_arguments.delta", {
                    "type": "response.function_call_arguments.delta",
                    "output_index": tc["_output_index"],
                    "item_id": tc["_fc_id"],
                    "delta": args_delta,
                }

    elif event_type == "content_block_stop":
        # Content block ended — tool arguments are complete
        block_index = data.get("index", 0)
        # Check if this was a tool_use block that just finished
        # (we track via current_tool_index mapping)
        pass

    elif event_type == "message_delta":
        delta = data.get("delta", {})
        usage = data.get("usage", {})
        stop_reason = delta.get("stop_reason", "")
        if usage:
            state.usage["output_tokens"] = usage.get("output_tokens", 0)

        if stop_reason:
            state.usage["_stop_reason"] = stop_reason

    elif event_type == "message_stop":
        if not state.finalized:
            stop_reason = state.usage.pop("_stop_reason", "end_turn")
            async for event_type, event_data in _finalize_anthropic_events(state, stop_reason):
                yield event_type, event_data


async def _close_text_message(
    state: AnthropicStreamState,
) -> AsyncGenerator[tuple[str, dict[str, Any]], None]:
    """Close the currently open text message output item."""
    if not state.message_open:
        return

    yield "response.output_text.done", {
        "type": "response.output_text.done",
        "output_index": state.output_item_index,
        "content_index": state.content_part_index,
        "text": state.accumulated_text,
    }
    yield "response.content_part.done", {
        "type": "response.content_part.done",
        "output_index": state.output_item_index,
        "content_index": state.content_part_index,
        "part": {"type": "output_text", "text": state.accumulated_text, "annotations": []},
    }

    msg_item = {
        "type": "message",
        "id": state.message_id,
        "role": "assistant",
        "content": [{"type": "output_text", "text": state.accumulated_text, "annotations": []}],
        "status": "completed",
    }
    yield "response.output_item.done", {
        "type": "response.output_item.done",
        "output_index": state.output_item_index,
        "item": msg_item,
    }
    state.output.append(msg_item)
    state.message_open = False
    state.output_item_index += 1
    state.accumulated_text = ""


async def _finalize_anthropic_events(
    state: AnthropicStreamState, stop_reason: str,
) -> AsyncGenerator[tuple[str, dict[str, Any]], None]:
    """Emit closing events for Anthropic stream."""
    # Close any open text message
    if state.message_open:
        async for event_type, event_data in _close_text_message(state):
            yield event_type, event_data

    # Close tool call items
    for tc in state.tool_calls:
        if not tc.get("_fc_id"):
            continue

        out_idx = tc["_output_index"]

        yield "response.function_call_arguments.done", {
            "type": "response.function_call_arguments.done",
            "output_index": out_idx,
            "item_id": tc["_fc_id"],
            "arguments": tc["arguments"],
        }

        fc_item = {
            "type": "function_call",
            "id": tc["_fc_id"],
            "call_id": tc["id"],
            "name": tc["name"],
            "arguments": tc["arguments"],
            "status": "completed",
        }
        yield "response.output_item.done", {
            "type": "response.output_item.done",
            "output_index": out_idx,
            "item": fc_item,
        }
        state.output.append(fc_item)

    state.finalized = True

    status = "completed" if stop_reason in ("end_turn", "stop") else "incomplete"
    total_tokens = state.usage.get("input_tokens", 0) + state.usage.get("output_tokens", 0)
    state.usage["total_tokens"] = total_tokens

    full_response: dict[str, Any] = {
        "id": state.response_id,
        "object": "response",
        "created_at": state.created_at,
        "model": state.model,
        "status": status,
        "output": state.output,
        "output_text": state.accumulated_text or "",
        "usage": state.usage,
        "metadata": {},
    }

    if stop_reason == "max_tokens":
        full_response["incomplete_details"] = {"reason": "max_output_tokens"}

    yield "response.completed", {
        "type": "response.completed",
        "response": full_response,
    }
