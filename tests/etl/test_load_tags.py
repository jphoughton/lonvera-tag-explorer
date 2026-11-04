"""Beef Liver line-map load: tag file shape (saw -> seen, media -> method, heard / gap / media_note -> extra), ordinal matching incl. blank
families, idempotency, taxonomy code mapping (tagging-catalog codes, post-cycle lines, two-phase rename), tag-set flip, reliability numbers."""
import json

import psycopg
import pytest
from psycopg.rows import dict_row

from etl import db, load_tags, load_taxonomy, sheets_io, tracker_sync
from conftest import trow, write_snapshot

HDR = ["Product", "Market Cluster", "ICP", "Pain Point", "Ingredient", "What It Attacks", "What It Supports", "Emotional Trigger", "Stories"]
# sheet order: (C1, I1, P1) (C1, I1, P2) (C1, I2, P1)  <- inserted after the tagging cycle  (C2, I3, P3)
OVERVIEW = [HDR,
            ["Beef Liver", "C1", "I1", "P1", "Iron", "a1", "s1", "Fear", "story one"],
            ["", "", "", "P2", "Iron", "a2", "s2", "Shame", "story two"],
            ["", "", "I2", "P1", "Iron", "a3", "s3", "Relief", "story new"],
            ["", "C2", "I3", "P3", "Zinc", "a4", "s4", "Pride", "story four"],
            ["Vita Bull", "VC", "VI", "VP", "Zinc", "x", "y", "Fear", "vb story"]]
CATALOG = [{"line": "B01", "cluster": "C1", "icp": "I1", "pain": "P1"}, {"line": "B02", "cluster": "C1", "icp": "I1", "pain": "P2"},
           {"line": "B03", "cluster": "C2", "icp": "I3", "pain": "P3"}]


@pytest.fixture
def bl_cycle(tmp_path):
    d = tmp_path / "bl-cycle"
    d.mkdir()
    (d / "catalog.json").write_text(json.dumps(CATALOG))
    return d


@pytest.fixture(autouse=True)
def reset_bl_tag_set(dburl):
    """products is not truncated between tests; these tests flip Beef Liver to 'linemap', so put it back around every test."""
    def reset():
        with psycopg.connect(dburl) as c:
            c.execute("UPDATE products SET tag_set='tracker' WHERE slug='beef-liver'")
    reset()
    yield
    reset()


def sync(conn, tmp_path, rows):
    snap = tmp_path / "snap"
    snap.mkdir(exist_ok=True)
    write_snapshot(snap, rows, [("2026-05-06", "x", 1, 0, 0, 0)])
    return tracker_sync.sync(conn, sheets_io.read_tab("creative_tracker", snap))


def tag(row, cid, fam, line="B01", cluster="C1", icp="I1", pain="P1", conf="high", **kw):
    t = {"row": row, "concept_id": cid, "ad_family": fam, "status": "8 - Launched", "type": "VID", "unit_id": f"U{row}", "line": line, "cluster": cluster,
         "icp": icp, "pain": pain, "trigger": "Fear", "confidence": conf, "runner_up": "I1 - P2 (Shame)", "why": "because", "saw": "a bottle on a counter",
         "heard": "she says iron", "gap": "", "media": "video: 4 frames + Whisper transcript", "media_note": "contact sheet of 4 frames available", "spend": 12.5,
         "cur_icp": "BL1", "cur_legacy_icp": "old icp", "cur_pain": "Hair Loss", "cur_emotion": "Fear"}
    t.update(kw)
    return t


def snapshot_tags(conn):
    return conn.execute("""SELECT tracker_row_id, tag_set, version, is_current, line_id, cluster, icp, pain_point, emotional_trigger, confidence, method, seen, why,
                           runner_up, copy_only_line, tagger, extra::text FROM ad_tags WHERE tag_set='linemap' ORDER BY tracker_row_id""").fetchall()


def seed(conn, tmp_path, bl_cycle):
    load_taxonomy.load(conn, OVERVIEW, cycle_dir=load_taxonomy.Path("/nonexistent"), bl_cycle_dir=bl_cycle)
    trs = [trow("c1", "fam1", "BeefLiver", Type="VID"), trow("c1", "fam1", "BeefLiver", Type="VID"), trow("c2", "", "BeefLiver", Type="IMG")]
    sync(conn, tmp_path, trs)


# ---- taxonomy codes ----------------------------------------------------------------------------------------------------------------------

def codes(conn, slug="beef-liver"):
    return conn.execute("""SELECT t.cluster, t.icp, t.pain_point, t.line_code FROM taxonomy_lines t JOIN products p ON p.id=t.product_id
                           WHERE p.slug=%s ORDER BY t.sort_order""", (slug,)).fetchall()


