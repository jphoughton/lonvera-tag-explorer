"""The why tool: statistics helpers, the tree definition, and the cascade against seeded analytics tables (Josh's example, decoys, spikes, routing shifts, optional views)."""
import datetime as dt
import json
import re

import psycopg
import pytest

from brain import tools as T
from brain import why as W
from brain import why_stats as S
from brain import why_tree as WT
from brain.sqlguard import check_select
from brain.knowledge import load
from brain_helpers import DB_NAME, PG_BASE, ROOT
from why_seed import END, PAGE_BAD, PAGE_P, PAGE_Y, PAGE_Z, STEP, Scenario, make_runner, seed

WHY_DB = DB_NAME + "_why"
WHY_URL = f"{PG_BASE}/{WHY_DB}"
KN = load(ROOT)


@pytest.fixture(scope="module")
def whydb():
    try:
        with psycopg.connect(f"{PG_BASE}/postgres", autocommit=True) as c:
            c.execute(f'DROP DATABASE IF EXISTS "{WHY_DB}" WITH (FORCE)')
            c.execute(f'CREATE DATABASE "{WHY_DB}"')
    except psycopg.OperationalError as e:
        pytest.skip(f"Postgres not available: {e}")
    return WHY_URL


def go(db, sc, **args):
    log = []
    seed(db, sc)
    a = {"start": str(STEP), "end": str(END), "compare": "prior_period", **args}
    return W.run_why(a, make_runner(db, log), KN), log


def find(tree, nid):
    if tree["id"] == nid:
        return tree
    for c in tree.get("children", []):
        r = find(c, nid)
        if r:
            return r
    return None


# ---------------------------------------------------------------- pure helpers
def test_compare_windows():
    s, e = dt.date(2026, 10, 18), dt.date(2026, 10, 31)
    assert S.compare_window("prior_period", s, e) == (dt.date(2026, 10, 4), dt.date(2026, 10, 17))
    assert S.compare_window("last_month", s, e) == (dt.date(2026, 9, 18), dt.date(2026, 10, 1))
    assert S.compare_window("last_year", s, e)[0] == dt.date(2025, 10, 19)
    assert S.compare_window("custom", s, e, dt.date(2026, 9, 1), dt.date(2026, 9, 14)) == (dt.date(2026, 9, 1), dt.date(2026, 9, 14))
    with pytest.raises(ValueError):
        S.compare_window("custom", s, e)


def test_grain_periods_use_monday_weeks_and_same_day_months():
    ps = S.grain_periods(dt.date(2026, 10, 31), {"day", "week", "month"})
    labels = [p[0] for p in ps]
    assert labels[0] == "d:2026-09-27" and labels[34] == "d:2026-10-31"
    weeks = [p for p in ps if p[0].startswith("w:")]
    assert all(dt.date.fromisoformat(p[0][2:]).weekday() == 0 for p in weeks) and sum(1 for p in weeks if not p[3].get("partial")) == 13 and weeks[-1][3].get("partial")
    mm = {p[0]: (p[1], p[2]) for p in ps if p[0].startswith("mm:")}
    assert mm["mm:2026-09"] == (dt.date(2026, 9, 1), dt.date(2026, 9, 30)) and mm["mm:2026-10"][1] == dt.date(2026, 10, 31)


def test_step_break_is_dated_and_spike_is_not_a_break():
    base = dt.date(2026, 9, 1)
    step = [(str(base + dt.timedelta(days=i)), 100.0 + (i % 3) if i < 30 else 70.0 + (i % 3)) for i in range(40)]
    a = S.analyse_series(step, "day", 0.06)
    assert a["shape"] == "step" and a["break"] == str(base + dt.timedelta(days=30)) and a["break_direction"] == "down"
    spike = [(str(base + dt.timedelta(days=i)), 100.0 + (i % 3) if i != 33 else 300.0) for i in range(40)]
    b = S.analyse_series(spike, "day", 0.06)
    assert b["shape"] == "spike" and b["break"] is None


