"""Lane routing — routing lane vs escalation lane.

Uses classifier results to decide whether a task stays on the default
routing lane or gets escalated to a stronger model lane.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Set

import yaml

from .classifier import classify
from .loop_guard import LoopGuard


@dataclass
class RoutingDecision:
    task_id: str
    triggered_groups: Set[str]
    lane: str  # "routing" or "escalation"
    tier: str
    reason: str


_DEFAULT_CONFIG = {
    "routing_lane": {"tier": "standard"},
    "escalation_lane": {"tier": "advanced"},
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
        Task context dict.  Must contain ``task_id``.
    config:
        Parsed conductor.yaml (or override dict).  Falls back to defaults.
    loop_guard:
        Optional LoopGuard instance to enforce one-escalation-per-task.
    """
    cfg = {**_DEFAULT_CONFIG, **(config or {})}
    task_id: str = ctx["task_id"]
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

    return RoutingDecision(
        task_id=task_id,
        triggered_groups=groups,
        lane=lane,
        tier=lane_cfg["tier"],
        reason=reason,
    )