def test_taxonomy_uses_tagging_catalog_codes_and_next_free_code(conn, bl_cycle):
    st = load_taxonomy.load(conn, OVERVIEW, cycle_dir=load_taxonomy.Path("/nonexistent"), bl_cycle_dir=bl_cycle)
    assert codes(conn) == [("C1", "I1", "P1", "B01"), ("C1", "I1", "P2", "B02"), ("C1", "I2", "P1", "B04"), ("C2", "I3", "P3", "B03")]   # B04 = added after the cycle
    assert st["products"]["beef-liver"]["not_in_tagging_catalog"] == 1 and any("B04" in w for w in st["warnings"])
    assert codes(conn, "vita-bull") == [("VC", "VI", "VP", "L01")]                                                                       # other products keep L codes
    before = conn.execute("SELECT id, line_code, sort_order FROM taxonomy_lines ORDER BY id").fetchall()
    load_taxonomy.load(conn, OVERVIEW, cycle_dir=load_taxonomy.Path("/nonexistent"), bl_cycle_dir=bl_cycle)                                # idempotent
    assert conn.execute("SELECT id, line_code, sort_order FROM taxonomy_lines ORDER BY id").fetchall() == before
    assert conn.execute("SELECT count(*) FROM line_ingredients").fetchone()[0] == 5


def test_taxonomy_recodes_existing_lines_two_phase(conn, bl_cycle):
    load_taxonomy.load(conn, OVERVIEW, cycle_dir=load_taxonomy.Path("/nonexistent"), bl_cycle_dir=bl_cycle)
    ids = dict(conn.execute("SELECT line_code, id FROM taxonomy_lines WHERE line_code LIKE 'B%'").fetchall())
    # rotate the codes so the plan has to swap B01<->B03 and B02<->B04 (a one-pass rename would violate UNIQUE (product_id, line_code))
    conn.execute("UPDATE taxonomy_lines SET line_code = 'tmp' || id WHERE line_code LIKE 'B%'")
    for old, new in (("B01", "B03"), ("B02", "B04"), ("B03", "B01"), ("B04", "B02")):
        conn.execute("UPDATE taxonomy_lines SET line_code=%s WHERE id=%s", (new, ids[old]))
    st = load_taxonomy.load(conn, OVERVIEW, cycle_dir=load_taxonomy.Path("/nonexistent"), bl_cycle_dir=bl_cycle)
    assert st["products"]["beef-liver"]["recoded"] == 4
    assert dict(conn.execute("SELECT line_code, id FROM taxonomy_lines WHERE line_code LIKE 'B%'").fetchall()) == ids          # same rows, codes restored


def test_taxonomy_legacy_l_codes_become_b_codes_without_losing_line_ids(conn, bl_cycle):
    load_taxonomy.load(conn, OVERVIEW, cycle_dir=load_taxonomy.Path("/nonexistent"), bl_cycle_dir=None)                        # no catalog: sheet order B01..B04
    assert [c[3] for c in codes(conn)] == ["B01", "B02", "B03", "B04"]
    conn.execute("UPDATE taxonomy_lines SET line_code = 'L' || substr(line_code, 2) WHERE line_code LIKE 'B%'")                  # what the DB held before this cycle
    ids = {r[0]: r[1] for r in conn.execute("SELECT pain_point || icp, id FROM taxonomy_lines WHERE line_code LIKE 'L%' AND product_id=(SELECT id FROM products WHERE slug='beef-liver')").fetchall()}
    load_taxonomy.load(conn, OVERVIEW, cycle_dir=load_taxonomy.Path("/nonexistent"), bl_cycle_dir=bl_cycle)
    assert [c[3] for c in codes(conn)] == ["B01", "B02", "B04", "B03"]
    assert {r[0]: r[1] for r in conn.execute("SELECT pain_point || icp, id FROM taxonomy_lines WHERE product_id=(SELECT id FROM products WHERE slug='beef-liver')").fetchall()} == ids


# ---- tag file shape + matching -----------------------------------------------------------------------------------------------------------

