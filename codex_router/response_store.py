"""In-memory response store for previous_response_id support."""

from __future__ import annotations

import logging
from collections import deque
from typing import Any

logger = logging.getLogger(__name__)


class ResponseStore:
    """Stores completed responses keyed by response_id for conversation chaining."""

    def __init__(self, maxlen: int = 200):
        self._store: dict[str, dict[str, Any]] = {}
        self._order: deque[str] = deque(maxlen=maxlen)

    def store(self, response_id: str, input_items: list | str, output: list[dict[str, Any]]) -> None:
        self._store[response_id] = {
            "input_items": input_items,
            "output": output,
        }
        self._order.append(response_id)

    def resolve(self, body: dict[str, Any]) -> dict[str, Any]:
        """Resolve previous_response_id by prepending conversation history to input.

        Returns a new body dict with the input field expanded to include history.
        """
        prev_id = body.get("previous_response_id")
        if not prev_id:
            return body

        prev = self._store.get(prev_id)
        if not prev:
            logger.warning("previous_response_id %s not found in store", prev_id)
            return body

        history: list[dict[str, Any]] = []

        # Previous request input items
        prev_input = prev.get("input_items")
        if isinstance(prev_input, list):
            for item in prev_input:
                if isinstance(item, dict):
                    history.append(item)
                else:
                    history.append(item.model_dump() if hasattr(item, "model_dump") else item)
        elif isinstance(prev_input, str):
            history.append({
                "type": "message",
                "role": "user",
                "content": prev_input,
            })

        # Previous response output (assistant messages + function calls)
        for item in prev.get("output", []):
            item_type = item.get("type")
            if item_type == "message":
                content = item.get("content", [])
                if isinstance(content, list):
                    text = "".join(
                        part.get("text", "") for part in content if isinstance(part, dict)
                    )
                else:
                    text = str(content)
                history.append({
                    "type": "message",
                    "role": item.get("role", "assistant"),
                    "content": text,
                })
            elif item_type == "function_call":
                history.append({
                    "type": "function_call",
                    "call_id": item.get("call_id", ""),
                    "name": item.get("name", ""),
                    "arguments": item.get("arguments", ""),
                })

        # Current input
        current_input = body.get("input", [])
        if isinstance(current_input, str):
            history.append({
                "type": "message",
                "role": "user",
                "content": current_input,
            })
        elif isinstance(current_input, list):
            history.extend(current_input)

        body = dict(body)  # shallow copy
        body["input"] = history
        del body["previous_response_id"]
        return body
