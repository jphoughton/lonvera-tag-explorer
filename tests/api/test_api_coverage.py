"""Coverage ("what we have not tested"): ICPs / pain points with no ads (tracker based) or ads but $0 spend (selected window / stages / source)."""
import pytest

from .helpers import get_ok


def pain_rows(client, slug, extra=""):
    d = get_ok(client, f"/api/products/{slug}/tree?root=cluster&depth=3&{extra}")
    return [r for r in d["rows"] if r["level"] == 2], [r for r in d["rows"] if r["level"] == 1]


def state_of(r):
    return "no_ads" if r["m"]["unique_ads"] == 0 else ("no_spend" if r["m"]["spend"] <= 0 else "tested")


@pytest.mark.parametrize("extra", ["window=launch", "window=7d", "window=30d", "from=2026-10-10&to=2026-10-20", "window=7d&stage=testing"])
def test_coverage_matches_the_tree_rows(client, extra):
    cov = get_ok(client, f"/api/products/vita-bull/coverage?{extra}")
    assert cov["available"] is True
    pains, icps = pain_rows(client, "vita-bull", extra)
    pains = [r for r in pains if r["cluster"] != "(untagged)"]                    # the tree's (untagged) bucket is not a Communication Overview line
    icps = [r for r in icps if r["cluster"] != "(untagged)"]
    want = {r["key"]: state_of(r) for r in pains + icps}
    got = {k: v["state"] for k, v in cov["keys"].items() if k.count("|") >= 1}
    assert got == want
    s = cov["summary"]["pain_points"]
    assert s["total"] == len(pains) and s["no_ads"] + s["no_spend"] + s["tested"] + s["added_after_tagging"] == s["total"]
    assert s["not_tested"] == s["no_ads"] + s["no_spend"]
    assert len(cov["rows"]) == sum(1 for v in want.values() if v != "tested")


def test_no_ads_does_not_change_with_window_stage_or_active_but_no_spend_does(client):
    base = get_ok(client, "/api/products/vita-bull/coverage?window=launch")
    no_ads = {r["key"] for r in base["rows"] if r["state"] == "no_ads"}
    for extra in ("window=1d", "window=7d", "window=90d", "window=7d&stage=scaling", "window=30d&active=1", "window=30d&compare=prev"):
        d = get_ok(client, f"/api/products/vita-bull/coverage?{extra}")
        assert {r["key"] for r in d["rows"] if r["state"] == "no_ads"} == no_ads, extra
    one_day = get_ok(client, "/api/products/vita-bull/coverage?window=1d")
    assert one_day["summary"]["pain_points"]["no_spend"] >= base["summary"]["pain_points"]["no_spend"]       # a shorter window can only add $0-spend lines


def test_added_after_tagging_is_reported_separately_and_not_counted_as_untested(client, pg):
    base = get_ok(client, "/api/products/vita-bull/coverage?window=launch")
    victim = next(r for r in base["rows"] if r["level"] == 2 and r["state"] == "no_ads")
    pg.execute("UPDATE taxonomy_lines SET added_after_tagging = true WHERE product_id = (SELECT id FROM products WHERE slug = 'vita-bull') AND pain_point = %s AND icp = %s AND cluster = %s",
               (victim["pain"], victim["icp"], victim["cluster"]))
    pg.commit()
    try:
        d = get_ok(client, "/api/products/vita-bull/coverage?window=launch")
        row = next(r for r in d["rows"] if r["key"] == victim["key"])
        assert row["added_after_tagging"] is True and d["keys"][victim["key"]]["added_after_tagging"] is True
        a, b = base["summary"]["pain_points"], d["summary"]["pain_points"]
        assert b["added_after_tagging"] == a["added_after_tagging"] + 1 and b["no_ads"] == a["no_ads"] - 1 and b["not_tested"] == a["not_tested"] - 1
    finally:
        pg.execute("UPDATE taxonomy_lines SET added_after_tagging = false WHERE product_id = (SELECT id FROM products WHERE slug = 'vita-bull')")
        pg.commit()


def test_tracker_tag_products_have_no_line_list(client):
    d = get_ok(client, "/api/products/boneflex/coverage?window=7d")
    if d["available"]:
        pytest.skip("fixture product is line-mapped")
    assert d["rows"] == [] and d["summary"] is None and "tracker" in d["reason"]