def test_bl_shape_mapping_matching_and_idempotency(conn, tmp_path, bl_cycle):
    seed(conn, tmp_path, bl_cycle)
    # the cycle export writes the concept id into blank families (c2); the tracker keeps them blank. File rows are stale (50/20/21): order decides ordinals.
    tags = [tag(50, "c1", "fam1", line="B02", pain="P2", conf="low", gap="no persona"),
            tag(20, "c1", "fam1", line="B01"),
            tag(21, "c2", "c2", line="B03", cluster="C2", icp="I3", pain="P3", conf="medium", media="no picture (copy only)", saw="no picture", heard="", type="IMG")]
    st = load_tags.load(conn, "beef-liver", "linemap", "linemap-v2-2026-11-04", tags)
    assert st["matched"] == 3 and st["tracker_rows"] == 3 and st["line_code_mismatch"] == 0 and st["unique_families"] == 2
    assert st["tracker_vs_file"]["blank_family_rows"] == 1 and st["product_tag_set_flipped"] == 1
    rows = {r[0]: r for r in conn.execute("""SELECT t.row_key, a.pain_point, a.seen, a.method, a.confidence, a.runner_up, a.why, a.emotional_trigger, a.extra, tl.line_code, a.copy_only_line, a.is_current
                                             FROM ad_tags a JOIN tracker_rows t ON t.id=a.tracker_row_id JOIN taxonomy_lines tl ON tl.id=a.line_id WHERE a.tag_set='linemap'""").fetchall()}
    assert set(rows) == {"c1#1", "c1#2", "c2#1"}
    first, second, blank = rows["c1#1"], rows["c1#2"], rows["c2#1"]
    assert first[1] == "P1" and first[9] == "B01" and second[1] == "P2" and second[9] == "B02"            # ordinal follows the file's `row` order (20 before 50)
    assert first[2] == "a bottle on a counter" and first[3] == "video: 4 frames + Whisper transcript"       # saw -> seen, media -> method
    assert (first[4], first[5], first[6], first[7], first[10], first[11]) == ("high", "I1 - P2 (Shame)", "because", "Fear", None, True)
    assert first[8]["heard"] == "she says iron" and first[8]["media_note"] == "contact sheet of 4 frames available" and first[8]["gap"] == ""
    assert first[8]["line_code"] == "B01" and first[8]["unit_id"] == "U20" and first[8]["cur_pain"] == "Hair Loss" and first[8]["tag_file_row"] == 20
    assert second[8]["gap"] == "no persona" and blank[3] == "no picture (copy only)" and blank[2] == "no picture" and blank[4] == "medium"
    snap = snapshot_tags(conn)
    st2 = load_tags.load(conn, "beef-liver", "linemap", "linemap-v2-2026-11-04", tags)                    # same version again = same rows
    assert snapshot_tags(conn) == snap and st2["product_tag_set_flipped"] == 0
    assert conn.execute("SELECT count(*) FROM ad_tags WHERE tag_set='linemap'").fetchone()[0] == 3


def test_vita_bull_file_shape_is_unchanged():
    """VB files carry seen / method already (and copy-only fields); `normalize` must leave them alone."""
    t = {"row": 5, "concept_id": "A", "ad_family": "fam", "seen": "", "method": "text only", "line": "L01", "text_line": "L02"}
    n = load_tags.normalize(t)
    assert n["seen"] == "" and n["method"] == "text only" and n["text_line"] == "L02"
    b = load_tags.normalize({"saw": "x", "media": "image"})
    assert b["seen"] == "x" and b["method"] == "image"
    assert load_tags.normalize({"saw": "", "media": ""})["seen"] == ""


def test_inconsistent_matches_and_low_coverage_fail_loudly(conn, tmp_path, bl_cycle):
    seed(conn, tmp_path, bl_cycle)
    ok = [tag(1, "c1", "fam1"), tag(2, "c1", "fam1"), tag(3, "c2", "c2", line="B03", cluster="C2", icp="I3", pain="P3", type="IMG")]
    with pytest.raises(RuntimeError, match="does not match the tracker.*2 of 3 tracker rows matched"):
        load_tags.load(conn, "beef-liver", "linemap", "v-short", ok[:2])                                  # 66.7% < the default 90%
    bad = [dict(ok[0], line="B09")] + ok[1:]                                                              # (C1, I1, P1) is B01 in the taxonomy, not B09
    with pytest.raises(RuntimeError, match="line codes disagree"):
        load_tags.load(conn, "beef-liver", "linemap", "v-code", bad)
    unknown = [dict(ok[0], pain="Nope")] + ok[1:]
    with pytest.raises(RuntimeError, match="missing from taxonomy"):
        load_tags.load(conn, "beef-liver", "linemap", "v-line", unknown)
    wrong_type = [dict(t, type="IMG" if t["type"] == "VID" else "VID") for t in ok]                       # every matched pair disagrees on VID/IMG
    with pytest.raises(RuntimeError, match="inconsistent with the tracker.*ad type differs"):
        load_tags.load(conn, "beef-liver", "linemap", "v-type", wrong_type)
    assert conn.execute("SELECT count(*) FROM ad_tags WHERE tag_set='linemap'").fetchone()[0] == 0


