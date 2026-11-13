"""Amazon keyword layer (/api/amazon/keywords, ad-search-terms, ad-search-terms/matches, negatives; app/amazon.py, tables of migration 252, views of 253).

Seed facts. TARGETING Oct 25 - Nov 7 (14 days): five keywords or targets with per-day numbers (impressions, clicks, cost, purchases, sales)
  111 'beef liver capsules' EXACT, ad group ag2 (Beef Liver)      100, 10, 20.0, 2, 60.0
  222 'vital bull' PHRASE, ag1 (Vita Bull)                          50,  5,  5.0, 1, 40.0
  333 'organ' BROAD, ag3 (two products: mixed)                      10,  2,  3.0, 0,  0.0
  444 'loose-match' automatic target, ag4 (its ASIN has no product) 20,  1,  1.0, 0,  0.0
  555 'ghost' BROAD, ag9 (no product ad on file)                     5,  0,  0.0, 0,  0.0
  plus 666 'idle keyword' (paused, bid 0.50) that is only in the keyword list. The keyword list has two snapshots (Nov 1: 111 bid 1.00; Nov 5: 111 bid 1.50, 333 paused): the latest wins.
SEARCH TERMS Oct 31 - Nov 7 (8 days): 'beef liver capsules' served by 111 (ag2: 30, 3, 6.0, 1, 30.0) and, typed as 'Beef  Liver Capsules', by 333 (ag3, mixed: 10, 1, 2.0, 0, 0.0); 'liver pills' by 222 (ag1: 20, 2, 4.0, 1, 20.0).
SEARCH QUERY PERFORMANCE weeks Oct 25 and Nov 1: 'beef liver capsules' for two Beef Liver ASINs (500 + 250 clicks of 5000, 40 + 20 purchases of 400) and one Vita Bull ASIN (100 clicks, 8 purchases); 'liver pills' is not listed.
NEGATIVES snapshot Nov 5: six rows (one archived, two exact); an older snapshot holds one more that must not appear."""
import datetime as dt

import psycopg
import pytest

from .conftest import DB_URL
from .helpers import get_ok
from .test_api_amazon import TABLES as AMAZON_TABLES, _sqp

D = dt.date
TG_DAYS = [D(2026, 10, 25) + dt.timedelta(days=i) for i in range(14)]
ST_DAYS = [D(2026, 10, 31) + dt.timedelta(days=i) for i in range(8)]
KW_TABLES = ("amazon_ads_search_term_daily", "amazon_ads_targeting_daily", "amazon_ads_keywords_snapshot", "amazon_ads_targets_snapshot", "amazon_ads_negative_keywords_snapshot",
             "amazon_ads_campaigns_snapshot", "amazon_ads_product_ads")
ALL = tuple(AMAZON_TABLES)           # includes the keyword tables
W = "from=2026-10-25&to=2026-11-07"
# (keyword id, text, match type, campaign, ad group, impressions, clicks, cost, purchases, sales) per day
TG = [("111", "beef liver capsules", "EXACT", "c1", "ag2", 100, 10, 20.0, 2, 60.0), ("222", "vital bull", "PHRASE", "c1", "ag1", 50, 5, 5.0, 1, 40.0), ("333", "organ", "BROAD", "c2", "ag3", 10, 2, 3.0, 0, 0.0),
      ("444", "loose-match", "TARGETING_EXPRESSION_PREDEFINED", "c3", "ag4", 20, 1, 1.0, 0, 0.0), ("555", "ghost", "BROAD", "c9", "ag9", 5, 0, 0.0, 0, 0.0)]
# (term, keyword id, keyword, match type, campaign, ad group, impressions, clicks, cost, purchases, sales) per day
ST = [("beef liver capsules", "111", "beef liver capsules", "EXACT", "c1", "ag2", 30, 3, 6.0, 1, 30.0), ("Beef  Liver Capsules", "333", "organ", "BROAD", "c2", "ag3", 10, 1, 2.0, 0, 0.0),
      ("liver pills", "222", "vital bull", "PHRASE", "c1", "ag1", 20, 2, 4.0, 1, 20.0)]


