"""etl.assignment_tags: ads linked to an open production assignment get the assignment's taxonomy line as a linemap tag.
Covers the three link types, never overwriting an existing current tag, dropped assignments, manual concepts, idempotence and the missing-table no-op."""
import datetime as dt

import pytest

from etl import assignment_tags as at
from etl import sheets_io, tracker_sync
from conftest import trow, write_snapshot

DAY = dt.date(2026, 11, 10)
MONDAY = dt.date(2026, 11, 9)


@pytest.fixture(autouse=True)
def clean(conn):
    conn.execute("TRUNCATE creative_priorities_assignments RESTART IDENTITY")
    conn.commit()


def sync(conn, tmp_path, rows):
    write_snapshot(tmp_path, rows, [("2026-05-06", "x", 1, 0, 0, 0)])
    tracker_sync.sync(conn, sheets_io.read_tab("creative_tracker", tmp_path))
    conn.commit()


def seed_line(conn, code="BF18"):
    pid = conn.execute("SELECT id FROM products WHERE slug='boneflex'").fetchone()[0]
    return conn.execute("INSERT INTO taxonomy_lines (product_id, line_code, cluster, icp, pain_point, emotional_trigger) VALUES (%s,%s,'Cl','Icp','Pain '||%s,'Fear') RETURNING id",
                        (pid, code, code)).fetchone()[0]


def assign(conn, line_id, vendor="Madre", cids=(), brief=None, creative=None, dropped=False, manual_id=None, key=None):
    conn.execute("""INSERT INTO creative_priorities_assignments (concept_key, text_key, product, vendor, line_id, manual_id, for_week, brief_url, creative_url,
                    tracker_concept_ids, dropped_at) VALUES (%s,'t','boneflex',%s,%s,%s,%s,%s,%s,%s,%s)""",
                 (key or f"k{vendor}{line_id}{manual_id}", vendor, line_id, manual_id, MONDAY, brief, creative, list(cids), dt.datetime.now() if dropped else None))
    conn.commit()


def tags(conn, concept):
    return conn.execute("""SELECT g.line_id, g.cluster, g.icp, g.pain_point, g.emotional_trigger, g.tagger, g.version, g.is_current, g.extra->>'source', g.tag_set
                           FROM ad_tags g JOIN tracker_rows t ON t.id=g.tracker_row_id WHERE t.concept_id=%s AND g.tag_set='linemap' ORDER BY g.id""", (concept,)).fetchall()


def test_three_link_types(conn, tmp_path):
    sync(conn, tmp_path, [trow("c1", "f1", "Boneflex"), trow("c2", "f2", "Boneflex"), trow("c3", "f3", "Boneflex", **{"Asset URL": "https://drive/x"}),
                          trow("c4", "f4", "Boneflex")])
    conn.execute("UPDATE tracker_rows SET raw = raw || '{\"Brief Link\": \"https://doc/b\"}'::jsonb WHERE concept_id='c2'")
    conn.commit()
    lid = seed_line(conn)
    assign(conn, lid, cids=["c1"], brief="https://doc/b", creative="https://drive/x")
    st = at.run(conn, DAY, log=lambda *a: None)
    assert st["tagged_rows"] == 3 and st["assignments_with_new_tags"] == 1
    for c in ("c1", "c2", "c3"):
        assert tags(conn, c) == [(lid, "Cl", "Icp", "Pain BF18", "Fear", "assignment", "assign-2026-11-10", True, "assignment", "linemap")]
    assert tags(conn, "c4") == []


def test_existing_tag_not_overwritten(conn, tmp_path):
    sync(conn, tmp_path, [trow("c1", "f1", "Boneflex"), trow("c2", "f2", "Boneflex")])
    lid, other = seed_line(conn), seed_line(conn, "BF21")
    conn.execute("""INSERT INTO ad_tags (tracker_row_id, tag_set, version, is_current, line_id, cluster, tagger)
                    SELECT id,'linemap','human-1',true,%s,'Human','human' FROM tracker_rows WHERE concept_id='c1'""", (other,))
    conn.commit()
    assign(conn, lid, cids=["c1", "c2"])
    st = at.run(conn, DAY, log=lambda *a: None)
    assert st["tagged_rows"] == 1
    assert tags(conn, "c1") == [(other, "Human", None, None, None, "human", "human-1", True, None, "linemap")]
    assert tags(conn, "c2")[0][5] == "assignment"


def test_dropped_assignment_ignored(conn, tmp_path):
    sync(conn, tmp_path, [trow("c1", "f1", "Boneflex")])
    assign(conn, seed_line(conn), cids=["c1"], dropped=True)
    assert at.run(conn, DAY, log=lambda *a: None)["tagged_rows"] == 0
    assert tags(conn, "c1") == []


def test_manual_and_lineless_skipped(conn, tmp_path):
    sync(conn, tmp_path, [trow("c1", "f1", "Boneflex"), trow("c2", "f2", "Boneflex")])
    assign(conn, None, cids=["c1"], manual_id=7)
    assign(conn, None, cids=["c2"], key="text:x")
    assert at.run(conn, DAY, log=lambda *a: None)["tagged_rows"] == 0
    assert tags(conn, "c1") == [] and tags(conn, "c2") == []


def test_blank_links_match_nothing(conn, tmp_path):
    sync(conn, tmp_path, [trow("c1", "f1", "Boneflex")])      # blank asset url and brief link on the tracker row
    assign(conn, seed_line(conn), brief="", creative="  ")
    assert at.run(conn, DAY, log=lambda *a: None)["tagged_rows"] == 0


def test_idempotent(conn, tmp_path):
    sync(conn, tmp_path, [trow("c1", "f1", "Boneflex")])
    assign(conn, seed_line(conn), cids=["c1"])
    assert at.run(conn, DAY, log=lambda *a: None)["tagged_rows"] == 1
    assert at.run(conn, DAY, log=lambda *a: None)["tagged_rows"] == 0
    assert at.run(conn, DAY + dt.timedelta(days=1), log=lambda *a: None)["tagged_rows"] == 0
    assert len(tags(conn, "c1")) == 1


def test_missing_table_and_empty_table_are_noops(conn):
    msgs = []
    assert at.run(conn, DAY, log=msgs.append)["tagged_rows"] == 0 and "no open assignments" in msgs[0]
    conn.execute("ALTER TABLE creative_priorities_assignments RENAME TO cpa_tmp")
    try:
        msgs.clear()
        st = at.run(conn, DAY, log=msgs.append)
        assert st["table_missing"] and "does not exist" in msgs[0]
    finally:
        conn.rollback()                    # DDL is transactional: the rename is undone
