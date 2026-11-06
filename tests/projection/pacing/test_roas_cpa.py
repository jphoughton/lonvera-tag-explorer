"""Projected NC ROAS and NC CPA: the order-count share curve, the projection math (against plain-Python oracles), GET /intraday-roascpa and the third card.
Pinned date 2026-11-03 (Tuesday); the closed-day window is Oct 4 .. Nov 2."""
import datetime as dt

import psycopg
import pytest

from app.projection.pacing import nc_curve, roas_cpa
from test_pacing_live_today import PINNED, put, snaps                      # noqa: F401  (fixture: empty live snapshot table)
from test_pacing_nc_today import nc                                         # noqa: F401  (fixture: 35 days of a fixed hourly shape, orders 1 per active hour)
from test_pacing_page import base_url, browser, open_page                   # noqa: F401

BASE = "/api/projection/pacing"
TODAY = dt.date(2026, 11, 3)
FLAT = [1.0] * 24                                                           # even Meta spend through the day


def rows_weekday_vs_weekend():
    """30 closed days. Weekday: revenue 100 in hour 8 and 100 in hour 20, 1 order in hour 8 and 3 in hour 20.
    Weekend: revenue 100 in hour 8 and 100 in hour 20, 3 orders in hour 8 and 1 in hour 20 (order shape differs from the revenue shape)."""
    out = []
    for i in range(1, 31):
        d = TODAY - dt.timedelta(days=i)
        wk = d.weekday() < 5
        out += [(d, 8, 100.0, 1 if wk else 3), (d, 20, 100.0, 3 if wk else 1)]
    return out


def oracle_order_share(rows, today, hours, same):
    """Independent: fraction of first orders in the window that land before `hours`, linear inside the hour."""
    days = {today - dt.timedelta(days=i) for i in range(1, 31)}
    tot = early = 0.0
    for d, h, rev, o in rows:
        if d not in days or (same and d.weekday() != today.weekday()):
            continue
        tot += o
        if h + 1 <= hours:
            early += o
        elif h < hours:
            early += o * (hours - h)
    return early / tot


# ---------------------------------------------------------------------------------------------------------------- order share curve

def test_order_curve_is_its_own_share_not_the_revenue_share():
    rows = rows_weekday_vs_weekend()
    r = nc_curve.build_orders(rows, TODAY, 12.0, 10, "all")
    want = oracle_order_share(rows, TODAY, 12.0, False)
    assert r["share"] == pytest.approx(want)
    assert r["projected_eod"] == pytest.approx(10 / want)
    rev_share = nc_curve.build(rows, TODAY, 12.0, 100.0, "all")["share"]
    assert rev_share == pytest.approx(0.5) and r["share"] != pytest.approx(rev_share)


def test_order_curve_same_weekday_toggle_uses_only_tuesdays():
    rows = rows_weekday_vs_weekend()
    allr = nc_curve.build_orders(rows, TODAY, 12.0, 10, "all")
    same = nc_curve.build_orders(rows, TODAY, 12.0, 10, "same")
    assert same["share"] == pytest.approx(oracle_order_share(rows, TODAY, 12.0, True)) == pytest.approx(0.25)       # Tuesdays: 1 of 4 orders is in by noon
    assert same["share"] != pytest.approx(allr["share"]) and same["days_used"] == 4


def test_order_curve_ignores_today_and_older_than_30_days():
    rows = rows_weekday_vs_weekend() + [(TODAY, 8, 1e6, 1e6), (TODAY - dt.timedelta(days=31), 8, 1, 1e6)]
    assert nc_curve.build_orders(rows, TODAY, 12.0, 10)["share"] == pytest.approx(oracle_order_share(rows, TODAY, 12.0, False))


def test_order_curve_early_morning_zero_share_gives_no_projection():
    r = nc_curve.build_orders(rows_weekday_vs_weekend(), TODAY, 6.0, 2)           # nothing lands before hour 8 on a typical day
    assert r["loaded"] and r["share"] == 0.0 and r["projected_eod"] is None and "Too early" in r["reason"]


