"""Convert Anthropic Messages SSE stream to Responses API SSE stream."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator

from codex_router.converters.common import (
    MESSAGE_ID_PREFIX,
    RESPONSE_ID_PREFIX,
    gen_id,
)
from codex_router.converters.streaming import StreamState, _skeleton_response, sse_event

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


async def convert_anthropic_stream(
    upstream_lines: AsyncGenerator[str, None],
    model: str,
) -> AsyncGenerator[str, None]:
    """Consume Anthropic SSE lines, yield Responses API SSE event strings."""
    state = AnthropicStreamState(
        response_id=gen_id(RESPONSE_ID_PREFIX),
        model=model,
        created_at=int(time.time()),
    )

    current_event_type = ""

    async for line in upstream_lines:
        stripped = line.strip()
        if not stripped:
            current_event_type = ""
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

        async for event_type, event_data in _process_anthropic_event(current_event_type, data, state):
            yield sse_event(event_type, event_data)


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

    async for line in upstream_lines:
        stripped = line.strip()
        if not stripped:
            current_event_type = ""
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

        async for event_type, event_data in _process_anthropic_event(current_event_type, data, state):
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
                "response": _skeleton_anthropic_response(state),
            }
            yield "response.in_progress", {
                "type": "response.in_progress",
                "response": _skeleton_anthropic_response(state),
            }

    elif event_type == "content_block_start":
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

    elif event_type == "content_block_delta":
        delta = data.get("delta", {})
        text_delta = delta.get("text", "")
        if text_delta:
            yield "response.output_text.delta", {
                "type": "response.output_text.delta",
                "output_index": state.output_item_index,
                "content_index": state.content_part_index,
                "delta": text_delta,
            }
            state.accumulated_text += text_delta

    elif event_type == "content_block_stop":
        pass  # handled in message_delta/message_stop

    elif event_type == "message_delta":
        delta = data.get("delta", {})
        usage = data.get("usage", {})
        if usage:
            state.usage["output_tokens"] = usage.get("output_tokens", 0)

    elif event_type == "message_stop":
        if not state.finalized:
            async for event_type, event_data in _finalize_anthropic_events(state, "completed"):
                yield event_type, event_data


async def _finalize_anthropic_events(
    state: AnthropicStreamState, status: str,
) -> AsyncGenerator[tuple[str, dict[str, Any]], None]:
    """Emit closing events for Anthropic stream."""
    if state.message_open:
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

    state.finalized = True

    total_tokens = state.usage.get("input_tokens", 0) + state.usage.get("output_tokens", 0)
    state.usage["total_tokens"] = total_tokens

    full_response: dict[str, Any] = {
        "id": state.response_id,
        "object": "response",
        "created_at": state.created_at,
        "model": state.model,
        "status": status,
        "output": state.output,
        "output_text": state.accumulated_text,
        "usage": state.usage,
        "metadata": {},
    }

    yield "response.completed", {
        "type": "response.completed",
        "response": full_response,
    }


def _skeleton_anthropic_response(state: AnthropicStreamState) -> dict[str, Any]:
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
