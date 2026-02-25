"""Lane routing — routing lane vs escalation lane.

Uses classifier results to decide whether a task stays on the default
routing lane or gets escalated to a stronger model lane.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Dict, Set

import yaml

from .classifier import classify
from .loop_guard import LoopGuard


@dataclass
class RoutingDecision:
    task_id: str
    trace_id: str
    triggered_groups: Set[str]
    lane: str  # "routing", "escalation", or "fallback"
    tier: str
    reason: str


_DEFAULT_CONFIG = {
    "routing_lane": {"tier": "standard"},
    "escalation_lane": {"tier": "advanced"},
    "fallback_lane": {"tier": "lightweight"},
}


def load_config(path: str = "conductor.yaml") -> dict:
    try:
        with open(path) as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}


def route(
    ctx: Dict[str, Any],
    *,
    config: dict | None = None,
    loop_guard: LoopGuard | None = None,
) -> RoutingDecision:
    """Evaluate triggers and return a routing decision.

    Parameters
    ----------
    ctx:
        Task context dict.  Must contain ``task_id``.  Optional ``trace_id``
        (UUID string) will be used if provided; otherwise a fresh UUID is generated.
        Optional ``retry_count`` (int, default 0) tracks how many times this task
        has already been retried.  Optional ``max_retries`` (int, default 2) caps
        the number of retries; when ``retry_count`` exceeds ``max_retries`` the
        task is routed to ``lane="fallback"`` unconditionally and normal routing
        logic is skipped.
    config:
        Parsed conductor.yaml (or override dict).  Falls back to defaults.
    loop_guard:
        Optional LoopGuard instance to enforce one-escalation-per-task.
    """
    cfg = {**_DEFAULT_CONFIG, **(config or {})}
    task_id: str = ctx["task_id"]
    trace_id: str = ctx.get("trace_id") or str(uuid.uuid4())

    retry_count: int = ctx.get("retry_count", 0)
    max_retries: int = ctx.get("max_retries", 2)

    # Retry cap: bypass normal routing when retries are exhausted.
    if retry_count > max_retries:
        lane_cfg = cfg.get("fallback_lane", _DEFAULT_CONFIG["fallback_lane"])
        decision = RoutingDecision(
            task_id=task_id,
            trace_id=trace_id,
            triggered_groups=set(),
            lane="fallback",
            tier=lane_cfg["tier"],
            reason="max retries exceeded",
        )
        from .logger import log_decision  # lazy import to avoid circular dependency
        log_decision(decision)
        return decision

    groups = classify(ctx)

    escalate = bool(groups)
    guard_blocked = False

    # Enforce one escalation per task_id
    if escalate and loop_guard is not None:
        if not loop_guard.allow(task_id):
            escalate = False
            guard_blocked = True

    if escalate:
        lane_cfg = cfg.get("escalation_lane", _DEFAULT_CONFIG["escalation_lane"])
        lane = "escalation"
        reason = f"triggered groups: {sorted(groups)}"
    else:
        lane_cfg = cfg.get("routing_lane", _DEFAULT_CONFIG["routing_lane"])
        lane = "routing"
        if guard_blocked:
            reason = "escalation already used for this task"
        else:
            reason = "no triggers fired"

    decision = RoutingDecision(
        task_id=task_id,
        trace_id=trace_id,
        triggered_groups=groups,
        lane=lane,
        tier=lane_cfg["tier"],
        reason=reason,
    )

    from .logger import log_decision  # lazy import to avoid circular dependency
    log_decision(decision)

    return decision
