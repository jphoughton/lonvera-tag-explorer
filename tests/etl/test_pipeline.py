import datetime as dt
import json

import pytest

from etl import backfill_sheet, build_metrics, db, load_tags, load_taxonomy, sheets_io, tracker_sync
from etl.tracker_sync import family_key
from conftest import trow, write_snapshot

D = dt.date


def test_family_key_and_blank():
    assert family_key("Bull-1 ", "Bull-1") == "bull-1"
    assert family_key("", "Concept-X ") == "id:concept-x"
    assert family_key("   ", "") == "id:"


def test_parse_date_formats():
    p = sheets_io.parse_date
    assert p("5/11/2026") == D(2026, 5, 11)
    assert p("10/16/26") == D(2026, 10, 16)
    assert p("2026-05-11") == D(2026, 5, 11)
    assert p("46329") == D(2026, 11, 3)
    assert p("") is None and p("garbage") is None


def make(tmp_path, trs, daily, lifetime=(), windows=()):
    return write_snapshot(tmp_path, trs, daily, lifetime, windows)


def sync(conn, snap):
    return tracker_sync.sync(conn, sheets_io.read_tab("creative_tracker", snap))


def test_row_keys_ordinals_and_leading_zero(conn, tmp_path):
    trs = [trow("0510", "0510", "VitaBull"), trow("510", "510", "VitaBull"),
           trow("Bull-1", "Bull-1", "VitaBull"), trow("Bull-1", "Bull-1", "VitaBull"),     # same concept + family twice -> #1, #2
           trow("Bull-1", "Other", "VitaBull"), trow("Solo", "", "BoneFlex"), trow("X", "x", "")]
    snap = make(tmp_path, trs, [("2026-05-06", "x", 1, 0, 0, 0)])
    sync(conn, snap)
    keys = [r[0] for r in conn.execute("SELECT row_key FROM tracker_rows ORDER BY sheet_row").fetchall()]
    assert keys == ["0510#1", "510#1", "Bull-1#1", "Bull-1#2", "Bull-1#1~Other", "Solo#1", "X#1"]   # same concept under another family is disambiguated
    assert len(set(keys)) == 7
    r = dict(conn.execute("SELECT concept_id, family_key FROM tracker_rows WHERE concept_id IN ('0510','510','Solo')").fetchall())
    assert r["0510"] == "0510" and r["510"] == "510" and r["Solo"] == "id:solo"
    assert conn.execute("SELECT count(*) FROM tracker_rows WHERE concept_id='0510'").fetchone()[0] == 1
    # product mapping, BoneFlex spelling, blank -> unassigned
    prods = dict(conn.execute("SELECT t.concept_id, p.slug FROM tracker_rows t JOIN products p ON p.id=t.product_id WHERE t.concept_id IN ('Solo','X')").fetchall())
    assert prods == {"Solo": "boneflex", "X": "unassigned"}


def test_tracker_sync_idempotent_and_tags(conn, tmp_path):
    trs = [trow("A", "fam-a", "VitaBull", pain="Pain 1", icp="VB1", cluster="C1"), trow("B", "fam-b", "BeefLiver")]
    snap = make(tmp_path, trs, [("2026-05-06", "x", 1, 0, 0, 0)])
    s1 = sync(conn, snap)
    c1 = conn.execute("SELECT count(*), count(*) FILTER (WHERE is_current) FROM ad_tags WHERE tag_set='tracker'").fetchone()
    s2 = sync(conn, snap)
    assert s1["rows_after"] == s2["rows_after"] == 2 and s2["inserted"] == 0
    assert conn.execute("SELECT count(*), count(*) FILTER (WHERE is_current) FROM ad_tags WHERE tag_set='tracker'").fetchone() == c1 == (2, 2)
    t = conn.execute("SELECT cluster, icp, pain_point FROM ad_tags a JOIN tracker_rows r ON r.id=a.tracker_row_id WHERE r.concept_id='B'").fetchone()
    assert t == ("(none)", "(none)", "(none)")
    t = conn.execute("SELECT cluster, icp, pain_point FROM ad_tags a JOIN tracker_rows r ON r.id=a.tracker_row_id WHERE r.concept_id='A'").fetchone()
    assert t == ("C1", "VB1", "Pain 1")


