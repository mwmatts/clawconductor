"""Tests for clawconductor.classifier."""

import pytest

import clawconductor.classifier as _classifier_mod
from clawconductor.classifier import (
    check_group_a,
    check_group_b,
    check_group_c,
    check_group_d,
    check_group_e,
    classify,
)


# --- Group A ---

@pytest.mark.parametrize("task_class", [
    "plan", "design", "architecture", "debugging", "strategy", "synthesis", "research",
])
def test_group_a_fires_on_valid_flags(task_class):
    assert check_group_a({"task_class": task_class}) is True


def test_group_a_case_insensitive():
    assert check_group_a({"task_class": "PLAN"}) is True


def test_group_a_no_flag():
    assert check_group_a({}) is False
    assert check_group_a({"task_class": "regular"}) is False


# --- Group A phrase matching ---

def test_group_a_fires_on_phrase():
    """A phrase in TRIGGER_PHRASES fires Group A via message_text."""
    _classifier_mod.TRIGGER_PHRASES[:] = ["help me decide"]
    try:
        assert check_group_a({"message_text": "I need help me decide between these two options"}) is True
    finally:
        _classifier_mod.TRIGGER_PHRASES.clear()


def test_group_a_phrase_no_match():
    """A message with no matching phrase does not fire Group A via phrase path."""
    _classifier_mod.TRIGGER_PHRASES[:] = ["help me decide"]
    try:
        assert check_group_a({"message_text": "just tell me what happened"}) is False
    finally:
        _classifier_mod.TRIGGER_PHRASES.clear()


def test_group_a_phrases_disabled_when_list_empty():
    """Phrase matching is off by default (empty TRIGGER_PHRASES)."""
    assert _classifier_mod.TRIGGER_PHRASES == []
    assert check_group_a({"message_text": "help me decide which database to use"}) is False


# --- configure() trigger_words loading ---

def test_configure_loads_custom_trigger_words():
    """configure() with a trigger_words list replaces GROUP_A_FLAGS with those words."""
    saved = set(_classifier_mod.GROUP_A_FLAGS)
    try:
        _classifier_mod.configure({"trigger_words": ["foo", "bar", "baz"]})
        assert _classifier_mod.GROUP_A_FLAGS == {"foo", "bar", "baz"}
    finally:
        _classifier_mod.GROUP_A_FLAGS.clear()
        _classifier_mod.GROUP_A_FLAGS.update(saved)


def test_configure_falls_back_to_defaults_when_no_trigger_words():
    """configure() with no trigger_words field resets GROUP_A_FLAGS to the built-in defaults."""
    saved = set(_classifier_mod.GROUP_A_FLAGS)
    try:
        _classifier_mod.configure({"trigger_words": ["custom"]})
        assert _classifier_mod.GROUP_A_FLAGS == {"custom"}
        _classifier_mod.configure({})
        assert "plan" in _classifier_mod.GROUP_A_FLAGS
        assert "research" in _classifier_mod.GROUP_A_FLAGS
        assert "custom" not in _classifier_mod.GROUP_A_FLAGS
    finally:
        _classifier_mod.GROUP_A_FLAGS.clear()
        _classifier_mod.GROUP_A_FLAGS.update(saved)


# --- Group B ---

def test_group_b_fires_at_threshold():
    assert check_group_b({"consecutive_tool_failures": 2}) is True
    assert check_group_b({"consecutive_tool_failures": 5}) is True


def test_group_b_below_threshold():
    assert check_group_b({"consecutive_tool_failures": 1}) is False
    assert check_group_b({}) is False


# --- Group C ---

def test_group_c_fires_on_signals():
    for sig in ("missing_required_input", "conflicting_constraints", "requires_tradeoff_reasoning"):
        assert check_group_c({"signals": [sig]}) is True


def test_group_c_no_signals():
    assert check_group_c({"signals": []}) is False
    assert check_group_c({}) is False


# --- Group D ---

def test_group_d_validation_failed_on_retry():
    assert check_group_d({"validation_failed": True, "retry_count": 1}) is True
    assert check_group_d({"validation_failed": True, "retry_count": 3}) is True


def test_group_d_no_retry():
    assert check_group_d({"validation_failed": True, "retry_count": 0}) is False


def test_group_d_no_failure():
    assert check_group_d({"validation_failed": False, "retry_count": 2}) is False


# --- Group E ---

def test_group_e_fires_on_signals():
    for sig in ("irreversible_change", "security_sensitive", "high_downstream_cost"):
        assert check_group_e({"signals": [sig]}) is True


def test_group_e_no_signals():
    assert check_group_e({"signals": []}) is False


# --- classify ---

def test_classify_multiple_groups():
    ctx = {
        "task_class": "plan",
        "consecutive_tool_failures": 3,
        "signals": ["security_sensitive"],
    }
    groups = classify(ctx)
    assert groups == {"A", "B", "E"}


def test_classify_empty():
    assert classify({"task_class": "regular", "signals": []}) == set()
