"""Trends, conversion rate by product: Meta purchases per link click and new customers per link click, by product and in total.

Own throwaway database. Every expected number is a plain-Python sum over the generator functions below, independent of app/trends.py. Also proves the numerator
and the denominator share ONE product mapping: purchases by product are read from ad_daily with etl.projection_pacing.classify_product_by_level (the function that
fills proj_feed_meta_product, whose link clicks are the denominator), and the product's spend, clicks and purchases all move together when a campaign is reclassified.
"""
from __future__ import annotations

import datetime as dt

import psycopg
import pytest

from app import trends as T
from etl import projection_pacing as PP

from . import seed_fixture as S
from .conftest import DB_NAME, PASSWORD, PG_BASE, make_app

D = dt.date
CV_DB = f"{DB_NAME}_convrate"
CV_URL = f"{PG_BASE}/{CV_DB}"
FIRST, LAST = D(2026, 10, 26), D(2026, 11, 22)
TODAY = D(2026, 11, 24)
W1, W2 = (D(2026, 11, 9), D(2026, 11, 15)), (D(2026, 11, 16), D(2026, 11, 22))
# (campaign id, campaign name, ad set name, product the rule must give)
CAMPS = [("c1", "VitaBull_Scaling", "adset a", "vitabull"), ("c2", "BeefLiver_Test_1", "adset b", "beefliver"), ("c3", "Boneflex_Scaling", "adset c", "boneflex"),
         ("c4", "All_US_Mixed", "Boneflex winners", "boneflex"),          # campaign name says nothing: the ad set name decides
         ("c5", "Vital Belle Scaling", "adset e", None), ("c6", "Unnamed Thing", "adset f", None)]   # outside the three products


def clicks(d, i): return 100 + 7 * i + d.day % 5
def purch(d, i):  return 2 + i + d.weekday() % 3
def spend(d, i):  return 50 + 10 * i + d.day % 4
def ncord(d, slug): return {"vitabull": 3, "beefliver": 2, "boneflex": 1}[slug] + d.weekday() % 2
def onc(d): return sum(ncord(d, s) for s in ("vitabull", "beefliver", "boneflex")) + 2          # 2 orders from products outside the three


def rng(a, b): return list(T.daterange(a, b))


@pytest.fixture(scope="module")
def cdb():
    try:
        S.recreate_db(f"{PG_BASE}/postgres", CV_DB)
        conn = psycopg.connect(CV_URL)
    except psycopg.OperationalError as e:
        pytest.skip(f"Postgres not available for API tests: {e}")
    with conn:
        S.apply_migrations(conn)
        days = rng(FIRST, LAST)
        with conn.cursor() as cur:
            for cid, cname, aset, _ in CAMPS:
                cur.execute("INSERT INTO meta_campaigns (campaign_id, account, name, stage) VALUES (%s,'Main',%s,'scaling')", (cid, cname))
                cur.execute("INSERT INTO meta_ads (ad_id, account, ad_name, meta_family_key, campaign_id, adset_name) VALUES (%s,'Main','F','f',%s,%s)", ("ad-" + cid, cid, aset))
            cur.executemany("""INSERT INTO ad_daily (ad_id, day, meta_family_key, meta_family, campaign_id, spend, purchases, conv_value, link_clicks, impressions, source)
                               VALUES (%s,%s,'f','F',%s,%s,%s,0,%s,1000,'meta_api')""",
                            [("ad-" + cid, d, cid, spend(d, i), purch(d, i), clicks(d, i)) for d in days for i, (cid, *_rest) in enumerate(CAMPS)])
            cur.executemany("INSERT INTO upstack_orders_daily (day, nc_rev, rc_rev, nc_orders, total_rev, orders) VALUES (%s,%s,100,%s,%s,%s)", [(d, 100 * onc(d), onc(d), 100 * onc(d) + 100, onc(d) + 3) for d in days])
            cur.executemany("INSERT INTO proj_feed_nc_product (day, product, nc_rev, nc_orders) VALUES (%s,%s,%s,%s)",
                            [(d, name, 100 * ncord(d, slug), ncord(d, slug)) for d in days for slug, name in T.PRODUCTS])
        PP.refresh_meta_from_app(conn, None)                 # builds proj_feed_meta_product exactly as production does
        conn.commit()
    return True


@pytest.fixture(scope="module")
def capp(cdb):
    a = make_app(DATABASE_URL=CV_URL)
    yield a
    a.extensions["pool"].close()


@pytest.fixture()
def cc(capp):
    c = capp.test_client()
    assert c.post("/api/login", json={"username": "JoshHoughton", "password": PASSWORD}).status_code == 204
    return c


def get(c, **kw):
    kw.setdefault("as_of", TODAY.isoformat())
    kw.setdefault("interval", "week")
    r = c.get("/api/trends", query_string=kw)
    assert r.status_code == 200, r.get_data(as_text=True)[:400]
    return r.get_json()


def week(d, start):
    return next(r for r in d["rows"] if r["start"] == start.isoformat())


