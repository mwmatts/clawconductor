"""Tests for clawconductor.logger."""

from __future__ import annotations

import json

import pytest

import clawconductor.logger as logger_mod
from clawconductor.logger import log_model_call
from clawconductor.router import route


def _ctx(**kwargs):
    ctx = {"task_id": "t-test", "signals": []}
    ctx.update(kwargs)
    return ctx


# ---------------------------------------------------------------------------
# log_decision (via route)
# ---------------------------------------------------------------------------

def test_log_decision_creates_file():
    route(_ctx())
    assert logger_mod._DECISION_LOG.exists()


def test_log_decision_routing_fields():
    route(_ctx())
    entry = json.loads(logger_mod._DECISION_LOG.read_text().strip())
    assert entry["component"] == "clawconductor"
    assert entry["task_id"] == "t-test"
    assert entry["lane"] == "routing"
    assert entry["escalation_decision"] is False
    assert isinstance(entry["triggered_groups"], list)
    assert "trace_id" in entry
    assert "timestamp" in entry
    assert "tier" in entry
    assert "reason" in entry


def test_log_decision_escalation_fields():
    route(_ctx(task_class="debugging"))
    entry = json.loads(logger_mod._DECISION_LOG.read_text().strip())
    assert entry["lane"] == "escalation"
    assert entry["escalation_decision"] is True
    assert "A" in entry["triggered_groups"]


def test_trace_id_propagated_from_ctx():
    route(_ctx(trace_id="my-custom-trace-id"))
    entry = json.loads(logger_mod._DECISION_LOG.read_text().strip())
    assert entry["trace_id"] == "my-custom-trace-id"


def test_trace_id_auto_generated_when_absent():
    route(_ctx())
    entry = json.loads(logger_mod._DECISION_LOG.read_text().strip())
    assert entry["trace_id"]  # non-empty UUID generated


def test_multiple_decisions_appended():
    route(_ctx(task_id="t-a"))
    route(_ctx(task_id="t-b"))
    lines = logger_mod._DECISION_LOG.read_text().strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["task_id"] == "t-a"
    assert json.loads(lines[1])["task_id"] == "t-b"


# ---------------------------------------------------------------------------
# log_model_call
# ---------------------------------------------------------------------------

def test_log_model_call_creates_file():
    log_model_call("trace-1", "claude-haiku-4-5", 100, 50, 0.0012)
    assert logger_mod._COST_LOG.exists()


def test_log_model_call_fields():
    log_model_call("trace-abc", "claude-sonnet-4-6", 200, 150, 0.0045)
    entry = json.loads(logger_mod._COST_LOG.read_text().strip())
    assert entry["trace_id"] == "trace-abc"
    assert entry["component"] == "clawconductor"
    assert entry["model"] == "claude-sonnet-4-6"
    assert entry["input_tokens"] == 200
    assert entry["output_tokens"] == 150
    assert entry["estimated_cost"] == pytest.approx(0.0045)
    assert "timestamp" in entry


def test_log_model_call_appends_multiple():
    log_model_call("t1", "haiku", 10, 5, 0.001)
    log_model_call("t2", "sonnet", 20, 10, 0.002)
    lines = logger_mod._COST_LOG.read_text().strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["trace_id"] == "t1"
    assert json.loads(lines[1])["trace_id"] == "t2"