def _wipe(c, tables=ALL):
    for t in tables:
        c.execute(f"TRUNCATE {t}")
    c.commit()


@pytest.fixture(scope="module")
def kw(app, fx):
    with psycopg.connect(DB_URL) as c:
        _wipe(c)
        def ins(table, **cols):
            c.execute(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})", list(cols.values()))
        for asin, slug in (("A1", "vita-bull"), ("A2", "beef-liver"), ("A3", "beef-liver"), ("A4", "boneflex"), ("A5", None)):
            ins("amazon_asin_products", asin=asin, sku="SKU-" + asin, product_slug=slug, product_name=slug, friendly_name=asin, refreshed_on=D(2026, 11, 7))
        for ad, ag, camp, asin in (("pa1", "ag1", "c1", "A1"), ("pa2", "ag2", "c1", "A2"), ("pa3", "ag2", "c1", "A3"), ("pa4", "ag3", "c2", "A1"), ("pa5", "ag3", "c2", "A4"), ("pa6", "ag4", "c3", "A5")):
            ins("amazon_ads_product_ads", ad_id=ad, ad_group_id=ag, campaign_id=camp, asin=asin, sku="SKU-" + asin, state="ENABLED", snapshot_date=D(2026, 11, 5))
        for d in TG_DAYS:
            for kid, text, mt, camp, ag, imp, clk, cost, pur, sales in TG:
                ins("amazon_ads_targeting_daily", day=d, campaign_id=camp, ad_group_id=ag, keyword_id=kid, match_type=mt, targeting=text, campaign_name=f"Camp {camp}", ad_group_name=f"AG {ag}",
                    keyword=text, impressions=imp, clicks=clk, cost=cost, purchases_7d=pur, sales_7d=sales)
        for d in ST_DAYS:
            for term, kid, kwd, mt, camp, ag, imp, clk, cost, pur, sales in ST:
                ins("amazon_ads_search_term_daily", day=d, campaign_id=camp, ad_group_id=ag, keyword_id=kid, match_type=mt, search_term=term, search_term_key=" ".join(term.lower().split()),
                    campaign_name=f"Camp {camp}", ad_group_name=f"AG {ag}", keyword=kwd, impressions=imp, clicks=clk, cost=cost, purchases_7d=pur, sales_7d=sales)
        for snap, rows in ((D(2026, 11, 1), [("111", "ag2", "c1", "beef liver capsules", "EXACT", 1.00, "ENABLED"), ("222", "ag1", "c1", "vital bull", "PHRASE", 2.00, "ENABLED"), ("333", "ag3", "c2", "organ", "BROAD", 0.80, "ENABLED")]),
                           (D(2026, 11, 5), [("111", "ag2", "c1", "beef liver capsules", "EXACT", 1.50, "ENABLED"), ("222", "ag1", "c1", "vital bull", "PHRASE", 2.00, "ENABLED"),
                                             ("333", "ag3", "c2", "organ", "BROAD", 0.80, "PAUSED"), ("666", "ag1", "c1", "idle keyword", "BROAD", 0.50, "PAUSED")])):
            for kid, ag, camp, text, mt, bid, st in rows:
                ins("amazon_ads_keywords_snapshot", snapshot_date=snap, keyword_id=kid, ad_group_id=ag, campaign_id=camp, keyword_text=text, match_type=mt, bid=bid, state=st)
        ins("amazon_ads_targets_snapshot", snapshot_date=D(2026, 11, 5), target_id="444", ad_group_id="ag4", campaign_id="c3", bid=None, state="ENABLED", expression_type="AUTO", expression_text="QUERY_HIGH_REL_MATCHES")
        for camp, name in (("c1", "Campaign One"), ("c2", "Campaign Two"), ("c3", "Campaign Three")):
            ins("amazon_ads_campaigns_snapshot", snapshot_date=D(2026, 11, 5), campaign_id=camp, name=name, state="ENABLED")
        for snap, rows in ((D(2026, 11, 1), [("n99", "ag1", "c1", "stale", "NEGATIVE_PHRASE", "ENABLED")]),
                           (D(2026, 11, 5), [("n1", "ag1", "c1", "free", "NEGATIVE_PHRASE", "ENABLED"), ("n2", "ag1", "c1", "cheap", "NEGATIVE_PHRASE", "ENABLED"), ("n3", "ag2", "c1", "gnc", "NEGATIVE_EXACT", "ENABLED"),
                                             ("n4", "ag3", "c2", "free shipping", "NEGATIVE_PHRASE", "ENABLED"), ("n5", "ag2", "c1", "old thing", "NEGATIVE_PHRASE", "ARCHIVED"),
                                             ("n6", "ag9", "c9", "zzz", "NEGATIVE_EXACT", "ENABLED")])):
            for kid, ag, camp, text, mt, st in rows:
                ins("amazon_ads_negative_keywords_snapshot", snapshot_date=snap, keyword_id=kid, ad_group_id=ag, campaign_id=camp, keyword_text=text, match_type=mt, state=st)
        for wk in (D(2026, 10, 25), D(2026, 11, 1)):
            mt = (200000, 5000, 1000, 400)
            for row in (_sqp("A2", "beef-liver", "beef liver capsules", wk, 0, 10000, mt, (20000, 500, 100, 40), text="Beef Liver Capsules"),
                        _sqp("A3", "beef-liver", "beef liver capsules", wk, 0, 10000, mt, (10000, 250, 50, 20), text="Beef Liver Capsules"),
                        _sqp("A1", "vita-bull", "beef liver capsules", wk, 0, 10000, mt, (4000, 100, 20, 8), text="Beef Liver Capsules")):
                ins("amazon_sqp_weekly", **row)
        c.commit()
    cl = app.test_client()
    from .conftest import PASSWORD
    assert cl.post("/api/login", json={"username": "JoshHoughton", "password": PASSWORD}).status_code == 204
    yield cl
    with psycopg.connect(DB_URL) as c:
        _wipe(c)