def metrics_for(conn, concept):
    return conn.execute("""SELECT m.is_first_row, m.spend_launch, m.purchases_launch, m.revenue_launch, m.spend_7d, m.live FROM ad_metrics m
                           JOIN tracker_rows t ON t.id=m.tracker_row_id WHERE t.concept_id=%s ORDER BY t.sheet_row""", (concept,)).fetchall()


def test_launch_rule_branches_first_row_and_blank_family(conn, tmp_path):
    # D0 = 2026-05-06 (first day in daily), cutoff 2026-09-17, data_through = 2026-11-02
    daily = [("2026-05-06", "seed", 1, 0, 0, 0)]
    for fam_, days in {"new": ["2026-10-01", "2026-10-30", "2026-11-02"], "old": ["2026-09-10", "2026-09-17", "2026-09-18", "2026-11-01", "2026-11-02"]}.items():
        daily += [(d, fam_, 10.0, 1, 20.0, 5) for d in days]
    trs = [trow("N1", "new", "VitaBull", launch="10/30/2026"),                 # launch >= D0: daily from launch (10-30, 11-02)
           trow("O1", "old", "VitaBull", launch="1/15/2026"),                  # launch < D0: lifetime + daily after cutoff (09-18, 11-01, 11-02)
           trow("O2", "old", "VitaBull", launch="1/16/2026"),                  # second row of the family: all zero
           trow("NL", "nolaunch", "VitaBull"),                                  # NULL launch -> launch metrics 0
           trow("BLANK-CONCEPT", "", "VitaBull", launch="5/7/2026")]            # blank family -> 0
    snap = make(tmp_path, trs, daily, lifetime=[("old", "old", "1", 500.0, 50, 900.0)], windows=[("new", "new"), ("old", "old"), ("nolaunch", "nolaunch")])
    sync(conn, snap)
    backfill_sheet.run(conn, snap, "sheet", delta=False)
    n = metrics_for(conn, "N1")[0]
    assert (n[0], float(n[1]), float(n[2]), float(n[3])) == (True, 20.0, 2.0, 40.0)
    assert n[4:] == (pytest.approx(20.0), True) or (float(n[4]), n[5]) == (20.0, True)     # 7d window 10-27..11-02 -> 10-30 + 11-02; live (11-02)
    o1, = metrics_for(conn, "O1")
    assert (o1[0], float(o1[1]), float(o1[2]), float(o1[3])) == (True, 530.0, 53.0, 960.0)   # 500 + 3*10 ; 50 + 3 ; 900 + 3*20
    assert metrics_for(conn, "O2") == [(False, 0, 0, 0, 0, False)] or float(metrics_for(conn, "O2")[0][1]) == 0
    o2 = metrics_for(conn, "O2")[0]
    assert o2[0] is False and float(o2[1]) == 0 and not o2[5]
    nl = metrics_for(conn, "NL")[0]
    assert nl[0] is True and float(nl[1]) == 0
    bl = metrics_for(conn, "BLANK-CONCEPT")[0]
    assert float(bl[1]) == 0 and not bl[5]


def test_first_row_follows_sheet_row_not_insert_order(conn, tmp_path):
    daily = [("2026-05-06", "f", 5.0, 0, 0, 0), ("2026-11-02", "f", 5.0, 1, 7.0, 1)]
    trs = [trow("R1", "f", "VitaBull", launch="5/6/2026"), trow("R2", "f", "VitaBull", launch="5/6/2026")]
    snap = make(tmp_path, trs, daily, windows=[("f", "f")])
    sync(conn, snap)
    # simulate a re-sort of the sheet: swap sheet_rows
    conn.execute("UPDATE tracker_rows SET sheet_row = CASE concept_id WHEN 'R1' THEN 9 ELSE 3 END")
    conn.commit()
    backfill_sheet.run(conn, snap, "sheet", delta=False)
    assert metrics_for(conn, "R2")[0][1] == 10 and metrics_for(conn, "R1")[0][1] == 0