def test_order_curve_needs_three_closed_days():
    rows = [r for r in rows_weekday_vs_weekend() if r[0] >= TODAY - dt.timedelta(days=2)]
    r = nc_curve.build_orders(rows, TODAY, 12.0, 2)
    assert r["loaded"] is False and r["projected_eod"] is None and "at least 3" in r["reason"]


# ---------------------------------------------------------------------------------------------------------------- projection math

def proj(**kw):
    base = dict(meta_hourly=FLAT, meta_spent=500.0, meta_hours=12.0, nc_rows=rows_weekday_vs_weekend(), today=TODAY, nc_rev=100.0, nc_orders=2,
                nc_hours=12.0, google=20.0, dow="all")
    base.update(kw)
    return roas_cpa.project(**base)


def test_projection_formulas_against_a_plain_python_oracle():
    r = proj()
    meta_eod = 500.0 / 0.5                                                    # even spend, noon = half the day
    google_eod = 20.0 * meta_eod / 500.0
    rev_eod = 100.0 / 0.5                                                     # revenue: hours 8 in, hour 20 out = 50%
    ord_eod = 2 / oracle_order_share(rows_weekday_vs_weekend(), TODAY, 12.0, False)
    total = meta_eod + google_eod
    assert (r["meta_eod"], r["google_eod"], r["total_spend_eod"]) == (pytest.approx(1000.0), pytest.approx(40.0), pytest.approx(total))
    assert r["nc_rev_eod"] == pytest.approx(rev_eod) and r["nc_orders_eod"] == pytest.approx(ord_eod)
    assert r["nc_roas_eod"] == pytest.approx(rev_eod / total) and r["nc_cpa_eod"] == pytest.approx(total / ord_eod)
    assert r["nc_roas_so_far"] == pytest.approx(100.0 / 520.0) and r["nc_cpa_so_far"] == pytest.approx(520.0 / 2) and r["google_included"] is True


def test_same_weekday_toggle_changes_the_order_projection():
    assert proj(dow="same")["nc_orders_eod"] == pytest.approx(2 / 0.25) and proj(dow="all")["nc_orders_eod"] != pytest.approx(8.0)


def test_typed_override_mixing_uses_each_value_where_it_belongs():
    live = proj()
    typed = proj(nc_rev=150.0)                                               # NC revenue typed over the live value, the other inputs stay live
    assert typed["meta_eod"] == live["meta_eod"] and typed["nc_orders_eod"] == live["nc_orders_eod"] and typed["total_spend_eod"] == live["total_spend_eod"]
    assert typed["nc_rev_eod"] == pytest.approx(300.0) and typed["nc_roas_eod"] == pytest.approx(300.0 / live["total_spend_eod"])
    meta_typed = proj(meta_spent=600.0)
    assert meta_typed["nc_rev_eod"] == live["nc_rev_eod"] and meta_typed["total_spend_eod"] == pytest.approx(1200.0 + 20.0 * 1200.0 / 600.0)


def test_early_morning_zero_share_means_no_projection_not_a_guess():
    r = proj(nc_hours=6.0, meta_hours=6.0)
    assert r["nc_rev_eod"] is None and r["nc_orders_eod"] is None and r["nc_roas_eod"] is None and r["nc_cpa_eod"] is None
    assert proj(meta_hours=0.0)["meta_eod"] is None and proj(meta_hours=0.0)["nc_roas_eod"] is None


def test_zero_new_customers_so_far_projects_zero_and_cpa_is_a_dash():
    r = proj(nc_orders=0)
    assert r["nc_orders_eod"] == 0.0 and r["nc_cpa_eod"] is None and r["nc_cpa_so_far"] is None and r["nc_roas_eod"] is not None


