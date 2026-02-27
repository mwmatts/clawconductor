"""Groups A-E escalation trigger evaluation.

Each group function takes a task context dict and returns True if the
escalation trigger fires.  ``classify`` returns the set of triggered groups.
"""

from __future__ import annotations

from typing import Any, Dict, Set

# Default trigger words used when conductor.yaml has no trigger_words field.
_DEFAULT_TRIGGER_WORDS: frozenset[str] = frozenset({
    "plan",
    "design",
    "architecture",
    "debug",
    "debugging",
    "strategy",
    "synthesis",
    "research",
    "refactor",
    "optimize",
    "migrate",
    "audit",
    "review",
    "diagnose",
    "evaluate",
    "analyze",
    "compare",
    "recommend",
})

# Active trigger word set — populated from conductor.yaml at startup via configure().
# Starts equal to the defaults so the classifier works without any config call.
GROUP_A_FLAGS: set[str] = set(_DEFAULT_TRIGGER_WORDS)


def configure(config: dict) -> None:
    """Update GROUP_A_FLAGS from the trigger_words list in config.

    Supports both one-word-per-item and comma-separated-per-item formats,
    matching the template examples in the README.  Falls back to
    _DEFAULT_TRIGGER_WORDS if trigger_words is absent or empty.
    """
    raw = config.get("trigger_words")
    words: set[str] = set()
    if raw and isinstance(raw, list):
        for item in raw:
            for word in str(item).split(","):
                w = word.strip().lower()
                if w:
                    words.add(w)
    GROUP_A_FLAGS.clear()
    GROUP_A_FLAGS.update(words if words else _DEFAULT_TRIGGER_WORDS)


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
