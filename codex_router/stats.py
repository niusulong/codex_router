"""Request statistics tracker for Codex Router."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field


@dataclass
class RequestStats:
    """Lightweight in-memory request statistics."""

    start_time: float = field(default_factory=time.time)
    _requests: deque[dict] = field(default_factory=lambda: deque(maxlen=200))

    def record(
        self,
        model: str,
        status: int,
        latency_ms: float,
        method: str = "http",
        error: str | None = None,
    ) -> None:
        self._requests.append({
            "timestamp": time.time(),
            "model": model,
            "status": status,
            "latency_ms": round(latency_ms),
            "method": method,
            "error": error,
        })

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
        }

    def get_recent(self, limit: int = 100) -> list[dict]:
        items = list(self._requests)
        return items[-limit:]
