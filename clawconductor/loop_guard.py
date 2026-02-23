"""One-escalation-per-task guard.

Tracks which task_ids have already been escalated and prevents repeat
escalations to avoid infinite escalation loops.
"""

from __future__ import annotations

import threading


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
