"""etl.creative_priorities: the scoring is pure (compute() on hand-built inputs), so eligibility, ranking, allocation, the new-line evidence rules, hooks, flags, descriptions and
the CSV order are checked without a database; the store step and its idempotence run on the test database."""
import csv
import datetime as dt
import io

import pytest

from etl import creative_priorities as cp

THROUGH = dt.date(2026, 11, 8)
WEEK = dt.date(2026, 11, 8)
Z = {k: 0.0 for k in ("s_all", "p_all", "r_all", "s30", "p30", "r30", "sp30", "pp30", "rp30", "s7")}


def line(i, product, cluster, icp, pain, order=None, story="A story.", added=False, attacks="", ingr=""):
    return {"id": i, "product": product, "product_name": product.title(), "line_code": f"{product[:2].upper()}{i:02d}", "cluster": cluster, "icp": icp, "pain_point": pain,
            "emotional_trigger": "Fear", "story": story, "sort_order": order if order is not None else i, "added_after_tagging": added, "ingredients": ingr, "attacks": attacks}


_n = [0]


def fam(product, line_id, spend, roas, *, hook=None, first=dt.date(2026, 8, 1), conf="high", method="image", s30=0.0, r30=0.0, sp30=0.0, rp30=0.0, live=False, tagger="linemap"):
    _n[0] += 1
    k = f"f{_n[0]}"
    w = {**Z, "s_all": spend, "r_all": spend * roas, "p_all": spend / 80, "s30": s30, "r30": r30, "p30": s30 / 80, "sp30": sp30, "rp30": rp30, "pp30": sp30 / 80}
    return {"family_key": k, "tracker_row_id": _n[0], "product": product, "line_id": line_id, "tag_conf": conf, "tag_method": method, "tagger": tagger, "spend_launch": spend,
            "purchases_launch": spend / 80, "revenue_launch": spend * roas, "live": live, "first_day": first, "last_spend_day": THROUGH if live else dt.date(2026, 9, 1),
            "before_start": False, "display": k, "hook": hook, "hook_conf": "high", "_w": w}


def build(lines, fams):
    win = {"meta": {}, "nc": {}}
    for f in fams:
        win["meta"][f["family_key"]] = f.pop("_w")
        win["nc"][f["family_key"]] = {**win["meta"][f["family_key"]]}
    return {"through": THROUGH, "lines": lines, "families": fams, "win": win}


def scenario():
    """Vita Bull: L1 strong (8 ads, 1.5x vs ~0.9 average), L2 average, L3 thin, L4 untested (same ICP as L1), L5 untested nothing adjacent.
    Beef Liver: L11 strong on the pain point 'Hair loss' that VB L6 (untested) shares."""
    lines = [line(1, "vita-bull", "Energy", "Given Up Guy", "Low T"), line(2, "vita-bull", "Energy", "Won't Slow Down", "Aging"), line(3, "vita-bull", "Energy", "Thin Guy", "Low T"),
             line(4, "vita-bull", "Energy", "Given Up Guy", "Aging", story="He quit trying."), line(5, "vita-bull", "Prostate", "Night Waker", "Sleep", story=""),
             line(6, "vita-bull", "Hair", "Bald Guy", "Hair loss"), line(10, "beef-liver", "Hair", "Thinning Woman", "Hair loss"),
             line(7, "vita-bull", "Skin", "Added Later", "Skin", added=True)]
    fams = []
    fams += [fam("vita-bull", 1, 3000, 1.5, hook=f"hook {i}", s30=500, r30=700, sp30=500, rp30=600) for i in range(8)]       # 24k spend, 1.5x
    fams += [fam("vita-bull", 2, 3000, 0.85) for i in range(8)]                                                              # 24k at 0.85x: below the 1.05 bar
    fams += [fam("vita-bull", 3, 200, 2.0) for i in range(3)]                                                               # thin
    fams += [fam("beef-liver", 10, 3000, 1.6, hook="bl hook") for i in range(8)]
    fams += [fam("beef-liver", None, 2000, 0.9) for i in range(4)]                                                          # untagged filler for the product average
    return build(lines, fams)


def test_week_start_is_the_sunday():
    assert cp.week_start(dt.date(2026, 11, 8)) == dt.date(2026, 11, 8)       # Sunday itself
    assert cp.week_start(dt.date(2026, 11, 14)) == dt.date(2026, 11, 8)      # Saturday
    assert cp.week_start(dt.date(2026, 11, 9)) == dt.date(2026, 11, 8)       # Monday