def test_missing_google_projects_meta_only_and_says_so():
    r = proj(google=None)
    assert r["google_included"] is False and r["google_eod"] is None and r["total_spend_eod"] == pytest.approx(1000.0)
    assert r["nc_roas_eod"] == pytest.approx(r["nc_rev_eod"] / 1000.0) and r["total_spend_so_far"] == 500.0
    zero = proj(google=0.0)
    assert zero["google_included"] is True and zero["google_eod"] == 0.0 and zero["total_spend_eod"] == pytest.approx(1000.0)


def test_missing_inputs_show_nothing():
    assert proj(meta_spent=None)["nc_roas_eod"] is None and proj(meta_spent=None)["total_spend_eod"] is None
    assert proj(nc_rev=None)["nc_roas_eod"] is None and proj(nc_rev=None)["nc_cpa_eod"] is not None
    assert proj(nc_orders=None)["nc_cpa_eod"] is None and proj(nc_orders=None)["nc_roas_eod"] is not None
    assert proj(meta_hourly=[0.0] * 24)["nc_roas_eod"] is None                 # no Meta curve loaded: no spend projection, no ROAS
    assert proj(meta_spent=0.0, google=5.0)["google_eod"] is None             # no ratio to scale Google by


# ---------------------------------------------------------------------------------------------------------------- endpoint

def meta_curve(conn):
    hourly = [0.0] * 24
    for r in conn.execute("SELECT hour, spend_30d FROM proj_pacing_hourly"):
        hourly[r["hour"]] = float(r["spend_30d"])
    return hourly


def test_endpoint_matches_the_pure_functions(api, nc, conn):               # noqa: F811
    q = "meta=7528&google=310.5&nc_rev=450&nc_orders=7&meta_time=12:30&nc_time=12:30"
    j = api.get(f"{BASE}/intraday-roascpa?{q}").get_json()
    sh = sum(meta_curve(conn)[:12]) / sum(meta_curve(conn)) + meta_curve(conn)[12] / sum(meta_curve(conn)) * 0.5
    meta_eod = 7528 / sh
    total = meta_eod * (1 + 310.5 / 7528)
    assert j["meta_eod"] == pytest.approx(meta_eod) and j["total_spend_eod"] == pytest.approx(total)
    assert j["nc_rev_eod"] == pytest.approx(1000.0)                          # 450 at 12:30, the fixture curve is 45% in
    assert j["nc_orders_eod"] == pytest.approx(7 / 0.50)                      # orders: hours 7, 9 and half of 12 = 2.5 of 5 order-hours
    assert j["nc_roas_eod"] == pytest.approx(1000.0 / total) and j["nc_cpa_eod"] == pytest.approx(total / 14.0)
    assert j["inputs"]["dow"] == "all" and j["as_of"] == "2026-11-03"


def test_endpoint_validation_and_empty_inputs(api, nc):                     # noqa: F811
    for q in ("meta=abc", "google=-1", "nc_orders=1e12", "meta_time=noon", "nc_time=25:00", "dow=weekend"):
        assert api.get(f"{BASE}/intraday-roascpa?{q}").status_code == 400, q
    j = api.get(f"{BASE}/intraday-roascpa?meta_time=12:00&nc_time=12:00").get_json()
    assert j["nc_roas_eod"] is None and j["nc_cpa_eod"] is None and j["total_spend_eod"] is None


def test_endpoint_needs_login_and_is_read_only(api_app, api):
    assert api_app.test_client().get(f"{BASE}/intraday-roascpa").status_code == 401
    assert api.post(f"{BASE}/intraday-roascpa", json={}).status_code in (404, 405)


def test_endpoint_without_the_hourly_table_projects_nothing(api, pacing_db):
    with psycopg.connect(pacing_db) as c:
        c.execute("DELETE FROM proj_feed_nc_hourly")
        c.commit()
    j = api.get(f"{BASE}/intraday-roascpa?meta=100&nc_rev=10&nc_orders=1&meta_time=12:00&nc_time=12:00").get_json()
    assert j["nc_rev_eod"] is None and j["nc_roas_eod"] is None


