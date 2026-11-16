"""Products without attribution (/api/no-attribution/products, app/no_attribution.py): Meta spend per product beside actual new-customer revenue.

Own throwaway database. Seed (integers, so every expected number is a plain sum computed here):
  spend feed  Oct 1 .. Nov 9 (VitaBull, Beef Liver, Boneflex, Other; plus 'Vital Belle' rows that must fold into Other; a 'sheet' row on every 'app' day that must be ignored,
              and a sheet-only day (Sep 30) with no app row that must count)
  NC feed     Sep 30 .. Nov 7 (the last two days of spend have no orders yet)
  orders      upstack_orders_daily Oct 1 .. Nov 7 = the products' revenue + a small restatement
Meta's last loaded day (app_meta data_through) is Nov 9, so the span the view answers for is Sep 30 .. Nov 7.
"""
from __future__ import annotations

import datetime as dt

import psycopg
import pytest

from . import seed_fixture as S
from .conftest import DB_NAME, PASSWORD, PG_BASE, make_app

D = dt.date
NA_DB = f"{DB_NAME}_noattr"
NA_URL = f"{PG_BASE}/{NA_DB}"
SP_FIRST, NC_FIRST, SP_LAST, NC_LAST = D(2026, 9, 30), D(2026, 9, 30), D(2026, 11, 9), D(2026, 11, 7)
NAMES = ["VitaBull", "Beef Liver", "Boneflex", "Other"]


def rng(a, b):
    return [a + dt.timedelta(days=i) for i in range((b - a).days + 1)]


def spend(d, i):   return 100 * (i + 1) + d.day                        # Meta spend of product i on day d
def clicks(d, i):  return 10 * (i + 1) + d.weekday()
def rev(d, i):     return 200 * (i + 1) + 7 * d.weekday()               # actual new-customer revenue
def orders(d, i):  return 2 + i + d.weekday() % 2
def belle_rev(d):  return 50 + d.day                                    # 'Vital Belle' revenue, folded into Other
def belle_sp(d):   return 11


def total(f, a, b, idx=None):
    return sum(f(d, i) for d in rng(a, b) for i in ([idx] if idx is not None else range(4)))


@pytest.fixture(scope="module")
def na_db():
    try:
        S.recreate_db(f"{PG_BASE}/postgres", NA_DB)
        conn = psycopg.connect(NA_URL)
    except psycopg.OperationalError as e:
        pytest.skip(f"Postgres not available for API tests: {e}")
    with conn:
        S.apply_migrations(conn)
        with conn.cursor() as cur:
            cur.executemany("INSERT INTO proj_feed_meta_product (day, product, source, spend, link_clicks) VALUES (%s,%s,%s,%s,%s)",
                            [(d, NAMES[i], "sheet" if d == SP_FIRST else "app", spend(d, i), clicks(d, i)) for d in rng(SP_FIRST, SP_LAST) for i in range(4)]
                            + [(d, "Vital Belle", "sheet" if d == SP_FIRST else "app", belle_sp(d), 0) for d in rng(SP_FIRST, SP_LAST)]
                            + [(d, "VitaBull", "sheet", 99999, 0) for d in rng(D(2026, 10, 1), SP_LAST)])          # ignored: those days have app rows
            cur.executemany("INSERT INTO proj_feed_nc_product (day, product, nc_rev, nc_orders) VALUES (%s,%s,%s,%s)",
                            [(d, NAMES[i], rev(d, i), orders(d, i)) for d in rng(NC_FIRST, NC_LAST) for i in range(4)]
                            + [(d, "Vital Belle", belle_rev(d), 1) for d in rng(NC_FIRST, NC_LAST)])
            cur.executemany("INSERT INTO upstack_orders_daily (day, nc_rev, rc_rev, nc_orders, total_rev, orders) VALUES (%s,%s,0,0,%s,0)",
                            [(d, sum(rev(d, i) for i in range(4)) + belle_rev(d) + 1, 1) for d in rng(D(2026, 10, 1), NC_LAST)])
        conn.execute("INSERT INTO app_meta (key, value) VALUES ('data_through', %s), ('daily_start', '2026-09-30')", (SP_LAST.isoformat(),))
        conn.commit()
    return True


@pytest.fixture(scope="module")
def napp(na_db):
    a = make_app(DATABASE_URL=NA_URL)
    yield a
    a.extensions["pool"].close()


@pytest.fixture()
def nc(napp):
    c = napp.test_client()
    assert c.post("/api/login", json={"username": "JoshHoughton", "password": PASSWORD}).status_code == 204
    return c


def get(c, qs=""):
    r = c.get("/api/no-attribution/products" + ("?" + qs if qs else ""))
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    return r.get_json()


def row(d, key):
    return next(r for r in d["rows"] if r["key"] == key)


def approx(a, b, tol=1e-9):
    return a is not None and abs(a - b) <= tol * max(1, abs(b))


def test_rows_are_the_three_products_then_other_and_a_total(nc):
    d = get(nc, "from=2026-10-10&to=2026-10-16")
    assert [r["key"] for r in d["rows"]] == ["vita-bull", "beef-liver", "boneflex", "other"]
    assert [r["name"] for r in d["rows"]] == ["Vita Bull", "Beef Liver", "Boneflex", "Other products"]
    assert [r["slug"] for r in d["rows"]] == ["vita-bull", "beef-liver", "boneflex", None]