def test_shift_share_components_sum_to_the_change():
    g = {"a": {"cur": {"n": 10, "d": 100}, "cmp": {"n": 30, "d": 100}}, "b": {"cur": {"n": 60, "d": 300}, "cmp": {"n": 30, "d": 100}}, "c": {"cur": {"n": 5, "d": 50}, "cmp": {"n": 0, "d": 0}}, "z": {"cur": {"n": 0, "d": 0}, "cmp": {"n": 4, "d": 40}}}
    ss = S.shift_share(g, "n", "d")
    assert abs(ss["rate_effect"] + ss["mix_effect"] - ss["delta"]) < 1e-12 and abs(ss["sum_check"]) < 1e-12
    assert abs(sum(r["contribution"] for r in ss["rows"].values()) - ss["delta"]) < 1e-12


# ---------------------------------------------------------------- the tree definition
def test_tree_definition_is_declarative_and_links_real_questions():
    d = T.Brain(root=ROOT).why_tree()
    ids = {n["id"] for n in d["nodes"]}
    assert d["root"] == "revenue" and {"revenue", "nc_revenue", "repeat_revenue", "spend", "nc_roas", "nc_cpa", "cpc", "cpm", "ctr", "conversion", "nc_aov", "routing_shift", "conv@family", "conv@landing_page"} <= ids
    for n in d["nodes"]:
        for ch in n.get("children", []):
            assert ch["id"] in ids
        assert "materiality" in n and "known_causes" in n and "past_questions" in n and n["comparisons"]
        for k in n["known_causes"]:
            assert k["status"] in ("verified", "refuted", "open") and k["date"]
    item_ids = {str(json.loads(l)["id"]) for l in (ROOT / "docs/questions/lonvera_items.jsonl").read_text().splitlines() if l.strip()}
    linked = {q for n in d["nodes"] for q in n["past_questions"]}
    assert linked and linked <= item_ids
    assert all(n.get("data_gap") for n in d["nodes"] if n["kind"] == "gap")


def test_every_sql_text_is_read_only_and_runs_on_seeded_views(whydb):
    log = []
    seed(whydb, Scenario(x_mode="new", routing="route", optional_all=True))
    run = make_runner(whydb, log)
    per = S.grain_periods(END, {"day", "week", "month"})
    v = lambda sql, **kw: sql.format_map({"periods": ", ".join(f"('{l}', date '{s}', date '{e}')" for l, s, e, _ in per), "prod": "", "hstart": END - dt.timedelta(days=34), "end": END, "start": STEP, "cstart": STEP - dt.timedelta(days=14), "cend": STEP - dt.timedelta(days=1), **kw})  # noqa: E731
    texts = [v(f["sql"]) for f in WT.FAMILIES.values()] + [v(WT.group_sql(g)) for g in WT.MIX_GROUPS] + [v(c["sql"]) for c in WT.CHANGE_SQL.values()]
    texts += [v(WT.group_sql("family", keys=["x'y"])), v(WT.group_sql("pack", keys=["1 bottle (single)"])), v(WT.group_sql("country"))]
    texts += [v(WT.LEAD_AD_SQL, keys="'fam-b0'"), v(WT.LP_FOR_ADS_SQL, keys="'fam-b0'"), WT.COVERAGE_SQL, WT.VIEWS_SQL]
    cp = [p for p in per[:0]] or [("cur", STEP, END, {}), ("cmp", STEP - dt.timedelta(days=14), STEP - dt.timedelta(days=1), {})]
    pv = ", ".join(f"('{l}', date '{s}', date '{e}')" for l, s, e, _ in cp)
    for d1, d2 in WT.ROUTING_PAIRS:
        spec, g1 = WT.D2[d2], WT.MIX_GROUPS[d1]
        texts.append(WT.ROUTING_SQL.replace("{periods_cte}", WT.PERIODS).replace("{d1}", g1["key"]).replace("{d2}", spec[0]).replace("{extra}", g1["extra"] + spec[1]).replace("{prod}", "").replace("{periods}", pv))
        texts.append(WT.ROUTING_FIRST_USE_SQL.replace("{extra}", g1["extra"] + spec[1]).replace("{d1}", g1["key"]).replace("{d2}", spec[0]).replace("{v1}", "C2").replace("{v2}", "x").replace("{prod}", "").replace("{hstart}", "2026-10-01").replace("{end}", "2026-10-31"))
    for b, _ in WT.ROUTING_BD_PAIRS:
        texts.append(WT.ROUTING_BD_SQL.replace("{periods_cte}", WT.PERIODS).replace("{b}", b).replace("{periods}", pv))
    texts.append(WT.BD_AD_SQL.replace("{periods_cte}", WT.PERIODS).replace("{fk}", "lonvera-x").replace("{periods}", pv))
    assert len(texts) > 40
    for t in texts:
        assert check_select(t)
        res = run(t, 5)
        assert res["columns"]