def test_backfill_idempotent_and_api_days_protected(conn, tmp_path):
    daily = [("2026-05-06", "a", 1.5, 0, 0, 0), ("2026-05-07", "a", 2.5, 1, 3.0, 2), ("2026-05-07", "b", 4.0, 0, 0, 0)]
    snap = make(tmp_path, [trow("A", "a", "VitaBull", launch="5/6/2026")], daily, lifetime=[("a", "a", "1", 10, 1, 12)], windows=[("a", "a")])
    sync(conn, snap)

    def state():
        return (conn.execute("SELECT count(*), sum(spend), sum(purchases), sum(conv_value) FROM ad_daily").fetchone(),
                conn.execute("SELECT count(*), sum(spend) FROM ad_lifetime").fetchone(),
                conn.execute("SELECT count(*) FROM meta_family_map").fetchone(),
                conn.execute("SELECT count(*), sum(spend_launch) FROM ad_metrics").fetchone(),
                conn.execute("SELECT count(*), sum(spend) FROM family_daily").fetchone())
    backfill_sheet.run(conn, snap, "sheet", delta=False); conn.commit()
    s1 = state()
    backfill_sheet.run(conn, snap, "sheet", delta=True); conn.commit()
    assert state() == s1
    # a day already covered by API rows is NOT overwritten by the sheet backfill
    conn.execute("DELETE FROM ad_daily WHERE day='2026-05-07'")
    conn.execute("INSERT INTO ad_daily (ad_id, day, meta_family_key, meta_family, campaign_id, spend, source) VALUES ('999','2026-05-07','a','a','c1',100,'meta_api')")
    conn.commit()
    backfill_sheet.run(conn, snap, "sheet", delta=False); conn.commit()
    assert float(conn.execute("SELECT sum(spend) FROM ad_daily WHERE day='2026-05-07'").fetchone()[0]) == 100.0


def test_stage_split_and_unmatched_product(conn, tmp_path):
    daily = [("2026-05-06", "BoneFlex Thing", 3.0, 0, 0, 0), ("2026-11-02", "Beef Liver unknown", 9.0, 1, 5.0, 1)]
    snap = make(tmp_path, [trow("T", "tfam", "Boneflex", launch="5/6/2026")], daily, windows=[])
    sync(conn, snap)
    backfill_sheet.run(conn, snap, "sheet", delta=False)
    conn.execute("INSERT INTO meta_family_map (meta_family_key, meta_family, tracker_family_key, rule) VALUES ('mx','mx','tfam','exact')")
    conn.execute("INSERT INTO meta_campaigns (campaign_id, name, stage) VALUES ('c1','Boneflex TEST x','scaling'),('c2','Boneflex Scale','testing')")
    conn.execute("INSERT INTO ad_daily (ad_id, day, meta_family_key, meta_family, campaign_id, spend, source) VALUES ('1','2026-11-02','mx','mx','c1',4,'meta_api'),('2','2026-11-02','mx','mx','c2',6,'meta_api')")
    build_metrics.rebuild(conn)
    st = dict(((r[0], r[1]), float(r[2])) for r in conn.execute("SELECT day::text, stage, spend FROM family_daily").fetchall())
    assert st[("2026-11-02", "testing")] == 4.0 and st[("2026-11-02", "scaling")] == 6.0       # stage recomputed from the name, not the stored value
    um = dict(conn.execute("SELECT m.meta_family, p.slug FROM unmatched_meta m JOIN products p ON p.id=m.product_id").fetchall())
    assert um["BoneFlex Thing"] == "boneflex" and um["Beef Liver unknown"] == "beef-liver"


