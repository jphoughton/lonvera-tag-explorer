"""Daily story facts, change events, narrative validation and pattern memory on the made-up panel (tests/daily_story_fixture.py): no database."""
import copy
import datetime as dt
import json

import pytest

from etl import daily_story as DS, daily_story_events as EV, daily_story_facts as F, daily_story_narrative as N, daily_story_patterns as P
from tests.daily_story_fixture import make_panel

D = dt.date(2026, 11, 11)


@pytest.fixture(scope="module")
def panel():
    return make_panel()


@pytest.fixture(scope="module")
def facts(panel):
    return DS.compute_facts(panel, D)


def test_headline_is_actual_new_customer_revenue_over_meta_plus_google(panel, facts):
    a = panel.acct[D]
    expect = a["acct_nc_rev"] / (a["spend"] + a["g_spend"])
    assert facts["metrics"]["nc_roas"]["value"] == pytest.approx(expect)
    assert facts["metrics"]["spend_total"]["value"] == pytest.approx(a["spend"] + a["g_spend"])
    # the ad-attributed ROAS is a separate metric and differs from the headline
    assert facts["metrics"]["ad_roas"]["value"] == pytest.approx(a["nc_rev"] / a["spend"])
    assert facts["metrics"]["ad_roas"]["value"] != pytest.approx(expect)


def test_funnel_definitions(panel, facts):
    a, m = panel.acct[D], facts["metrics"]
    assert m["cpm"]["value"] == pytest.approx(a["spend"] * 1000 / a["impressions"])
    assert m["ctr"]["value"] == pytest.approx(a["clicks"] / a["impressions"])
    assert m["cplc"]["value"] == pytest.approx(a["spend"] / a["clicks"])
    assert m["conv"]["value"] == pytest.approx(a["acct_nc"] / a["clicks"])          # all new customers / Meta link clicks
    assert m["aov"]["value"] == pytest.approx(a["acct_nc_rev"] / a["acct_nc"])
    assert m["cpnc"]["value"] == pytest.approx((a["spend"] + a["g_spend"]) / a["acct_nc"])
    assert m["c2lpv"]["value"] == pytest.approx(a["lpv"] / a["clicks"])
    assert m["lpv2atc"]["value"] == pytest.approx(a["atc"] / a["lpv"])


def test_baseline_is_the_same_weekday_four_weeks(panel, facts):
    assert facts["windows"]["baseline_same_weekday"] == [(D - dt.timedelta(days=7 * k)).isoformat() for k in (1, 2, 3, 4)]
    days = [panel.acct[D - dt.timedelta(days=7 * k)] for k in (1, 2, 3, 4)]
    vals = sorted(a["acct_nc_rev"] / (a["spend"] + a["g_spend"]) for a in days)
    assert facts["metrics"]["nc_roas"]["baseline_median"] == pytest.approx((vals[1] + vals[2]) / 2)
    assert facts["metrics"]["nc_roas"]["n_baseline"] == 4


def test_decomposition_terms_add_back_exactly(facts):
    d = facts["decomposition"]
    assert d["metric"] == "nc_roas"
    assert sum(t["log"] for t in d["terms"]) == pytest.approx(d["total_log"], abs=1e-12)
    assert d["total_log"] == pytest.approx(__import__("math").log(d["day"] / d["base"]), abs=1e-12)
    assert d["check_sum_pct_points"] == pytest.approx(d["pct_change"], abs=1e-12)
    assert {t["name"] for t in d["terms"]} == {"aov", "spend_mix", "cpm", "ctr", "c2lpv", "lpv2atc", "atc2nc"}


def test_initiated_checkout_splits_the_last_term_when_the_column_exists():
    d = DS.compute_facts(make_panel(with_ic=True), D)["decomposition"]
    names = {t["name"] for t in d["terms"]}
    assert "atc2ic" in names and "ic2nc" in names and "atc2nc" not in names
    assert d["check_sum_pct_points"] == pytest.approx(d["pct_change"], abs=1e-12)


