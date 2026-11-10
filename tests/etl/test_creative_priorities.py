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
    assert rows[0][:9] == ["market_cluster", "icp", "pain_point", "story", "product", "section", "market_cluster_description", "icp_description", "pain_point_description"]
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


# ---------------------------------------------------------------------------------------------------------------- statuses, revive / stop sections, competitors
def status_scenario():
    """Vita Bull lines: 1 working (strong then and now), 2 fading (strong then, weak now), 3 not working (weak throughout), 4 rising (weak then, strong now), 5 dormant retired winner,
    6 untested. All ads of a line carry the line's windows."""
    lines = [line(i, "vita-bull", "Cl", f"Icp{i}", f"Pain{i}") for i in range(1, 7)]
    fams = []

    def add(lid, life, then, now, s30=1500.0, st=0.0, n=8, new30=0):
        for k in range(n):
            f = fam("vita-bull", lid, 3000, life, s30=s30 / n, r30=s30 / n * now, first=THROUGH - dt.timedelta(days=100))
            f["_w"].update({"s60": 1500.0 / n, "r60": 1500.0 / n * then, "p60": 1500.0 / n / 80})
            fams.append(f)
    add(1, 1.3, 1.3, 1.3)
    add(2, 1.3, 1.3, 0.55)
    add(3, 0.6, 0.6, 0.6)
    add(4, 0.95, 0.7, 1.3)
    add(5, 1.4, 1.4, 0.0, s30=0.0)
    inp = build(lines, fams)
    return inp


def statuses(inp):
    r = cp.compute(inp, WEEK, keep_lines=True)
    return {l["line_code"]: l["status"] for l in r["all_lines"]}, r


def test_statuses_from_rates():
    st, r = statuses(status_scenario())
    assert st["VI01"] == cp.ST_WORKING and st["VI02"] == cp.ST_FADING and st["VI03"] == cp.ST_NOT and st["VI04"] == cp.ST_RISING and st["VI05"] == cp.ST_FADING
    assert st["VI06"] == cp.ST_UNTESTED
    assert sum(r["stats"]["status_counts_total"].values()) == 6


def test_sections_follow_status_and_revive_says_when_and_how_much():
    st, r = statuses(status_scenario())
    by = {}
    for x in r["rows"]:
        by.setdefault(x["section"], []).append(x["line_code"])
    # no weekly history in this scenario: only the lifetime-strong lines (classify FADING) reach the faded section, flagged as having no sustained weekly peak
    assert set(by["iterate"]) == {"VI01", "VI04"} and set(by["faded"]) == {"VI02", "VI05"} and by["stop"] == ["VI03"]
    fd = [x for x in r["rows"] if x["section"] == "faded" and x["line_code"] == "VI02"][0]
    assert fd["status"] == "FADED" and "no sustained weekly peak" in fd["flags"] and "Then (days 31-90)" in fd["then_now"] and "consistent with" in fd["diagnosis"].lower()
    dormant = [x for x in r["rows"] if x["section"] == "faded" and x["line_code"] == "VI05"][0]
    assert dormant["status"] == "STOPPED" and "spend moved away" in dormant["diagnosis"]
    stop = [x for x in r["rows"] if x["section"] == "stop"][0]
    assert stop["concepts"] == 0 and sum(x["concepts"] for x in r["rows"] if x["section"] == "faded") <= cp.CONCEPTS_FADED


def test_thin_lines_are_too_early_not_judged():
    inp = status_scenario()
    inp["lines"].append(line(9, "vita-bull", "Cl", "Thin", "Thin"))
    inp["families"] += [fam("vita-bull", 9, 400, 3.0) for _ in range(3)]
    st, _ = statuses(inp)
    assert st["VI09"] == cp.ST_EARLY


def comp_rows(**kw):
    base = {"brand": "Acme", "days": 90, "variants": 2, "hook": "A competitor hook", "product": "vita-bull", "line_code": "", "cluster": "", "icp": "", "pain_point": "", "new": False, "confidence": "high", "url": ""}
    return {**base, **kw}


