"""Hook load (migration 010, etl.load_hooks): ordinal matching shared with load_tags (empty hooks must not shift their neighbours), upsert + versions,
coverage failure, closed lists, tracker growth, placeholder / soft-deleted rows, cascade on delete, the CLI's etl_runs row."""
import json
import sys

import psycopg
import pytest

from etl import load_hooks, migrate, sheets_io, tracker_sync
from conftest import trow, write_snapshot


def sync(conn, tmp_path, rows):
    snap = tmp_path / "snap"
    snap.mkdir(exist_ok=True)
    write_snapshot(snap, rows, [("2026-05-06", "x", 1, 0, 0, 0)])
    return tracker_sync.sync(conn, sheets_io.read_tab("creative_tracker", snap))


def entry(row, cid, fam, hook, **kw):
    e = {"row": row, "concept_id": cid, "ad_family": fam, "unit_id": f"U{row}", "hook": hook, "spoken": None, "on_screen": None, "hook_source": "spoken",
         "confidence": "high", "note": None}
    e.update(kw)
    return e


def seed(conn, tmp_path):
    """Vita Bull: c1/fam1 twice + c2 with a blank family (the tag-map row files write the concept id into blank families)."""
    sync(conn, tmp_path, [trow("c1", "fam1", "VitaBull", Type="VID"), trow("c1", "fam1", "VitaBull", Type="VID"), trow("c2", "", "VitaBull", Type="IMG")])


def hooks(conn):
    return {r[0]: r[1:] for r in conn.execute("""SELECT t.row_key, h.hook, h.spoken, h.on_screen, h.hook_source, h.confidence, h.note, h.version, h.extractor
                                                 FROM ad_hooks h JOIN tracker_rows t ON t.id = h.tracker_row_id""").fetchall()}


def three(**over):
    """a file covering all three tracker rows; stale `row` numbers on purpose, the order of the `row` values decides the ordinals"""
    es = [entry(50, "c1", "fam1", "second fam1 hook"), entry(20, "c1", "fam1", "first fam1 hook"), entry(21, "c2", "c2", "blank family hook", hook_source="on_screen")]
    for e in es:
        e.update(over)
    return es


def test_migration_is_safe_to_run_twice(conn, dburl):
    sql = (migrate.migrations_dir() / "010_ad_hooks.sql").read_text()
    conn.execute(sql)
    conn.execute(sql)
    cols = {r[0]: r[1] for r in conn.execute("SELECT column_name, is_nullable FROM information_schema.columns WHERE table_name='ad_hooks'").fetchall()}
    assert cols == {"tracker_row_id": "NO", "hook": "NO", "spoken": "YES", "on_screen": "YES", "hook_source": "YES", "confidence": "YES", "note": "YES",
                    "version": "NO", "extractor": "YES", "extracted_at": "NO"}
    assert conn.execute("SELECT 1 FROM schema_migrations WHERE version = '010_ad_hooks.sql'").fetchone()                  # the runner applied it
    conn.rollback()


def test_match_upsert_and_fields(conn, tmp_path):
    seed(conn, tmp_path)
    es = [entry(50, "c1", "fam1", "  second\nfam1   hook "), entry(20, "c1", "fam1", "first fam1 hook", spoken="So here is the thing", on_screen="HEADLINE", hook_source="both",
                                                                  confidence="medium", note="audio clear"),
          entry(21, "c2", "c2", "blank family hook", hook_source="On-screen", confidence="LOW", extractor="manual")]
    st = load_hooks.load(conn, "vita-bull", "hooks-v1-2026-11-05", es, extractor="claude-sonnet-5-5")
    assert (st["matched"], st["tracker_rows"], st["coverage_pct"], st["hooks_written"], st["skipped_empty_hook"]) == (3, 3, 100.0, 3, 0)
    assert st["tracker_rows_without_hook"] == 0 and st["hooked_pct"] == 100.0 and st["tags_skipped_not_in_tracker"] == 0
    assert st["hook_source"] == {"both": 1, "spoken": 1, "on_screen": 1} and st["confidence"] == {"medium": 1, "high": 1, "low": 1}
    got = hooks(conn)
    assert set(got) == {"c1#1", "c1#2", "c2#1"}
    assert got["c1#1"] == ("first fam1 hook", "So here is the thing", "HEADLINE", "both", "medium", "audio clear", "hooks-v1-2026-11-05", "claude-sonnet-5-5")   # row 20 sorts first
    assert got["c1#2"][0] == "second fam1 hook" and got["c1#2"][3:6] == ("spoken", "high", None)                                                       # whitespace collapsed, empties -> NULL
    assert got["c2#1"][3:5] == ("on_screen", "low") and got["c2#1"][7] == "manual"                                                                       # closed lists normalised; an entry's own extractor wins
    assert conn.execute("SELECT count(*) FROM ad_hooks WHERE extracted_at IS NOT NULL").fetchone()[0] == 3


