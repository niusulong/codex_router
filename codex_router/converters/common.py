"""Shared utilities for converters."""

from __future__ import annotations

import uuid

# Status mapping: Chat Completions finish_reason → Responses API status
STATUS_MAP = {
    "stop": "completed",
    "length": "incomplete",
    "tool_calls": "completed",
    "content_filter": "incomplete",
}

# ID prefix constants
RESPONSE_ID_PREFIX = "resp_"
MESSAGE_ID_PREFIX = "msg_"
FUNCTION_CALL_ID_PREFIX = "fc_"
CALL_ID_PREFIX = "call_"

# UUID truncation length
_ID_LENGTH = 24


def gen_id(prefix: str) -> str:
    """Generate a prefixed ID using truncated UUID4 hex."""
    return f"{prefix}{uuid.uuid4().hex[:_ID_LENGTH]}"


def convert_usage(usage: dict) -> dict:
    """Convert Chat Completions usage to Responses API usage."""
    result: dict = {
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