def test_no_competitor_file_means_no_competitor_section_or_signal():
    r = cp.compute(scenario(), WEEK)
    assert not [x for x in r["rows"] if x["section"] == "competitor"] and all(x["competitor"] is None for x in r["rows"]) and r["competitors_as_of"] is None


def test_competitor_signal_lifts_an_untested_line_and_unmatched_angles_get_their_own_section():
    inp = scenario()
    inp["competitors"] = {"as_of": "2026-11-08", "source": "t", "loaded_at": None, "rows": [
        comp_rows(brand="A", line_code="VI05"), comp_rows(brand="B", days=120, icp="Night Waker", pain_point="Sleep"), comp_rows(brand="C", days=75, line_code="VI05"),
        comp_rows(brand="D", cluster="Brand New Market", icp="New Guy", pain_point="New Pain", new=True), comp_rows(brand="E", cluster="Brand New Market", icp="New Guy", pain_point="New Pain", days=70, new=True)]}
    r = cp.compute(inp, WEEK)
    base = {x["line_code"]: x for x in cp.compute(scenario(), WEEK)["rows"] if x["section"] == "new"}
    new = {x["line_code"]: x for x in r["rows"] if x["section"] == "new"}
    l5 = new["VI05"]
    assert l5["competitor"]["competitors"] == 3 and l5["competitor"]["long_running"] == 3 and "running 60+ days" in l5["competitor"]["text"]
    assert l5["score"] > base["VI05"]["score"] and "not proven performance" in l5["why"]
    comp = [x for x in r["rows"] if x["section"] == "competitor"]
    assert len(comp) == 1 and comp[0]["cluster"] == "Brand New Market" and comp[0]["competitor"]["competitors"] == 2 and comp[0]["line_code"] == "not on sheet"
    assert r["competitors_as_of"] == "2026-11-08"


def test_csv_has_status_and_competitor_columns():
    inp = scenario()
    inp["competitors"] = {"as_of": "2026-11-08", "source": "t", "loaded_at": None, "rows": [comp_rows(line_code="VI05")]}
    rows = list(csv.reader(io.StringIO(cp.to_csv(cp.compute(inp, WEEK)))))
    h = rows[0]
    assert h[9] == "status" and "what_others_are_running" in h and "diagnosis" in h
    assert any(dict(zip(h, r))["what_others_are_running"] for r in rows[1:])


# ---------------------------------------------------------------------------------------------------------------- the competitor_concepts_fixture view contract (read-only input)
CONTRACT_DDL = """CREATE TABLE competitor_concepts_fixture (week_start date, brand text, concept_key text, first_ad_start_date date, days_running int, variant_count int, format text, hook text,
  primary_text_excerpt text, mapped_product text, line_code text, cluster text, icp text, pain_point text, confidence text, is_new_angle boolean, new_angle_label text, as_of date, is_stale boolean)"""


@pytest.fixture()
def fixture_view(conn, monkeypatch):
    monkeypatch.setattr(cp, "COMPETITOR_VIEW", "competitor_concepts_fixture")     # a private name: never collides with the real view the competitor job creates
    conn.execute("DROP TABLE IF EXISTS competitor_concepts_fixture")
    conn.commit()
    yield conn
    conn.rollback()
    conn.execute("DROP TABLE IF EXISTS competitor_concepts_fixture")
    conn.commit()


def test_loader_hides_everything_when_the_view_is_absent_or_empty(fixture_view):
    c = fixture_view
    assert cp.load_competitors(c) is None                                  # absent
    c.execute(CONTRACT_DDL)
    c.commit()
    assert cp.load_competitors(c) is None                                  # empty


def test_loader_ignores_a_view_missing_a_contract_column(fixture_view):
    c = fixture_view
    c.execute("CREATE TABLE competitor_concepts_fixture (week_start date, brand text)")
    c.execute("INSERT INTO competitor_concepts_fixture VALUES ('2026-11-08', 'A')")
    c.commit()
    assert cp.load_competitors(c) is None