def test_tracker_growth_is_not_an_error(conn, tmp_path, bl_cycle):
    """Tracker rows added after the cycle stay untagged; tags whose key left the tracker are skipped with a warning; coverage decides."""
    seed(conn, tmp_path, bl_cycle)                                                                        # 3 tracker rows: c1#1, c1#2, c2#1
    ok = [tag(1, "c1", "fam1"), tag(2, "c1", "fam1"), tag(3, "c2", "c2", line="B03", cluster="C2", icp="I3", pain="P3", type="IMG")]
    extra = tag(4, "gone-from-tracker", "gone", line="B02", pain="P2")
    st = load_tags.load(conn, "beef-liver", "linemap", "v1", ok + [extra])                                  # a tag with no tracker row: skipped, 3 of 3 rows matched
    assert st["matched"] == 3 and st["coverage_pct"] == 100.0 and st["tags_skipped_not_in_tracker"] == 1 and st["skipped_tag_keys"] == ["gone-from-tracker | gone"]
    assert st["tracker_rows_untagged"] == 0 and st["product_tag_set_flipped"] == 1
    # the tracker grows by two rows: 3 of 5 = 60% matched -> fails at the default, passes with a lower floor, rows stay untagged
    sync(conn, tmp_path, [trow("c1", "fam1", "BeefLiver", Type="VID"), trow("c1", "fam1", "BeefLiver", Type="VID"), trow("c2", "", "BeefLiver", Type="IMG"),
                          trow("new1", "n1", "BeefLiver", Type="VID"), trow("new2", "n2", "BeefLiver", Type="IMG")])
    with pytest.raises(RuntimeError, match="3 of 5 tracker rows matched"):
        load_tags.load(conn, "beef-liver", "linemap", "v2", ok)
    conn.execute("UPDATE products SET tag_set='tracker' WHERE slug='beef-liver'")
    st = load_tags.load(conn, "beef-liver", "linemap", "v2", ok, min_coverage=0.6)
    assert (st["matched"], st["tracker_rows"], st["tracker_rows_untagged"], st["coverage_pct"], st["tagged_pct"]) == (3, 5, 2, 60.0, 60.0)
    assert sorted(st["untagged_keys"]) == ["new1 | n1", "new2 | n2"] and st["product_tag_set_flipped"] == 1                                   # 60% >= 60%
    assert conn.execute("SELECT count(*) FROM ad_tags a JOIN tracker_rows t ON t.id=a.tracker_row_id WHERE a.tag_set='linemap' AND t.concept_id LIKE 'new%'").fetchone()[0] == 0
    # below the floor: allowed only with allow_mismatch, and then the product is not flipped
    conn.execute("UPDATE products SET tag_set='tracker' WHERE slug='beef-liver'")
    st = load_tags.load(conn, "beef-liver", "linemap", "v3", ok[:2], allow_mismatch=True, min_coverage=0.9)
    assert st["matched"] == 2 and st["product_tag_set_flipped"].startswith("skipped") and conn.execute("SELECT tag_set FROM products WHERE slug='beef-liver'").fetchone()[0] == "tracker"
    # rows a newer file does not cover keep their current tag (v2's tag of c2#1 survives v3, which only tags c1#1 and c1#2)
    assert conn.execute("SELECT version, count(*) FROM ad_tags WHERE tag_set='linemap' AND is_current GROUP BY 1 ORDER BY 1").fetchall() == [("v2", 1), ("v3", 2)]


def test_tag_set_flips_only_on_full_coverage_and_versions_roll(conn, tmp_path, bl_cycle):
    seed(conn, tmp_path, bl_cycle)
    ok = [tag(1, "c1", "fam1"), tag(2, "c1", "fam1"), tag(3, "c2", "c2", line="B03", cluster="C2", icp="I3", pain="P3", type="IMG")]
    assert conn.execute("SELECT tag_set FROM products WHERE slug='beef-liver'").fetchone()[0] == "tracker"
    st = load_tags.load(conn, "beef-liver", "linemap", "linemap-v1", ok[:2], allow_mismatch=True)
    assert st["product_tag_set_flipped"].startswith("skipped") and conn.execute("SELECT tag_set FROM products WHERE slug='beef-liver'").fetchone()[0] == "tracker"
    load_tags.load(conn, "beef-liver", "linemap", "linemap-v2", ok)
    assert conn.execute("SELECT tag_set FROM products WHERE slug='beef-liver'").fetchone()[0] == "linemap"
    assert conn.execute("SELECT version, is_current, count(*) FROM ad_tags WHERE tag_set='linemap' GROUP BY 1,2 ORDER BY 1").fetchall() == [("linemap-v1", False, 2), ("linemap-v2", True, 3)]
    conn.execute("UPDATE products SET tag_set='tracker' WHERE slug='beef-liver'")
    load_tags.load(conn, "beef-liver", "linemap", "linemap-v3", ok, flip=False)
    assert conn.execute("SELECT tag_set FROM products WHERE slug='beef-liver'").fetchone()[0] == "tracker"
    # tracker tags are a separate tag set and survive
    assert conn.execute("SELECT count(*) FROM ad_tags WHERE tag_set='tracker' AND is_current").fetchone()[0] == 3


# ---- reliability -------------------------------------------------------------------------------------------------------------------------

