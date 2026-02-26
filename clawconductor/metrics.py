"""In-memory metrics for the live /admin/status endpoint.

Tracks request counts and escalation triggers since last process start.
Resets at midnight via /admin/reset-metrics. Complements events.py
(which is the persistent historical store).

Thread-safe via a single Lock.
"""

from __future__ import annotations

import threading
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any


class Metrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._date: str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self._routing_requests: int = 0
        self._escalation_requests: int = 0
        self._escalation_triggers: dict[str, int] = defaultdict(int)
        self._last_request_at: dict[str, str] = {}
        self._last_heartbeat_at: float = 0.0  # monotonic

    # --- Write methods ---

    def record_routing(self) -> None:
        with self._lock:
            self._routing_requests += 1
            self._last_request_at["routing"] = datetime.now(timezone.utc).isoformat()

    def record_escalation(self, groups: set[str] | list[str]) -> None:
        with self._lock:
            self._escalation_requests += 1
            for g in groups:
                self._escalation_triggers[g] += 1
            self._last_request_at["escalation"] = datetime.now(timezone.utc).isoformat()

    def needs_heartbeat(self, interval_seconds: float = 1800.0) -> bool:
        """True if it's been more than interval_seconds since the last heartbeat."""
        import time
        with self._lock:
            return (time.monotonic() - self._last_heartbeat_at) >= interval_seconds

    def mark_heartbeat(self) -> None:
        import time
        with self._lock:
            self._last_heartbeat_at = time.monotonic()

    def reset(self) -> None:
        with self._lock:
            self._date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            self._routing_requests = 0
            self._escalation_requests = 0
            self._escalation_triggers = defaultdict(int)
            self._last_request_at = {}

    # --- Read methods ---

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "date": self._date,
                "routing_requests": self._routing_requests,
                "escalation_requests": self._escalation_requests,
                "escalation_triggers": dict(self._escalation_triggers),
                "last_request_at": dict(self._last_request_at),
            }


# Module-level singleton
metrics = Metrics()
