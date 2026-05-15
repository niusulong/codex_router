"""Request statistics tracker for Codex Router."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from codex_router.token_db import TokenDB


@dataclass
class RequestStats:
    """Lightweight in-memory request statistics."""

    start_time: float = field(default_factory=time.time)
    token_db: TokenDB | None = field(default=None, repr=False)
    _requests: deque[dict] = field(default_factory=lambda: deque(maxlen=200))

    def record(
        self,
        model: str,
        status: int,
        latency_ms: float,
        method: str = "http",
        error: str | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        total_tokens: int = 0,
        preset_name: str = "default",
    ) -> None:
        self._requests.append({
            "timestamp": time.time(),
            "model": model,
            "status": status,
            "latency_ms": round(latency_ms),
            "method": method,
            "error": error,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "preset_name": preset_name,
        })
        if self.token_db is not None and total_tokens > 0:
            self.token_db.record(
                preset_name=preset_name,
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                method=method,
            )

    def get_summary(self) -> dict:
        total = len(self._requests)
        success = sum(1 for r in self._requests if 200 <= r["status"] < 300)
        fail = total - success
        avg_latency = (
            round(sum(r["latency_ms"] for r in self._requests) / total)
            if total > 0 else 0
        )
        last_ts = self._requests[-1]["timestamp"] if self._requests else None
        return {
            "uptime_seconds": round(time.time() - self.start_time),
            "total_requests": total,
            "success_count": success,
            "fail_count": fail,
            "active_connections": 0,
            "avg_latency_ms": avg_latency,
            "last_request_at": last_ts,
            "total_input_tokens": sum(r.get("input_tokens", 0) for r in self._requests),
            "total_output_tokens": sum(r.get("output_tokens", 0) for r in self._requests),
            "total_tokens_all": sum(r.get("total_tokens", 0) for r in self._requests),
        }

    def get_recent(self, limit: int = 100) -> list[dict]:
        items = list(self._requests)
        return items[-limit:]
