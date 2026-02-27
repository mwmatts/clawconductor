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

# Layperson phrase examples from the README — provided as a reference default set.
# NOT active unless loaded via configure() from conductor.yaml trigger_phrases.
_DEFAULT_TRIGGER_PHRASES: tuple[str, ...] = (
    "figure out",
    "help me understand",
    "what should i",
    "how do i",
    "is it better to",
    "what's the difference",
    "walk me through",
    "i'm not sure",
    "i don't know how",
    "can you explain",
    "why is it",
    "what would happen if",
    "help me decide",
    "what are my options",
    "something is wrong",
    "it's not working",
    "i think i messed up",
)

# Active phrase list — populated from conductor.yaml trigger_phrases at startup.
# Empty by default; phrase matching is disabled until explicitly configured.
TRIGGER_PHRASES: list[str] = []

# Group B failure threshold — updated by configure() from escalation.group_b_failure_threshold.
_GROUP_B_THRESHOLD: int = 2


def configure(config: dict) -> None:
    """Update GROUP_A_FLAGS, TRIGGER_PHRASES, and _GROUP_B_THRESHOLD from config.

    trigger_words: supports one-word-per-item and comma-separated formats.
      Falls back to _DEFAULT_TRIGGER_WORDS if absent or empty.
    trigger_phrases: each item is a phrase for substring match against the
      full user message (case-insensitive). Empty by default — opt-in only.
    escalation.group_b_failure_threshold: consecutive tool failures before
      Group B fires. Defaults to 2 if absent.
    """
    global _GROUP_B_THRESHOLD

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

    raw_phrases = config.get("trigger_phrases")
    phrases: list[str] = []
    if raw_phrases and isinstance(raw_phrases, list):
        for item in raw_phrases:
            p = str(item).strip().lower()
            if p:
                phrases.append(p)
    TRIGGER_PHRASES.clear()
    TRIGGER_PHRASES.extend(phrases)

    escalation_cfg = config.get("escalation", {})
    threshold = escalation_cfg.get("group_b_failure_threshold", 2) if isinstance(escalation_cfg, dict) else 2
    _GROUP_B_THRESHOLD = int(threshold)


def check_group_a(ctx: Dict[str, Any]) -> bool:
    """Task-class keyword or trigger phrase present in the user message."""
    task_class = ctx.get("task_class", "")
    if isinstance(task_class, str) and task_class.lower() in GROUP_A_FLAGS:
        return True
    if TRIGGER_PHRASES:
        message_text = ctx.get("message_text", "").lower()
        if message_text and any(phrase in message_text for phrase in TRIGGER_PHRASES):
            return True
    return False


def check_group_b(ctx: Dict[str, Any]) -> bool:
    """Consecutive tool failures >= group_b_failure_threshold."""
    return ctx.get("consecutive_tool_failures", 0) >= _GROUP_B_THRESHOLD


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