def test_taxonomy_fill_down_and_idempotent(conn):
    rows = [["Market Cluster ", "Market Cluster", "ICP", "Pain Point", "Ingredient", "What It Attacks", "What It Supports", "Emotional Trigger", "Stories"],
            ["Vita Bull", "C1", "I1", "P1", "Zinc", "a1", "s1", "Fear", "story one"],
            ["", "", "", "", "Choline", "a2", "s2"],
            ["", "", "", "P2", "Zinc", "a3", "s3", "Shame", "story two"],
            ["", "", "I2", "P1", "Zinc", "a4", "s4", "Pride", "story three"],
            ["", "C2", "I3", "P3", "Copper", "a5", "s5", "Relief", "story four"],
            ["Boneflex", "K1", "J1", "Q1", "Collagen", "x", "y", "Fear", "s"]]
    s1 = load_taxonomy.load(conn, rows, cycle_dir=load_taxonomy.Path("/nonexistent"))
    n1 = conn.execute("SELECT count(*) FROM taxonomy_lines").fetchone()[0], conn.execute("SELECT count(*) FROM line_ingredients").fetchone()[0]
    assert n1 == (5, 6)
    assert s1["products"]["vita-bull"]["lines"] == 4 and s1["products"]["boneflex"]["lines"] == 1
    got = conn.execute("""SELECT line_code, cluster, icp, pain_point, emotional_trigger FROM taxonomy_lines t JOIN products p ON p.id=t.product_id
                          WHERE p.slug='vita-bull' ORDER BY t.sort_order""").fetchall()
    assert got == [("L01", "C1", "I1", "P1", "Fear"), ("L02", "C1", "I1", "P2", "Shame"), ("L03", "C1", "I2", "P1", "Pride"), ("L04", "C2", "I3", "P3", "Relief")]
    load_taxonomy.load(conn, rows, cycle_dir=load_taxonomy.Path("/nonexistent"))
    assert (conn.execute("SELECT count(*) FROM taxonomy_lines").fetchone()[0], conn.execute("SELECT count(*) FROM line_ingredients").fetchone()[0]) == n1


def test_load_tags_ordinal_match_and_versions(conn, tmp_path):
    rows = [["Market Cluster ", "Market Cluster", "ICP", "Pain Point", "Ingredient", "What It Attacks", "What It Supports", "Emotional Trigger", "Stories"],
            ["Vita Bull", "C1", "I1", "P1", "Zinc", "a1", "s1", "Fear", "story"], ["", "", "", "P2", "Zinc", "a3", "s3", "Shame", "story2"]]
    load_taxonomy.load(conn, rows, cycle_dir=load_taxonomy.Path("/nonexistent"))
    trs = [trow("A", "fam", "VitaBull"), trow("A", "fam", "VitaBull"), trow("B", "fam2", "VitaBull")]
    sync(conn, make(tmp_path, trs, [("2026-05-06", "x", 1, 0, 0, 0)]))
    # the tag file's `row` numbers are stale (rows 40, 41, 7): matching must use (concept, family) + ordinal by `row` order
    tags = [{"row": 41, "concept_id": "A", "ad_family": "fam", "line": "L02", "cluster": "C1", "icp": "I1", "pain": "P2", "trigger": "Shame", "confidence": "low", "method": "text only"},
            {"row": 40, "concept_id": "A", "ad_family": "fam", "line": "L01", "cluster": "C1", "icp": "I1", "pain": "P1", "trigger": "Fear", "confidence": "high", "method": "image", "text_line": "L02"},
            {"row": 7, "concept_id": "B", "ad_family": "fam2", "line": "L01", "cluster": "C1", "icp": "I1", "pain": "P1", "trigger": "Fear", "confidence": "medium", "method": "image"}]
    st = load_tags.load(conn, "vita-bull", "linemap", "linemap-v1", tags)
    assert st["matched"] == 3
    r = {k: v for k, v in conn.execute("""SELECT t.row_key, a.pain_point FROM ad_tags a JOIN tracker_rows t ON t.id=a.tracker_row_id WHERE a.tag_set='linemap'""").fetchall()}
    assert r == {"A#1": "P1", "A#2": "P2", "B#1": "P1"}                       # ordinal follows the tag file's `row` order
    assert conn.execute("SELECT copy_only_line FROM ad_tags WHERE pain_point='P1' AND confidence='high'").fetchone()[0] == "L02"
    load_tags.load(conn, "vita-bull", "linemap", "linemap-v1", tags)         # idempotent
    assert conn.execute("SELECT count(*) FROM ad_tags WHERE tag_set='linemap'").fetchone()[0] == 3
    load_tags.load(conn, "vita-bull", "linemap", "linemap-v2", tags)         # new version flips the old one to not-current
    assert conn.execute("SELECT version, is_current, count(*) FROM ad_tags WHERE tag_set='linemap' GROUP BY 1,2 ORDER BY 1").fetchall() == [("linemap-v1", False, 3), ("linemap-v2", True, 3)]
    with pytest.raises(RuntimeError):                                          # mismatched multiset must fail loudly
        load_tags.load(conn, "vita-bull", "linemap", "linemap-v3", tags[:2])