def a(client, path, qs=""):
    return get_ok(client, f"/api/amazon/{path}?{qs}")


def rows_by(d, key="keyword_id"):
    return {r[key]: r for r in d["rows"]}


# ---------------------------------------------------------------------------------------------------------------------------- routes
def test_routes_need_a_session(anon):
    for path in ("keywords", "ad-search-terms", "ad-search-terms/matches", "negatives"):
        assert anon.get(f"/api/amazon/{path}").status_code == 401


def test_bad_arguments_answer_400(kw):
    for url in ("keywords?product=nope", "keywords?state=archived", "ad-search-terms?product=nope", "ad-search-terms/matches", "negatives?match=fuzzy", "negatives?state=paused"):
        assert kw.get(f"/api/amazon/{url}&{W}" if "?" in url else f"/api/amazon/{url}?{W}").status_code == 400, url


# ---------------------------------------------------------------------------------------------------------------------------- keywords
def test_keyword_rows_are_range_sums_with_ratios_of_sums(kw):
    d = a(kw, "keywords", W)
    n = len(TG_DAYS)
    assert d["loaded"] is True and d["view"] == "keywords" and d["channel"] == "amazon" and d["window_info"]["channel"] == "amazon"
    r = rows_by(d)
    k1 = r["111"]
    assert (k1["impressions"], k1["clicks"], k1["cost"], k1["purchases"], k1["sales"]) == (100 * n, 10 * n, 20.0 * n, 2 * n, 60.0 * n)
    assert k1["ctr"] == pytest.approx(0.1) and k1["cpc"] == 2.0 and k1["conversion_rate"] == pytest.approx(0.2) and k1["return_on_ad_spend"] == 3.0 and k1["acos"] == pytest.approx(1 / 3, abs=1e-4)
    assert k1["text"] == "beef liver capsules" and k1["match_label"] == "Exact" and k1["campaign"] == "Camp c1" and k1["ad_group"] == "AG ag2"
    t = d["total"]
    assert (t["impressions"], t["clicks"], t["cost"], t["purchases"], t["sales"]) == (2590, 252, 406.0, 42, 1400.0) and t["return_on_ad_spend"] == pytest.approx(1400 / 406, abs=1e-4)
    assert t["acos"] == pytest.approx(406 / 1400, abs=1e-4) and d["total_rows"] == 5 and d["truncated"] is False
    assert [x["keyword_id"] for x in d["rows"]] == ["111", "222", "333", "444", "555"]                 # most cost first
    z = r["555"]
    assert z["cost"] == 0.0 and z["clicks"] == 0 and z["cpc"] is None and z["conversion_rate"] is None and z["return_on_ad_spend"] is None and z["acos"] is None      # a ratio of 0 / 0 is blank


