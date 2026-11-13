"""Meta breakdowns: the two read endpoints (/api/meta/breakdowns, /api/meta/breakdowns/coverage) and the analytics view analytics.meta_breakdown_daily."""
import datetime as dt

import psycopg
import pytest

from app import queries as q

D = dt.date
VIEW_COLS = ["day", "account", "campaign_id", "adset_id", "ad_id", "breakdown", "value", "spend", "impressions", "reach", "link_clicks", "unique_outbound_clicks", "purchases",
             "purchase_value", "add_to_carts", "initiated_checkouts"]
MAIN, BACKUP = "act_1671338033619223", "act_1543579384022317"
TEN = {"user_segment_key": "audience_segment", "age": "age", "gender": "gender", "age,gender": "age_gender", "country": "country", "region": "region",
       "publisher_platform": "publisher_platform", "platform_position": "platform_position", "device_platform": "device_platform",
       "hourly_stats_aggregated_by_advertiser_time_zone": "hourly", "impression_device": "impression_device", "publisher_platform,platform_position": "placement",
       "publisher_platform,platform_position,impression_device": "placement_device"}


def ins(conn, day, acct, level, eid, camp, adset, bset, dims, spend=10, imps=1000, reach=800, clicks=40, link=30, ob=25, uob=20, purch=2, val=100):
    vals = list(dims) + [None] * (3 - len(dims))
    key = "|".join(f"{n}={v}" for n, v in zip(bset.split(","), dims))
    conn.execute("INSERT INTO meta_breakdown_daily (day, account_id, level, entity_id, campaign_id, adset_id, breakdown_set, dim1, dim2, dim3, dims_key, spend, impressions, reach, "
                 "clicks, inline_link_clicks, outbound_clicks, unique_outbound_clicks, purchases, purchase_value) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                 (day, acct, level, eid, camp, adset, bset, *vals, key, spend, imps, reach, clicks, link, ob, uob, purch, val))


def cov(conn, bset, level, status="ok", rows=1, first=None, last=None, err=None):
    conn.execute("INSERT INTO meta_breakdown_coverage (breakdown_set, level, status, first_day, last_day, last_error, rows) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                 (bset, level, status, first, last, err, rows))


@pytest.fixture()
def mb(pg, fx):
    """Rows for tests; removed afterwards. Ads: one that belongs to the vita-bull product and one that does not."""
    pid = fx.product_ids["vita-bull"]
    inside = pg.execute("WITH " + q._pfa(pid).replace("%(prod)s", str(pid)) + " SELECT ad_id, campaign_id FROM meta_ads WHERE meta_family_key IN (SELECT meta_family_key FROM pfa) "
                        "AND campaign_id IS NOT NULL ORDER BY ad_id LIMIT 1").fetchone()
    outside = pg.execute("WITH " + q._pfa(pid).replace("%(prod)s", str(pid)) + " SELECT ad_id, campaign_id FROM meta_ads WHERE meta_family_key NOT IN (SELECT meta_family_key FROM pfa) "
                         "AND campaign_id IS NOT NULL AND campaign_id <> %s ORDER BY ad_id LIMIT 1", (inside[1],)).fetchone()
    pg.execute("DELETE FROM meta_breakdown_daily")
    pg.execute("DELETE FROM meta_breakdown_coverage")
    pg.commit()
    yield {"in": inside, "out": outside}
    pg.rollback()
    pg.execute("DELETE FROM meta_breakdown_daily")
    pg.execute("DELETE FROM meta_breakdown_coverage")
    pg.commit()


def seed_age(pg, mb):
    ia, ic = mb["in"]
    oa, oc = mb["out"]
    for d in (D(2026, 11, 1), D(2026, 11, 2)):
        ins(pg, d, MAIN, "ad", ia, ic, "s1", "age", ["25-34"], spend=10, imps=1000, purch=2, val=100)
        ins(pg, d, MAIN, "ad", ia, ic, "s1", "age", ["35-44"], spend=30, imps=2000, purch=0, val=0, clicks=None, link=None)
        ins(pg, d, BACKUP, "ad", oa, oc, "s9", "age", ["25-34"], spend=5, imps=500, purch=1, val=40)
    cov(pg, "age", "ad", rows=6, first=D(2026, 11, 1), last=D(2026, 11, 2))
    pg.commit()


def test_coverage_lists_every_set_and_level(client, pg, mb):
    cov(pg, "age", "ad", rows=12, first=D(2026, 10, 1), last=D(2026, 11, 1))
    cov(pg, "region", "ad", "unsupported", 0, err="(#100) Invalid parameter")
    cov(pg, "region", "campaign", rows=5, first=D(2026, 10, 20), last=D(2026, 11, 1))
    pg.commit()
    d = client.get("/api/meta/breakdowns/coverage").get_json()
    assert d["count"] == 3 and d["with_data"] == 2
    by = {(r["breakdown_set"], r["level"]): r for r in d["rows"]}
    assert by[("region", "ad")]["status"] == "unsupported" and by[("region", "ad")]["last_error"].startswith("(#100)")
    assert by[("age", "ad")]["first_day"] == "2026-10-01" and by[("age", "ad")]["rows"] == 12
    assert [(r["breakdown_set"], r["level"]) for r in d["rows"]] == sorted(by)
    assert client.get("/api/meta/breakdowns/coverage").status_code == 200


def test_requires_login(anon):
    assert anon.get("/api/meta/breakdowns?set=age").status_code in (401, 403)
    assert anon.get("/api/meta/breakdowns/coverage").status_code in (401, 403)


def test_aggregates_over_the_range_with_derived_ratios(client, pg, mb):
    seed_age(pg, mb)
    d = client.get("/api/meta/breakdowns?set=age&start=2026-11-01&end=2026-11-02").get_json()
    assert d["level"] == "ad" and d["dimensions"] == ["age"] and d["product"] is None and not d["truncated"]
    rows = {r["dims"]["age"]: r for r in d["rows"]}
    a = rows["25-34"]
    assert a["spend"] == 30 and a["impressions"] == 3000 and a["purchases"] == 6 and a["revenue"] == 280 and a["clicks"] == 160 and a["link_clicks"] == 120 and a["source_rows"] == 4
    assert a["roas"] == 9.333 and a["cost_per_purchase"] == 5.0 and a["cost_per_1000_impressions"] == 10.0 and a["click_rate"] == 0.0533 and a["link_click_rate"] == 0.04
    b = rows["35-44"]
    assert b["spend"] == 60 and b["purchases"] == 0 and b["cost_per_purchase"] is None and b["roas"] == 0 and b["clicks"] is None and b["click_rate"] is None
    assert [r["dims"]["age"] for r in d["rows"]] == ["35-44", "25-34"]                    # highest spend first
    assert "reach" not in a and "frequency" not in a and "reach" in d["note"].lower()


def test_default_range_is_the_last_seven_days_held(client, pg, mb):
    seed_age(pg, mb)
    d = client.get("/api/meta/breakdowns?set=age").get_json()
    assert (d["start"], d["end"]) == ("2026-11-01", "2026-11-02")
    one = client.get("/api/meta/breakdowns?set=age&start=2026-11-02&end=2026-11-02").get_json()
    assert {r["dims"]["age"]: r["spend"] for r in one["rows"]} == {"25-34": 15, "35-44": 30}


def test_product_and_campaign_filters(client, pg, mb):
    seed_age(pg, mb)
    p = client.get("/api/meta/breakdowns?set=age&product=vita-bull").get_json()
    assert p["product"] == "vita-bull" and {r["dims"]["age"]: r["spend"] for r in p["rows"]} == {"25-34": 20, "35-44": 60}
    c = client.get(f"/api/meta/breakdowns?set=age&campaign_id={mb['out'][1]}").get_json()
    assert {r["dims"]["age"]: r["spend"] for r in c["rows"]} == {"25-34": 10}
    both = client.get(f"/api/meta/breakdowns?set=age&product=vita-bull&campaign_id={mb['out'][1]}").get_json()
    assert both["rows"] == []
    assert client.get("/api/meta/breakdowns?set=age&product=nope").status_code == 404


def test_multi_dimension_set_and_plus_sign(client, pg, mb):
    ia, ic = mb["in"]
    ins(pg, D(2026, 11, 1), MAIN, "ad", ia, ic, "s1", "age,gender", ["25-34", "female"], spend=7)
    ins(pg, D(2026, 11, 1), MAIN, "ad", ia, ic, "s1", "age,gender", ["25-34", "male"], spend=3)
    cov(pg, "age,gender", "ad", rows=2, first=D(2026, 11, 1), last=D(2026, 11, 1))
    pg.commit()
    for s in ("age,gender", "age+gender"):                                                  # a plus in a URL arrives as a space
        d = client.get(f"/api/meta/breakdowns?set={s}").get_json()
        assert d["set"] == "age,gender" and d["dimensions"] == ["age", "gender"]
        assert d["rows"][0]["dims"] == {"age": "25-34", "gender": "female"} and d["rows"][0]["spend"] == 7


def test_fallback_level_is_picked_and_account_rows_reject_product_filter(client, pg, mb):
    ia, ic = mb["in"]
    ins(pg, D(2026, 11, 1), MAIN, "campaign", ic, ic, None, "dma", ["Los Angeles"], spend=9)
    ins(pg, D(2026, 11, 1), MAIN, "account", MAIN, None, None, "hourly_stats_aggregated_by_advertiser_time_zone", ["00:00:00 - 00:59:59"], spend=2)
    cov(pg, "dma", "ad", "unsupported", 0)
    cov(pg, "dma", "campaign", rows=1, first=D(2026, 11, 1), last=D(2026, 11, 1))
    cov(pg, "hourly_stats_aggregated_by_advertiser_time_zone", "account", rows=1, first=D(2026, 11, 1), last=D(2026, 11, 1))
    pg.commit()
    d = client.get("/api/meta/breakdowns?set=dma").get_json()
    assert d["level"] == "campaign" and d["rows"][0]["spend"] == 9
    assert client.get("/api/meta/breakdowns?set=dma&product=vita-bull").get_json()["level"] == "campaign"
    h = "/api/meta/breakdowns?set=hourly_stats_aggregated_by_advertiser_time_zone"
    assert client.get(h).get_json()["level"] == "account"
    assert client.get(h + "&product=vita-bull").status_code == 400 and client.get(h + "&campaign_id=x").status_code == 400


def test_validation(client, pg, mb):
    seed_age(pg, mb)
    assert client.get("/api/meta/breakdowns").status_code == 400
    assert client.get("/api/meta/breakdowns?set=nope").status_code == 404
    assert client.get("/api/meta/breakdowns?set=age&level=weird").status_code == 400
    assert client.get("/api/meta/breakdowns?set=age&level=campaign").status_code == 404
    assert client.get("/api/meta/breakdowns?set=age&start=x").status_code == 400
    assert client.get("/api/meta/breakdowns?set=age&start=2026-11-03&end=2026-11-01").status_code == 400
    for method in ("post", "put", "patch", "delete"):
        assert getattr(client, method)("/api/meta/breakdowns", json={}).status_code == 405
        assert getattr(client, method)("/api/meta/breakdowns/coverage", json={}).status_code == 405


# ------------------------------------------------------------------------------------------------ analytics.meta_breakdown_daily
def test_view_has_exactly_the_agreed_columns(pg):
    cols = [r[0] for r in pg.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='analytics' AND table_name='meta_breakdown_daily' ORDER BY ordinal_position")]
    assert cols == VIEW_COLS


def test_view_holds_only_the_thirteen_breakdowns_at_ad_grain(pg, mb):
    ia, ic = mb["in"]
    for i, (bset, name) in enumerate(TEN.items()):
        dims = ["25-34", "female"] if bset == "age,gender" else [f"v{i}", f"w{i}", f"x{i}"][:len(bset.split(","))]
        ins(pg, D(2026, 11, 1), MAIN, "ad", ia, ic, "s1", bset, dims, spend=i + 1)
    ins(pg, D(2026, 11, 1), MAIN, "ad", ia, ic, "s1", "image_asset", ["a"], spend=99)               # not one of the ten
    ins(pg, D(2026, 11, 1), MAIN, "ad", ia, ic, "s1", "action_device", ["iphone"], spend=None, imps=None, reach=None, clicks=None, link=None, ob=None, uob=None)
    pg.commit()
    rows = pg.execute("SELECT breakdown, value, ad_id, adset_id, campaign_id, account, spend, reach, link_clicks, unique_outbound_clicks, purchases, purchase_value "
                      "FROM analytics.meta_breakdown_daily ORDER BY spend").fetchall()
    assert [r[0] for r in rows] == list(TEN.values())
    by = {r[0]: r for r in rows}
    assert by["age_gender"][1] == "25-34|female" and by["age"][1] == "v1"
    assert by["placement"][1] == "v11|w11" and by["placement_device"][1] == "v12|w12|x12" and by["impression_device"][1] == "v10"
    r = by["age"]
    assert r[2:6] == (ia, "s1", ic, "Lonvera Main") and r[7] == 800 and r[8] == 30 and r[9] == 20 and r[10] == 2 and r[11] == 100
    assert 99 not in [x[6] for x in rows]


def test_view_value_formats_and_higher_level_rows(pg, mb):
    ia, ic = mb["in"]
    ins(pg, D(2026, 11, 1), BACKUP, "ad", ia, ic, "s1", "age,gender", ["45-54", "male"])
    ins(pg, D(2026, 11, 1), MAIN, "campaign", ic, ic, None, "region", ["Texas"])
    ins(pg, D(2026, 11, 1), "act_other", "account", "act_other", None, None, "hourly_stats_aggregated_by_advertiser_time_zone", ["03:00:00 - 03:59:59"])
    pg.commit()
    rows = {r[0]: r for r in pg.execute("SELECT breakdown, value, ad_id, adset_id, campaign_id, account FROM analytics.meta_breakdown_daily")}
    assert rows["age_gender"][1] == "45-54|male" and rows["age_gender"][5] == "Lonvera BackUp"
    assert rows["region"][2:5] == (None, None, ic)                                          # Meta refused ad level: campaign row, ad and ad set empty
    assert rows["hourly"][2:6] == (None, None, None, "act_other") and rows["hourly"][1] == "03:00:00 - 03:59:59"


def test_view_is_not_aggregated_above_ad(pg, mb):
    ia, ic = mb["in"]
    ins(pg, D(2026, 11, 1), MAIN, "ad", ia, ic, "s1", "age", ["25-34"], spend=1)
    ins(pg, D(2026, 11, 1), MAIN, "ad", "other-ad", ic, "s1", "age", ["25-34"], spend=2)
    pg.commit()
    assert pg.execute("SELECT count(*) FROM analytics.meta_breakdown_daily WHERE breakdown='age'").fetchone()[0] == 2


def test_view_is_readable_by_the_analytics_reader_role(pg):
    if not pg.execute("SELECT 1 FROM pg_roles WHERE rolname='analytics_reader'").fetchone():
        pytest.skip("no analytics_reader role in this local database (the migration grants it when the role exists)")
    assert pg.execute("SELECT has_table_privilege('analytics_reader', 'analytics.meta_breakdown_daily', 'SELECT')").fetchone()[0]
    assert not pg.execute("SELECT has_table_privilege('analytics_reader', 'public.meta_breakdown_daily', 'SELECT')").fetchone()[0]


def test_view_and_columns_are_commented_and_state_the_ad_level_rule(pg):
    assert pg.execute("SELECT obj_description('analytics.meta_breakdown_daily'::regclass, 'pg_class')").fetchone()[0]
    missing = [r[0] for r in pg.execute("SELECT a.attname FROM pg_attribute a WHERE a.attrelid = 'analytics.meta_breakdown_daily'::regclass AND a.attnum > 0 "
                                        "AND col_description(a.attrelid, a.attnum) IS NULL")]
    assert missing == []
    text = pg.execute("SELECT obj_description('analytics.meta_breakdown_daily'::regclass, 'pg_class')").fetchone()[0]
    assert "ad level" in text.lower() and "ad_id" in text and "NULL" in text
    assert "ad level" in pg.execute("SELECT col_description('analytics.meta_breakdown_daily'::regclass, 6)").fetchone()[0].lower()