def test_the_standard_sql_runs_against_the_real_analytics_views(migrated_db):
    """Column names are checked against the migrated schema: every non-optional query must parse and execute on the real (empty) views."""
    per = S.grain_periods(END, {"day"})
    kw = {"periods": ", ".join(f"('{l}', date '{s}', date '{e}')" for l, s, e, _ in per), "prod": "", "hstart": END - dt.timedelta(days=34), "end": END, "start": STEP, "cstart": STEP - dt.timedelta(days=14), "cend": STEP - dt.timedelta(days=1)}
    texts = [f["sql"] for k, f in WT.FAMILIES.items() if not f["needs"]]
    texts += [WT.group_sql(g) for g, spec in WT.MIX_GROUPS.items() if not spec["needs"]]
    texts += [c["sql"] for c in WT.CHANGE_SQL.values() if not c["needs"]] + [WT.COVERAGE_SQL, WT.VIEWS_SQL, WT.LEAD_AD_SQL.replace("{keys}", "'x'")]
    run = make_runner(migrated_db)
    for t in texts:
        res = run(t.format_map(kw) if "{periods}" in t or "{hstart}" in t else t, 5)
        assert res["columns"]


# ---------------------------------------------------------------- Josh's example
def test_new_ad_takes_the_spend_at_low_conversion_and_the_chain_reaches_ad_and_page(whydb):
    out, log = go(whydb, Scenario(x_mode="new", with_lp=True))
    assert out["tree"]["status"] == "material" and out["tree"]["values"]["rel"] < -0.06
    assert out["tree"]["grains"]["when_it_broke"]["day"]["break"] == str(STEP)
    top = out["findings"][0]
    assert top["entity"]["group"] == "family" and top["entity"]["key"] == "lonvera-x" and top["label"] == "supported by control"
    assert top["break_date"] == str(STEP)
    assert any(e["type"] == "ad_family_launched" and e["date"] == str(STEP) for e in top["change_events_within_3_days"])
    lp = top["ad_detail"]["landing_page"]
    assert lp["url"] == PAGE_BAD and lp["copy_since"] == str(STEP)
    assert any(e["type"] == "landing_page_version_changed" for e in top["change_events_within_3_days"])
    assert PAGE_BAD in out["headline"] and "supported by control" in out["headline"]
    assert top["controls"]["counterfactual_exclusion"]["gap_closed"] > 0.9 and top["controls"]["like_for_like"]["holds"] is True
    assert top["suggested_action"] and top["past_questions"]
    # rates carry denominators, arrows are accounting, confidence words are present
    conv = find(out["tree"], "conversion")
    assert conv["denominator"].startswith("per ") and "of which" in out["arrows_mean"]
    assert all("label" in f for f in out["findings"])
    assert out["steps_used"] <= 40 and len(log) == out["steps_used"] + (out["investigation"].get("steps_used") or 0)


def test_decoy_change_on_the_same_day_in_an_unmoved_product_is_refuted(whydb):
    out, _ = go(whydb, Scenario(x_mode="new"))
    refuted = {(f["entity"]["group"], f["entity"]["key"]) for f in out["refuted_candidates"]}
    assert ("product", "boneflex") in refuted and ("family", "lonvera-y") in refuted
    assert all(f["label"] == "refuted" for f in out["refuted_candidates"])
    live = {(f["entity"]["group"], f["entity"]["key"]) for f in out["findings"]}
    assert ("family", "lonvera-x") in live and ("product", "boneflex") not in live
    assert any(e["type"] == "ad_family_launched" for e in out["refuted_candidates"][0]["change_events_within_3_days"])


def test_no_cause_present_gives_an_unexplained_residual_with_next_questions(whydb):
    out, _ = go(whydb, Scenario(uniform_drop=True))
    assert out["tree"]["status"] == "material"
    assert not [f for f in out["findings"] if f["label"] == "supported by control"]
    assert out["unexplained"]["text"].startswith("unexplained: 100%") and out["unexplained"]["share_of_change"] == 1.0
    assert out["next_questions"] and all(q["where_or_test"] for q in out["next_questions"])
    wheres = " ".join(q["where_or_test"] for q in out["next_questions"])
    assert "CheckoutChamp" in wheres or "Ads Manager" in wheres