def test_current_bid_and_state_come_from_the_latest_snapshot(kw):
    r = rows_by(a(kw, "keywords", W))
    assert r["111"]["bid"] == 1.5 and r["111"]["state"] == "enabled"                                     # Nov 5 snapshot, not the Nov 1 bid of 1.00
    assert r["333"]["state"] == "paused" and r["333"]["bid"] == 0.8
    assert r["444"]["bid"] is None and r["444"]["bid_inherited"] is True and r["444"]["state"] == "enabled" and r["444"]["match_label"] == "Automatic target"
    assert r["555"]["state"] is None and r["555"]["bid"] is None                                         # in the report, not in any list: removed since
    d = a(kw, "keywords", W)
    assert "snapshot" in d["bids_note"] and "Nov 5, 2026" in d["bids_note"]


def test_product_comes_from_the_ad_group_and_mixed_is_not_guessed(kw):
    d = a(kw, "keywords", W)
    r = rows_by(d)
    assert [r[k]["product"] for k in ("111", "222", "333", "444", "555")] == ["beef-liver", "vita-bull", "mixed", "unmapped", "unmapped"]
    assert r["333"]["product_label"] == "Mixed products" and r["444"]["product_label"] == "No product found"
    facets = {f["key"]: f["cost"] for f in d["facets"]["products"]}
    assert facets == {"vita-bull": 70.0, "beef-liver": 280.0, "mixed": 42.0, "unmapped": 14.0}
    only = a(kw, "keywords", W + "&product=mixed")
    assert [x["keyword_id"] for x in only["rows"]] == ["333"] and only["total"]["cost"] == 42.0 and only["total_rows"] == 1
    assert [x["keyword_id"] for x in a(kw, "keywords", W + "&product=unmapped")["rows"]] == ["444", "555"]


def test_keywords_with_no_impressions_are_listed_on_request_with_zeros(kw):
    d = a(kw, "keywords", W + "&all=1")
    r = rows_by(d)
    assert set(r) == {"111", "222", "333", "444", "555", "666"} and d["total_rows"] == 6
    i = r["666"]
    assert i["impressions"] == 0 and i["cost"] == 0.0 and i["ctr"] is None and i["bid"] == 0.5 and i["state"] == "paused" and i["campaign"] == "Campaign One" and i["product"] == "vita-bull"
    assert a(kw, "keywords", W)["scope"]["with_impressions"] is True and d["scope"]["with_impressions"] is False