def test_every_cut_mix_and_rate_add_back(facts):
    for cut, t in facts["cuts"].items():
        if not t or not t["mix_rate"]:
            continue
        mr = t["mix_rate"]
        assert mr["mix"] + mr["rate"] == pytest.approx(mr["change"], abs=1e-9), cut


def test_structure_cut_sees_the_troas_ramp(facts):
    t = facts["cuts"]["structure"]
    row = next(r for r in t["rows"] if r["key"] == "Scaling / tROAS / 7-day click")
    assert row["share"] > 0.3 and row["base_share"] < 0.15
    assert facts["metrics"]["troas_scaling_share"]["value"] > 0.6


def test_z_score_flags_use_a_28_day_noise(facts):
    m = facts["metrics"]["nc_roas"]
    assert m["noise_std_28d"] and m["z"] == pytest.approx((m["value"] - m["baseline_median"]) / m["noise_std_28d"])
    assert (m["flag"] is not None) == (abs(m["z"]) >= 2)
    for a in facts["anomalies"]:
        assert abs(a["z"]) >= 2


def test_change_ledger_lists_the_planted_changes_with_first_day_and_share(facts):
    by = {(c["type"], c["first_day"]) for c in facts["changes"]}
    assert ("new_adsets", "2026-11-09") in by
    assert ("troas_share_up", "2026-11-10") in by
    assert ("sms_blast", "2026-11-11") in by
    assert ("families_launched", "2026-11-09") not in by                      # one family: below the 5-family threshold
    ns = next(c for c in facts["changes"] if c["type"] == "new_adsets" and c["first_day"] == "2026-11-09")
    assert ns["share_of_D_meta_spend"] and 0 < ns["share_of_D_meta_spend"] < 1
    assert any(c["type"] == "cluster_entered" and c["subject"] == "Hair Thinning" for c in facts["changes"])


def test_events_of_the_day_are_stored_without_refs(facts):
    assert facts["events"] and all("ref" not in e for e in facts["events"])
    json.dumps(facts)                                                          # serialisable


def test_sections_klaviyo_google_amazon_are_explicit(facts):
    s = facts["sections"]
    assert s["klaviyo"]["available"] and "not additive" in s["klaviyo"]["basis"]
    assert {c["channel"] for c in s["klaviyo"]["cells"]} == {"email", "sms"} and {c["source"] for c in s["klaviyo"]["cells"]} == {"campaign", "flow"}
    assert s["google"]["available"] and s["google"]["by_type"] and "denominator" in s["google"]["basis"]
    assert s["amazon"]["available"] and s["amazon"]["lag_days"] == 0 and "Never added" in s["amazon"]["basis"]
    assert s["amazon"]["acos"] == pytest.approx(1500 and s["amazon"]["ad_spend"] / s["amazon"]["attributed_sales"])


def test_amazon_lag_and_missing_klaviyo_say_so(panel):
    p = copy.deepcopy(panel)
    del p.amazon[D]
    del p.klaviyo[D]
    f = DS.compute_facts(p, D)
    a = f["sections"]["amazon"]
    assert a["available"] is False and a["lag_days"] == 1 and "loaded through 2026-11-10" in a["note"]
    assert f["sections"]["klaviyo"]["available"] is False
    assert any("Klaviyo" in c for c in f["cannot_see"])


def test_not_ready_names_the_missing_source(panel):
    p = copy.deepcopy(panel)
    p.acct[D]["has_orders"] = False
    p.acct[D]["acct_nc_rev"] = None
    f = DS.compute_facts(p, D)
    assert f["readiness"]["ready"] is False and f["readiness"]["missing_core"] == ["Upstack orders"] and f["readiness"]["coverage"] == "not_ready"
    t = N.template(f)
    assert "Data not ready" in t["headline"] and "Upstack orders" in t["headline"]