def test_a_flat_metric_stops_the_cascade(whydb):
    out, log = go(whydb, Scenario(), start="2026-10-04", end="2026-10-17")
    assert out["tree"]["status"] == "flat" and out["tree"]["children"] == [] and "stops here" in out["headline"]
    assert out["steps_used"] <= 8 and out["findings"] == []


def test_a_one_day_spike_does_not_drive_the_cascade(whydb):
    out, _ = go(whydb, Scenario(spike=True), start="2026-10-25", end="2026-10-31")
    t = out["tree"]
    assert t["status"] == "spike_only" and t["children"] == [] and t["grains"]["day"]["shape"] == "spike" and t["grains"]["day"]["break"] is None
    assert out["findings"] == [] and "spike" in " ".join(t["reasons"])


def test_grain_option_limits_the_cuts(whydb):
    out, _ = go(whydb, Scenario(x_mode="new"), grain="week")
    g = out["tree"]["grains"]
    assert "week" in g and "day" not in g and "month" not in g


# ---------------------------------------------------------------- product and tag mix
def test_product_and_cluster_movers_and_mix_plus_rate_sum_to_the_parent_change(whydb):
    out, _ = go(whydb, Scenario(cluster_drop=True), metric="nc_roas")
    prod = find(out["tree"], "nc_roas@product")
    assert prod and prod["status"] == "material"
    assert prod["top_spend_share_movers"][0]["key"] == "boneflex" and prod["top_spend_share_movers"][0]["spend_share_cur"] < prod["top_spend_share_movers"][0]["spend_share_cmp"]
    mr = prod["mix_vs_rate"]
    assert abs(mr["mix_effect"] + mr["rate_effect"] - mr["delta"]) < 1e-9 and "not cause" in mr["label"]
    cl = find(out["tree"], "nc_roas@cluster")
    assert cl and "C3" in [t["key"] for t in cl["top_rate_movers"]]
    assert abs(cl["mix_vs_rate"]["mix_effect"] + cl["mix_vs_rate"]["rate_effect"] - cl["mix_vs_rate"]["delta"]) < 1e-9
    row = next(t for t in cl["values_table"] if t["key"] == "C3")
    assert row["meta_roas_cur"] is not None and row["nc_roas_upstack_cur"] is not None and row["live_ads_cur"] >= 1       # separate bases side by side
    assert cl["untagged"] is None or cl["untagged"]["key"].startswith("(")


def test_scoped_to_a_product_uses_attributed_numbers_and_gaps_the_account_only_nodes(whydb):
    out, _ = go(whydb, Scenario(x_mode="new"), metric="nc_cpa", product="vita-bull")
    assert "attributed" in out["scope"]["basis"] and out["tree"]["status"] == "material"
    with pytest.raises(W.WhyError):
        go(whydb, Scenario(), product="beef-liver", platform="google")


# ---------------------------------------------------------------- routing shift
def test_routing_shift_names_cluster_both_pages_date_and_supported(whydb):
    out, _ = go(whydb, Scenario(routing="route", with_lp=True), metric="conversion")
    rs = [f for f in out["findings"] if f["node"] == "routing_shift" and f["entity"]["key"] == "C2" and f["routing"]["pair"].startswith("cluster x landing page")]
    assert rs, [f["sentence"] for f in out["findings"]]
    f = rs[0]
    assert f["kind"] == "routing_shift" and f["label"] == "supported by control"
    assert f["routing"]["old_destination"] == PAGE_Z and f["routing"]["new_destination"] == PAGE_Y and f["break_date"] == str(STEP)
    assert f["routing"]["distribution_shift_points"] >= 30 and f["routing"]["conversion_new_now"] < f["routing"]["conversion_old_before"]
    assert f["controls"]["others_on_new_destination_worse"] is False and f["controls"]["old_destination_still_converts"] is True
    assert "C2" in f["sentence"] and PAGE_Z in f["sentence"] and PAGE_Y in f["sentence"] and "supported by control" in f["sentence"] and str(STEP) in f["sentence"]
    assert f["counterfactual_estimate"]["label"].startswith("estimate") and f["counterfactual_estimate"]["extra_new_customers"] > 0