def test_keyword_filters_state_and_text(kw):
    assert [x["keyword_id"] for x in a(kw, "keywords", W + "&all=1&state=paused")["rows"]] == ["333", "666"]
    assert [x["keyword_id"] for x in a(kw, "keywords", W + "&q=VITAL+BULL")["rows"]] == ["222"]                 # the text is normalised like a search term
    assert [x["keyword_id"] for x in a(kw, "keywords", W + "&q=ag3")["rows"]] == ["333"]                      # ad group names are searched too
    assert a(kw, "keywords", W + "&q=zzzz")["rows"] == []


def test_keyword_window_is_inside_the_data(kw):
    d = a(kw, "keywords", "from=2026-11-05&to=2026-11-07")
    r = rows_by(d)
    assert r["111"]["impressions"] == 300 and r["111"]["cost"] == 60.0
    assert d["range"] == {"from": "2026-11-05", "to": "2026-11-07"}


def test_every_keyword_table_says_what_it_is_made_of(kw):
    for url in (f"keywords?{W}", f"ad-search-terms?{W}", "negatives"):
        d = a(kw, url.split("?")[0], url.split("?")[1] if "?" in url else "")
        t = d["coverage_text"]
        assert t.startswith("Sponsored Products only, Amazon's 7-day attribution, search terms from Oct 31, 2026 and targeting from Oct 25, 2026, last loaded ") and t.endswith(".") and "—" not in t
    with psycopg.connect(DB_URL) as c:
        loaded = c.execute("SELECT (max(loaded_at) AT TIME ZONE 'America/Los_Angeles')::date FROM amazon_ads_targeting_daily").fetchone()[0]
    assert d["coverage"]["last_loaded"] == loaded.isoformat() and f"{loaded:%b} {loaded.day}, {loaded.year}" in d["coverage_text"]


# ---------------------------------------------------------------------------------------------------------------------------- ad search terms
def test_a_search_term_is_one_row_across_campaigns_and_normalised(kw):
    d = a(kw, "ad-search-terms", W)
    n = len(ST_DAYS)
    r = rows_by(d, "key")
    assert set(r) == {"beef liver capsules", "liver pills"} and d["total_terms"] == 2
    b = r["beef liver capsules"]
    assert b["term"] == "beef liver capsules" and b["keywords"] == 2 and b["campaigns"] == 2                      # 'Beef  Liver Capsules' joined the same row
    assert (b["impressions"], b["clicks"], b["cost"], b["purchases"], b["sales"]) == (40 * n, 4 * n, 8.0 * n, 1 * n, 30.0 * n)
    assert b["cpc"] == 2.0 and b["conversion_rate"] == pytest.approx(0.25) and b["return_on_ad_spend"] == pytest.approx(3.75) and b["acos"] == pytest.approx(8 / 30, abs=1e-4)
    assert d["total"]["cost"] == 12.0 * n and [x["key"] for x in d["rows"]] == ["beef liver capsules", "liver pills"]


def test_organic_columns_are_search_query_performance_and_blank_where_it_has_no_row(kw):
    d = a(kw, "ad-search-terms", W)
    r = rows_by(d, "key")
    b, p = r["beef liver capsules"], r["liver pills"]
    assert b["search_volume"] == 10000 and b["our_click_share"] == 17.0 and b["our_purchase_share"] == 17.0 and b["weeks_listed"] == 1 and b["weeks_in_range"] == 1     # MAX market, SUM ours, recomputed; the range is clipped to the search-term days (Oct 31 on), so one full week (Nov 1) is in it
    assert p["search_volume"] is None and p["our_click_share"] is None and p["our_purchase_share"] is None and p["weeks_listed"] is None            # blank, never zero
    assert d["organic_terms"] == 1 and d["organic_cost_share"] == pytest.approx(64 / 96, abs=1e-4)
    assert "not the same as zero" in d["note"]