def test_early_days_have_partial_baselines_and_no_noise(panel):
    f = DS.compute_facts(panel, dt.date(2026, 9, 10))
    assert f["metrics"]["nc_roas"]["n_baseline"] == 1
    assert f["metrics"]["nc_roas"]["z"] is None


def test_mix_vs_rate_top_movers_sum_to_the_move(panel):
    mv = F.family_movers(panel, D, n=50)
    assert sum(r["total"] for r in mv["rows"]) == pytest.approx(mv["change"], abs=1e-9)


# ----------------------------------------------------------------------------------------------------------------------------------------- narrative
def test_template_is_short_tldr_first_and_valid(facts):
    t = N.template(facts)
    assert t["story"].startswith("TLDR:")
    assert N.word_count(t["story"]) <= 170
    assert "Cannot see:" in t["story"].splitlines()[-1]
    ok, bad = N.validate(t["headline"] + "\n" + t["story"], facts)
    assert ok, bad
    assert "caused" not in t["story"].lower() and "because" not in t["story"].lower()


def test_validate_rejects_a_number_not_in_the_facts(facts):
    assert N.validate("NC ROAS was 7.77 today", facts) == (False, ["7.77"])
    ok, _ = N.validate("There were 4 weeks and 28 days in 2026.", facts)
    assert ok
    nc = facts["metrics"]["nc_roas"]["value"]
    assert N.validate(f"NC ROAS {nc:.2f} and {nc:.3f}", facts)[0]
    assert N.validate(f"{nc * 100:.0f}% is not a percent of anything but is in the facts as x100", facts)[0]
    assert not N.validate("Revenue was $987,654 yesterday", facts)[0]


def _answer(story, headline="NC ROAS was higher."):
    return json.dumps({"headline": headline, "story": story, "confidence": {"move": "consistent with"}})


def test_generate_accepts_a_valid_model_answer(facts):
    nc = facts["metrics"]["nc_roas"]["value"]
    story = f"TLDR: NC ROAS was {nc:.2f}.\nCannot see: the Meta change log."
    out = N.generate(facts, caller=lambda s, u: _answer(story))
    assert out["source"] == "llm" and out["model"] == "claude-sonnet-5-5" and out["story"] == story


def test_generate_falls_back_to_the_template_when_a_number_is_invented(facts):
    calls = []
    def bad(system, user):
        calls.append(user)
        return _answer("TLDR: NC ROAS was 9.99.\nCannot see: the Meta change log.")
    out = N.generate(facts, caller=bad)
    assert out["source"] == "template" and "9.99" in out["note"] and len(calls) == 3           # retries with the reason
    assert "rejected" in calls[1]
    assert out["story"].startswith("TLDR:")


def test_generate_rejects_causal_language_and_invented_mechanisms(facts):
    nc = facts["metrics"]["nc_roas"]["value"]
    for bad in (f"TLDR: NC ROAS was {nc:.2f} because of new ad sets.\nCannot see: x.", f"TLDR: NC ROAS was {nc:.2f}, consistent with low-intent traffic.\nCannot see: x.",
                f"TLDR: NC ROAS was {nc:.2f}; nothing explains it.\nCannot see: x."):
        out = N.generate(facts, caller=lambda s, u, b=bad: _answer(b))
        assert out["source"] == "template" and "claims a cause" in out["note"]


def test_generate_falls_back_on_overlong_missing_cannot_see_and_garbage(facts):
    long = "TLDR: " + " ".join(["word"] * 200) + "\nCannot see: x."
    assert N.generate(facts, caller=lambda s, u: _answer(long))["source"] == "template"
    assert N.generate(facts, caller=lambda s, u: _answer("TLDR: fine."))["source"] == "template"
    assert N.generate(facts, caller=lambda s, u: "not json")["source"] == "template"
    def boom(s, u):
        raise RuntimeError("down")
    out = N.generate(facts, caller=boom)
    assert out["source"] == "template" and "down" in out["note"]