def test_loader_reads_the_latest_week_and_flags_stale_brands(fixture_view):
    c = fixture_view
    c.execute(CONTRACT_DDL)
    ins = "INSERT INTO competitor_concepts_fixture VALUES (%s,%s,%s,%s,%s,%s,'video',%s,'x',%s,%s,%s,%s,%s,'high',%s,%s,%s,%s)"
    c.execute(ins, ("2026-11-01", "Old", "k0", "2026-08-01", 100, 1, "old hook", "vita-bull", "L01", "Cl", "Icp", "Pain", False, None, "2026-11-01", False))
    c.execute(ins, ("2026-11-08", "Acme", "k1", "2026-08-01", 99, 3, "A hook", "vita-bull", "", "Cl", "Icp", "Pain", True, "New thing", "2026-11-08", False))
    c.execute(ins, ("2026-11-08", "Gone", "k2", "2026-09-01", 60, 1, "G hook", "beef-liver", "B01", "Cl2", "Icp2", "Pain2", False, None, "2026-11-08", True))
    c.commit()
    d = cp.load_competitors(c)
    assert d["as_of"] == "2026-11-08" and {r["brand"] for r in d["rows"]} == {"Acme", "Gone"} and d["stale_brands"] == ["Gone"]
    acme = [r for r in d["rows"] if r["brand"] == "Acme"][0]
    assert acme["days"] == 99 and acme["variants"] == 3 and acme["new"] is True and acme["product"] == "vita-bull" and acme["new_angle_label"] == "New thing"
    # the loaded rows drive the signal end to end
    inp = scenario()
    inp["competitors"] = d
    r = cp.compute(inp, WEEK)
    assert r["stats"]["competitors"]["stale_brands"] == ["Gone"] and r["competitors_as_of"] == "2026-11-08"


# ---------------------------------------------------------------------------------------------------------------- proposed new market clusters
def test_every_draft_proposal_is_well_formed():
    import json
    from etl import auto_tag as at
    props = cp.load_proposals()
    assert len(props) >= 10
    codes = {c["line"] for s in cp.PRODUCTS for c in at.load_catalog(s)}
    for p in props:
        assert p["product"] in cp.PRODUCTS and len(p["story"].split()) <= 15 and p["story"].endswith(".")
        assert p["flag"].startswith(("NEW CLUSTER", "NEW LINE")) and p["match_terms"] and p["cluster_description"] and p["icp_description"] and p["pain_description"]
        assert set(p["adjacent_lines"]) <= codes, p["adjacent_lines"]


def proposal_scenario(tmp_path, monkeypatch):
    import json
    f = tmp_path / "props.json"
    f.write_text(json.dumps([
        {"product": "vita-bull", "flag": "NEW CLUSTER", "cluster": "Chest Fat", "icp": "Chest Guy", "pain_point": "Belly", "story": "He is lean except his chest.", "cluster_description": "c", "icp_description": "i",
         "pain_description": "p", "match_terms": ["chest fat"], "adjacent_lines": ["VI01"], "source": "src A"},
        {"product": "vita-bull", "flag": "NEW LINE inside Energy", "cluster": "Energy", "icp": "Night Owl", "pain_point": "Sleep", "story": "He sleeps badly.", "cluster_description": "c", "icp_description": "i",
         "pain_description": "p", "match_terms": ["night owl"], "adjacent_lines": ["VI02"], "source": "src B"},
        {"product": "vita-bull", "flag": "NEW CLUSTER", "cluster": "X", "icp": "Given Up Guy", "pain_point": "Y", "story": "Already on the sheet.", "cluster_description": "c", "icp_description": "i",
         "pain_description": "p", "match_terms": ["zzz"], "adjacent_lines": [], "source": "dup"}]))
    monkeypatch.setattr(cp, "PROPOSALS_PATH", str(f))