def test_numbers_are_sums_and_ratios_come_from_sums(nc):
    a, b = D(2026, 10, 10), D(2026, 10, 16)
    d = get(nc, f"from={a}&to={b}")
    for i, key in enumerate(["vita-bull", "beef-liver", "boneflex"]):
        r = row(d, key)
        sp, rv, od, ck = total(spend, a, b, i), total(rev, a, b, i), total(orders, a, b, i), total(clicks, a, b, i)
        assert (r["spend"], r["nc_revenue"], r["nc_orders"], r["link_clicks"]) == (sp, rv, od, ck)
        assert approx(r["nc_roas"], rv / sp) and approx(r["nc_cpa"], sp / od) and approx(r["aov"], rv / od) and approx(r["cpc"], sp / ck) and approx(r["conv_rate"], od / ck)
    t = d["total"]
    assert t["spend"] == sum(r["spend"] for r in d["rows"]) and t["nc_revenue"] == sum(r["nc_revenue"] for r in d["rows"])
    assert approx(t["nc_roas"], t["nc_revenue"] / t["spend"]) and approx(t["nc_cpa"], t["spend"] / t["nc_orders"])
    assert approx(sum(r["spend_share"] for r in d["rows"]), 1.0) and approx(sum(r["nc_revenue_share"] for r in d["rows"]), 1.0)


def test_other_collects_the_other_bucket_and_unknown_products(nc):
    a, b = D(2026, 10, 10), D(2026, 10, 16)
    o = row(get(nc, f"from={a}&to={b}"), "other")
    assert o["spend"] == total(spend, a, b, 3) + sum(belle_sp(d) for d in rng(a, b))
    assert o["nc_revenue"] == total(rev, a, b, 3) + sum(belle_rev(d) for d in rng(a, b))
    assert o["nc_orders"] == total(orders, a, b, 3) + 7


def test_app_days_win_over_sheet_rows_and_a_sheet_only_day_counts(nc):
    d = get(nc, "from=2026-09-30&to=2026-10-02")          # Sep 30 has only sheet rows; Oct 1-2 also carry a 99999 sheet row that must be ignored
    assert row(d, "vita-bull")["spend"] == total(spend, D(2026, 9, 30), D(2026, 10, 2), 0)


def test_window_is_cut_to_the_days_both_feeds_hold(nc):
    d = get(nc, "window=7d")
    assert d["data_through"] == "2026-11-07" and d["window_info"]["to"] == "2026-11-07" and d["window_info"]["from"] == "2026-11-01"
    assert row(d, "vita-bull")["spend"] == total(spend, D(2026, 11, 1), D(2026, 11, 7), 0)          # Nov 8-9 spend is not counted: those days have no orders yet
    assert d["window_info"]["source"]["label"] == "No attribution" and d["compare_info"] is None


def test_since_launch_is_every_day_held(nc):
    d = get(nc, "window=launch")
    assert d["window_info"]["kind"] == "launch" and d["window_info"]["label"] == "All days held · Sep 30, 2026 – Nov 7, 2026" and d["data_from"] == "2026-09-30"
    assert row(d, "beef-liver")["nc_revenue"] == total(rev, NC_FIRST, NC_LAST, 1)


def test_check_against_total_new_customer_revenue(nc):
    a, b = D(2026, 10, 10), D(2026, 10, 16)
    c = get(nc, f"from={a}&to={b}")["check"]
    prod = sum(total(rev, a, b, i) for i in range(4)) + sum(belle_rev(d) for d in rng(a, b))
    assert c["products_nc_revenue"] == prod and c["orders_daily_nc_revenue"] == prod + 7
    assert approx(c["diff_pct"], -7 / (prod + 7))


def test_ignores_attribution_parameters(nc):
    base = get(nc, "from=2026-10-10&to=2026-10-16")
    other = get(nc, "from=2026-10-10&to=2026-10-16&source=upstack&basis=all&stage=testing&active=1&compare=prev")
    assert other["rows"] == base["rows"] and other["total"] == base["total"] and other["compare_info"] is None


def test_requires_login(napp):
    assert napp.test_client().get("/api/no-attribution/products").status_code == 401


def test_empty_feeds_answer_with_a_message(napp):
    S.recreate_db(f"{PG_BASE}/postgres", NA_DB + "_empty")
    url = f"{PG_BASE}/{NA_DB}_empty"
    with psycopg.connect(url) as c:
        S.apply_migrations(c)
        c.execute("INSERT INTO app_meta (key, value) VALUES ('data_through','2026-11-09'), ('daily_start','2026-09-30')")
        c.commit()
    a = make_app(DATABASE_URL=url)
    try:
        cl = a.test_client()
        assert cl.post("/api/login", json={"username": "JoshHoughton", "password": PASSWORD}).status_code == 204
        d = cl.get("/api/no-attribution/products").get_json()
        assert d["loaded"] is False and d["rows"] == [] and "not loaded" in d["message"]
    finally:
        a.extensions["pool"].close()