def test_model_off_without_a_key_gives_the_template(facts, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setenv("DAILY_STORY_LLM", "auto")
    out = N.generate(facts)
    assert out["source"] == "template" and "ANTHROPIC_API_KEY" in out["note"]
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setenv("DAILY_STORY_LLM", "0")
    assert N.backend() is None


def test_the_model_only_sees_facts_numbers(facts):
    c = json.dumps(N.condense(facts))
    ok, bad = N.validate(c, facts)
    assert ok, bad[:5]
    assert len(c) < 30000


# ----------------------------------------------------------------------------------------------------------------------------------------- patterns
def _facts_series(panel, days):
    memo = F.Memo(panel)
    return {d: DS.compute_facts(panel, d, memo) for d in days}


def test_pattern_rows_record_events_and_next_three_day_outcomes(panel):
    days = [dt.date(2026, 11, 1) + dt.timedelta(days=i) for i in range(11)]
    fs = _facts_series(panel, days)
    rows = P.build_rows(fs)
    allrows = [r for r in rows if r["change_type"] == P.ALL_DAYS]
    assert len(allrows) == len(days)
    r = next(r for r in rows if r["change_type"] == "troas_share_up" and r["day"] == dt.date(2026, 11, 10))
    assert r["magnitude_bucket"] == "40+ points" and r["out_days"] == 1                          # only 11-11 follows
    nxt = fs[dt.date(2026, 11, 11)]["metrics"]["nc_roas"]["pct_vs_baseline"]
    assert r["out_nc_roas"] == pytest.approx(nxt) and r["d1_nc_roas"] == pytest.approx(nxt)
    r2 = next(r for r in rows if r["change_type"] == P.ALL_DAYS and r["day"] == dt.date(2026, 11, 5))
    exp = [fs[dt.date(2026, 11, 5) + dt.timedelta(days=k)]["metrics"]["cpm"]["pct_vs_baseline"] for k in (1, 2, 3)]
    assert r2["out_cpm"] == pytest.approx(sum(exp) / 3) and r2["out_days"] == 3


def test_pattern_rows_skip_days_that_are_not_ready(panel):
    p = copy.deepcopy(panel)
    p.acct[D]["has_google"] = False
    f = {D: DS.compute_facts(p, D)}
    assert P.build_rows(f) == []


def test_change_feed_replaces_the_derived_ad_set_events_and_reaches_the_ledger(panel):
    p = copy.deepcopy(panel)
    p.feed_name = "analytics.meta_change_ledger"
    p.feed_events[dt.date(2026, 11, 10)] = [{"type": "bid_amount", "count": 4, "adset_ids": ["as0", "as1", "as2", "as3"], "subject": "adset 0; adset 1", "detail": "80 to 90", "source": "ledger"}]
    p.feed_events[dt.date(2026, 11, 9)] = [{"type": "new_adset", "count": 1, "adset_ids": ["as-new"], "subject": "new adset", "detail": "", "source": "ledger"}]
    f = DS.compute_facts(p, D)
    types = {c["type"] for c in f["changes"] if c["first_day"] >= "2026-11-09"}
    assert "feed_bid_amount" in types and "feed_new_adset" in types and "new_adsets" not in types and "campaign_launch" not in types
    c = next(c for c in f["changes"] if c["type"] == "feed_bid_amount")
    assert c["bucket"] == "3-9 objects" and c["source"] == "Meta change feed" and 0 < c["share_of_D_meta_spend"] < 1
    assert not any("change log" in x for x in f["cannot_see"])


def test_missing_initiated_checkout_history_is_none_not_zero():
    p = make_panel(with_ic=True)
    for d in list(p.rows)[:20]:
        for r in p.rows[d]:
            r["ic"] = None
        p.acct[d]["ic"] = None
    f = DS.compute_facts(p, dt.date(2026, 9, 15))
    assert f["metrics"]["atc2ic"]["value"] is None
    assert "atc2ic" not in {t["name"] for t in (f["decomposition"] or {"terms": []})["terms"]}
