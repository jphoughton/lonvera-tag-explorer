"""etl.production_status: open production cards move forward to what their linked Creative Tracker rows show, through the one set_status writer; never backward, never dropped or launched cards;
every move lands in the history as actor 'tracker'; idempotent; a no-op before migration 245."""
import datetime as dt

import pytest
from psycopg.rows import dict_row, tuple_row

from etl import production_status as ps
from etl import sheets_io, tracker_sync
from conftest import trow, write_snapshot

MONDAY = dt.date(2026, 11, 9)


@pytest.fixture(autouse=True)
def clean(conn):
    conn.execute("TRUNCATE creative_priorities_assignments RESTART IDENTITY CASCADE")
    conn.commit()
    yield
    conn.rollback()
    conn.row_factory = tuple_row


def run(conn, **kw):
    conn.row_factory = dict_row                                    # the step runs on dict rows (etl.production_status.main opens its connection that way); the shared fixtures use tuples
    try:
        return ps.run(conn, **kw)
    finally:
        conn.row_factory = tuple_row


def sync(conn, tmp_path, rows):
    write_snapshot(tmp_path, rows, [("2026-05-06", "x", 1, 0, 0, 0)])
    tracker_sync.sync(conn, sheets_io.read_tab("creative_tracker", tmp_path))
    conn.commit()


def assign(conn, cid, status="assigned", vendor="Madre", dropped=False):
    return conn.execute("""INSERT INTO creative_priorities_assignments (concept_key, text_key, product, vendor, for_week, tracker_concept_ids, status, dropped_at, position)
                           VALUES (%s,'t','boneflex',%s,%s,%s,%s,%s,1) RETURNING id""", (f"k{vendor}{cid}", vendor, MONDAY, [cid], status, dt.datetime.now() if dropped else None)).fetchone()[0]


def state(conn, aid):
    conn.row_factory = dict_row
    try:
        return _state(conn, aid)
    finally:
        conn.row_factory = tuple_row


def _state(conn, aid):
    return conn.execute("SELECT status, ready_at IS NOT NULL AS ready, launched_at IS NOT NULL AS launched FROM creative_priorities_assignments WHERE id = %s", (aid,)).fetchone()


def test_moves_forward_only_and_logs_the_tracker(conn, tmp_path):
    sync(conn, tmp_path, [trow("c1", "f1", "Boneflex"), trow("c2", "f2", "Boneflex"), trow("c3", "f3", "Boneflex"), trow("c4", "f4", "Boneflex"), trow("c5", "f5", "Boneflex")])
    for cid, st in (("c1", "7 - Approved - Ready To Launch"), ("c2", "8 - Launched"), ("c3", "5 - Briefed"), ("c4", "6 - Creative In"), ("c5", "7 - Approved - Ready To Launch")):
        conn.execute("UPDATE tracker_rows SET status = %s WHERE concept_id = %s", (st, cid))
    a1, a2, a3 = assign(conn, "c1"), assign(conn, "c2", "in_review", "Victoria"), assign(conn, "c3", "in_production", "Internal")
    a4, a5 = assign(conn, "c4", "ready", "TrybeOrion"), assign(conn, "c5", "assigned", "10xROAS", dropped=True)
    conn.commit()
    msgs = []
    st = run(conn, log=msgs.append)
    assert st["moved"] == 2 and not st["table_missing"]
    assert state(conn, a1) == {"status": "ready", "ready": True, "launched": False}
    assert state(conn, a2)["status"] == "launched" and state(conn, a2)["launched"] is True
    assert state(conn, a3)["status"] == "in_production"                                           # tracker 5 is behind: untouched
    assert state(conn, a4)["status"] == "ready"                                                   # never back from ready to in review
    assert state(conn, a5)["status"] == "assigned"                                                # dropped cards are left alone
    conn.row_factory = dict_row
    ev = conn.execute("SELECT assignment_id, actor, kind, stage_before, stage_after, detail FROM creative_priorities_assignment_events WHERE kind = 'status_changed' ORDER BY assignment_id").fetchall()
    assert [(e["assignment_id"], e["actor"], e["stage_before"], e["stage_after"]) for e in ev] == [(a1, "tracker", "assigned", "ready"), (a2, "tracker", "in_review", "launched")]
    assert "Creative Tracker" in ev[0]["detail"]
    conn.row_factory = tuple_row
    assert run(conn, log=msgs.append)["moved"] == 0                                            # idempotent
    conn.execute("UPDATE tracker_rows SET status = '3 - Brief Drafted' WHERE concept_id IN ('c1','c2')")
    conn.commit()
    assert run(conn, log=msgs.append)["moved"] == 0 and state(conn, a1)["status"] == "ready"   # the tracker going backward moves nothing


def test_dry_run_counts_and_writes_nothing(conn, tmp_path):
    sync(conn, tmp_path, [trow("c1", "f1", "Boneflex")])
    conn.execute("UPDATE tracker_rows SET status = '7 - Approved - Ready To Launch' WHERE concept_id = 'c1'")
    a = assign(conn, "c1")
    conn.commit()
    st = run(conn, dry_run=True, log=lambda *x: None)
    assert st["moved"] == 1 and state(conn, a)["status"] == "assigned"
    assert conn.execute("SELECT count(*) FROM creative_priorities_assignment_events WHERE kind = 'status_changed'").fetchone()[0] == 0


def test_no_open_cards_and_missing_columns_are_noops(conn, monkeypatch):
    import psycopg
    from app import priorities_assign as pas
    assert run(conn, log=lambda *x: None)["moved"] == 0
    def boom(w, actor="tracker"):
        raise psycopg.errors.UndefinedColumn('column "status" does not exist')
    monkeypatch.setattr(pas, "pull_forward", boom)
    msgs = []
    st = run(conn, log=msgs.append)
    assert st["table_missing"] and st["moved"] == 0 and "migration 245" in msgs[0]
