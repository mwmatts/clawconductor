"""Tests for clawconductor.router."""

import pytest

from clawconductor.loop_guard import LoopGuard
from clawconductor.router import route


def _base_ctx(**overrides):
    ctx = {"task_id": "t-1", "signals": []}
    ctx.update(overrides)
    return ctx


def test_no_triggers_routes_to_routing_lane():
    decision = route(_base_ctx())
    assert decision.lane == "routing"
    assert decision.triggered_groups == set()


def test_group_a_escalates():
    decision = route(_base_ctx(task_class="debugging"))
    assert decision.lane == "escalation"
    assert "A" in decision.triggered_groups


def test_custom_config_tiers():
    cfg = {
        "routing_lane": {"tier": "standard"},
        "escalation_lane": {"tier": "advanced"},
    }
    decision = route(_base_ctx(task_class="plan"), config=cfg)
    assert decision.tier == "advanced"

    decision = route(_base_ctx(), config=cfg)
    assert decision.tier == "standard"


def test_loop_guard_blocks_second_escalation():
    guard = LoopGuard()
    ctx = _base_ctx(task_class="plan")

    d1 = route(ctx, loop_guard=guard)
    assert d1.lane == "escalation"

    d2 = route(ctx, loop_guard=guard)
    assert d2.lane == "routing"
    assert "already used" in d2.reason


def test_different_task_ids_escalate_independently():
    guard = LoopGuard()
    d1 = route(_base_ctx(task_class="plan"), loop_guard=guard)
    assert d1.lane == "escalation"

    ctx2 = _base_ctx(task_class="design")
    ctx2["task_id"] = "t-2"
    d2 = route(ctx2, loop_guard=guard)
    assert d2.lane == "escalation"


def test_max_retries_exceeded_routes_to_fallback():
    """When retry_count exceeds max_retries, the decision lane must be 'fallback'."""
    # retry_count=3 > max_retries=2 → fallback, no normal routing logic runs
    decision = route(_base_ctx(retry_count=3, max_retries=2))
    assert decision.lane == "fallback"
    assert decision.reason == "max retries exceeded"


def test_max_retries_at_limit_still_routes_normally():
    """retry_count == max_retries is NOT exceeded — normal routing applies."""
    decision = route(_base_ctx(retry_count=2, max_retries=2))
    assert decision.lane in ("routing", "escalation")  # normal, not fallback
