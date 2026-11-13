"""Nightly step: pull production cards forward to what the Creative Tracker shows.

What it does: for every open assignment on the Production board (creative_priorities_assignments, not dropped, not launched) it looks at the Creative Tracker rows linked to it (same link rules as the
board: Concept ID, brief link, creative link) and, when they show a later step than the stored status, moves the card forward: tracker status 3 to 5 = Brief sent, 6 = In review, 7 = Ready to launch,
8 or 9 = Launched. It never moves a card backward and never touches a dropped card. Every move goes through app.priorities_assign.set_status, the one writer of the status, and lands in the history
(creative_priorities_assignment_events, kind status_changed, actor 'tracker'). If the board columns (migration 244) are not applied yet, the step logs one line and exits 0.
Nothing is written to Google Sheets or Meta. Usage: python -m etl.production_status [--dry-run]"""
from __future__ import annotations

import argparse
import json
import sys

import psycopg
from psycopg.rows import dict_row

from . import db


def run(conn, dry_run: bool = False, log=db.log) -> dict:
    from app import priorities_assign as pas
    stats = {"dry_run": dry_run, "moved": 0, "table_missing": False}
    try:
        if dry_run:
            recs = pas._fetch(conn, "WHERE dropped_at IS NULL AND status <> 'launched'")
            ads = pas._tracker_ads(conn, recs)
            stats["moved"] = sum(1 for r in recs if (t := pas.tracker_status_of([a["status"] for a in ads[r["id"]]])) and pas.status_rank(t) > pas.status_rank(r["status"]))
        else:
            stats["moved"] = pas.pull_forward(conn)
            conn.commit()
    except (psycopg.errors.UndefinedTable, psycopg.errors.UndefinedColumn):
        conn.rollback()
        stats["table_missing"] = True
        log("  production_status: the board columns (migration 244) do not exist yet, nothing to do")
        return stats
    log(f"  production_status: {stats['moved']} cards {'would move' if dry_run else 'moved'} forward from the Creative Tracker")
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="count the cards that would move; no writes")
    a = ap.parse_args()
    with db.JobRun("production_status") as job, psycopg.connect(db.database_url(), row_factory=dict_row) as conn:
        st = run(conn, dry_run=a.dry_run)
        job.stats = st
        job.rows_written = st["moved"]
        db.log(f"  {json.dumps(st, default=str)}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:                                                # noqa: BLE001
        db.log("production_status FAILED:", db.scrub(e))
        sys.exit(1)