def test_a_product_filter_reads_that_products_search_query_rows(kw):
    d = a(kw, "ad-search-terms", W + "&product=beef-liver")
    assert [x["key"] for x in d["rows"]] == ["beef liver capsules"]
    b = d["rows"][0]
    assert b["cost"] == 6.0 * len(ST_DAYS) and b["keywords"] == 1 and b["our_click_share"] == 15.0 and b["our_purchase_share"] == 15.0          # the two Beef Liver ASINs only
    m = a(kw, "ad-search-terms", W + "&product=mixed")
    assert [x["key"] for x in m["rows"]] == ["beef liver capsules"] and m["rows"][0]["cost"] == 2.0 * len(ST_DAYS)
    assert m["rows"][0]["our_click_share"] == 17.0 and "no single product" in m["note"]                                                        # a mixed ad group reads all products
    assert a(kw, "ad-search-terms", W + "&q=pills")["rows"][0]["key"] == "liver pills"


def test_no_full_week_means_blank_organic_columns(kw):
    d = a(kw, "ad-search-terms", "from=2026-11-02&to=2026-11-07")                                              # Monday to Saturday: no Sunday to Saturday week inside
    assert d["weeks_in_range"] == 0 and all(r["search_volume"] is None for r in d["rows"]) and "No full week" in d["note"]


def test_the_matches_of_one_term(kw):
    d = a(kw, "ad-search-terms/matches", W + "&term=BEEF+LIVER++capsules")
    assert d["key"] == "beef liver capsules" and len(d["rows"]) == 2
    r = {x["text"]: x for x in d["rows"]}
    assert r["beef liver capsules"]["match_label"] == "Exact" and r["beef liver capsules"]["product"] == "beef-liver" and r["beef liver capsules"]["cost"] == 6.0 * len(ST_DAYS)
    assert r["organ"]["product"] == "mixed" and r["organ"]["campaign"] == "Camp c2"
    assert d["total"]["cost"] == 8.0 * len(ST_DAYS) and a(kw, "ad-search-terms/matches", W + "&term=nothing+here")["rows"] == []


# ---------------------------------------------------------------------------------------------------------------------------- negatives
def test_negatives_are_the_latest_snapshot_with_counts(kw):
    d = a(kw, "negatives")
    assert d["matched"] == 6 and d["total"] == 6 and d["shown"] == 6 and d["truncated"] is False and d["snapshot_date"] == "2026-11-05"
    r = rows_by(d, "key")
    assert "n99" not in r and r["n1"]["text"] == "free" and r["n1"]["campaign"] == "Campaign One" and r["n1"]["ad_group"] == "AG ag1" and r["n1"]["product"] == "vita-bull"
    assert r["n3"]["match_label"] == "Negative exact" and r["n5"]["state"] == "archived" and r["n4"]["product"] == "mixed" and r["n6"]["product"] == "unmapped"
    assert d["facets"]["states"] == {"enabled": 5, "archived": 1} and d["facets"]["match"] == {"NEGATIVE_PHRASE": 4, "NEGATIVE_EXACT": 2}
    assert [x["text"] for x in d["rows"]] == ["cheap", "free", "free shipping", "gnc", "old thing", "zzz"]
    assert "Campaign-level negatives" in d["note"]


def test_negative_filters_search_and_limit(kw):
    assert [x["key"] for x in a(kw, "negatives", "q=FREE")["rows"]] == ["n1", "n4"]
    assert [x["key"] for x in a(kw, "negatives", "q=campaign+two")["rows"]] == ["n4"]                          # the campaign name is searched too
    assert {x["key"] for x in a(kw, "negatives", "match=exact")["rows"]} == {"n3", "n6"}
    assert [x["key"] for x in a(kw, "negatives", "state=archived")["rows"]] == ["n5"]
    assert [x["key"] for x in a(kw, "negatives", "product=mixed")["rows"]] == ["n4"]
    d = a(kw, "negatives", "limit=2")
    assert d["matched"] == 6 and d["shown"] == 2 and d["truncated"] is True and len(d["rows"]) == 2
    f = a(kw, "negatives", "q=free&match=exact")
    assert f["matched"] == 0 and f["rows"] == [] and f["facets"]["match"] == {"NEGATIVE_PHRASE": 2}               # the facets follow the text, not the other chips


