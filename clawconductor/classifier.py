"""Groups A-E escalation trigger evaluation.

Each group function takes a task context dict and returns True if the
escalation trigger fires.  ``classify`` returns the set of triggered groups.
"""

from __future__ import annotations

from typing import Any, Dict, Set

# Group A — explicit task-class flags
GROUP_A_FLAGS: set[str] = {
    "plan",
    "design",
    "architecture",
    "debugging",
    "strategy",
    "synthesis",
    "research",
}


def check_group_a(ctx: Dict[str, Any]) -> bool:
    """Explicit task-class flag present."""
    task_class = ctx.get("task_class", "")
    if isinstance(task_class, str):
        return task_class.lower() in GROUP_A_FLAGS
    return False


def check_group_b(ctx: Dict[str, Any]) -> bool:
    """Consecutive tool failures >= 2."""
    return ctx.get("consecutive_tool_failures", 0) >= 2


def check_group_c(ctx: Dict[str, Any]) -> bool:
    """Missing required input, conflicting constraints, or requires tradeoff reasoning."""
    signals = {"missing_required_input", "conflicting_constraints", "requires_tradeoff_reasoning"}
    return bool(signals & set(ctx.get("signals", [])))


def check_group_d(ctx: Dict[str, Any]) -> bool:
    """Validation failed on retry."""
    return ctx.get("validation_failed", False) and ctx.get("retry_count", 0) >= 1


def check_group_e(ctx: Dict[str, Any]) -> bool:
    """Irreversible change, security-sensitive, or high downstream cost."""
    signals = {"irreversible_change", "security_sensitive", "high_downstream_cost"}
    return bool(signals & set(ctx.get("signals", [])))


_CHECKS = {
    "A": check_group_a,
    "B": check_group_b,
    "C": check_group_c,
    "D": check_group_d,
    "E": check_group_e,
}


def classify(ctx: Dict[str, Any]) -> Set[str]:
    """Return the set of triggered escalation groups (e.g. {"A", "C"})."""
    return {name for name, fn in _CHECKS.items() if fn(ctx)}