@pytest.fixture()
def last28(pacing_db):
    """28 closed days Oct 6..Nov 2: Meta 1,000 + Google 50 spend, 700 NC revenue, 10 new customers a day. The three Trends tables are not in the pacing
    test database, so they are created here (same columns the Trends frame reads) and dropped afterwards."""
    ddl = ["CREATE TABLE IF NOT EXISTS ad_daily_upstack (day date, nc_purchases numeric DEFAULT 0, nc_conv_value numeric DEFAULT 0, purchases numeric DEFAULT 0, conv_value numeric DEFAULT 0)",
           "CREATE TABLE IF NOT EXISTS google_daily (day date, campaign_key text, spend numeric DEFAULT 0, impressions numeric DEFAULT 0, clicks numeric DEFAULT 0, purchases numeric DEFAULT 0, "
           "purchases_value numeric DEFAULT 0, nc numeric DEFAULT 0, nc_rev numeric DEFAULT 0, orders numeric DEFAULT 0, rev numeric DEFAULT 0)",
           "CREATE TABLE IF NOT EXISTS upstack_orders_daily (day date PRIMARY KEY, nc_rev numeric DEFAULT 0, rc_rev numeric DEFAULT 0, nc_orders numeric DEFAULT 0, total_rev numeric DEFAULT 0)"]
    with psycopg.connect(pacing_db) as c:
        for q in ddl:
            c.execute(q)
        for i in range(1, 29):
            d = TODAY - dt.timedelta(days=i)
            c.execute("INSERT INTO ad_daily (ad_id, day, meta_family_key, meta_family, spend, source) VALUES ('t1', %s, 'f', 'f', 1000, 'test')", (d,))
            c.execute("INSERT INTO google_daily (day, campaign_key, spend) VALUES (%s, 'g', 50)", (d,))
            c.execute("INSERT INTO upstack_orders_daily (day, nc_rev, nc_orders) VALUES (%s, 700, 10)", (d,))
        c.commit()
    yield
    with psycopg.connect(pacing_db) as c:
        c.execute("DELETE FROM ad_daily")
        for t in ("ad_daily_upstack", "google_daily", "upstack_orders_daily"):
            c.execute(f"DROP TABLE IF EXISTS {t}")
        c.commit()


def test_reference_is_the_trailing_28_closed_days_of_the_trends_totals(api, last28):
    ref = api.get(f"{BASE}/intraday-roascpa").get_json()["reference_28"]
    assert (ref["from"], ref["to"], ref["days"]) == ("2026-10-06", "2026-11-02", 28)
    assert ref["nc_roas"] == pytest.approx(700 / 1050) and ref["nc_cpa"] == pytest.approx(1050 / 10)
    assert ref["total_spend"] == pytest.approx(28 * 1050) and ref["new_customers"] == pytest.approx(280)


def test_reference_is_none_when_the_sources_are_empty(api):
    assert api.get(f"{BASE}/intraday-roascpa").get_json()["reference_28"] is None


# ---------------------------------------------------------------------------------------------------------------- the card

BOX = "[data-box='roascpa']"
META = "[data-box='intraday']"
NCB = "[data-box='intraday-nc']"


def card(page):
    return page.locator(f"{BOX} .pacing-roascpa-out").inner_text()


def wait_for(page, text):
    page.wait_for_function("(t) => document.querySelector(\"[data-box='roascpa'] .pacing-roascpa-out\").innerText.includes(t)", arg=text)