def test_proposals_are_ranked_by_evidence_and_never_carry_performance_numbers(tmp_path, monkeypatch):
    proposal_scenario(tmp_path, monkeypatch)
    inp = scenario()
    inp["competitors"] = {"as_of": "2026-11-08", "source": "t", "loaded_at": None, "rows": [
        comp_rows(brand="A", cluster="Chest Fat", icp="x", pain_point="y", days=90), comp_rows(brand="B", cluster="Chest Fat", icp="x", pain_point="y", days=70)]}
    r = cp.compute(inp, WEEK)
    pr = [x for x in r["rows"] if x["section"] == "proposal"]
    assert [x["icp"] for x in pr] == ["Chest Guy", "Night Owl"]                   # the draft whose ICP exists on the sheet dropped out; competitor + adjacent evidence ranks first
    first = pr[0]
    assert first["line_code"] == "DRAFT" and first["status"].startswith("PROPOSED") and "NEW CLUSTER" in first["flags"] and "DRAFT for approval" in first["flags"]
    assert first["competitor"]["competitors"] == 2 and "not proven performance" in first["why"] and first["story"] == "He is lean except his chest."
    assert first["metrics"]["spend"] == 0 and "roas" not in first["metrics"] and first["concepts"] == 1
    assert "Competitors run it" not in pr[1]["why"]
    # the competitor section does not repeat an angle already drafted
    inp["competitors"]["rows"].append(comp_rows(brand="C", cluster="Other Angle", icp="z", pain_point="w", new=True))
    r2 = cp.compute(inp, WEEK)
    assert [x["cluster"] for x in r2["rows"] if x["section"] == "competitor"] == ["Other Angle"]


def test_proposals_without_a_competitor_file_still_show_adjacent_evidence_only(tmp_path, monkeypatch):
    proposal_scenario(tmp_path, monkeypatch)
    pr = [x for x in cp.compute(scenario(), WEEK)["rows"] if x["section"] == "proposal"]
    assert pr and all(x["competitor"] is None for x in pr) and "Adjacent results of ours: VI01" in pr[0]["why"]


def test_csv_and_markdown_carry_proposals(tmp_path, monkeypatch):
    proposal_scenario(tmp_path, monkeypatch)
    r = cp.compute(scenario(), WEEK)
    rows = list(csv.reader(io.StringIO(cp.to_csv(r))))
    h = rows[0]
    prop = [dict(zip(h, x)) for x in rows[1:] if dict(zip(h, x))["section"] == "proposal"]
    assert prop and prop[0]["status"].startswith("PROPOSED") and prop[0]["story"] and "NEW CLUSTER" in prop[0]["flags"]
    assert "Proposed new market clusters" in cp.to_markdown(r)


# ---------------------------------------------------------------------------------------------------------------- was working, now isn't (weekly history)
MON = dt.date(2026, 9, 14)


def weeks(n):
    return [MON + dt.timedelta(days=7 * i) for i in range(n)]


def wk_series(pattern):
    """[(spend, roas)] per week -> {monday: (spend, revenue, purchases)}"""
    return {w: (sp, sp * ro, sp / 80) for w, (sp, ro) in zip(weeks(len(pattern)), pattern)}


def test_best_stretch_finds_the_sustained_run_and_its_dates():
    prod = wk_series([(1000, 1.0)] * 8)
    line = wk_series([(500, 1.5)] * 4 + [(500, 0.5)] * 4)
    s = cp.best_stretch(line, prod)
    assert s["start"] == "2026-09-14" and s["end"] == "2026-10-11" and s["weeks"] == 4
    assert s["roas"] == 1.5 and s["index"] == 1.5 and s["spend"] == 2000 and s["cpa"] == 80.0 and s["excess"] == 1000


