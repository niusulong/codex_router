"""Convert Chat Completions SSE stream to Responses API SSE stream."""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator

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
    state = StreamState(
        response_id="resp_" + uuid.uuid4().hex[:24],
        model=model,
        created_at=int(time.time()),
    )

    async for line in upstream_lines:
        stripped = line.strip()
        if not stripped:
            continue

        if stripped == "data: [DONE]":
            if not state.finalized:
                async for event in _finalize(state, "stop"):
                    yield event
            return

        if not stripped.startswith("data: "):
            continue

        try:
            chunk = json.loads(stripped[6:])
        except json.JSONDecodeError:
            continue

        async for event in _process_chunk(chunk, state):
            yield event


async def _process_chunk(
    chunk: dict[str, Any], state: StreamState
) -> AsyncGenerator[str, None]:
    """Process a single Chat Completions streaming chunk."""
    # Emit initial events on first real chunk
    if not state.initialized:
        state.initialized = True
        yield sse_event("response.created", {
            "type": "response.created",
            "response": _skeleton_response(state),
        })
        yield sse_event("response.in_progress", {
            "type": "response.in_progress",
            "response": _skeleton_response(state),
        })

    choices = chunk.get("choices", [])
    if not choices:
        # Usage-only chunk at end of stream
        if chunk.get("usage"):
            state.usage = _convert_usage(chunk["usage"])
        return

    choice = choices[0]
    delta = choice.get("delta", {})
    finish_reason = choice.get("finish_reason")

    # --- Text content delta ---
    content_delta = delta.get("content")
    if content_delta is not None:
        if not state.message_open:
            state.message_open = True
            state.message_id = "msg_" + uuid.uuid4().hex[:24]
            state.content_part_index = 0

            yield sse_event("response.output_item.added", {
                "type": "response.output_item.added",
                "output_index": state.output_item_index,
                "item": {
                    "type": "message",
                    "id": state.message_id,
                    "role": "assistant",
                    "content": [],
                    "status": "in_progress",
                },
            })
            state.content_part_open = True
            yield sse_event("response.content_part.added", {
                "type": "response.content_part.added",
                "output_index": state.output_item_index,
                "content_index": state.content_part_index,
                "part": {"type": "output_text", "text": "", "annotations": []},
            })

        yield sse_event("response.output_text.delta", {
            "type": "response.output_text.delta",
            "output_index": state.output_item_index,
            "content_index": state.content_part_index,
            "delta": content_delta,
        })
        state.accumulated_text += content_delta

    # --- Tool calls delta ---
    tool_calls_delta = delta.get("tool_calls")
    if tool_calls_delta is not None:
        # Close any open message item first
        if state.message_open:
            async for event in _close_message(state):
                yield event

        for tc_delta in tool_calls_delta:
            tc_index = tc_delta.get("index", 0)

            # Ensure slot
            while len(state.tool_calls) <= tc_index:
                state.tool_calls.append({"id": "", "name": "", "arguments": "", "_fc_id": "", "_output_index": 0})

            # New tool call starting
            if tc_delta.get("id"):
                state.tool_calls[tc_index]["id"] = tc_delta["id"]
                fc_id = "fc_" + uuid.uuid4().hex[:24]
                state.tool_calls[tc_index]["_fc_id"] = fc_id
                state.tool_calls[tc_index]["_output_index"] = state.output_item_index

                fn_name = tc_delta.get("function", {}).get("name", "")
                state.tool_calls[tc_index]["name"] = fn_name

                yield sse_event("response.output_item.added", {
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
                })

            # Function name (may arrive separately from id in some providers)
            fn_name = tc_delta.get("function", {}).get("name")
            if fn_name and not tc_delta.get("id"):
                state.tool_calls[tc_index]["name"] = fn_name

            # Arguments delta
            fn_args_delta = tc_delta.get("function", {}).get("arguments")
            if fn_args_delta:
                state.tool_calls[tc_index]["arguments"] += fn_args_delta
                yield sse_event("response.function_call_arguments.delta", {
                    "type": "response.function_call_arguments.delta",
                    "output_index": state.tool_calls[tc_index]["_output_index"],
                    "item_id": state.tool_calls[tc_index]["_fc_id"],
                    "delta": fn_args_delta,
                })

            # Increment output_item_index only when a new tool call starts
            if tc_delta.get("id"):
                state.output_item_index += 1

    # --- Finish reason ---
    if finish_reason is not None:
        # Capture usage from the final chunk if present
        if chunk.get("usage"):
            state.usage = _convert_usage(chunk["usage"])

        async for event in _finalize(state, finish_reason):
            yield event


async def _close_message(state: StreamState) -> AsyncGenerator[str, None]:
    """Close the currently open message output item."""
    if state.content_part_open:
        yield sse_event("response.output_text.done", {
            "type": "response.output_text.done",
            "output_index": state.output_item_index,
            "content_index": state.content_part_index,
            "text": state.accumulated_text,
        })
        yield sse_event("response.content_part.done", {
            "type": "response.content_part.done",
            "output_index": state.output_item_index,
            "content_index": state.content_part_index,
            "part": {"type": "output_text", "text": state.accumulated_text, "annotations": []},
        })
        state.content_part_open = False

    if state.message_open:
        msg_item = {
            "type": "message",
            "id": state.message_id,
            "role": "assistant",
            "content": [{"type": "output_text", "text": state.accumulated_text, "annotations": []}],
            "status": "completed",
        }
        yield sse_event("response.output_item.done", {
            "type": "response.output_item.done",
            "output_index": state.output_item_index,
            "item": msg_item,
        })
        state.output.append(msg_item)
        state.message_open = False
        state.output_item_index += 1
        state.accumulated_text = ""


async def _finalize(
    state: StreamState, finish_reason: str
) -> AsyncGenerator[str, None]:
    """Emit closing events and response.completed."""
    # Close any open message
    if state.message_open:
        async for event in _close_message(state):
            yield event

    # Close tool call items
    for tc in state.tool_calls:
        if not tc.get("_fc_id"):
            continue

        out_idx = tc["_output_index"]

        yield sse_event("response.function_call_arguments.done", {
            "type": "response.function_call_arguments.done",
            "output_index": out_idx,
            "item_id": tc["_fc_id"],
            "arguments": tc["arguments"],
        })

        fc_item = {
            "type": "function_call",
            "id": tc["_fc_id"],
            "call_id": tc["id"],
            "name": tc["name"],
            "arguments": tc["arguments"],
            "status": "completed",
        }
        yield sse_event("response.output_item.done", {
            "type": "response.output_item.done",
            "output_index": out_idx,
            "item": fc_item,
        })
        state.output.append(fc_item)

    # Determine final status
    status_map = {
        "stop": "completed",
        "length": "incomplete",
        "tool_calls": "completed",
        "content_filter": "incomplete",
    }
    status = status_map.get(finish_reason, "completed")
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

    yield sse_event("response.completed", {
        "type": "response.completed",
        "response": full_response,
    })


def _convert_usage(usage: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "input_tokens": usage.get("prompt_tokens", 0),
        "output_tokens": usage.get("completion_tokens", 0),
        "total_tokens": usage.get("total_tokens", 0),
    }
    details_in = usage.get("prompt_tokens_details")
    details_out = usage.get("completion_tokens_details")
    if details_in:
        result["input_tokens_details"] = {"cached_tokens": details_in.get("cached_tokens", 0)}
    if details_out:
        result["output_tokens_details"] = {"reasoning_tokens": details_out.get("reasoning_tokens", 0)}
    return result
