"""Tests for clawconductor.loop_guard."""

from clawconductor.loop_guard import LoopGuard


def test_allow_first_call():
    guard = LoopGuard()
    assert guard.allow("t-1") is True


def test_block_second_call():
    guard = LoopGuard()
    guard.allow("t-1")
    assert guard.allow("t-1") is False


def test_different_ids_independent():
    guard = LoopGuard()
    assert guard.allow("t-1") is True
    assert guard.allow("t-2") is True


def test_has_escalated():
    guard = LoopGuard()
    assert guard.has_escalated("t-1") is False
    guard.allow("t-1")
    assert guard.has_escalated("t-1") is True


def test_reset_single():
    guard = LoopGuard()
    guard.allow("t-1")
    guard.allow("t-2")
    guard.reset("t-1")
    assert guard.allow("t-1") is True
    assert guard.allow("t-2") is False


def test_reset_all():
    guard = LoopGuard()
    guard.allow("t-1")
    guard.allow("t-2")
    guard.reset()
    assert guard.allow("t-1") is True
    assert guard.allow("t-2") is True
