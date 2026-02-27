"""Persistent SQLite event store for ClawConductor.

Records state-change events (escalations, budget fallbacks, restores,
startups, heartbeats) to ~/.openclaw/clawconductor.db.

Design principles:
- Writes are synchronous and wrapped in try/except — a write failure must
  never disrupt request routing.
- Reads are also synchronous; call from a thread executor if needed on
  the hot path (they aren't on the hot path currently).
- No external dependencies beyond stdlib sqlite3.
"""

from __future__ import annotations

import csv
import io
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_DB_PATH = Path.home() / ".openclaw" / "clawconductor.db"
_lock = threading.Lock()
_conn: sqlite3.Connection | None = None

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT    NOT NULL,
    event_type  TEXT    NOT NULL,
    lane        TEXT,
    tier        TEXT,
    model       TEXT,
    groups      TEXT,
    reason      TEXT,
    task_id     TEXT,
    trace_id    TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_ts   ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
CREATE INDEX IF NOT EXISTS idx_events_model ON events(model);
"""

# Valid event types
EVENT_TYPES = frozenset({
    "escalation",
    "budget_fallback",
    "budget_restored",
    "fallback_stuck",
    "startup",
    "heartbeat",
})


def init(db_path: Path | None = None) -> None:
    """Open (or create) the SQLite database and ensure the schema exists.

    Safe to call multiple times — subsequent calls are no-ops.
    """
    global _conn
    path = db_path or _DB_PATH
    with _lock:
        if _conn is not None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), check_same_thread=False)
        conn.executescript(_CREATE_TABLE)
        conn.commit()
        _conn = conn


def record(
    event_type: str,
    *,
    lane: str | None = None,
    tier: str | None = None,
    model: str | None = None,
    groups: list[str] | set[str] | None = None,
    reason: str | None = None,
    task_id: str | None = None,
    trace_id: str | None = None,
    ts: str | None = None,
) -> None:
    """Insert one event row. Never raises — errors are swallowed and ignored."""
    if _conn is None:
        return
    try:
        groups_str = ",".join(sorted(groups)) if groups else ""
        ts_val = ts or datetime.now(timezone.utc).isoformat()
        with _lock:
            _conn.execute(
                "INSERT INTO events (ts, event_type, lane, tier, model, groups, reason, task_id, trace_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (ts_val, event_type, lane, tier, model, groups_str, reason, task_id, trace_id),
            )
            _conn.commit()
    except Exception:
        pass  # never let monitoring break the proxy


def query(
    *,
    days: int = 1,
    event_type: str | None = None,
    lane: str | None = None,
    limit: int = 1000,
) -> list[dict[str, Any]]:
    """Return recent events as a list of dicts, newest-first."""
    if _conn is None:
        return []
    try:
        clauses = ["ts >= datetime('now', ?)", "event_type != 'heartbeat'"]
        params: list[Any] = [f"-{days} days"]
        if event_type:
            clauses.append("event_type = ?")
            params.append(event_type)
        if lane:
            clauses.append("lane = ?")
            params.append(lane)
        where = " AND ".join(clauses)
        sql = f"SELECT id, ts, event_type, lane, tier, model, groups, reason, task_id, trace_id FROM events WHERE {where} ORDER BY ts DESC LIMIT ?"
        params.append(limit)
        with _lock:
            cur = _conn.execute(sql, params)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []


def query_raw(sql: str, params: list | None = None) -> list[dict[str, Any]]:
    """Execute an arbitrary SELECT and return rows as dicts. Read-only."""
    if _conn is None:
        return []
    try:
        with _lock:
            cur = _conn.execute(sql, params or [])
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []


def daily_summary(date: str | None = None) -> dict[str, Any]:
    """Return aggregated stats for a given date (YYYY-MM-DD, default today)."""
    if _conn is None:
        return {}
    day = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        with _lock:
            # Counts by event type
            cur = _conn.execute(
                "SELECT event_type, COUNT(*) FROM events WHERE date(ts) = ? GROUP BY event_type",
                [day],
            )
            counts = dict(cur.fetchall())

            # Escalation trigger breakdown
            cur = _conn.execute(
                "SELECT groups FROM events WHERE date(ts) = ? AND event_type = 'escalation' AND groups != ''",
                [day],
            )
            trigger_counts: dict[str, int] = {}
            for (g_str,) in cur.fetchall():
                for g in g_str.split(","):
                    trigger_counts[g] = trigger_counts.get(g, 0) + 1

            # Fallback events with event_type for icon selection
            cur = _conn.execute(
                "SELECT lane, ts, event_type, reason FROM events WHERE date(ts) = ? AND event_type IN ('budget_fallback','budget_restored') ORDER BY ts",
                [day],
            )
            fallback_rows = [{"lane": r[0], "ts": r[1], "event_type": r[2], "reason": r[3]} for r in cur.fetchall()]

        return {
            "date": day,
            "counts": counts,
            "escalation_triggers": trigger_counts,
            "fallback_rows": fallback_rows,
            "total_escalations": counts.get("escalation", 0),
            "total_fallbacks": counts.get("budget_fallback", 0),
            "total_restores": counts.get("budget_restored", 0),
        }
    except Exception:
        return {"date": day}


def format_table(rows: list[dict[str, Any]]) -> str:
    """Format event rows as a plain-text ASCII table (newest-first)."""
    if not rows:
        return "(no events)"

    col_widths = {"ts": 25, "model": 20, "event_type": 16, "lane": 10, "groups": 6, "reason": 40}

    def _cell(val: Any, width: int) -> str:
        s = str(val or "")
        return s[:width].ljust(width)

    header = (
        _cell("Time (UTC)", col_widths["ts"])
        + "  " + _cell("Model", col_widths["model"])
        + "  " + _cell("Event", col_widths["event_type"])
        + "  " + _cell("Lane", col_widths["lane"])
        + "  " + _cell("Groups", col_widths["groups"])
        + "  " + _cell("Reason", col_widths["reason"])
    )
    sep = "-" * len(header)

    lines = [header, sep]
    for row in rows:
        ts = row.get("ts", "")[:25]
        lines.append(
            _cell(ts, col_widths["ts"])
            + "  " + _cell(row.get("model"), col_widths["model"])
            + "  " + _cell(row.get("event_type"), col_widths["event_type"])
            + "  " + _cell(row.get("lane"), col_widths["lane"])
            + "  " + _cell(row.get("groups"), col_widths["groups"])
            + "  " + _cell(row.get("reason"), col_widths["reason"])
        )
    return "\n".join(lines)


def to_csv(rows: list[dict[str, Any]]) -> str:
    """Serialise event rows to a CSV string."""
    if not rows:
        return "id,ts,event_type,lane,tier,model,groups,reason,task_id,trace_id\n"
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=list(rows[0].keys()), extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue()


def reset_today() -> None:
    """Delete all today's events (called by /admin/reset-metrics at midnight)."""
    if _conn is None:
        return
    try:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with _lock:
            _conn.execute("DELETE FROM events WHERE date(ts) = ?", [day])
            _conn.commit()
    except Exception:
        pass