def test_card_with_live_values(browser, base_url, snaps, nc, last28):          # noqa: F811
    put(snaps, minutes_ago=10)                                                   # 12:30 PT: Meta 7,528, Google 310.50, NC revenue 450, 7 new customers
    ctx, page, errors = open_page(browser, base_url)
    wait_for(page, "Projected NC ROAS")
    t = card(page)
    assert "Projected NC ROAS" in t and "x*" in t and "Projected NC CPA $" in t and "NC ROAS so far 0.06x" in t and "NC CPA so far $1,119.79" in t
    assert "vs last 28 days: 0.67x / $105.00 (2026-10-06 to 2026-11-02)." in t
    assert "Google has no hourly curve" in t and "scaled by the same" in t
    assert page.input_value(f"{BOX} input[aria-label='Google spend so far today']") == "310.5"
    assert page.input_value(f"{BOX} input[aria-label='New customers so far today']") == "7"
    j = page.evaluate("fetch('/api/projection/pacing/intraday-roascpa?meta=7528&google=310.5&nc_rev=450&nc_orders=7&meta_time=12:30&nc_time=12:30').then(r => r.json())")
    assert f"Projected NC ROAS {j['nc_roas_eod']:.2f}x*" in t and f"Projected NC CPA ${j['nc_cpa_eod']:,.2f}*" in t
    assert page.locator(f"{BOX} .pg-source").inner_text().count("30 to 60 minutes") == 1
    assert errors == []
    ctx.close()


def test_card_override_in_the_nc_card_uses_the_typed_value_and_live_for_the_rest(browser, base_url, snaps, nc, last28):     # noqa: F811
    put(snaps, minutes_ago=10)
    ctx, page, errors = open_page(browser, base_url)
    wait_for(page, "Projected NC ROAS")
    before = page.evaluate("fetch('/api/projection/pacing/intraday-roascpa?meta=7528&google=310.5&nc_rev=450&nc_orders=7&meta_time=12:30&nc_time=12:30').then(r => r.json())")
    page.fill(f"{NCB} input[type='number']", "900")
    want = page.evaluate("fetch('/api/projection/pacing/intraday-roascpa?meta=7528&google=310.5&nc_rev=900&nc_orders=7&meta_time=12:30&nc_time=12:30').then(r => r.json())")
    wait_for(page, f"Projected NC ROAS {want['nc_roas_eod']:.2f}x*")
    assert want["nc_roas_eod"] == pytest.approx(2 * before["nc_roas_eod"])
    assert f"Projected NC CPA ${want['nc_cpa_eod']:,.2f}*" in card(page)
    page.fill(f"{BOX} input[aria-label='New customers so far today']", "14")        # and a typed customer count over the live one
    wait_for(page, f"Projected NC CPA ${want['nc_cpa_eod'] / 2:,.2f}*")
    assert errors == []
    ctx.close()


def test_card_empty_state_without_inputs(browser, base_url, snaps, nc):          # noqa: F811
    ctx, page, errors = open_page(browser, base_url)
    wait_for(page, "Enter Meta spend and new-customer revenue so far")
    assert "x*" not in card(page)
    page.fill(f"{META} input[type='number']", "7528")
    page.fill(f"{NCB} input[type='number']", "450")
    page.fill(f"{META} input[type='time']", "12:30")
    page.fill(f"{NCB} input[type='time']", "12:30")
    wait_for(page, "Projected NC ROAS")
    assert "Google spend is not included because none was given." in card(page)
    assert "vs last 28 days: not available." in card(page)
    assert errors == []
    ctx.close()


def test_card_zero_new_customers_shows_a_dash_for_cpa(browser, base_url, snaps, nc):      # noqa: F811
    put(snaps, minutes_ago=5, nc_orders=0)
    ctx, page, errors = open_page(browser, base_url)
    wait_for(page, "Projected NC ROAS")
    assert "Projected NC CPA - (no new customers projected yet)." in card(page) or "Projected NC CPA -" in card(page)
    ctx.close()


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_phone_width_375_no_sideways_scroll_and_readable_text(browser, base_url, snaps, nc, last28, scheme):      # noqa: F811
    put(snaps, minutes_ago=200)
    ctx, page, errors = open_page(browser, base_url, (375, 812), scheme)
    wait_for(page, "Projected NC ROAS")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    assert page.evaluate("(sel) => { const e = document.querySelector(sel); return e.scrollWidth <= e.clientWidth + 1; }", BOX)
    sizes = page.evaluate("(sel) => [...document.querySelectorAll(sel + ' p, ' + sel + ' label')].map(e => parseFloat(getComputedStyle(e).fontSize))", BOX)
    assert sizes and min(sizes) >= 12
    assert errors == []
    ctx.close()