def test_best_stretch_needs_three_consecutive_weeks_enough_spend_and_a_real_edge():
    prod = wk_series([(1000, 1.0)] * 8)
    assert cp.best_stretch(wk_series([(500, 1.5), (500, 1.5), (500, 0.5), (500, 1.5), (500, 1.5), (500, 0.5)]), prod) is None        # runs of 2
    assert cp.best_stretch(wk_series([(100, 1.5)] * 6), prod) is None                                                              # weeks under the weekly minimum
    assert cp.best_stretch(wk_series([(300, 1.5)] * 3), prod) is None                                                              # $900 total < $2,000
    assert cp.best_stretch(wk_series([(500, 1.05)] * 6), prod) is None                                                             # above average but under the 1.10 edge
    line = wk_series([(700, 1.5)] * 3 + [(0, 0)] + [(700, 1.5)] * 3)
    assert cp.best_stretch(line, prod)["weeks"] == 3                                                                               # a gap week breaks the run


def test_faded_kind_stopped_collapsed_faded_or_none():
    stt = {"roas": 1.5}
    base = {"spend_30d": 2000.0, "idx_30": 0.8, "roas_30d": 0.9}
    assert cp.faded_kind(base, None) is None
    assert cp.faded_kind({**base, "spend_30d": 10.0}, stt) == "STOPPED"
    assert cp.faded_kind({**base, "idx_30": 0.8, "roas_30d": 0.9}, stt) == "COLLAPSED"
    assert cp.faded_kind({**base, "idx_30": 0.95, "roas_30d": 1.3}, stt) == "FADED"
    assert cp.faded_kind({**base, "idx_30": 1.02, "roas_30d": 1.6}, stt) is None


def test_former_winners_stopped_or_decayed_with_peak_week():
    b_roas = 1.0
    live = {"w": {"s30": 600.0, "r30": 300.0}, "live3": True}
    stopped = {"w": {"s30": 0.0, "r30": 0.0}, "live3": False}
    healthy = {"w": {"s30": 600.0, "r30": 700.0}, "live3": True}
    mk = lambda extra, spend, roas, key: {**extra, "family_key": key, "tracker_row_id": 1, "display": key, "hook": f"hook {key}", "spend_launch": spend, "revenue_launch": spend * roas, "last_spend_day": dt.date(2026, 10, 1)}
    fams = [mk(live, 4000, 1.4, "dec"), mk(stopped, 6000, 1.2, "stp"), mk(healthy, 3000, 1.3, "ok"), mk(stopped, 500, 2.0, "small"), mk(stopped, 5000, 0.8, "weak")]
    weekly = {"stp": {MON: (100, 90, 1), MON + dt.timedelta(days=7): (100, 200, 2)}}
    out, total, healthy_n = cp.former_winners(fams, b_roas, weekly, THROUGH)
    assert [w["ad"] for w in out] == ["stp", "dec"] or [w["ad"] for w in out] == ["dec", "stp"]
    assert total == 3 and healthy_n == 1
    stp = [w for w in out if w["ad"] == "stp"][0]
    assert stp["kind"] == "stopped" and stp["stopped"] == "2026-10-01" and stp["peak_week"] == "2026-09-21"
    dec = [w for w in out if w["ad"] == "dec"][0]
    assert dec["kind"] == "decayed" and dec["roas_30d"] == 0.5


def faded_inp():
    """Vita Bull: line 1 worked for 4 weeks then fell off while fresh ads also underperform; line 2 is steady; line 3 never worked; line 4 worked then went idle."""
    lines = [line(i, "vita-bull", "Cl", f"Icp{i}", f"Pain{i}") for i in range(1, 5)]
    fams = []
    weekly = {}

    def add(lid, pat, now_roas, s30, n=6, hook=True):
        for k in range(n):
            f = fam("vita-bull", lid, 3000, 1.2 if lid in (1, 4) else 0.9 if lid == 2 else 0.5, hook=f"hook {lid}-{k}" if hook else None, s30=s30 / n, r30=s30 / n * now_roas,
                    first=dt.date(2026, 5, 20), live=s30 > 0)
            f["_w"].update({"s60": 100.0, "r60": 100.0 * pat[0][1], "p60": 1.2})
            fams.append(f)
            weekly[f["family_key"]] = {w: (sp / n, sp / n * ro, sp / n / 80) for w, (sp, ro) in zip(weeks(len(pat)), pat)}
    add(1, [(600, 1.6)] * 4 + [(600, 0.5)] * 4, 0.5, 2400)
    add(2, [(600, 0.9)] * 8, 0.95, 2400)
    add(3, [(600, 0.5)] * 8, 0.5, 2400)
    add(4, [(600, 1.6)] * 4 + [(0, 0)] * 4, 0.0, 0.0)
    inp = build(lines, fams)
    inp["weekly"] = weekly
    return inp


