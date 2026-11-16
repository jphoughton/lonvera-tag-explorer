"""The creative priority score (app/priority_score.py) as a pure function: monotonicity, shrinkage of small samples, level invariance, no-data behaviour, the loss cap, the pipeline
factor, tie-breaks and the explanation text. No database."""
import copy

import pytest

from app import priority_score as ps


def cut(**kw):
    """A healthy mid-size cut: $10,000 in 30 days at its product mix's ROAS, hit rate at the mix rate, launches in line with spend."""
    base = {"labels": {"icp": "Beer Drinker"}, "spend_30d": 10000.0, "nc_revenue_30d": 5000.0, "nc_purchases_30d": 80, "spend_prior30": 9000.0, "nc_revenue_prior30": 4500.0,
            "expected_nc_roas_30d": 0.5, "expected_nc_roas_prior30": 0.5, "blended_nc_roas_30d": 0.5, "judged_ads": 20, "hit_ads": 5, "expected_hit_rate": 0.25,
            "live_ads": 6, "launches_60d": 10, "days_since_launch": 5, "spend_share_30d": 0.10, "launch_share_60d": 0.10, "top_ad_share_30d": 0.3,
            "old_spend_share_30d": 0.4, "pipeline_open": 0}
    base.update(kw)
    return base


def score(**kw):
    return ps.score_one(cut(**kw))


@pytest.mark.parametrize("field,lo,hi", [("hit_ads", 3, 10), ("nc_revenue_30d", 4000.0, 6500.0), ("spend_30d", 3000.0, 40000.0), ("days_since_launch", 2, 40)])
def test_monotone_in_each_good_signal(field, lo, hi):
    a, b = score(**{field: lo}), score(**{field: hi})
    assert b["score"] >= a["score"], (field, a["score"], b["score"])


def test_more_pipeline_lowers_the_score_and_says_so():
    free, busy = score(), score(pipeline_open=5)
    assert busy["score"] < free["score"]
    pip = [d for d in busy["drivers"] if d["signal"] == "pipeline"]
    assert pip and pip[0]["points"] < 0 and "already in production" in pip[0]["plain_text"]
    assert busy["score"] == pytest.approx(free["score"] * 0.5, abs=0.11)            # 5 open vs need max(3, 10/2) = 5 -> full coverage -> half
    assert "pipeline already covers the cut's launch pace" in busy["flags"]


def test_shrinkage_small_cut_cannot_top_a_proven_one():
    tiny = cut(labels={"icp": "Tiny"}, spend_30d=300.0, nc_revenue_30d=900.0, judged_ads=1, hit_ads=1, spend_share_30d=0.003, launch_share_60d=0.0, days_since_launch=60, spend_prior30=0.0, nc_revenue_prior30=0.0)
    proven = cut(labels={"icp": "Proven"}, hit_ads=9, nc_revenue_30d=6000.0)
    out = ps.score_cuts([tiny, proven])
    assert out[0]["labels"]["icp"] == "Proven"
    t = next(o for o in out if o["labels"]["icp"] == "Tiny")
    assert t["confidence"] == "low" and t["tier"] in ("Keep steady", "Low priority")
    assert ps.shrunk_roas(900.0, 300.0, 0.5) / 0.5 < 1.6 < (900.0 / 300.0) / 0.5     # a 3.0x ROAS on $300 (index 6.0) is pulled back to index 1.54


def test_shrunk_roas_moves_with_evidence():
    assert ps.shrunk_roas(0, 0, 0.5) == pytest.approx(0.5)
    assert ps.shrunk_roas(900, 300, 0.5) < ps.shrunk_roas(90000, 30000, 0.5)
    assert ps.shrunk_roas(90000, 30000, 0.5) == pytest.approx((90000 + 2500 * 0.5) / 32500)


def test_level_invariance_score_depends_only_on_the_cut_itself():
    a = cut(labels={"icp": "A"})
    alone = ps.score_cuts([copy.deepcopy(a)])[0]
    crowd = ps.score_cuts([copy.deepcopy(a), cut(labels={"icp": "B"}, hit_ads=15), cut(labels={"icp": "C"}, spend_30d=50.0)])
    assert next(o for o in crowd if o["labels"]["icp"] == "A")["score"] == alone["score"]
    deeper = ps.score_one({**a, "labels": {"cluster": "Alcohol", "icp": "A", "pain_point": "Alcohol Face"}})   # same numbers at a deeper level = same score
    assert deeper["score"] == alone["score"]