# ---------------------------------------------------------------------------------------------------------------------------- the analytics views
@pytest.fixture()
def pgq(kw):
    with psycopg.connect(DB_URL) as c:
        yield lambda sql, *p: c.execute(sql, p).fetchall()


def test_ad_group_products_view_resolves_single_mixed_and_unmapped(pgq):
    got = {r[0]: r[1:] for r in pgq("SELECT ad_group_id, product_slug, product_resolution, asins, products FROM analytics.amazon_ads_ad_group_products")}
    assert got == {"ag1": ("vita-bull", "single", 1, 1), "ag2": ("beef-liver", "single", 2, 1), "ag3": (None, "mixed", 2, 2), "ag4": (None, "unmapped", 1, 0)}
    rows = pgq("SELECT product_resolution, count(*), sum(cost) FROM analytics.amazon_ads_targeting_daily GROUP BY 1 ORDER BY 1")
    assert [(r[0], r[1], float(r[2])) for r in rows] == [("mixed", 14, 42.0), ("single", 28, 350.0), ("unmapped", 28, 14.0)]       # ag9 has no product ad: unmapped
    assert pgq("SELECT product_resolution FROM analytics.amazon_ads_keywords_snapshot WHERE keyword_id = '333' LIMIT 1")[0][0] == "mixed"
    assert pgq("SELECT product_slug, product_resolution FROM analytics.amazon_ads_campaigns_snapshot WHERE campaign_id = 'c2'")[0] == (None, "mixed")
    assert pgq("SELECT product_slug FROM analytics.amazon_ads_campaigns_snapshot WHERE campaign_id = 'c1'")[0][0] is None            # c1 advertises two products across its ad groups


def test_paid_versus_organic_view_joins_on_week_product_and_normalised_term(pgq):
    rows = pgq("""SELECT product_slug, organic_scope, cost, our_click_share, our_purchase_share, search_volume, has_organic_row
                  FROM analytics.amazon_ads_search_terms_vs_organic WHERE week_start = '2026-11-01' AND search_term_key = 'beef liver capsules' ORDER BY product_slug NULLS LAST""")
    assert [(r[0], r[1], float(r[2]), float(r[3]), float(r[4]), r[5], r[6]) for r in rows] == [("beef-liver", "product", 42.0, 15.0, 15.0, 10000, True), (None, "all products", 14.0, 17.0, 17.0, 10000, True)]
    p = pgq("SELECT cost, our_click_share, search_volume, has_organic_row FROM analytics.amazon_ads_search_terms_vs_organic WHERE search_term_key = 'liver pills' AND week_start = '2026-11-01'")[0]
    assert float(p[0]) == 28.0 and p[1] is None and p[2] is None and p[3] is False                                  # blank, not zero
    assert float(pgq("SELECT cost FROM analytics.amazon_ads_search_terms_vs_organic WHERE week_start = '2026-10-25' AND product_slug = 'beef-liver'")[0][0]) == 6.0     # Oct 31 only: Sunday-start weeks
    assert float(pgq("SELECT sum(cost) FROM analytics.amazon_ads_search_terms_vs_organic")[0][0]) == pytest.approx(12.0 * len(ST_DAYS))


def test_new_views_are_in_the_catalog_under_the_amazon_domain(pgq):
    names = {r[0] for r in pgq("SELECT view_name FROM analytics.catalog WHERE domain = 'amazon'")}
    assert {"amazon_ads_search_terms_daily", "amazon_ads_targeting_daily", "amazon_ads_keywords_snapshot", "amazon_ads_targets_snapshot", "amazon_ads_negative_keywords_snapshot",
            "amazon_ads_campaigns_snapshot", "amazon_ads_product_ads", "amazon_ads_ad_group_products", "amazon_ads_search_terms_vs_organic"} <= names