@pytest.fixture
def qa_cycle(tmp_path):
    d = tmp_path / "qa-cycle"
    (d / "results").mkdir(parents=True)
    (d / "qa_results").mkdir()
    cat = [{"line": "B01", "cluster": "C1", "icp": "I1", "pain": "P1"}, {"line": "B02", "cluster": "C1", "icp": "I1", "pain": "P2"},
           {"line": "B03", "cluster": "C1", "icp": "I2", "pain": "P1"}, {"line": "B04", "cluster": "C2", "icp": "I3", "pain": "P3"}]
    (d / "catalog.json").write_text(json.dumps(cat))
    # U2 appears in two result files: the LATER file (sorted) is the final tag, exactly like final_stats.py
    (d / "results" / "a.json").write_text(json.dumps([{"unit_id": "U1", "line": "B01", "confidence": "high"}, {"unit_id": "U2", "line": "B02", "confidence": "low"},
                                                      {"unit_id": "U3", "line": "B03", "confidence": "medium"}, {"unit_id": "U4", "line": "B04", "confidence": "medium"}]))
    (d / "results" / "b.json").write_text(json.dumps([{"unit_id": "U2", "line": "B02", "confidence": "medium"}]))
    (d / "qa_results" / "q.json").write_text(json.dumps([{"unit_id": "U1", "line": "B01"}, {"unit_id": "U2", "line": "B03"},           # same cluster, other ICP
                                                         {"unit_id": "U3", "line": "B01"}, {"unit_id": "U4", "line": "B04"}]))             # U3: same cluster, other ICP
    return d


def test_compute_reliability_uses_final_first_tags(qa_cycle):
    r = load_tags.compute_reliability(qa_cycle)
    assert r["n"] == 4
    assert r["overall"] == {"line": 0.5, "icp": 0.5, "cluster": 1.0, "pain": 0.75}
    assert r["by_confidence"]["high"] == {"n": 1, "line": 1.0, "icp": 1.0, "cluster": 1.0, "pain": 1.0}
    assert r["by_confidence"]["medium"]["n"] == 3 and r["by_confidence"]["medium"]["line"] == pytest.approx(0.3333, abs=1e-4)   # U2 counts as medium (final), not low
    assert "low" not in r["by_confidence"]


def test_reliability_stored_per_product_and_read_by_the_note(conn, dburl, qa_cycle):
    from app import queries
    load_tags.store_reliability(conn, "beef-liver", load_tags.compute_reliability(qa_cycle))
    load_tags.store_reliability(conn, "vita-bull", load_tags.VB_RELIABILITY)
    conn.commit()
    with psycopg.connect(dburl, row_factory=dict_row) as c:
        bl = queries.reliability_stats(c, "beef-liver")
        assert queries.reliability_note("image", "high", bl) == "High confidence — blind re-tag agreed ~100%. Blind re-tag agreement overall: line 50%, ICP 50%, cluster 100%."
        assert queries.reliability_note("no picture (copy only)", "medium", bl).startswith("Tagged from copy only (no image). Medium confidence — blind re-tag agreed ~33%.")
        assert queries.reliability_note("image", "low", bl) == "Low confidence. Blind re-tag agreement overall: line 50%, ICP 50%, cluster 100%."   # no low units in the sample
        vb = queries.reliability_stats(c, "vita-bull")
        assert queries.reliability_note("image", "high", vb) == "High confidence — blind re-tag agreed ~93%. Blind re-tag agreement overall: line 79%, ICP 84%, cluster 88%."
        assert queries.reliability_note("text only", "low", vb).startswith("Tagged from copy only (no image). Low confidence — blind re-tag agreed ~52%.")
        assert queries.reliability_stats(c, "boneflex") is None
    assert queries.reliability_note("image", "medium", None) == "Medium confidence."                      # no numbers -> no percentages
    assert queries.reliability_note("text only", None, None) == "Tagged from copy only (no image)."


def test_reliability_vita_bull_falls_back_without_app_meta(conn, dburl):
    from app import queries
    with psycopg.connect(dburl, row_factory=dict_row) as c:
        assert queries.reliability_stats(c, "vita-bull") is queries._VB_RELIABILITY
        assert queries.reliability_stats(c, "beef-liver") is None
        db.set_meta(conn, "reliability:beef-liver", "not json")
        conn.commit()
        assert queries.reliability_stats(c, "beef-liver") is None


# ---- Boneflex: two lines that differ only by emotional trigger (migration 006), BF codes, placeholder rows ----------------------------------

BF_OVERVIEW = [HDR,
               ["Boneflex", "K1", "J1", "Q1", "Chondroitin", "a", "s", "Shame", "story shame"],
               ["", "", "", "Q2", "Zinc", "a", "s", "Fear", "story fear"],
               ["", "", "", "Q1", "Chondroitin", "a", "s", "Desire", "story desire"],            # same cluster / ICP / pain as the first line, other trigger
               ["", "K2", "J2", "Q3", "Zinc", "a", "s", "Pride", "story pride"]]
BF_CATALOG = [{"line": "BF01", "cluster": "K1", "icp": "J1", "pain": "Q1", "trigger": "Shame"}, {"line": "BF02", "cluster": "K1", "icp": "J1", "pain": "Q2", "trigger": "Fear"},
              {"line": "BF03", "cluster": "K1", "icp": "J1", "pain": "Q1", "trigger": "Desire"}, {"line": "BF04", "cluster": "K2", "icp": "J2", "pain": "Q3", "trigger": "Pride"}]


@pytest.fixture
def bf_cycle(tmp_path):
    d = tmp_path / "bf-cycle"
    d.mkdir()
    (d / "catalog.json").write_text(json.dumps(BF_CATALOG))
    return d


