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


def test_custom_config_models():
    cfg = {
        "routing_lane": {"model": "gpt-4o-mini"},
        "escalation_lane": {"model": "o1-preview"},
    }
    decision = route(_base_ctx(task_class="plan"), config=cfg)
    assert decision.model == "o1-preview"

    decision = route(_base_ctx(), config=cfg)
    assert decision.model == "gpt-4o-mini"


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