def test_no_data_cut_is_untested_and_sorts_last():
    empty = {"labels": {"icp": "Proposed"}, "blended_nc_roas_30d": 0.5, "expected_hit_rate": 0.25, "pipeline_open": 0}
    out = ps.score_cuts([empty, cut(labels={"icp": "Weak"}, hit_ads=0, nc_revenue_30d=3000.0, spend_30d=2000.0, spend_share_30d=0.02)])
    assert out[-1]["labels"]["icp"] == "Proposed" and out[-1]["tier"] == "Untested"
    assert 0 <= out[-1]["score"] < 20 and any(f.startswith("untested") for f in out[-1]["flags"])


def test_missing_and_garbage_values_never_raise():
    o = ps.score_one({"labels": {"icp": "x"}, "spend_30d": None, "nc_revenue_30d": "n/a", "expected_nc_roas_30d": float("nan"), "days_since_launch": None})
    assert o["tier"] == "Untested" and 0 <= o["score"] <= 100


def test_big_spend_below_mix_is_capped_and_flagged():
    o = score(spend_30d=60000.0, nc_revenue_30d=18000.0, spend_share_30d=0.5, launch_share_60d=0.05, hit_ads=12, judged_ads=20)   # 0.30x vs a 0.50x mix
    assert o["score"] <= 44.0 and o["tier"] in ("Keep steady", "Low priority")
    assert any(d["signal"] == "loser_cap" for d in o["drivers"])
    assert any("below its product mix" in f for f in o["flags"])


def test_dead_cut_with_great_history_is_a_revive_flag_not_make_more():
    o = score(spend_30d=0.0, nc_revenue_30d=0.0, spend_prior30=0.0, nc_revenue_prior30=0.0, judged_ads=6, hit_ads=5, spend_share_30d=0.0, live_ads=0, days_since_launch=90)
    assert o["tier"] in ("Keep steady", "Low priority") and o["confidence"] == "low"
    assert any("revive" in f for f in o["flags"])


def test_one_ad_carrying_the_cut_is_flagged():
    assert any("one ad carries" in f for f in score(top_ad_share_30d=0.9)["flags"])


def test_points_add_up_to_the_score():
    for kw in ({}, {"pipeline_open": 2}, {"hit_ads": 12, "spend_30d": 30000.0}):
        o = score(**kw)
        assert sum(d["points"] for d in o["drivers"]) == pytest.approx(o["score"], abs=0.3)


def test_explanation_names_the_top_drivers_in_plain_words():
    o = score(hit_ads=12)
    assert o["drivers"][0]["signal"] == "hit_rate"
    assert o["why"].startswith("12 of 20 tested ads reached $1,000 (60%) vs 25% for its product mix")
    assert "—" not in o["why"] and all("—" not in d["plain_text"] for d in o["drivers"])      # no em dashes in UI text


def test_tie_break_confidence_then_spend_then_labels():
    a = cut(labels={"icp": "B"})
    b = cut(labels={"icp": "A"})
    out = ps.score_cuts([a, b])
    assert [o["labels"]["icp"] for o in out] == ["A", "B"]                          # equal score, confidence and spend -> labels A to Z
    c = cut(labels={"icp": "C"})
    c2 = dict(c, labels={"icp": "D"}, spend_30d=10000.0)
    assert ps.score_cuts([c, c2])[0]["labels"]["icp"] == "C"


def test_tiers_follow_the_documented_floors():
    assert [lab for _, lab in ps.DEFAULT_CONFIG["tiers"]] == ["Make more now", "Make more", "Keep steady", "Low priority"]
    assert abs(sum(ps.DEFAULT_CONFIG["weights"].values()) - 1.0) < 1e-9


def test_config_override_changes_weights():
    o = ps.score_one(cut(hit_ads=12), {"weights": {"economics": 0.0, "hit_rate": 1.0, "supply_gap": 0.0, "scale": 0.0, "momentum": 0.0}})
    assert o["score"] == pytest.approx(100 * o["components"]["hit_rate"], abs=0.1)