def test_empty_hooks_do_not_shift_the_ordinals(conn, tmp_path):
    seed(conn, tmp_path)
    es = [entry(1, "c1", "fam1", ""), entry(2, "c1", "fam1", "only the second row has one"), entry(3, "c2", "c2", "   ")]
    st = load_hooks.load(conn, "vita-bull", "v1", es)
    assert (st["matched"], st["hooks_written"], st["skipped_empty_hook"], st["tracker_rows_without_hook"]) == (3, 1, 2, 2)
    assert hooks(conn) == {"c1#2": ("only the second row has one", None, None, "spoken", "high", None, "v1", None)}                                       # not on c1#1


def test_versions_replace_and_uncovered_rows_keep_their_hook(conn, tmp_path):
    seed(conn, tmp_path)
    load_hooks.load(conn, "vita-bull", "v1", three())
    snap = hooks(conn)
    st = load_hooks.load(conn, "vita-bull", "v1", three())                                                          # same version again = same rows
    assert hooks(conn) == snap and conn.execute("SELECT count(*) FROM ad_hooks").fetchone()[0] == 3 and st["hooks_written"] == 3
    # a newer file covers c1#1 only (its c1#2 and c2 entries are left out) and gives c1#1 a new hook
    load_hooks.load(conn, "vita-bull", "v2", [entry(20, "c1", "fam1", "rewritten hook", confidence="low")], allow_mismatch=True)
    got = hooks(conn)
    assert got["c1#1"][0] == "rewritten hook" and got["c1#1"][4] == "low" and got["c1#1"][6] == "v2"
    assert got["c1#2"] == snap["c1#2"] and got["c2#1"] == snap["c2#1"]                                               # uncovered rows keep the v1 hook
    # an empty hook in a newer file does not wipe the old one
    load_hooks.load(conn, "vita-bull", "v3", [entry(20, "c1", "fam1", "")], allow_mismatch=True)
    assert hooks(conn)["c1#1"][0] == "rewritten hook" and hooks(conn)["c1#1"][6] == "v2"
    assert conn.execute("SELECT version, count(*) FROM ad_hooks GROUP BY 1 ORDER BY 1").fetchall() == [("v1", 2), ("v2", 1)]


def test_low_coverage_fails_loudly_and_writes_nothing(conn, tmp_path):
    seed(conn, tmp_path)
    es = three()
    with pytest.raises(RuntimeError, match="does not match the tracker.*2 of 3 tracker rows matched.*66.7%.*min coverage 90%"):
        load_hooks.load(conn, "vita-bull", "v1", es[:2])
    assert conn.execute("SELECT count(*) FROM ad_hooks").fetchone()[0] == 0
    st = load_hooks.load(conn, "vita-bull", "v1", es[:2], min_coverage=0.6)                                          # a lower floor lets it through
    assert (st["matched"], st["coverage_pct"], st["tracker_rows_without_hook"]) == (2, 66.7, 1)
    conn.execute("TRUNCATE ad_hooks")
    st = load_hooks.load(conn, "vita-bull", "v1", es[:1], allow_mismatch=True)                                       # ... and so does --allow-mismatch
    assert st["matched"] == 1 and st["hooks_written"] == 1


def test_unknown_source_or_confidence_fails_before_writing(conn, tmp_path):
    seed(conn, tmp_path)
    with pytest.raises(RuntimeError, match="hook source 'voiceover' is not one of spoken, on_screen, both, copy, none"):
        load_hooks.load(conn, "vita-bull", "v1", three()[:2] + [entry(21, "c2", "c2", "x", hook_source="voiceover")])
    with pytest.raises(RuntimeError, match="hook confidence 'sure' is not one of high, medium, low"):
        load_hooks.load(conn, "vita-bull", "v1", [entry(21, "c2", "c2", "x", confidence="sure")] + three()[:2])
    assert conn.execute("SELECT count(*) FROM ad_hooks").fetchone()[0] == 0
    for bad in (None, {"row": 1}, [{"concept_id": "c1", "hook": "x"}], [{"row": 1, "concept_id": " ", "hook": "x"}], ["not an object"]):
        with pytest.raises(RuntimeError, match="hook file must be a JSON list of objects|needed to match the tracker"):
            load_hooks.load(conn, "vita-bull", "v1", bad)
    with pytest.raises(RuntimeError, match="unknown product 'nope'"):
        load_hooks.load(conn, "nope", "v1", three())