def test_a_page_that_is_worse_for_everyone_is_a_page_problem_not_a_routing_shift(whydb):
    out, _ = go(whydb, Scenario(routing="page_problem", with_lp=True), metric="conversion")
    fs = [f for f in out["findings"] if f["node"] == "routing_shift" and f["routing"]["pair"].startswith("cluster x landing page")]
    assert fs and all(f["kind"] == "page_problem" for f in fs)
    assert not [f for f in out["findings"] if f["node"] == "routing_shift" and f.get("kind") == "routing_shift" and f["label"] == "supported by control"]
    assert "page problem" in fs[0]["suggested_action"]


def test_routing_pairs_that_need_missing_views_say_so(whydb):
    out, _ = go(whydb, Scenario(routing="route"), metric="conversion")
    rt = out["routing_shift_coverage"]
    assert any("landing_page_ads" in s["reason"] for s in rt["skipped_pairs"]) and any("breakdowns are not stored" in s["reason"] for s in rt["skipped_pairs"]) and rt["pairs_checked"]


# ---------------------------------------------------------------- optional views: gap until they exist, run when they do
def test_klaviyo_nodes_are_data_gaps_without_the_views(whydb):
    out, _ = go(whydb, Scenario(repeat_drop=True), metric="repeat_revenue")
    k = find(out["tree"], "klaviyo_billing_reminders")
    assert k and k["status"] == "data_gap" and k["data_gap"]["why"] == "Klaviyo not in the database yet"
    assert find(out["tree"], "renewals_per_due")["status"] == "data_gap"


def test_klaviyo_nodes_run_when_the_views_exist_and_flow_changes_are_listed(whydb):
    out, _ = go(whydb, Scenario(repeat_drop=True, with_klaviyo=True), metric="repeat_revenue")
    k = find(out["tree"], "klaviyo_billing_reminders")
    assert k and k["status"] == "material" and k["values"]["rel"] > 1.0
    assert any(c["matches_current_pattern"] for c in k["known_causes"])
    fc = out["changes_in_window"]
    assert fc["counts"].get("klaviyo_flow_updated") == 1 and "read from analytics.klaviyo_flows" in fc["klaviyo_flows"]


def test_shopify_and_breakdown_nodes_run_when_the_views_exist(whydb):
    seed(whydb, Scenario(x_mode="ramp", with_shopify=True, with_breakdown=True))
    log = []
    w = W.Why(make_runner(whydb, log), KN)
    w.setup({"start": str(STEP), "end": str(END)})
    assert w.walk("nc_units_per_order", 2, None, [])["status"] != "data_gap"
    assert w.walk("bd_roas@region", 2, {}, [])["status"] != "data_gap"        # NULL ad set rows are aggregated, never joined


def test_ad_level_breakdown_shift_is_a_candidate_with_the_same_control_rules(whydb):
    out, _ = go(whydb, Scenario(x_mode="ramp", with_breakdown=True))
    fam = next(f for f in out["findings"] if f["entity"]["key"] == "lonvera-x" and f["entity"]["group"] == "family")
    shifts = fam["ad_detail"]["breakdown_shift"]["values"]
    top = next(v for v in shifts if v["breakdown"] == "audience_segment" and v["value"] == "engaged_audience")
    assert top["ad_family_share_cmp"] == pytest.approx(0.30, abs=0.01) and top["ad_family_share_cur"] == pytest.approx(0.65, abs=0.01) and top["label"] == "supported by control"
    bd = [f for f in out["findings"] if f["node"] == "bd_ad_shift"]
    assert bd and "engaged_audience" in bd[0]["sentence"] and bd[0]["meta_reported_only"] is True
    seg = find(out["tree"], "bd_roas@audience_segment")
    assert seg is None or seg["status"] != "data_gap"


# ---------------------------------------------------------------- budget and the tool surface
def test_a_small_budget_returns_what_it_has_and_says_so(whydb):
    out, log = go(whydb, Scenario(x_mode="new"), max_steps=8)
    assert out["steps_used"] <= 8 and out["budget_exhausted"] is True
    assert any("max_steps" in q["question"] for q in out["next_questions"])