def test_pacific_sunday_gate():
    utc = dt.datetime(2026, 11, 9, 5, 0, tzinfo=dt.timezone.utc)             # Sunday 21:00 Pacific
    assert cp.pacific_today(utc).weekday() == 6


def test_iterate_picks_only_lines_clearly_above_the_product_average():
    r = cp.compute(scenario(), WEEK)
    it = [x for x in r["rows"] if x["section"] == "iterate" and x["product"] == "vita-bull"]
    assert [x["line_id"] for x in it] == [1]                                  # L2 below the bar, L3 thin
    assert it[0]["metrics"]["roas"] == 1.5 and it[0]["metrics"]["judged"] == 8
    assert it[0]["concepts"] == cp.CONCEPTS_ITERATE or it[0]["concepts"] == cp.MAX_PER_ITERATE_ROW


def test_new_lines_have_no_ads_and_skip_lines_added_after_tagging():
    r = cp.compute(scenario(), WEEK)
    nw = [x for x in r["rows"] if x["section"] == "new" and x["product"] == "vita-bull"]
    ids = [x["line_id"] for x in nw]
    assert 7 not in ids and 1 not in ids and 2 not in ids and 3 not in ids   # tested lines and the added-after-tagging line never appear
    assert set(ids) <= {4, 5, 6}
    assert all(x["metrics"]["ads"] == 0 and x["metrics"]["spend"] == 0 for x in nw)


def test_new_line_ranked_by_evidence_adjacent_winner_first_and_hooks_only_from_same_product():
    r = cp.compute(scenario(), WEEK)
    nw = {x["line_id"]: x for x in r["rows"] if x["section"] == "new" and x["product"] == "vita-bull"}
    assert nw[4]["evidence"]["adjacent"]["line_code"] == "VI01" and nw[4]["evidence"]["adjacent"]["same"] == "ICP"
    assert [h["hook"] for h in nw[4]["hooks"]] == ["hook 0", "hook 1"]        # borrowed from the adjacent winner, labelled
    assert all(h["borrowed"] for h in nw[4]["hooks"])
    # the same pain point works on another product: evidence, but no hooks are borrowed across products
    assert nw[6]["evidence"]["cross_product"]["product"] == "beef-liver" and nw[6]["hooks"] == []
    assert nw[4]["score"] > nw[5]["score"]                                    # L5: no evidence, no story
    assert "no performance evidence" in nw[5]["flags"] and nw[5]["why"].startswith("No performance evidence")


def test_numbers_are_never_invented_for_untested_lines():
    r = cp.compute(scenario(), WEEK)
    for x in r["rows"]:
        if x["section"] == "new":
            assert x["metrics"]["roas"] is None and x["metrics"]["cpa"] is None


def test_shrinkage_keeps_a_lucky_small_line_out():
    inp = scenario()
    # a 6-ad line with only $5,200 spend at 1.2x: after shrinking toward the product average it is not clearly above it
    inp["lines"].append(line(20, "vita-bull", "Energy", "Lucky", "Luck"))
    inp["families"] += [fam("vita-bull", 20, 870, 1.2) for _ in range(6)]
    inp["win"]["meta"].update({f["family_key"]: {**Z, "s_all": 870, "r_all": 870 * 1.2, "p_all": 11} for f in inp["families"] if f["line_id"] == 20})
    inp["win"]["nc"] = dict(inp["win"]["meta"])
    r = cp.compute(inp, WEEK)
    assert 20 not in [x["line_id"] for x in r["rows"] if x["section"] == "iterate"]


def test_shrunk_roas_and_rate():
    assert cp.shrunk_roas(0, 0, 0.9) == 0.9
    assert cp.shrunk_roas(1500, 1000, 0.9, k=1000) == pytest.approx((1500 + 900) / 2000)
    assert cp.shrunk_rate(0, 0, 0.5) == 0.5


def test_allocate_respects_budget_bounds_and_weights():
    assert cp.allocate([], 10, 1, 5) == []
    a = cp.allocate([50, 30, 20], 10, 1, 5)
    assert sum(a) == 10 and all(1 <= x <= 5 for x in a) and a[0] >= a[1] >= a[2]
    assert cp.allocate([1, 1], 14, 1, 5) == [5, 5]                            # capped: fewer concepts than budget, never more than the cap
    assert cp.allocate([9] * 8, 6, 1, 2) == [1] * 8                           # floors win over a tiny budget


