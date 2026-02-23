"""Structured JSON decision logging."""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

from .router import RoutingDecision

_logger = logging.getLogger("clawconductor")


def setup_logging(*, level: int = logging.INFO, stream: Any = None) -> None:
    """Configure the clawconductor logger with JSON output."""
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(_JsonFormatter())
    _logger.handlers.clear()
    _logger.addHandler(handler)
    _logger.setLevel(level)


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "msg": record.getMessage(),
        }
        if hasattr(record, "extra"):
            entry.update(record.extra)  # type: ignore[arg-type]
        return json.dumps(entry, default=str)


def log_decision(decision: RoutingDecision) -> None:
    """Emit a structured log entry for a routing decision."""
    extra = {
        "task_id": decision.task_id,
        "triggered_groups": sorted(decision.triggered_groups),
        "lane": decision.lane,
        "model": decision.model,
        "reason": decision.reason,
    }
    record = _logger.makeRecord(
        name=_logger.name,
        level=logging.INFO,
        fn="",
        lno=0,
        msg="routing_decision",
        args=(),
        exc_info=None,
    )
    record.extra = extra  # type: ignore[attr-defined]
    _logger.handle(record)
