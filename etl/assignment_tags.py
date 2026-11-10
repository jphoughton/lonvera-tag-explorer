"""Nightly step: give ads that are linked to a production assignment the assignment's concept line tags.

What it does: for every open assignment on /priorities (creative_priorities_assignments, not dropped) that has a taxonomy line (line_id), it finds the live
Creative Tracker rows linked to it and gives each one that has NO current linemap tag the assignment's line (cluster / ICP / pain point / trigger of that
taxonomy line). A tracker row is linked when its concept_id is in the assignment's tracker_concept_ids, or its tracker 'Brief Link' equals the assignment's
brief_url, or its asset_url equals the assignment's creative_url (exact, non-empty matches only).
Writes ad_tags(tag_set='linemap', version='assign-YYYY-MM-DD', tagger='assignment', extra.source='assignment'). It only fills rows that have NO current linemap
tag: any existing current tag (human, cycle or auto) is never overwritten or marked not-current. Re-running finds nothing new. Manual concepts (no line_id) and
assignments whose line no longer exists in taxonomy_lines are skipped. If the assignments table does not exist yet (migration 202 not applied) or holds no
open assignments, the step logs one line and exits 0. Nothing is written to Google Sheets or Meta.
Runs before etl.auto_tag in scripts/deploy/run_etl.sh so linked ads get the planned line instead of a model guess.
Usage: python -m etl.assignment_tags [--dry-run]"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys

from . import db

TAGGER = "assignment"


def table_exists(conn) -> bool:
    return conn.execute("SELECT to_regclass('public.creative_priorities_assignments') IS NOT NULL").fetchone()[0]


def open_assignments(conn) -> list[tuple]:
    """Open assignments with a live taxonomy line: (id, line_id, line_code, cluster, icp, pain_point, trigger, concept_ids, brief_url, creative_url)."""
    return conn.execute("""SELECT a.id, l.id, l.line_code, l.cluster, l.icp, l.pain_point, l.emotional_trigger,
                                  a.tracker_concept_ids, nullif(btrim(a.brief_url), ''), nullif(btrim(a.creative_url), '')
                           FROM creative_priorities_assignments a JOIN taxonomy_lines l ON l.id = a.line_id
                           WHERE a.dropped_at IS NULL AND a.manual_id IS NULL
                           ORDER BY a.id""").fetchall()


def untagged_linked_rows(conn, concept_ids: list[str], brief: str | None, creative: str | None) -> list[int]:
    ids = [c for c in (concept_ids or []) if c and c.strip()]
    if not ids and not brief and not creative:
        return []
    return [r[0] for r in conn.execute("""
        SELECT t.id FROM tracker_rows t
        WHERE t.deleted_at IS NULL
          AND (t.concept_id = ANY(%s)
               OR (%s::text IS NOT NULL AND nullif(btrim(t.raw->>'Brief Link'), '') = %s::text)
               OR (%s::text IS NOT NULL AND nullif(btrim(t.asset_url), '') = %s::text))
          AND NOT EXISTS (SELECT 1 FROM ad_tags g WHERE g.tracker_row_id = t.id AND g.tag_set = 'linemap' AND g.is_current)
        ORDER BY t.id""", (ids, brief, brief, creative, creative)).fetchall()]


def run(conn, day: dt.date | None = None, dry_run: bool = False, log=db.log) -> dict:
    day = day or dt.date.today()
    version = "assign-" + day.isoformat()
    stats = {"version": version, "dry_run": dry_run, "assignments": 0, "assignments_with_new_tags": 0, "tagged_rows": 0, "table_missing": False}
    if not table_exists(conn):
        stats["table_missing"] = True
        log("  assignment_tags: creative_priorities_assignments does not exist yet, nothing to do")
        return stats
    assigns = open_assignments(conn)
    stats["assignments"] = len(assigns)
    if not assigns:
        log("  assignment_tags: no open assignments with a taxonomy line, nothing to do")
        return stats
    for aid, line_id, code, cluster, icp, pain, trigger, cids, brief, creative in assigns:
        rows = untagged_linked_rows(conn, cids, brief, creative)
        if not rows:
            continue
        if dry_run:
            stats["tagged_rows"] += len(rows)
            stats["assignments_with_new_tags"] += 1
            continue
        extra = db.jsonb({"source": "assignment", "assignment_id": aid, "line_code": code})
        n = 0
        with conn.transaction():
            for rid in rows:
                n += conn.execute("""INSERT INTO ad_tags (tracker_row_id, tag_set, version, is_current, line_id, cluster, icp, pain_point, emotional_trigger,
                                     confidence, method, tagger, extra)
                                     SELECT %s,'linemap',%s,true,%s,%s,%s,%s,%s,'high','assignment',%s,%s
                                     WHERE NOT EXISTS (SELECT 1 FROM ad_tags WHERE tracker_row_id=%s AND tag_set='linemap' AND is_current)""",
                                  (rid, version, line_id, cluster, icp, pain, trigger, TAGGER, extra, rid)).rowcount
        if n:
            stats["tagged_rows"] += n
            stats["assignments_with_new_tags"] += 1
    log(f"  assignment_tags: {stats['tagged_rows']} ad rows tagged from {stats['assignments_with_new_tags']} of {stats['assignments']} assignments")
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="count rows that would be tagged; no writes")
    a = ap.parse_args()
    with db.JobRun("assignment_tags") as job, db.connect() as conn:
        job.stats = {}
        st = run(conn, dry_run=a.dry_run)
        conn.commit()
        job.stats = st
        job.rows_written = st["tagged_rows"]
        db.log(f"  {json.dumps(st, default=str)}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:                                                # noqa: BLE001
        db.log("assignment_tags FAILED:", db.scrub(e))
        sys.exit(1)