def test_tool_registration_validates_input(whydb):
    b = T.Brain(root=ROOT)
    with pytest.raises(T.ToolError, match="metric must be one of"):
        T.call(b, "why", {"metric": "zzz"})
    with pytest.raises(T.ToolError, match="unknown argument"):
        T.call(b, "why", {"nope": 1})
    assert T.call(b, "why_tree", {})["root"] == "revenue" and "sql" in T.call(b, "why_tree", {"include_sql": True})


# ---------------------------------------------------------------- the ROAS -> AOV branch
def test_roas_splits_exactly_into_aov_and_cpa_and_the_chain_descends_into_aov(whydb):
    out, _ = go(whydb, Scenario(pack_shift=True, with_shopify=True, with_lp=True), metric="nc_roas")
    rs = out["tree"]["roas_split"]
    le = rs["log_effects"]
    assert abs(le["aov"] + le["cpa"] - le["roas"]) < 1e-9 and rs["descend_into"] == "nc_aov"
    assert rs["text"].startswith("ROAS -") and "AOV -" in rs["text"] and "CPA flat" in rs["text"]
    assert "nc_aov" in [c["id"] for c in out["tree"]["children"]] and "nc_cpa" not in [c["id"] for c in out["tree"]["children"]]
    assert find(out["tree"], "nc_cpa") is None
    h = out["headline"]
    assert "ROAS down: AOV effect" in h and "CPA flat" in h and h.index("pack mix") > h.index("CPA flat") and "single-bottle share up from 38% to 56%" in h
    assert "ad family launched 'Lonvera-P'" in h and PAGE_P in h


def test_pack_mix_leaf_is_supported_with_within_pack_aov_unchanged(whydb):
    out, _ = go(whydb, Scenario(pack_shift=True, with_shopify=True, with_lp=True), metric="nc_roas")
    f = next(x for x in out["findings"] if x["node"] == "aov@pack")
    assert f["entity"]["key"] == "1 bottle (single)" and f["label"] == "supported by control" and f["break_date"] == str(STEP)
    assert f["share_cmp"] == pytest.approx(0.38, abs=0.01) and f["share_cur"] == pytest.approx(0.56, abs=0.01)
    assert f["controls"]["counterfactual_exclusion"]["gap_closed"] > 0.9 and f["controls"]["like_for_like"]["holds"] is True
    assert f["events_matched_by"].startswith("date only") and any(e["type"] == "ad_family_launched" and e["landing_page"]["url"] == PAGE_P for e in f["change_events_within_3_days"])
    mv = find(out["tree"], "aov@pack")
    assert abs(mv["mix_vs_rate"]["mix_effect"] + mv["mix_vs_rate"]["rate_effect"] - mv["mix_vs_rate"]["delta"]) < 1e-9
    assert abs(mv["mix_vs_rate"]["rate_effect"]) < 0.05 * abs(mv["mix_vs_rate"]["delta"])
    row = next(t for t in mv["values_table"] if t["key"] == "1 bottle (single)")
    assert row["aov_cur"] == pytest.approx(39.0) and "orders_cur" in row
    assert mv["weight"].startswith("share of new-customer orders")


def test_aov_nodes_are_data_gaps_without_shopify_orders(whydb):
    out, _ = go(whydb, Scenario(), metric="nc_aov", start="2026-10-04", end="2026-10-17")
    assert out["tree"]["status"] == "flat"


# ---------------------------------------------------------------- guardrails
def test_every_result_lists_the_guardrails_it_checked(whydb):
    out, _ = go(whydb, Scenario(x_mode="new"))
    g = {x["id"]: x for x in out["guardrails_checked"]}
    assert set(g) >= {"G01", "G03", "G04", "G05", "G06", "G07", "G08", "G10", "G15", "G19", "G22"} and all(x["result"] and x["rule"] for x in g.values())
    assert g["G07"]["ok"] and g["G08"]["ok"] and g["G10"]["ok"] and "gross" in g["G05"]["result"] and "Pacific" in g["G06"]["result"]
    assert g["G03"]["ok"] is True and f"last loaded day {END}" in g["G03"]["result"]
    import datetime as _dt
    from zoneinfo import ZoneInfo
    today = _dt.datetime.now(ZoneInfo("America/Los_Angeles")).date()
    out2, _ = go(whydb, Scenario(x_mode="new"), start=str(today - _dt.timedelta(days=6)), end=str(today))
    g3 = next(x for x in out2["guardrails_checked"] if x["id"] == "G03")
    assert g3["ok"] is False and "includes today" in g3["result"] and "loaded through" in g3["result"]


