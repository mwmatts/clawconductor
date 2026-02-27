"""One-escalation-per-task guard and Group A escalation cooldown.

LoopGuard tracks which task_ids have already been escalated and prevents
repeat escalations to avoid infinite escalation loops.

EscalationCooldown suppresses Group-A-only re-escalation when the same
keyword fires again within a configurable window (e.g. carry-over from a
follow-up message that references the original task).
"""

from __future__ import annotations

import time
import threading
from dataclasses import dataclass


class LoopGuard:
    """Thread-safe guard that allows at most one escalation per task_id."""

    def __init__(self) -> None:
        self._seen: set[str] = set()
        self._lock = threading.Lock()

    def allow(self, task_id: str) -> bool:
        """Return True (and record) if this task_id has not been escalated yet."""
        with self._lock:
            if task_id in self._seen:
                return False
            self._seen.add(task_id)
            return True

    def has_escalated(self, task_id: str) -> bool:
        with self._lock:
            return task_id in self._seen

    def reset(self, task_id: str | None = None) -> None:
        """Reset one task or all tasks."""
        with self._lock:
            if task_id is None:
                self._seen.clear()
            else:
                self._seen.discard(task_id)


@dataclass
class _EscalationRecord:
    timestamp: float
    keyword: str


class EscalationCooldown:
    """Suppress Group-A-only escalation when the same keyword fires again
    within *cooldown_seconds* of the previous escalation.

    This prevents carry-over false positives where the user's follow-up
    message incidentally contains the keyword that triggered the original
    escalation (e.g. "that research looks good — now do X").
    """

    def __init__(self, cooldown_seconds: float = 300.0) -> None:
        self._cooldown = cooldown_seconds
        self._last: _EscalationRecord | None = None
        self._lock = threading.Lock()

    def should_suppress(self, keyword: str) -> bool:
        """Return True if this Group A keyword hit should be suppressed.

        Suppresses when the *same* keyword fired within the cooldown window.
        A different keyword is never suppressed — it likely signals a new task.
        """
        with self._lock:
            if self._last is None:
                return False
            elapsed = time.monotonic() - self._last.timestamp
            if elapsed > self._cooldown:
                self._last = None  # expired — clear for next escalation
                return False
            return keyword == self._last.keyword

    def record(self, keyword: str) -> None:
        """Record that a Group A escalation just fired for *keyword*."""
        with self._lock:
            self._last = _EscalationRecord(
                timestamp=time.monotonic(),
                keyword=keyword,
            )

    def reset(self) -> None:
        """Clear cooldown state (e.g. on daily metrics reset)."""
        with self._lock:
            self._last = None
