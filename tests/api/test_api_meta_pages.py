"""By page view: GET /api/meta/pages (app/meta_pages.py) over analytics.meta_ad_daily joined to meta_ad_pages / meta_pages.

Rules under test: spend by page adds up to total Meta ad-level spend in the window (ads with no page are the '(unknown page)' row), ratios are ratios of sums, the Lonvera group is the
Lonvera page only, the product and stage filters narrow the same totals, the like-for-like note and basis are in the payload, and the not-loaded empty state."""
import pytest


@pytest.fixture()
def pages(pg, fx):
    def wipe():
        pg.rollback()
        pg.execute("DELETE FROM meta_ad_pages")
        pg.execute("DELETE FROM meta_pages WHERE page_id NOT IN ('161085663758792', '830267930177476')")
        pg.commit()
    wipe()
    yield pg
    wipe()


def window(pg):
    r = pg.execute("SELECT min(day), max(day) FROM analytics.meta_ad_daily").fetchone()
    return r[0], r[1]


def top_ads(pg, n=3):
    lo, hi = window(pg)
    return [r[0] for r in pg.execute("SELECT ad_id FROM analytics.meta_ad_daily WHERE day BETWEEN %s AND %s GROUP BY ad_id HAVING sum(spend) > 0 ORDER BY sum(spend) DESC LIMIT %s", (lo, hi, n)).fetchall()]


def assign(pg, ad_id, page_id):
    pg.execute("INSERT INTO meta_ad_pages (ad_id, ad_account, page_id, first_seen, last_seen, source) VALUES (%s,'Lonvera Main',%s,'2026-11-18','2026-11-18','effective_object_story_id') "
               "ON CONFLICT (ad_id) DO UPDATE SET page_id = EXCLUDED.page_id", (ad_id, page_id))
    if page_id:
        pg.execute("INSERT INTO meta_pages (page_id) VALUES (%s) ON CONFLICT DO NOTHING", (page_id,))


def get(client, pg, qs=""):
    lo, hi = window(pg)
    r = client.get(f"/api/meta/pages?from={lo}&to={hi}" + qs)
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def test_requires_login(anon):
    assert anon.get("/api/meta/pages").status_code in (401, 403)


def test_not_loaded_state_before_the_feed_ran(client, pages):
    d = get(client, pages)
    assert d["loaded"] is False and "not loaded" in d["message"]


def test_rows_group_lonvera_vs_other_and_total_ties_to_all_meta_spend(client, pages):
    a, b, c = top_ads(pages)
    assign(pages, a, "161085663758792")
    assign(pages, b, "830267930177476")
    pages.execute("UPDATE meta_pages SET name = 'Other handle' WHERE page_id = '830267930177476'")
    pages.commit()                                                                                    # c has no page row
    d = get(client, pages)
    lo, hi = window(pages)
    total = float(pages.execute("SELECT sum(spend) FROM analytics.meta_ad_daily WHERE day BETWEEN %s AND %s", (lo, hi)).fetchone()[0])
    assert d["loaded"] and abs(d["total"]["spend"] - total) < 0.01 and abs(sum(r["spend"] for r in d["rows"]) - total) < 0.05
    names = {r["page"]: r for r in d["rows"]}
    assert set(names) == {"Lonvera", "Other handle", "(unknown page)"} or "Page 161085663758792" in names
    lon = next(r for r in d["rows"] if r["is_lonvera"])
    spend_a = float(pages.execute("SELECT sum(spend) FROM analytics.meta_ad_daily WHERE ad_id = %s AND day BETWEEN %s AND %s", (a, lo, hi)).fetchone()[0])
    assert abs(lon["spend"] - spend_a) < 0.01
    g = {x["key"]: x for x in d["groups"]}
    assert set(g) == {"lonvera", "other", "unknown"} and abs(g["lonvera"]["spend"] - lon["spend"]) < 0.01
    assert abs(g["lonvera"]["spend"] + g["other"]["spend"] + g["unknown"]["spend"] - d["total"]["spend"]) < 0.05
    assert abs(sum(r["share"] for r in d["rows"]) - 1.0) < 0.001


def test_ratios_are_ratios_of_sums(client, pages):
    a = top_ads(pages, 1)[0]
    assign(pages, a, "161085663758792")
    pages.commit()
    d = get(client, pages)
    lon = next(r for r in d["rows"] if r["is_lonvera"])
    lo, hi = window(pages)
    s, imp, clk, pur, val = pages.execute("SELECT sum(spend), sum(impressions), sum(link_clicks), sum(meta_purchases), sum(meta_revenue) FROM analytics.meta_ad_daily WHERE ad_id = %s AND day BETWEEN %s AND %s", (a, lo, hi)).fetchone()
    s, imp, clk, pur, val = (float(x or 0) for x in (s, imp, clk, pur, val))
    assert lon["cpm"] == round(s * 1000 / imp, 2) and lon["ctr"] == round(clk / imp, 4) and lon["cpc"] == round(s / clk, 2)
    if pur:
        assert lon["cost_per_purchase"] == round(s / pur, 2) and lon["aov"] == round(val / pur, 2) and lon["conv_rate"] == round(pur / clk, 4) and lon["meta_roas"] == round(val / s, 2)


def test_product_and_stage_filters_narrow_the_total(client, pages):
    for ad in top_ads(pages, 3):
        assign(pages, ad, "161085663758792")
    pages.commit()
    full = get(client, pages)
    prods = full["products"]
    assert prods and all(p["slug"] for p in prods)
    one = get(client, pages, "&product=" + prods[0]["slug"])
    assert one["product"] == prods[0]["slug"] and one["total"]["spend"] <= full["total"]["spend"] + 0.01
    st = get(client, pages, "&stage=testing")
    assert st["total"]["spend"] <= full["total"]["spend"] + 0.01
    assert full["like_for_like"] and "consistent with" in full["like_for_like"] and "Meta-reported" in full["basis"]