def bf_codes(conn):
    return conn.execute("""SELECT t.line_code, t.cluster, t.icp, t.pain_point, t.emotional_trigger FROM taxonomy_lines t JOIN products p ON p.id=t.product_id
                           WHERE p.slug='boneflex' ORDER BY t.sort_order""").fetchall()


def test_migration_006_unique_key_includes_the_trigger(conn):
    pid = conn.execute("SELECT id FROM products WHERE slug='boneflex'").fetchone()[0]
    ins = "INSERT INTO taxonomy_lines (product_id, line_code, cluster, icp, pain_point, emotional_trigger) VALUES (%s,%s,'c','i','p',%s)"
    conn.execute(ins, (pid, "X1", "Shame"))
    conn.execute(ins, (pid, "X2", "Desire"))                                                      # same cluster / ICP / pain, other trigger: allowed now
    for code, trig in (("X3", "Shame"), ("X4", None)):
        if trig is None:
            conn.execute(ins, (pid, "X5", None))                                                  # a line without a trigger is allowed once ...
        with pytest.raises(psycopg.errors.UniqueViolation):
            with conn.transaction():
                conn.execute(ins, (pid, code, trig))                                              # ... and the full key (NULL read as '') stays unique
    with pytest.raises(psycopg.errors.UniqueViolation):
        with conn.transaction():
            conn.execute(ins, (pid, "X1", "Pride"))                                               # (product_id, line_code) stays unique
    conn.rollback()


def test_two_lines_sharing_a_pain_point_are_kept_apart(conn, tmp_path, bf_cycle):
    # state before this cycle: the old loader folded the Desire line into the Shame line, which sat under a legacy L code with its ingredient rows
    pid = conn.execute("SELECT id FROM products WHERE slug='boneflex'").fetchone()[0]
    first = conn.execute("""INSERT INTO taxonomy_lines (product_id, line_code, cluster, icp, pain_point, emotional_trigger, story, sort_order) VALUES (%s,'L01','K1','J1','Q1','Shame','old',1)
                            RETURNING id""", (pid,)).fetchone()[0]
    conn.execute("INSERT INTO line_ingredients (line_id, ingredient, sort_order) VALUES (%s,'Chondroitin',0),(%s,'Merged from the Desire line',1000)", (first, first))
    st = load_taxonomy.load(conn, BF_OVERVIEW, cycle_dir=load_taxonomy.Path("/nonexistent"), bl_cycle_dir=None, bf_cycle_dir=bf_cycle, only={"boneflex"})
    assert bf_codes(conn) == [("BF01", "K1", "J1", "Q1", "Shame"), ("BF02", "K1", "J1", "Q2", "Fear"), ("BF03", "K1", "J1", "Q1", "Desire"), ("BF04", "K2", "J2", "Q3", "Pride")]
    assert conn.execute("SELECT id FROM taxonomy_lines WHERE line_code='BF01' AND product_id=%s", (pid,)).fetchone()[0] == first                 # same row, same id
    assert st["products"]["boneflex"] == {"lines": 4, "clusters": 2, "ingredient_rows": 4, "recoded": 1, "inserted": 3, "not_in_tagging_catalog": 0, "lines_sharing_a_pain_point": 2}
    ing = conn.execute("""SELECT t.line_code, i.ingredient FROM line_ingredients i JOIN taxonomy_lines t ON t.id=i.line_id WHERE t.product_id=%s ORDER BY t.sort_order, i.sort_order""", (pid,)).fetchall()
    assert ing == [("BF01", "Chondroitin"), ("BF02", "Zinc"), ("BF03", "Chondroitin"), ("BF04", "Zinc")]                                    # rebuilt from the sheet, merged row gone
    snap = conn.execute("SELECT id, line_code, sort_order, story FROM taxonomy_lines ORDER BY id").fetchall()
    load_taxonomy.load(conn, BF_OVERVIEW, cycle_dir=load_taxonomy.Path("/nonexistent"), bl_cycle_dir=None, bf_cycle_dir=bf_cycle, only={"boneflex"})   # idempotent
    assert conn.execute("SELECT id, line_code, sort_order, story FROM taxonomy_lines ORDER BY id").fetchall() == snap


def test_edited_trigger_keeps_row_and_code(conn, bf_cycle):
    load_taxonomy.load(conn, BF_OVERVIEW, cycle_dir=load_taxonomy.Path("/nonexistent"), bl_cycle_dir=None, bf_cycle_dir=bf_cycle, only={"boneflex"})
    ids = dict(conn.execute("SELECT line_code, id FROM taxonomy_lines WHERE line_code LIKE 'BF%'").fetchall())
    edited = [list(r) for r in BF_OVERVIEW]
    edited[2][7] = "Frustration"                                                                  # Q2 line: Fear -> Frustration (the only line with this cluster/ICP/pain)
    st = load_taxonomy.load(conn, edited, cycle_dir=load_taxonomy.Path("/nonexistent"), bl_cycle_dir=None, bf_cycle_dir=bf_cycle, only={"boneflex"})
    assert st["products"]["boneflex"]["inserted"] == 0 and st["products"]["boneflex"]["recoded"] == 0 and st["products"]["boneflex"]["not_in_tagging_catalog"] == 0
    assert dict(conn.execute("SELECT line_code, id FROM taxonomy_lines WHERE line_code LIKE 'BF%'").fetchall()) == ids
    assert conn.execute("SELECT emotional_trigger FROM taxonomy_lines WHERE line_code='BF02'").fetchone()[0] == "Frustration"


