"""etl.retag_pain with `claude -p` mocked: selection (mismatch only), dry run writes nothing, apply / idempotency / same-line skip / stale guard, revert,
--from, resume from the --out file, read-only dry-run session, previous tag shown to the model."""
import datetime as dt
import json
import subprocess

import pytest

from etl import auto_tag as at
from etl import db, retag_pain as rp
from conftest import trow
from test_auto_tag import FakeClaude, add_thumb, answer, envelope, seed_taxonomy, sync

TODAY = dt.date(2026, 11, 10)
VER = "retag-pain-2026-11-10"


@pytest.fixture(autouse=True)
def token(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-testtesttesttesttest")
    monkeypatch.setattr(at, "BACKOFF", (0, 0))


def old_tag(conn, family, code, extra=None):
    rid = conn.execute("SELECT id FROM tracker_rows WHERE family_key=%s", (family,)).fetchone()[0]
    cat = {c["line"]: c for c in at.load_catalog("boneflex")}[code]
    lid = conn.execute("SELECT id FROM taxonomy_lines WHERE line_code=%s", (code,)).fetchone()[0]
    conn.execute("""INSERT INTO ad_tags (tracker_row_id, tag_set, version, is_current, line_id, cluster, icp, pain_point, emotional_trigger, confidence, method, seen, why, tagger, extra)
                    VALUES (%s,'linemap','linemap-v2-2026-11-05',true,%s,%s,%s,%s,%s,'medium','image + copy','man on a farm','organs and ancestral diet','linemap',%s)""",
                 (rid, lid, cat["cluster"], cat["icp"], cat["pain"], cat["trigger"], db.jsonb(extra or {"heard": "grandfather ate organ meat"})))
    conn.commit()
    return rid


@pytest.fixture
def world(conn, tmp_path):
    sync(conn, tmp_path, [trow("c1", "fam1", "Boneflex", pain="Bone Density Loss"),          # mismatch (tag = Joint Pain)
                          trow("c2", "fam2", "Boneflex", pain="Joint Pain & Mobility Loss"),  # tag already on the tracker pain
                          trow("c3", "fam3", "Boneflex", pain="No Clear Pain Point"),         # placeholder
                          trow("c4", "fam4", "Boneflex", pain="Not A Real Pain"),             # tracker pain on no line
                          trow("c5", "fam5", "Boneflex", pain="Bone Density Loss")])          # mismatch, spends more
    seed_taxonomy(conn)
    for f in ("fam1", "fam2", "fam3", "fam4", "fam5"):
        add_thumb(conn, f)
    ids = {f: old_tag(conn, f, "BF18" if f != "fam2" else "BF18") for f in ("fam1", "fam2", "fam3", "fam4", "fam5")}
    conn.execute("""INSERT INTO ad_metrics (tracker_row_id, product_id, family_key, is_first_row, spend_launch, spend_30d)
                    SELECT id, product_id, family_key, true, 900, 30 FROM tracker_rows WHERE family_key='fam1'""")
    conn.execute("""INSERT INTO ad_metrics (tracker_row_id, product_id, family_key, is_first_row, spend_launch, spend_30d)
                    SELECT id, product_id, family_key, true, 5000, 400 FROM tracker_rows WHERE family_key='fam5'""")
    conn.commit()
    return ids


def model(code="BF03", **kw):
    return FakeClaude([(0, envelope(answer(line=code, **kw)), "")])


def tags(conn):
    return conn.execute("""SELECT t.concept_id, g.version, g.is_current, g.tagger, g.extra->>'previous_tag_id' FROM ad_tags g JOIN tracker_rows t ON t.id=g.tracker_row_id
                           WHERE g.tag_set='linemap' ORDER BY t.concept_id, g.id""").fetchall()


def test_select_mismatch_only_and_biggest_spend_first(conn, world):
    rows = rp.select_mismatch(conn)
    assert [r["concept_id"] for r in rows] == ["c5", "c1"]
    assert rows[0]["spend_30d"] == 400 and rows[0]["old_line"] == "BF18" and rows[0]["old_pain"] == "Joint Pain & Mobility Loss"
    assert [r["concept_id"] for r in rp.select_mismatch(conn, limit=1)] == ["c5"]
    assert [r["concept_id"] for r in rp.select_mismatch(conn, ids=[world["fam1"], world["fam3"]])] == ["c1"]


def test_sample_ids_top_plus_seeded_random_and_forced(conn, world):
    rows = rp.select_mismatch(conn)
    a = rp.sample_ids(rows, 1, 5, seed=7)
    assert a == rp.sample_ids(rows, 1, 5, seed=7) and a[0] == rows[0]["id"] and sorted(a) == sorted({r["id"] for r in rows})
    assert rows[1]["id"] in rp.sample_ids(rows, 0, 1, seed=1, must=(rows[1]["id"],))


def test_dry_run_writes_nothing_and_gives_the_model_the_previous_tag(conn, world, tmp_path):
    out = tmp_path / "o.jsonl"
    fake = model("BF03")
    st = rp.run(conn, rp.select_mismatch(conn), str(out), day=TODAY, runner=fake, sleep=lambda s: None, workers=1)
    assert st["model_rows"] == 2 and st["errors"] == 0 and st["applied"] == 0
    assert [t[1:3] for t in tags(conn)] == [("linemap-v2-2026-11-05", True)] * 5          # database untouched
    recs = [json.loads(x) for x in out.read_text().splitlines()]
    r1 = next(r for r in recs if r["concept_id"] == "c1")
    assert r1["old_line"] == "BF18" and r1["new_line"] == "BF03" and r1["new_pain"] == "Bone Density Loss" and r1["tracker_pain"] == "Bone Density Loss"
    assert r1["kept_tracker_pain"] is True and r1["override_reason"] is None and r1["spend_launch"] == 900 and r1["spend_30d"] == 30
    assert r1["method"] == "image + copy (auto)" and r1["new_cluster"] and r1["confidence"] == "high"
    p = fake.calls[0]["input"]
    assert '"previous_tag"' in p and "organs and ancestral diet" in p and "grandfather ate organ meat" in p and "may have confused the product mechanism" in p
    assert '"tracker_pain_point": "Bone Density Loss"' in p


def test_dry_run_session_is_read_only(dburl):
    c = rp.connect_ro()
    try:
        with pytest.raises(Exception, match="read-only"):
            c.execute("CREATE TABLE zz_should_fail (x int)")
    finally:
        c.rollback()
        c.close()


def test_resume_skips_rows_already_in_out_file(conn, world, tmp_path):
    out = tmp_path / "o.jsonl"
    rows = rp.select_mismatch(conn)
    rp.run(conn, rows[:1], str(out), day=TODAY, runner=model(), sleep=lambda s: None, workers=1)
    fake = model()
    st = rp.run(conn, rows, str(out), day=TODAY, runner=fake, sleep=lambda s: None, workers=1)
    assert st["skipped_done"] == 1 and st["model_rows"] == 1 and len(fake.calls) == 1
    assert len(out.read_text().splitlines()) == 2


def test_apply_is_one_swap_per_row_and_idempotent(conn, world, tmp_path):
    out = tmp_path / "o.jsonl"
    rows = rp.select_mismatch(conn)
    st = rp.run(conn, rows, str(out), apply=True, day=TODAY, runner=model("BF03"), sleep=lambda s: None, workers=1)
    assert st["applied"] == 2
    t = tags(conn)
    cur = [x for x in t if x[2]]
    assert sorted((x[0], x[1], x[3]) for x in cur if x[0] in ("c1", "c5")) == [("c1", VER, rp.TAGGER), ("c5", VER, rp.TAGGER)]
    assert all(x[2] is False for x in t if x[1] == "linemap-v2-2026-11-05" and x[0] in ("c1", "c5"))
    assert [x for x in t if x[0] == "c2"][0][2] is True                                      # non-mismatch rows untouched
    new = conn.execute("SELECT line_id, pain_point, extra FROM ad_tags WHERE version=%s AND tracker_row_id=%s", (VER, world["fam1"])).fetchone()
    assert new[1] == "Bone Density Loss" and new[2]["previous_tag_id"] and new[2]["previous_line_code"] == "BF18" and new[2]["kept_tracker_pain"] is True
    # rerun: nothing new is written (rows in the --out file are applied again but find the version already there; the selection itself is now empty)
    assert rp.select_mismatch(conn) == []
    recs = rp.read_done(str(out))
    st2 = rp.run(conn, rows, None, apply=True, day=TODAY, from_records=recs)
    assert st2["applied"] == 0 and st2["exists"] == 2
    assert len([x for x in tags(conn) if x[1] == VER]) == 2


def test_apply_skips_same_line_and_stale(conn, world):
    rows = rp.select_mismatch(conn)
    lines = {("boneflex", c): i for c, i in conn.execute("SELECT line_code, id FROM taxonomy_lines").fetchall()}
    base = {"id": world["fam1"], "product": "boneflex", "old_tag_id": rows[1]["tag_id"], "old_line": "BF18", "new_line": "BF18", "new_cluster": "c", "new_icp": "i", "new_pain": "p",
            "new_trigger": "t", "confidence": "high", "method": "m"}
    assert rp.apply_record(conn, base, VER, lines) == "same_line"
    assert rp.apply_record(conn, dict(base, new_line="BF03", old_tag_id=999999), VER, lines) == "stale"
    assert rp.apply_record(conn, {"id": 1, "error": "x"}, VER, lines) == "error"
    conn.commit()
    assert [x for x in tags(conn) if x[1] == VER] == []


def test_revert_restores_the_previous_tags(conn, world, tmp_path):
    before = tags(conn)
    rp.run(conn, rp.select_mismatch(conn), None, apply=True, day=TODAY, runner=model(), sleep=lambda s: None, workers=1)
    assert tags(conn) != before
    res = rp.revert(conn, VER)
    conn.commit()
    assert res["reverted"] == 2
    assert [(t[0], t[1], t[2]) for t in tags(conn)] == [(t[0], t[1], t[2]) for t in before]
    assert rp.revert(conn, VER)["reverted"] == 0
    with pytest.raises(db.JobFailed):
        rp.revert(conn, "auto-2026-11-10")


def test_revert_leaves_a_row_that_was_changed_since(conn, world):
    rp.run(conn, rp.select_mismatch(conn), None, apply=True, day=TODAY, runner=model(), sleep=lambda s: None, workers=1)
    rid = world["fam1"]
    conn.execute("UPDATE ad_tags SET is_current=false WHERE tracker_row_id=%s AND version=%s", (rid, VER))
    conn.commit()
    res = rp.revert(conn, VER)
    assert res["reverted"] == 1 and res["not_current"] == 1


def test_a_failing_row_is_recorded_not_applied(conn, world, tmp_path):
    out = tmp_path / "o.jsonl"
    bad = FakeClaude([(0, envelope("not json"), "")])
    st = rp.run(conn, rp.select_mismatch(conn), str(out), apply=True, day=TODAY, runner=bad, sleep=lambda s: None, workers=1)
    assert st["errors"] == 2 and st["applied"] == 0
    assert all("error" in json.loads(x) for x in out.read_text().splitlines())
    assert not [x for x in tags(conn) if x[1] == VER]


def test_usage_limit_stops_the_run(conn, world, tmp_path):
    lim = FakeClaude([(1, "", "You have reached your usage limit")])
    st = rp.run(conn, rp.select_mismatch(conn), None, day=TODAY, runner=lim, sleep=lambda s: None, workers=1)
    assert st["stopped_early"] and "limit" in st["stopped_early"]