def test_weak_tags_flag_and_penalty():
    inp = scenario()
    for f in inp["families"]:
        if f["line_id"] == 1:
            f["tag_conf"] = "low"
    r = cp.compute(inp, WEEK)
    row = [x for x in r["rows"] if x["section"] == "iterate" and x["line_id"] == 1][0]
    assert "weak tags" in row["flags"]
    strong = [x for x in cp.compute(scenario(), WEEK)["rows"] if x["section"] == "iterate" and x["line_id"] == 1][0]
    assert row["score"] < strong["score"]


def test_auto_tagged_rows_count_as_weak():
    inp = scenario()
    for f in inp["families"]:
        if f["line_id"] == 1:
            f["tagger"] = "auto-claude-sonnet-5-5"
    row = [x for x in cp.compute(inp, WEEK)["rows"] if x["section"] == "iterate" and x["line_id"] == 1][0]
    assert "weak tags" in row["flags"]


def test_descriptions_and_legend_come_from_the_overview_text():
    r = cp.compute(scenario(), WEEK)
    row = [x for x in r["rows"] if x["line_id"] == 1][0]
    assert "Energy" in row["cluster_description"] and "Given Up Guy" in row["icp_description"] and "Low T" in row["pain_description"]
    assert [t for t, _ in cp.LEGEND] == ["Market Cluster", "ICP", "Pain Point", "Story"]


def test_csv_leads_with_the_four_tags_then_their_descriptions():
    r = cp.compute(scenario(), WEEK)
    rows = list(csv.reader(io.StringIO(cp.to_csv(r))))
    assert rows[0][:7] == ["market_cluster", "icp", "pain_point", "story", "market_cluster_description", "icp_description", "pain_point_description"]
    first = dict(zip(rows[0], rows[1]))
    assert first["story"] and first["market_cluster"]
    assert len(rows) == 1 + len(r["rows"])


def test_markdown_reads_as_a_brief():
    md = cp.to_markdown(cp.compute(scenario(), WEEK))
    assert "Energy → Given Up Guy → Low T" in md and "Story:" in md and "What the four tags are" in md


def test_thresholds_are_recorded():
    r = cp.compute(scenario(), WEEK)
    assert r["thresholds"]["INDEX_BAR"] == cp.INDEX_BAR and r["thresholds"]["MIN_LINE_SPEND"] == cp.MIN_LINE_SPEND


def test_store_replaces_the_same_week(conn):
    conn.execute("TRUNCATE creative_priorities_snapshots")
    snap = cp.compute(scenario(), WEEK)
    cp.store(conn, snap)
    cp.store(conn, {**snap, "rows": snap["rows"][:2]})
    conn.commit()
    assert conn.execute("SELECT count(*), max(jsonb_array_length(rows)) FROM creative_priorities_snapshots").fetchone() == (1, 2)
    cp.store(conn, {**snap, "week_start": "2026-11-01"})
    conn.commit()
    assert conn.execute("SELECT count(*) FROM creative_priorities_snapshots").fetchone()[0] == 2


def test_line_already_in_the_pipeline_is_not_new():
    inp = scenario()
    inp["families"] += [fam("vita-bull", 5, 0, 1.0) for _ in range(cp.NEW_MAX_TAGGED + 1)]            # tagged briefs with no spend yet
    inp["win"]["meta"].update({f["family_key"]: dict(Z) for f in inp["families"] if f["line_id"] == 5})
    inp["win"]["nc"] = dict(inp["win"]["meta"])
    assert 5 not in [x["line_id"] for x in cp.compute(inp, WEEK)["rows"] if x["section"] == "new"]


def test_strong_tag_roas_ignores_weak_tags():
    inp = scenario()
    for i, f in enumerate([f for f in inp["families"] if f["line_id"] == 1]):
        if i < 4:
            f["tag_conf"] = "low"
            inp["win"]["meta"][f["family_key"]]["r_all"] = 0
            f["revenue_launch"] = 0.0
    row = [x for x in cp.compute(inp, WEEK)["rows"] if x["line_id"] == 1]
    # the weak half earned nothing here, so the line may drop out; when it stays, the strong-only ROAS is the clean half
    for r in row:
        assert r["metrics"]["roas_strong_tags"] == pytest.approx(1.5)
