"""Structured JSON decision logging."""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .router import RoutingDecision

_logger = logging.getLogger("clawconductor")

_LOG_DIR = Path.home() / ".openclaw" / "logs"
_DECISION_LOG = _LOG_DIR / "clawconductor.log"
_COST_LOG = _LOG_DIR / "model-costs.log"


def _ensure_log_dir() -> None:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)


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
    """Write a structured JSON log line for a routing decision.

    Appends one JSON line to ~/.openclaw/logs/clawconductor.log.
    Creates the log directory if it does not exist.
    """
    entry: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "trace_id": decision.trace_id,
        "component": "clawconductor",
        "task_id": decision.task_id,
        "triggered_groups": sorted(decision.triggered_groups),
        "escalation_decision": decision.lane == "escalation",
        "lane": decision.lane,
        "tier": decision.tier,
        "reason": decision.reason,
    }
    _ensure_log_dir()
    with _DECISION_LOG.open("a") as fh:
        fh.write(json.dumps(entry) + "\n")


def log_model_call(
    trace_id: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    estimated_cost: float,
) -> None:
    """Write a structured JSON log line for a model API call.

    Appends one JSON line to ~/.openclaw/logs/model-costs.log.
    Can be imported and called by any component in the agent stack.
    """
    entry: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "trace_id": trace_id,
        "component": "clawconductor",
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "estimated_cost": estimated_cost,
    }
    _ensure_log_dir()
    with _COST_LOG.open("a") as fh:
        fh.write(json.dumps(entry) + "\n")