def test_plan_codes_and_matching_helpers():
    L = lambda c, i, p, t: {"cluster": c, "icp": i, "pain": p, "trigger": t}
    catalog = {("K1", "J1", "Q1", "Shame"): "BF01", ("K1", "J1", "Q1", "Desire"): "BF03"}
    assert load_taxonomy.lookup_code(catalog, "K1", "J1", "Q1", "Desire") == "BF03"
    assert load_taxonomy.lookup_code(catalog, "K1", "J1", "Q1", "Pride") is None                  # two catalog lines share the key: no guessing
    assert load_taxonomy.lookup_code({("K", "J", "P", "Shame"): "BF09"}, "K", "J", "P", "Fear") == "BF09"      # one catalog line: trigger drift tolerated
    lines = [L("K1", "J1", "Q1", "Shame"), L("K1", "J1", "Q1", "Desire"), L("K9", "J9", "Q9", "Fear"), L("K8", "J8", "Q8", None)]
    assert load_taxonomy.plan_codes("boneflex", lines, [None, None, "BF40", None], catalog) == ["BF01", "BF03", "BF40", "BF41"]
    existing = [(1, "K1", "J1", "Q1", "Shame", "L01"), (2, "K1", "J1", "Q1", "Desire", "L02")]
    assert [m[0] for m in load_taxonomy.match_existing(lines[:2], existing)] == [1, 2]
    assert [m[0] for m in load_taxonomy.match_existing(list(reversed(lines[:2])), existing)] == [2, 1]          # exact trigger first
    assert load_taxonomy.match_existing([L("K1", "J1", "Q1", "Fear")], existing[:1])[0][0] == 1                 # edited trigger: same cluster/ICP/pain still matches


def test_tags_map_to_lines_by_trigger_and_placeholder_rows_are_ignored(conn, tmp_path, bf_cycle):
    load_taxonomy.load(conn, BF_OVERVIEW, cycle_dir=load_taxonomy.Path("/nonexistent"), bl_cycle_dir=None, bf_cycle_dir=bf_cycle, only={"boneflex"})
    sync(conn, tmp_path, [trow("a", "fam-a", "Boneflex", Type="VID"), trow("b", "fam-b", "Boneflex", Type="IMG"), trow("c", "fam-c", "Boneflex", Type="IMG")])
    pid = conn.execute("SELECT id FROM products WHERE slug='boneflex'").fetchone()[0]
    conn.execute("INSERT INTO tracker_rows (product_id, row_key, concept_id, ad_family, family_key, sheet_row, status) VALUES (%s,'#ph','','','id:',36,'3 - Briefing')", (pid,))
    mk = lambda row, cid, line, pain, trig: tag(row, cid, "fam-" + cid, line=line, cluster="K1", icp="J1", pain=pain, trigger=trig, type="VID" if cid == "a" else "IMG")
    tags = [mk(1, "a", "BF01", "Q1", "Shame"), mk(2, "b", "BF03", "Q1", "Desire"), mk(3, "c", "BF02", "Q2", "Fear")]
    st = load_tags.load(conn, "boneflex", "linemap", "linemap-v2-2026-11-05", tags)
    assert st["matched"] == 3 and st["tracker_rows"] == 3 and st["ignored_blank_concept_rows"] == 1 and st["product_tag_set_flipped"] == 1 and st["line_code_mismatch"] == 0
    got = dict(conn.execute("""SELECT t.concept_id, tl.line_code FROM ad_tags a JOIN tracker_rows t ON t.id=a.tracker_row_id JOIN taxonomy_lines tl ON tl.id=a.line_id
                               WHERE a.tag_set='linemap' AND t.product_id=%s""", (pid,)).fetchall())
    assert got == {"a": "BF01", "b": "BF03", "c": "BF02"}                                           # same cluster/ICP/pain, told apart by the trigger
    assert conn.execute("SELECT count(*) FROM ad_tags a JOIN tracker_rows t ON t.id=a.tracker_row_id WHERE t.concept_id='' AND a.tag_set='linemap'").fetchone()[0] == 0
    # a tag whose trigger no longer exists is mapped only when the cluster/ICP/pain is unambiguous
    bad = [dict(tags[0], trigger="Pride")] + tags[1:]                                                # Q1 has two lines: ambiguous -> error
    with pytest.raises(RuntimeError, match="missing from taxonomy"):
        load_tags.load(conn, "boneflex", "linemap", "v-bad", bad)
    ok = tags[:2] + [dict(tags[2], trigger="Pride")]                                                 # Q2 has one line: trigger drift tolerated
    assert load_tags.load(conn, "boneflex", "linemap", "v-drift", ok)["line_code_mismatch"] == 0