def test_tracker_growth_is_not_an_error(conn, tmp_path):
    """Rows added to the tracker after the cycle stay without a hook; hooks whose key left the tracker are skipped and named; coverage decides."""
    seed(conn, tmp_path)
    es = three() + [entry(99, "gone", "gone-fam", "left the tracker")]
    st = load_hooks.load(conn, "vita-bull", "v1", es)
    assert (st["matched"], st["tags_skipped_not_in_tracker"], st["skipped_keys"], st["hooks_written"]) == (3, 1, ["gone | gone-fam"], 3)
    sync(conn, tmp_path, [trow("c1", "fam1", "VitaBull", Type="VID"), trow("c1", "fam1", "VitaBull", Type="VID"), trow("c2", "", "VitaBull", Type="IMG"),
                          trow("new1", "n1", "VitaBull", Type="VID")])
    with pytest.raises(RuntimeError, match="3 of 4 tracker rows matched"):                                          # 75% < 90%
        load_hooks.load(conn, "vita-bull", "v2", three())
    st = load_hooks.load(conn, "vita-bull", "v2", three(), min_coverage=0.7)
    assert (st["matched"], st["tracker_rows"], st["tracker_rows_without_hook"], st["hooked_pct"]) == (3, 4, 1, 75.0)
    assert "new1#1" not in hooks(conn)


def test_other_products_placeholders_and_deleted_rows_are_not_part_of_the_match(conn, tmp_path):
    sync(conn, tmp_path, [trow("a", "fam-a", "VitaBull", Type="VID"), trow("b", "fam-b", "BeefLiver", Type="VID"), trow("gone", "fam-gone", "VitaBull", Type="IMG")])
    pid = conn.execute("SELECT id FROM products WHERE slug='vita-bull'").fetchone()[0]
    conn.execute("INSERT INTO tracker_rows (product_id, row_key, concept_id, ad_family, family_key, sheet_row, status) VALUES (%s,'#ph','','','id:',36,'3 - Briefing')", (pid,))
    conn.execute("UPDATE tracker_rows SET deleted_at=now() WHERE concept_id='gone'")
    es = [entry(1, "a", "fam-a", "vita bull hook"), entry(2, "b", "fam-b", "beef liver ad in the vita bull file"), entry(3, "gone", "fam-gone", "deleted row")]
    st = load_hooks.load(conn, "vita-bull", "v1", es)
    assert (st["matched"], st["tracker_rows"], st["tags_skipped_not_in_tracker"], st["skipped_keys"]) == (1, 1, 2, ["b | fam-b", "gone | fam-gone"])
    assert set(hooks(conn)) == {"a#1"}                                                                               # the placeholder (blank Concept ID) is neither matched nor required


def test_hooks_go_with_their_tracker_row(conn, tmp_path):
    seed(conn, tmp_path)
    load_hooks.load(conn, "vita-bull", "v1", three())
    conn.execute("DELETE FROM tracker_rows WHERE row_key='c1#2'")
    assert set(hooks(conn)) == {"c1#1", "c2#1"}


def test_long_hooks_are_counted(conn, tmp_path):
    seed(conn, tmp_path)
    long = " ".join(["word"] * 19)
    st = load_hooks.load(conn, "vita-bull", "v1", [entry(1, "c1", "fam1", long), entry(2, "c1", "fam1", " ".join(["word"] * 18)), entry(3, "c2", "c2", "short")])
    assert st["hooks_over_18_words"] == 1 and st["hooks_written"] == 3                                               # loaded as given, just reported


def test_cli_records_an_etl_run(conn, dburl, tmp_path, monkeypatch):
    seed(conn, tmp_path)
    conn.commit()
    f = tmp_path / "hooks.json"
    f.write_text(json.dumps(three()))
    monkeypatch.setattr(sys, "argv", ["load_hooks", "--file", str(f), "--version", "hooks-v1-2026-11-05", "--product", "vita-bull", "--extractor", "test"])
    load_hooks.main()
    with psycopg.connect(dburl) as c:
        run = c.execute("SELECT job, status, rows_written, stats->>'matched', finished_at IS NOT NULL FROM etl_runs ORDER BY id DESC LIMIT 1").fetchone()
        assert run == ("load_hooks", "ok", 3, "3", True)
        assert c.execute("SELECT count(*), min(extractor), min(version) FROM ad_hooks").fetchone() == (3, "test", "hooks-v1-2026-11-05")
    # a rejected file leaves a failed run with the reason and no rows touched
    monkeypatch.setattr(sys, "argv", ["load_hooks", "--file", str(f), "--version", "v2", "--product", "boneflex"])
    with pytest.raises(RuntimeError, match="does not match the tracker"):
        load_hooks.main()
    with psycopg.connect(dburl) as c:
        job, status, error = c.execute("SELECT job, status, error FROM etl_runs ORDER BY id DESC LIMIT 1").fetchone()
        assert (job, status) == ("load_hooks", "failed") and "min coverage" in error
        assert c.execute("SELECT count(*) FROM ad_hooks WHERE version = 'v2'").fetchone()[0] == 0
