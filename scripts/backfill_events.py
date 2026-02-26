#!/usr/bin/env python3
"""One-time backfill: parse existing logs into the events SQLite database.

Sources:
  1. ~/.openclaw/logs/clawconductor.log  — JSONL routing decisions
  2. journalctl output                    — budget/fallback events from stdout

Run from the clawconductor repo root:
    python scripts/backfill_events.py [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

DB_PATH = Path.home() / ".openclaw" / "clawconductor.db"
JSONL_LOG = Path.home() / ".openclaw" / "logs" / "clawconductor.log"

CREATE_TABLE = """
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
CREATE INDEX IF NOT EXISTS idx_events_ts    ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_type  ON events(event_type);
CREATE INDEX IF NOT EXISTS idx_events_model ON events(model);
"""

TIER_TO_MODEL = {
    "lightweight": "claude-haiku-4-5",
    "standard": "claude-sonnet-4-6",
    "advanced": "claude-sonnet-4-6",
}

# Patterns for journalctl stdout lines
_PAT_BUDGET_429 = re.compile(r"Budget 429 on (\w+) lane")
_PAT_FALLBACK_DIRECT = re.compile(r"Lane (\w+) in budget fallback — routing direct to (\S+)")
_PAT_PATCHED = re.compile(r"Patched session model to litellm/(\S+)")
_PAT_RESTORED = re.compile(r"Fallback reset for lanes: \[([^\]]+)\]")


def _parse_jsonl(conn: sqlite3.Connection, dry_run: bool) -> int:
    if not JSONL_LOG.exists():
        print(f"[backfill] JSONL log not found: {JSONL_LOG}", file=sys.stderr)
        return 0
    inserted = 0
    with open(JSONL_LOG) as f:
        for lineno, raw in enumerate(f, 1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                entry = json.loads(raw)
            except json.JSONDecodeError:
                print(f"[backfill] Line {lineno}: invalid JSON, skipping", file=sys.stderr)
                continue

            lane = entry.get("lane", "")
            if lane != "escalation":
                continue  # only log escalations from JSONL (routing is noise)

            ts = entry.get("timestamp", "")
            tier = entry.get("tier", "")
            groups_list = entry.get("triggered_groups", [])
            model = TIER_TO_MODEL.get(tier, tier)

            if not dry_run:
                conn.execute(
                    "INSERT OR IGNORE INTO events (ts, event_type, lane, tier, model, groups, reason, task_id, trace_id) "
                    "VALUES (?, 'escalation', ?, ?, ?, ?, ?, ?, ?)",
                    (
                        ts, lane, tier, model,
                        ",".join(sorted(groups_list)),
                        entry.get("reason", ""),
                        entry.get("task_id"),
                        entry.get("trace_id"),
                    ),
                )
            inserted += 1

    print(f"[backfill] JSONL: {inserted} escalation events", file=sys.stderr)
    return inserted


def _parse_journalctl(conn: sqlite3.Connection, dry_run: bool) -> int:
    """Parse budget/fallback events from journalctl stdout."""
    try:
        result = subprocess.run(
            ["journalctl", "--user", "-u", "clawconductor.service",
             "--since", "2026-02-01", "--no-pager", "--output=short-iso"],
            capture_output=True, text=True, timeout=30,
        )
        lines = result.stdout.splitlines()
    except Exception as e:
        print(f"[backfill] journalctl failed: {e}", file=sys.stderr)
        return 0

    inserted = 0
    # short-iso format: 2026-02-25T09:15:32+0000 hostname service[pid]: MESSAGE
    _ts_re = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:?\d{2})")

    for line in lines:
        m_ts = _ts_re.match(line)
        if not m_ts:
            continue
        raw_ts = m_ts.group(1)
        # Normalise to UTC ISO: "2026-02-25T09:15:32+0000" → keep as-is (sqlite handles it)
        ts = raw_ts

        # Format: "2026-02-25T15:25:10-05:00 hostname unit[pid]: message"
        # Strip everything up to and including the first ": "
        rest = line[m_ts.end():].strip()
        colon_pos = rest.find(": ")
        msg = rest[colon_pos + 2:].strip() if colon_pos >= 0 else rest

        row: tuple | None = None

        m = _PAT_BUDGET_429.search(msg)
        if m:
            lane = m.group(1)
            row = (ts, "budget_fallback", lane, None, "gemini-2.5-flash", "", f"Budget 429 on {lane} lane", None, None)

        elif _PAT_FALLBACK_DIRECT.search(msg):
            pass  # these are "already in fallback" lines, not the initial trigger

        elif _PAT_RESTORED.search(msg):
            m2 = _PAT_RESTORED.search(msg)
            lanes_str = m2.group(1).replace("'", "").replace('"', "")
            for lane in [l.strip() for l in lanes_str.split(",")]:
                r = (ts, "budget_restored", lane, None, None, "", "Budget reset via admin endpoint", None, None)
                if not dry_run:
                    conn.execute(
                        "INSERT OR IGNORE INTO events (ts, event_type, lane, tier, model, groups, reason, task_id, trace_id) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", r
                    )
                inserted += 1
            continue

        if row and not dry_run:
            conn.execute(
                "INSERT OR IGNORE INTO events (ts, event_type, lane, tier, model, groups, reason, task_id, trace_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", row
            )
            inserted += 1
        elif row:
            inserted += 1

    print(f"[backfill] journalctl: {inserted} budget/fallback events", file=sys.stderr)
    return inserted


def _add_startup(conn: sqlite3.Connection, dry_run: bool) -> None:
    """Add a synthetic startup event at the earliest known timestamp."""
    if not dry_run:
        conn.execute(
            "INSERT OR IGNORE INTO events (ts, event_type, reason) VALUES (?, 'startup', ?)",
            ("2026-02-25T09:00:00+00:00", "Backfilled: earliest known log entry"),
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill ClawConductor events DB from logs")
    parser.add_argument("--dry-run", action="store_true", help="Count events without writing")
    parser.add_argument("--db", default=str(DB_PATH), help="Path to SQLite DB")
    args = parser.parse_args()

    db_path = Path(args.db)
    dry_run = args.dry_run

    print(f"[backfill] DB: {db_path}  dry_run={dry_run}", file=sys.stderr)

    conn = sqlite3.connect(str(db_path))
    conn.executescript(CREATE_TABLE)

    n1 = _parse_jsonl(conn, dry_run)
    n2 = _parse_journalctl(conn, dry_run)

    if not dry_run:
        _add_startup(conn, dry_run)
        conn.commit()

    conn.close()
    total = n1 + n2
    print(f"[backfill] Done. {'Would insert' if dry_run else 'Inserted'} {total} events.", file=sys.stderr)


if __name__ == "__main__":
    main()