def test_weekly_history_puts_was_working_lines_first_and_never_worked_in_stop():
    r = cp.compute(faded_inp(), WEEK)
    by = {}
    for x in r["rows"]:
        by.setdefault(x["section"], {})[x["line_code"]] = x
    assert set(by["faded"]) == {"VI01", "VI04"} and set(by["stop"]) == {"VI03"} and "VI02" not in by["faded"]
    a = by["faded"]["VI01"]
    assert a["status"] in ("COLLAPSED", "FADED") and a["stretch"]["start"] == "2026-09-14" and a["stretch"]["weeks"] == 4
    assert "Weeks of 2026-09-14 to 2026-10-11" in a["when_it_worked"] and "Last 30 days" in a["how_it_stopped"] and "from the peak" in a["how_it_stopped"]
    assert "consistent with" in a["diagnosis"] and a["revive_play"].startswith(("1 concept", "2 concepts", "3 concepts")) and "hook" in a["revive_play"]
    assert a["hooks"] and a["former_winners"] and a["winners_summary"]["winners"] == 6
    assert by["faded"]["VI04"]["status"] == "STOPPED" and "spend moved away" in by["faded"]["VI04"]["diagnosis"]
    assert r["stats"]["faded_by_kind"]["STOPPED"] == 1 and r["stats"]["faded_candidates"] == 2


def test_faded_rows_lead_csv_and_markdown_and_carry_the_story_columns():
    r = cp.compute(faded_inp(), WEEK)
    rows = list(csv.reader(io.StringIO(cp.to_csv(r))))
    h = rows[0]
    assert dict(zip(h, rows[1]))["section"] == "faded"                                      # the first data row is a was-working-now-isn't row
    first = dict(zip(h, rows[1]))
    assert first["when_it_worked"] and first["how_it_stopped"] and first["diagnosis"] and first["revive_play"] and first["former_winner_ads"]
    md = cp.to_markdown(r)
    assert md.index("Was working, now isn't") < md.index("Iterate on what is working") or "Iterate on what is working" not in md
    assert "Revive play:" in md and "Former winner:" in md


def test_funnel_counts_launched_under_100_tested_and_10k_hits():
    inp = scenario()
    for f, sp in zip([x for x in inp["families"] if x["product"] == "vita-bull"][:3], (50.0, 100.0, 12000.0)):
        f["spend_launch"] = sp
    inp["funnel"] = {"vita-bull": {"launched": 40, "under_tested": 8, "tested": 32, "top": 5}}
    r = cp.compute(inp, WEEK)
    b = r["baselines"]["vita-bull"]
    assert b["funnel"] == {"launched": 40, "under_tested": 8, "tested": 32, "top": 5} and b["hit_spend"] == 10000.0 and b["tested_spend"] == 100.0
    f30 = b["funnel_30d"]
    assert f30["active"] == f30["under_tested"] + f30["tested"] and f30["top"] <= f30["tested"]
    # without the SQL funnel the counts come from the families in hand and still add up
    inp2 = scenario()
    del_ = inp2.pop("funnel", None)
    b2 = cp.compute(inp2, WEEK)["baselines"]["vita-bull"]["funnel"]
    assert b2["launched"] == b2["under_tested"] + b2["tested"] and b2["top"] <= b2["tested"]
    md = cp.to_markdown(cp.compute(inp, WEEK))
    assert "ALL TIME" in md and "LAST 30 DAYS" in md and "under $100 spend 8 (20%)" in md and "$10,000+ (hits) 5" in md