def expected(a, b):
    """{slug: (meta purchases, clicks, nc orders)} by the fixture's own mapping (CAMPS), plus 'all'."""
    out = {s: [0, 0, 0] for s in ("vitabull", "beefliver", "boneflex", "other")}
    for d in rng(a, b):
        for i, (_c, _n, _a, slug) in enumerate(CAMPS):
            o = out[slug or "other"]
            o[0] += purch(d, i)
            o[1] += clicks(d, i)
        for s in ("vitabull", "beefliver", "boneflex"):
            out[s][2] += ncord(d, s)
    tot_p = sum(v[0] for v in out.values())
    tot_c = sum(v[1] for v in out.values())
    return out, tot_p, tot_c, sum(onc(d) for d in rng(a, b))


@pytest.mark.parametrize("span", [W1, W2])
def test_closed_weeks_tie_to_hand_computed_sums_to_the_fourth_decimal(cc, span):
    d = get(cc)
    row = week(d, span[0])
    out, tp, tc, nc = expected(*span)
    for slug in ("vitabull", "beefliver", "boneflex"):
        p, c, o = out[slug]
        assert row["v"][f"p_{slug}_cvr_meta"] == pytest.approx(p / c, abs=1e-6)
        assert row["v"][f"p_{slug}_cvr_nc"] == pytest.approx(o / c, abs=1e-6)
    assert row["v"]["pc_meta_total"] == pytest.approx(tp / tc, abs=1e-6)
    assert row["v"]["pc_nc_total"] == pytest.approx(nc / tc, abs=1e-6)
    # the three products plus 'Other' clicks are exactly the total clicks: the splits are not lossy
    assert sum(v[1] for v in out.values()) == tc == round(row["v"]["m_clicks"])


def test_numerator_and_denominator_share_one_product_mapping(cc, cdb):
    """The fixture mapping (CAMPS) is the rule's mapping, the feed's per-product clicks equal the ad_daily clicks classified by the same function, and the page's
    purchases-by-product use that function too: one mapping for spend, clicks and purchases."""
    rules = PP.DEFAULT_COMPILED
    for _cid, cname, aset, slug in CAMPS:
        want = {"vitabull": "VitaBull", "beefliver": "Beef Liver", "boneflex": "Boneflex", None: "Other"}[slug]
        assert T.product_of(cname, aset, rules) == want == PP.classify_product_by_level(cname, aset, rules)
    with psycopg.connect(CV_URL) as conn:
        feed = dict(conn.execute("SELECT product, sum(link_clicks) FROM proj_feed_meta_product WHERE source='app' AND day BETWEEN %s AND %s GROUP BY 1", W1).fetchall())
        fr, present = T.fetch_frame(conn_rows(conn), W1[0], W1[1], T_cov(conn))
    out, *_ = expected(*W1)
    for slug, name in T.PRODUCTS:
        assert float(feed[name]) == out[slug][1]                                                   # denominator = classified link clicks
        s = T.sums(fr, *W1)
        assert s[f"{slug}_clk"] == out[slug][1] and s[f"{slug}_mp"] == out[slug][0] and s[f"{slug}_ncord"] == out[slug][2]   # numerators


def conn_rows(conn):
    from psycopg.rows import dict_row
    conn.row_factory = dict_row
    return conn


def T_cov(conn):
    return T.coverage(conn, TODAY)


def test_dashes_before_the_data_and_period_change_is_percentage_points(cc):
    d = get(cc, interval="week", periods=8)
    first = week(d, D(2026, 10, 26))                     # first loaded week: fine
    assert first["v"]["p_vitabull_cvr_meta"] is not None
    cur, prev = week(d, W2[0]), week(d, W1[0])
    ch = cur["d"]["p_vitabull_cvr_meta"]
    assert ch["t"] == "pp" and round(ch["v"], 5) == round(cur["v"]["p_vitabull_cvr_meta"] - prev["v"]["p_vitabull_cvr_meta"], 5)
    m = get(cc, interval="month", periods=3)
    sep = next(r for r in m["rows"] if r["start"] == "2026-10-01")
    assert sep["v"]["p_vitabull_cvr_meta"] is None and "starts Oct 26, 2026" in sep["why"]["p_vitabull_cvr_meta"]     # month begins before the data
    assert sep["v"]["p_vitabull_cvr_nc"] is None


def test_layout_titles_group_and_captions(cc):
    d = get(cc)
    by = {b["key"]: b for b in d["layout"]}
    assert by["prod_cvr_meta"]["title"] == "CONVERSION RATE, META (PURCHASES PER LINK CLICK)" and by["prod_cvr_nc"]["title"] == "CONVERSION RATE, NEW CUSTOMERS PER LINK CLICK"
    for k in ("prod_cvr_meta", "prod_cvr_nc"):
        assert by[k]["group"] == "prodconv" and [c["label"] for c in by[k]["cols"]] == ["VitaBull", "Beef Liver", "Boneflex", "Total"]
    g = next(x for x in d["groups"] if x["key"] == "prodconv")
    assert g["default"] is True and g["label"] == "Conversion by product" and len(g["metrics"]) == 8
    assert {m["key"]: m["kind"] for m in d["metrics"] if m["key"].endswith(("_cvr_meta", "_cvr_nc")) or m["key"].startswith("pc_")} and all(
        m["change"] == "pp" for m in d["metrics"] if m["key"].startswith("pc_") or "_cvr_" in m["key"])
    assert "Meta-reported" in d["captions"]["prod_cvr_meta"] and "Shopify first orders via Upstack" in d["captions"]["prod_cvr_nc"]
    for text in d["captions"].values():
        assert "—" not in text
