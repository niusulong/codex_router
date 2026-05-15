"""Convert Chat Completions SSE stream to Responses API SSE stream."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator

from codex_router.converters.common import (
    FUNCTION_CALL_ID_PREFIX,
    MESSAGE_ID_PREFIX,
    RESPONSE_ID_PREFIX,
    STATUS_MAP,
    convert_usage,
    gen_id,
)

logger = logging.getLogger(__name__)


def sse_event(event_type: str, data: dict[str, Any]) -> str:
    """Format a single SSE event."""
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


@dataclass
class StreamState:
    response_id: str = ""
    model: str = ""
    created_at: int = 0
    output_item_index: int = 0
    content_part_index: int = 0
    initialized: bool = False
    finalized: bool = False
    message_open: bool = False
    content_part_open: bool = False
    message_id: str = ""
    accumulated_text: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    output: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)


def _skeleton_response(state: StreamState) -> dict[str, Any]:
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


async def convert_stream(
    upstream_lines: AsyncGenerator[str, None],
    model: str,
) -> AsyncGenerator[str, None]:
    """Consume Chat Completions SSE lines, yield Responses API SSE event strings."""
    async for event_type, data in convert_stream_events(upstream_lines, model):
        yield sse_event(event_type, data)


async def convert_stream_events(
    upstream_lines: AsyncGenerator[str, None],
    model: str,
) -> AsyncGenerator[tuple[str, dict[str, Any]], None]:
    """Consume Chat Completions SSE lines, yield (event_type, data_dict) tuples.

    Used by WebSocket handler to avoid SSE serialize→deserialize→reserialize overhead.
    """
    state = StreamState(
        response_id=gen_id(RESPONSE_ID_PREFIX),
        model=model,
        created_at=int(time.time()),
    )

    async for line in upstream_lines:
        stripped = line.strip()
        if not stripped:
            continue

        if stripped == "data: [DONE]":
            if not state.finalized:
                async for event_type, data in _finalize_events(state, "stop"):
                    yield event_type, data
            return

        if not stripped.startswith("data: "):
            continue

        try:
            chunk = json.loads(stripped[6:])
        except json.JSONDecodeError:
            continue

        async for event_type, data in _process_chunk_events(chunk, state):
            yield event_type, data


async def _process_chunk_events(
    chunk: dict[str, Any], state: StreamState
) -> AsyncGenerator[tuple[str, dict[str, Any]], None]:
    """Process a single Chat Completions streaming chunk, yielding (event_type, data) tuples."""
    # Emit initial events on first real chunk
    if not state.initialized:
        state.initialized = True
        yield "response.created", {
            "type": "response.created",
            "response": _skeleton_response(state),
        }
        yield "response.in_progress", {
            "type": "response.in_progress",
            "response": _skeleton_response(state),
        }

    choices = chunk.get("choices", [])
    if not choices:
        # Usage-only chunk at end of stream
        if chunk.get("usage"):
            state.usage = convert_usage(chunk["usage"])
        return

    choice = choices[0]
    delta = choice.get("delta", {})
    finish_reason = choice.get("finish_reason")

    # --- Text content delta ---
    content_delta = delta.get("content")
    if content_delta is not None:
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
            state.content_part_open = True
            yield "response.content_part.added", {
                "type": "response.content_part.added",
                "output_index": state.output_item_index,
                "content_index": state.content_part_index,
                "part": {"type": "output_text", "text": "", "annotations": []},
            }

        yield "response.output_text.delta", {
            "type": "response.output_text.delta",
            "output_index": state.output_item_index,
            "content_index": state.content_part_index,
            "delta": content_delta,
        }
        state.accumulated_text += content_delta

    # --- Tool calls delta ---
    tool_calls_delta = delta.get("tool_calls")
    if tool_calls_delta is not None:
        # Close any open message item first
        if state.message_open:
            async for event_type, data in _close_message_events(state):
                yield event_type, data

        for tc_delta in tool_calls_delta:
            tc_index = tc_delta.get("index", 0)

            # Ensure slot
            while len(state.tool_calls) <= tc_index:
                state.tool_calls.append({"id": "", "name": "", "arguments": "", "_fc_id": "", "_output_index": 0})

            # New tool call starting
            if tc_delta.get("id"):
                state.tool_calls[tc_index]["id"] = tc_delta["id"]
                fc_id = gen_id(FUNCTION_CALL_ID_PREFIX)
                state.tool_calls[tc_index]["_fc_id"] = fc_id
                state.tool_calls[tc_index]["_output_index"] = state.output_item_index

                fn_name = tc_delta.get("function", {}).get("name", "")
                state.tool_calls[tc_index]["name"] = fn_name

                yield "response.output_item.added", {
                    "type": "response.output_item.added",
                    "output_index": state.output_item_index,
                    "item": {
                        "type": "function_call",
                        "id": fc_id,
                        "call_id": tc_delta["id"],
                        "name": fn_name,
                        "arguments": "",
                        "status": "in_progress",
                    },
                }

            # Function name (may arrive separately from id in some providers)
            fn_name = tc_delta.get("function", {}).get("name")
            if fn_name and not tc_delta.get("id"):
                state.tool_calls[tc_index]["name"] = fn_name

            # Arguments delta
            fn_args_delta = tc_delta.get("function", {}).get("arguments")
            if fn_args_delta:
                state.tool_calls[tc_index]["arguments"] += fn_args_delta
                yield "response.function_call_arguments.delta", {
                    "type": "response.function_call_arguments.delta",
                    "output_index": state.tool_calls[tc_index]["_output_index"],
                    "item_id": state.tool_calls[tc_index]["_fc_id"],
                    "delta": fn_args_delta,
                }

            # Increment output_item_index only when a new tool call starts
            if tc_delta.get("id"):
                state.output_item_index += 1

    # --- Finish reason ---
    if finish_reason is not None:
        # Capture usage from the final chunk if present
        if chunk.get("usage"):
            state.usage = convert_usage(chunk["usage"])

        async for event_type, data in _finalize_events(state, finish_reason):
            yield event_type, data


async def _close_message_events(state: StreamState) -> AsyncGenerator[tuple[str, dict[str, Any]], None]:
    """Close the currently open message output item, yielding (event_type, data) tuples."""
    if state.content_part_open:
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
        state.content_part_open = False

    if state.message_open:
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


async def _finalize_events(
    state: StreamState, finish_reason: str
) -> AsyncGenerator[tuple[str, dict[str, Any]], None]:
    """Emit closing events and response.completed, yielding (event_type, data) tuples."""
    # Close any open message
    if state.message_open:
        async for event_type, data in _close_message_events(state):
            yield event_type, data

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

    # Determine final status
    status = STATUS_MAP.get(finish_reason, "completed")
    state.finalized = True

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
    if finish_reason == "length":
        full_response["incomplete_details"] = {"reason": "max_output_tokens"}

    yield "response.completed", {
        "type": "response.completed",
        "response": full_response,
    }