def test_row_funnel_and_14_day_numbers_with_a_100_dollar_floor():
    inp = scenario()
    fs = [x for x in inp["families"] if x["product"] == "vita-bull" and x["line_id"] == 1]
    for f in fs:
        inp["win"]["meta"][f["family_key"]].update(s14=20.0, p14=1.0, r14=30.0)
    inp["win"]["meta"][fs[0]["family_key"]].update(s14=500.0, p14=6.0, r14=700.0)
    r = cp.compute(inp, WEEK)
    row = next(x for x in r["rows"] if x["line_id"] == 1)
    m = row["metrics"]
    assert m["funnel"]["launched"] >= m["funnel"]["tested"] and m["spend_14d"] == 640 and round(m["roas_14d"], 3) == round((700 + 7 * 30) / 640, 3)
    for f in fs:
        inp["win"]["meta"][f["family_key"]].update(s14=10.0, p14=1.0, r14=15.0)
    low = next(x for x in cp.compute(inp, WEEK)["rows"] if x["line_id"] == 1)["metrics"]
    assert low["spend_14d"] == 80 and low["roas_14d"] is None            # under $100 in the window: spend shown, no ratio
    csvrows = list(csv.reader(io.StringIO(cp.to_csv(r))))
    h = csvrows[0]
    assert h[-4:-2] == ["spend_14d", "roas_14d"] and "ads_10k_plus_spend" in h


def test_scoring_still_uses_the_1000_dollar_hit_not_the_10k_display_hit():
    assert cp.HIT_SPEND == 1000.0 and cp.TOP_SPEND == 10000.0


def test_launch_cohorts_split_new_ads_by_spend_to_date():
    inp = scenario()
    vb = [f for f in inp["families"] if f["product"] == "vita-bull"]
    for i, f in enumerate(vb[:6]):
        f["eff_launch"] = THROUGH - dt.timedelta(days=[1, 5, 29, 30, 45, 70][i])
        f["has_launch"] = True
        f["spend_launch"] = [50.0, 500.0, 12000.0, 20000.0, 90.0, 300.0][i]
    r = cp.compute(inp, WEEK)
    c = r["baselines"]["vita-bull"]["cohorts"]
    assert [x["label"] for x in c] == ["Launched in the last 30 days", "Launched 31 to 60 days ago", "Launched 61 to 90 days ago"]
    assert (c[0]["launched"], c[0]["under_tested"], c[0]["tested"], c[0]["top"]) == (3, 1, 2, 1)
    assert (c[1]["launched"], c[1]["under_tested"], c[1]["top"]) == (2, 1, 1) and c[2]["launched"] == 1 and c[2]["top"] == 0


def test_14_day_product_block_and_unmapped_spend():
    inp = scenario()
    vb = [f for f in inp["families"] if f["product"] == "vita-bull"]
    for f in vb:
        inp["win"]["meta"][f["family_key"]].update(s14=100.0, p14=1.0, r14=80.0)
    for f in vb[:2]:
        f["line_id"] = None                                          # ads with no line
    vb[0]["eff_launch"] = THROUGH - dt.timedelta(days=3)
    vb[0]["has_launch"] = True
    r = cp.compute(inp, WEEK)
    b = r["baselines"]["vita-bull"]
    assert b["spend_14d"] == 100.0 * len(vb) and b["spend_14d_no_line"] == 200.0 and b["live_14d"] == len(vb) and b["purchases_14d"] == len(vb)
    assert round(b["roas_14d"], 3) == 0.8 and b["cpa_14d"] == 100.0 and b["value_14d"] == 80.0 * len(vb)
    assert b["cohort_14d"]["launched"] >= 1 and b["cohort_14d"]["label"] == "Launched in the last 14 days"
    m = next(x for x in r["rows"] if x["line_id"] == 1)["metrics"]
    assert m["value_14d"] == 80.0 * m["purchases_14d"] and m["spend_14d"] == 100.0 * m["purchases_14d"]