def test_soft_deleted_rows_are_not_part_of_the_match(conn, tmp_path, bf_cycle):
    if not conn.execute("SELECT 1 FROM information_schema.columns WHERE table_name='tracker_rows' AND column_name='deleted_at'").fetchone():
        pytest.skip("migration 007 (soft delete) not applied")
    load_taxonomy.load(conn, BF_OVERVIEW, cycle_dir=load_taxonomy.Path("/nonexistent"), bl_cycle_dir=None, bf_cycle_dir=bf_cycle, only={"boneflex"})
    sync(conn, tmp_path, [trow("a", "fam-a", "Boneflex", Type="VID"), trow("gone", "fam-gone", "Boneflex", Type="IMG")])
    conn.execute("UPDATE tracker_rows SET deleted_at=now() WHERE concept_id='gone'")
    st = load_tags.load(conn, "boneflex", "linemap", "v1", [tag(1, "a", "fam-a", line="BF01", cluster="K1", icp="J1", pain="Q1", trigger="Shame")])
    assert st["matched"] == 1 and st["tracker_rows"] == 1 and st["product_tag_set_flipped"] == 1


def test_pain_level_row_shows_both_lines_of_a_shared_pain_point():
    from app import queries
    lines = [{"line_code": "BF36", "cluster": "A", "icp": "I", "pain_point": "P", "emotional_trigger": "Shame", "cluster_color": "#fff", "sort_order": 1},
             {"line_code": "BF37", "cluster": "A", "icp": "I", "pain_point": "Q", "emotional_trigger": "Fear", "cluster_color": "#fff", "sort_order": 2},
             {"line_code": "BF38", "cluster": "A", "icp": "I", "pain_point": "P", "emotional_trigger": "Desire", "cluster_color": "#fff", "sort_order": 3}]
    sk = queries._skeleton(lines, "pain", "A", "I")
    assert [(s["label"], s["line_code"], s["trigger"]) for s in sk] == [("P", "BF36 / BF38", "Shame / Desire"), ("Q", "BF37", "Fear")]
    assert [s["label"] for s in queries._skeleton(lines, "icp", "A", None)] == ["I"]                  # upper levels unchanged
    children, info = queries._tax_children(lines)
    assert info[("A", "I", "P")]["line_code"] == "BF36 / BF38" and info[("A", "I", "P")]["trigger"] == "Shame / Desire" and info[("A", "I", "Q")]["line_code"] == "BF37"
    assert children[("A", "I")] == [("A", "I", "P"), ("A", "I", "Q")]


def test_tag_of_another_products_line_on_a_moved_row_is_retired(conn, tmp_path, bl_cycle, bf_cycle):
    """A tracker row that moved from Beef Liver to Boneflex keeps its old current tag (a Beef Liver line); loading Boneflex tags retires it, even when the
    Boneflex file does not cover that row."""
    load_taxonomy.load(conn, OVERVIEW + BF_OVERVIEW[1:], cycle_dir=load_taxonomy.Path("/nonexistent"), bl_cycle_dir=bl_cycle, bf_cycle_dir=bf_cycle)
    sync(conn, tmp_path, [trow("a", "fam-a", "BeefLiver", Type="VID"), trow("b", "fam-b", "Boneflex", Type="IMG"), trow("c", "fam-c", "Boneflex", Type="IMG")])
    bl_line = conn.execute("SELECT id FROM taxonomy_lines WHERE line_code='B01'").fetchone()[0]
    rid = conn.execute("SELECT id FROM tracker_rows WHERE concept_id='c'").fetchone()[0]
    conn.execute("INSERT INTO ad_tags (tracker_row_id, tag_set, version, is_current, line_id, cluster, icp, pain_point) VALUES (%s,'linemap','linemap-v2-2026-11-04',true,%s,'C1','I1','P1')", (rid, bl_line))
    tags = [tag(1, "a", "fam-a", line="BF01", cluster="K1", icp="J1", pain="Q1", trigger="Shame"), tag(2, "b", "fam-b", line="BF02", cluster="K1", icp="J1", pain="Q2", trigger="Fear")]
    tags[0]["concept_id"], tags[0]["ad_family"], tags[0]["type"] = "b", "fam-b", "IMG"
    tags = tags[:1]                                                                                  # the Boneflex file tags only b; c is not covered
    st = load_tags.load(conn, "boneflex", "linemap", "linemap-v2-2026-11-05", tags, min_coverage=0.5)
    assert st["foreign_line_tags_retired"] == 1 and st["matched"] == 1
    assert conn.execute("SELECT count(*) FROM ad_tags WHERE tracker_row_id=%s AND is_current AND tag_set='linemap'", (rid,)).fetchone()[0] == 0
