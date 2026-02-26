"""Tests for clawconductor.events."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

import clawconductor.events as ev


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    """Each test gets its own in-memory-ish DB via a temp file, and resets module state."""
    db_file = tmp_path / "test.db"
    # Reset module-level connection so init() runs fresh
    ev._conn = None
    ev.init(db_path=db_file)
    yield db_file
    # Cleanup: close connection
    if ev._conn:
        ev._conn.close()
        ev._conn = None


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------

def test_init_creates_db(tmp_path):
    db = tmp_path / "new.db"
    ev._conn = None
    ev.init(db_path=db)
    assert db.exists()
    ev._conn.close()
    ev._conn = None


def test_init_is_idempotent(tmp_path):
    db = tmp_path / "idem.db"
    ev._conn = None
    ev.init(db_path=db)
    first_conn = ev._conn
    ev.init(db_path=db)  # second call should be no-op
    assert ev._conn is first_conn  # same object
    ev._conn.close()
    ev._conn = None


# ---------------------------------------------------------------------------
# record
# ---------------------------------------------------------------------------

def test_record_escalation():
    ev.record("escalation", lane="escalation", tier="advanced",
               model="claude-sonnet-4-6", groups=["A", "B"],
               reason="triggered groups: ['A', 'B']", task_id="abc123")
    rows = ev.query(days=1, event_type="escalation")
    assert len(rows) == 1
    r = rows[0]
    assert r["event_type"] == "escalation"
    assert r["model"] == "claude-sonnet-4-6"
    assert r["groups"] == "A,B"
    assert r["task_id"] == "abc123"


def test_record_budget_fallback():
    ev.record("budget_fallback", lane="routing", model="gemini-2.5-flash",
               reason="Budget 429 on routing lane")
    rows = ev.query(days=1, event_type="budget_fallback")
    assert len(rows) == 1
    assert rows[0]["lane"] == "routing"


def test_record_budget_restored():
    ev.record("budget_restored", lane="routing", reason="Budget reset via admin endpoint")
    rows = ev.query(days=1, event_type="budget_restored")
    assert len(rows) == 1


def test_record_startup():
    ev.record("startup", reason="ClawConductor started")
    rows = ev.query_raw("SELECT * FROM events WHERE event_type='startup'")
    assert len(rows) == 1


def test_record_empty_groups():
    ev.record("escalation", groups=[])
    rows = ev.query(days=1, event_type="escalation")
    assert rows[0]["groups"] == ""


def test_record_set_groups():
    ev.record("escalation", groups={"B", "A"})
    rows = ev.query(days=1, event_type="escalation")
    assert rows[0]["groups"] == "A,B"  # sorted


def test_record_is_nonfatal_when_not_init():
    ev._conn = None
    # Should not raise
    ev.record("escalation", lane="routing")


# ---------------------------------------------------------------------------
# query
# ---------------------------------------------------------------------------

def test_query_empty_db():
    assert ev.query(days=1) == []


def test_query_filters_by_event_type():
    ev.record("escalation", lane="escalation")
    ev.record("budget_fallback", lane="routing")
    rows = ev.query(days=1, event_type="escalation")
    assert len(rows) == 1
    assert rows[0]["event_type"] == "escalation"


def test_query_filters_by_lane():
    ev.record("budget_fallback", lane="routing")
    ev.record("budget_fallback", lane="escalation")
    rows = ev.query(days=1, lane="routing")
    assert all(r["lane"] == "routing" for r in rows)


def test_query_excludes_heartbeats_by_default():
    ev.record("heartbeat", model="claude-haiku-4-5")
    ev.record("escalation", lane="escalation")
    rows = ev.query(days=1)
    assert all(r["event_type"] != "heartbeat" for r in rows)


def test_query_returns_newest_first():
    ev.record("escalation", ts="2026-02-25T10:00:00+00:00")
    ev.record("budget_fallback", ts="2026-02-25T11:00:00+00:00")
    rows = ev.query(days=30)
    assert rows[0]["ts"] > rows[1]["ts"]


# ---------------------------------------------------------------------------
# daily_summary
# ---------------------------------------------------------------------------

def test_daily_summary_counts():
    today = __import__("datetime").datetime.utcnow().strftime("%Y-%m-%d")
    ev.record("escalation", groups=["A"])
    ev.record("escalation", groups=["A", "B"])
    ev.record("budget_fallback", lane="routing")
    summary = ev.daily_summary(today)
    assert summary["total_escalations"] == 2
    assert summary["total_fallbacks"] == 1
    assert summary["escalation_triggers"]["A"] == 2
    assert summary["escalation_triggers"]["B"] == 1


def test_daily_summary_empty():
    summary = ev.daily_summary("2000-01-01")
    assert summary.get("total_escalations", 0) == 0


# ---------------------------------------------------------------------------
# format_table
# ---------------------------------------------------------------------------

def test_format_table_empty():
    assert ev.format_table([]) == "(no events)"


def test_format_table_has_header():
    ev.record("escalation", lane="escalation", model="claude-sonnet-4-6", groups=["A"])
    rows = ev.query(days=1)
    table = ev.format_table(rows)
    assert "Time" in table
    assert "Model" in table
    assert "escalation" in table


# ---------------------------------------------------------------------------
# to_csv
# ---------------------------------------------------------------------------

def test_to_csv_empty():
    csv_out = ev.to_csv([])
    assert "event_type" in csv_out  # header still present


def test_to_csv_has_data():
    ev.record("budget_fallback", lane="routing", model="gemini-2.5-flash", reason="Budget 429")
    rows = ev.query(days=1)
    csv_out = ev.to_csv(rows)
    assert "budget_fallback" in csv_out
    assert "gemini-2.5-flash" in csv_out


# ---------------------------------------------------------------------------
# reset_today
# ---------------------------------------------------------------------------

def test_reset_today_clears_current_day():
    ev.record("escalation", lane="escalation")
    ev.record("budget_fallback", lane="routing")
    assert len(ev.query(days=1)) == 2
    ev.reset_today()
    assert ev.query(days=1) == []


def test_reset_today_preserves_old_days():
    ev.record("escalation", ts="2026-01-01T00:00:00+00:00")
    ev.reset_today()
    rows = ev.query_raw("SELECT * FROM events WHERE ts LIKE '2026-01-01%'")
    assert len(rows) == 1