# ---------------------------------------------------------------- routing, parents' known causes, compact view
@pytest.mark.parametrize("raw,node", [("spend cliff", "spend"), ("why did the spend cliff happen", "spend"), ("frequency up", "frequency"), ("renewals down", "repeat_revenue"),
                                      ("CPM jump", "cpm"), ("repeat revenue", "repeat_revenue"), ("Meta spend", "meta_spend"), ("new customers fell", "nc_revenue"), ("nc_cpa", "nc_cpa"), ("billing reminders", "klaviyo_billing_reminders")])
def test_plain_words_route_to_the_right_node(raw, node):
    assert W.resolve_metric(raw) == node


def test_ancestors_lists_parents_nearest_first():
    assert "spend" in WT.ancestors("meta_spend")
    assert WT.ancestors("no_such_node") == []


def test_known_cause_on_a_gap_node_surfaces_by_date_overlap(monkeypatch):
    w = W.Why(lambda q, n: {"columns": [], "rows": []}, None)
    w.start, w.end = dt.date(2026, 9, 14), dt.date(2026, 9, 20)
    node = {"known_causes": [{"date": "2026-10-26", "claim": "c", "status": "open", "confidence": "consistent with", "direction": "down", "between": ["2026-09-14", "2026-09-16"], "note": None},
                             {"date": "2026-10-26", "claim": "far", "status": "open", "confidence": "untested", "direction": None, "between": ["2025-01-01", "2025-02-01"], "note": None}]}
    near, far = w.kc_block(node, None, None)
    assert near["matches_current_pattern"] is True and "dated overlap" in near["matched_by"]
    assert far["matches_current_pattern"] is False


def test_the_tool_answer_is_compact_unless_detail(whydb):
    log = []
    seed(whydb, Scenario(x_mode="new", with_lp=True))
    full = W.run_why({"start": str(STEP), "end": str(END), "compare": "prior_period"}, make_runner(whydb, log), KN)
    c = W.compact(full)
    assert "tree" not in c and "queries_run" not in c and len(json.dumps(c, default=str)) < len(json.dumps(full, default=str)) / 2
    assert c["headline"] == full["headline"] and c["findings"][0]["label"] == full["findings"][0]["label"] and len(c["findings"]) <= 6


def test_meta_history_fills_windows_before_platform_daily_starts():
    w = W.Why(lambda q, n: {"columns": [], "rows": []}, None)
    w.q = lambda sql, limit=200: [{"p": "cur", "hdays": 30, "hspend": 1000.0}, {"p": "cmp", "hdays": 30, "hspend": 500.0}]
    out = {"cur": {"spend": 1000.0, "meta_spend": 1000.0, "meta_days": 30.0}, "cmp": {"spend": 20.0, "meta_spend": 0.0, "meta_days": 0.0}}
    w.fmt_sql = lambda s: s
    w.add_meta_history(out)
    assert out["cmp"]["meta_spend"] == 500.0 and out["cmp"]["spend"] == 520.0 and out["cmp"]["meta_days"] == 30.0
    assert out["cur"]["meta_spend"] == 1000.0 and out["cur"]["spend"] == 1000.0


def test_compact_puts_the_asked_metrics_known_causes_first_and_refuted_last():
    res = {"metric": "cpm", "headline": "h", "findings": [], "refuted_candidates": [], "changes_in_window": {}, "guardrails_checked": [],
           "known_cause_matches": [{"node": "nc_cpa", "claim": "child", "status": "verified", "confidence": "consistent with"}, {"node": "cpm", "claim": "own refuted", "status": "refuted", "confidence": "refuted"},
                                   {"node": "cpm", "claim": "own", "status": "verified", "confidence": "consistent with"}]}
    c = W.compact(res)
    assert [k["claim"] for k in c["known_causes_to_report"]] == ["own", "own refuted", "child"]
    assert "EACH known cause" in c["how_to_answer"]
