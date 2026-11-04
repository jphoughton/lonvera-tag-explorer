"""Read-only references beside the plan: realized CPA from the feeds and the Amazon feed run-rate."""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from conftest import put_cells, small_plan

from app.projection.drivers import reference

BASE = "/api/projection/drivers"
M = lambda y, m: date(y, m, 1)                # noqa: E731
TODAY = date(2026, 11, 3)


def days(a: date, b: date):
    d = a
    while d <= b:
        yield d
        d += timedelta(days=1)


def feeds(pg, *, nc=100.0, meta=8000.0, google=1000.0, amazon=5000.0, start=date(2026, 8, 1), end=date(2026, 11, 2)):
    with pg.cursor() as cur:
        cur.executemany("INSERT INTO proj_feed_orders (day, nc_orders) VALUES (%s,%s)", [(d, nc) for d in days(start, end)])
        cur.executemany("INSERT INTO proj_feed_amazon (day, revenue, ad_spend) VALUES (%s,%s,%s)", [(d, amazon, 700.0) for d in days(start, end)])
        cur.executemany("INSERT INTO proj_feed_platform (day, platform, spend) VALUES (%s,'Google Ads',%s)", [(d, google) for d in days(start, end)])
        cur.executemany("INSERT INTO ad_daily (ad_id, day, meta_family_key, meta_family, spend, source) VALUES ('a', %s, 'f', 'F', %s, 'meta_api')",
                        [(d, meta) for d in days(start, end)])


@pytest.fixture()
def world(rw, stub_world):
    return small_plan(rw)


def table(client):
    return client.get(f"{BASE}/table").get_json()


def row(t, key):
    return next((r for r in t["rows"] if r["key"] == key), None)


def at(t, key, month):
    return row(t, key)["values"][t["months"].index(month.isoformat())]


def test_without_feeds_there_are_no_reference_rows_and_the_chip_says_why(client, world):
    t = table(client)
    assert row(t, "ref_cpa") is None and row(t, "ref_amazon") is None and t["reference"]["amazon"] is None
    assert t["reference"]["cpa"]["chip"] == "Realized CPA unavailable: Shopify new-customer feed has no data" and t["reference"]["cpa"]["realized"] is None


def test_realized_cpa_by_month_and_the_last_28_days(client, world, pg):
    feeds(pg)                                                                  # (8,000 + 1,000) / 100 = $90 a customer, every day
    t = table(client)
    r = row(t, "ref_cpa")
    assert r["kind"] == "ratio" and r["fmt"] == "usd" and not r["input"] and r["label"] == "Realized CPA"
    assert at(t, "ref_cpa", M(2026, 9)) == pytest.approx(90.0) and at(t, "ref_cpa", M(2026, 10)) == pytest.approx(90.0)
    assert at(t, "ref_cpa", M(2026, 8)) == pytest.approx(90.0)
    assert at(t, "ref_cpa", M(2026, 11)) is None and at(t, "ref_cpa", M(2026, 7)) is None           # open month and months before the feed: blank
    ix = [x["key"] for x in t["rows"]]
    assert ix.index("ref_cpa") == ix.index("drivers.nc_cpa") + 1                                   # directly beneath the CPA input
    c = t["reference"]["cpa"]
    assert c["chip"] == "Plan $95 vs last 28 days $90" and c["realized"] == pytest.approx(90.0) and c["tone"] == "warn"      # 5.6% above, over the 5% band
    assert c["window"] == {"from": "2026-10-05", "to": "2026-11-01"}                                # 28 complete days, the newest feed day (Nov 2) dropped


def test_the_chip_is_quiet_inside_the_tolerance(client, world, pg):
    feeds(pg, nc=104.0)                                                         # $86.5 vs plan $95 is +9.8%: warn; with tolerance 0.15 it is info
    assert table(client)["reference"]["cpa"]["tone"] == "warn"
    r = client.put(f"{BASE}/levers", json={"scenario": "current", "levers": {"reference_tolerance": 0.15}})
    assert r.status_code == 200
    assert table(client)["reference"]["cpa"]["tone"] == "info"


def test_the_ad_spend_channels_lever_changes_the_realized_cpa(client, world, pg):
    feeds(pg)
    assert at(table(client), "ref_cpa", M(2026, 10)) == pytest.approx(90.0)
    client.put(f"{BASE}/levers", json={"scenario": "current", "levers": {"ad_spend_channels": ["meta"]}})
    assert at(table(client), "ref_cpa", M(2026, 10)) == pytest.approx(80.0)
    client.put(f"{BASE}/levers", json={"scenario": "current", "levers": {"ad_spend_channels": ["meta", "google", "amazon"]}})
    assert at(table(client), "ref_cpa", M(2026, 10)) == pytest.approx(97.0)


def test_the_window_length_is_a_lever(client, world, pg):
    feeds(pg)
    client.put(f"{BASE}/levers", json={"scenario": "current", "levers": {"reference_days": 14}})
    t = table(client)
    assert t["reference"]["cpa"]["window"] == {"from": "2026-10-19", "to": "2026-11-01"} and "last 14 days" in t["reference"]["cpa"]["chip"]
    assert row(t, "ref_amazon")["label"] == "Amazon feed run-rate (last 14 days)"


def test_a_month_with_missing_feed_days_has_no_realized_cpa(client, world, pg):
    feeds(pg)
    pg.execute("DELETE FROM proj_feed_orders WHERE day = '2026-09-15'")
    assert at(table(client), "ref_cpa", M(2026, 9)) is None and at(table(client), "ref_cpa", M(2026, 10)) is not None


def test_amazon_reference_row_months_and_run_rate(client, world, pg):
    feeds(pg)                                                                   # $5,000 a day
    t = table(client)
    r = row(t, "ref_amazon")
    assert r["label"] == "Amazon feed run-rate (last 28 days)" and not r["input"] and r["kind"] == "ratio"
    ix = [x["key"] for x in t["rows"]]
    assert ix.index("ref_amazon") == ix.index("drivers.amazon_rev") + 1
    assert at(t, "ref_amazon", M(2026, 9)) == 5000.0 * 30 and at(t, "ref_amazon", M(2026, 10)) == 5000.0 * 31      # closed months: the feed's own totals
    assert at(t, "ref_amazon", M(2026, 11)) == pytest.approx(5000.0 * 365.25 / 12) == at(t, "ref_amazon", M(2027, 2))          # open and later months: one flat run-rate
    assert at(t, "ref_amazon", M(2026, 7)) is None                                                                  # before the feed starts


def test_the_amazon_note_lists_typed_months_beyond_the_tolerance_and_changes_no_value(client, world, rw, pg):
    feeds(pg)                                                                   # feed: Aug 155,000, Sep 150,000, Oct 155,000, run-rate 152,188 a month
    assert table(client)["reference"]["amazon"]["note"] is None                 # the plan's $150,000 sits inside the 5% band everywhere
    put_cells(rw, "drivers.amazon_rev", {M(2026, 8): 163000.0, M(2026, 9): 175500.0, M(2026, 10): 183000.0, M(2026, 11): 175000.0})
    t = table(client)
    note = t["reference"]["amazon"]["note"]
    assert "Aug 2026 +5%" in note and "Sep 2026 +17%" in note and "Oct 2026 +18%" in note and "last 28 days" in note and note.endswith(".")
    assert at(t, "drivers.amazon_rev", M(2026, 9)) == 175500.0 and at(t, "rev_amazon", M(2026, 9)) == 175500.0          # shown, not changed


def test_the_newest_feed_day_is_dropped_and_today_is_never_used(pg):
    feeds(pg, end=date(2026, 11, 3))                                            # the feed already holds today
    ref = reference.load_reference(pg, ("meta", "google"), 28, TODAY)
    assert ref.cpa_window["to"] == "2026-11-02"                                 # min(newest day - 1, yesterday)
    assert ref.amazon_window["to"] == "2026-11-02"


def test_missing_feed_tables_give_an_empty_reference_not_an_error(pg):
    feeds(pg)
    pg.execute("DROP TABLE proj_feed_platform")
    try:
        ref = reference.load_reference(pg, ("meta", "google"), 28, TODAY)
        assert ref.cpa_by_month == {} and ref.cpa_window is None and ref.unavailable["cpa"] == "Google spend feed is not loaded"
    finally:
        pg.execute("CREATE TABLE proj_feed_platform (day date NOT NULL, platform text NOT NULL, spend numeric(18,2) NOT NULL DEFAULT 0, "
                   "purchases numeric(18,2) NOT NULL DEFAULT 0, purchase_value numeric(18,2) NOT NULL DEFAULT 0, nc_purchases numeric(18,2) NOT NULL DEFAULT 0, "
                   "nc_purchase_value numeric(18,2) NOT NULL DEFAULT 0, link_clicks numeric(18,2) NOT NULL DEFAULT 0, clicks numeric(18,2) NOT NULL DEFAULT 0, "
                   "loaded_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY (day, platform))")


@pytest.mark.parametrize("levers,fragment", [({"reference_days": 3}, "reference_days"), ({"reference_days": 91}, "reference_days"), ({"reference_days": 28.5}, "reference_days"),
                                             ({"reference_tolerance": 0}, "reference_tolerance"), ({"reference_tolerance": 2}, "reference_tolerance")])
def test_reference_levers_are_validated(client, world, levers, fragment):
    r = client.put(f"{BASE}/levers", json={"scenario": "current", "levers": levers})
    assert r.status_code == 400 and fragment in r.get_json()["error"]


# ---------------------------------------------------------------------------------------------------------------- guards: coverage and sanity

def test_an_empty_meta_feed_never_gives_a_cpa(client, world, pg):
    feeds(pg)
    pg.execute("DELETE FROM ad_daily")                                          # the case that produced $5.51: Google only, Meta missing
    ref = reference.load_reference(pg, ("meta", "google"), 28, TODAY)
    assert ref.cpa_window is None and ref.cpa_by_month == {} and ref.unavailable["cpa"] == "Meta spend feed has 0 of 28 days"
    t = table(client)
    c = t["reference"]["cpa"]
    assert c["unavailable"] == "Meta spend feed has 0 of 28 days" and c["chip"] == "Realized CPA unavailable: Meta spend feed has 0 of 28 days" and c["tone"] == "warn"
    assert row(t, "ref_cpa") is None


def test_a_partial_feed_names_how_many_days_it_has(pg):
    feeds(pg)
    pg.execute("DELETE FROM ad_daily WHERE day < '2026-10-24'")                 # window Oct 5 to Nov 1 keeps 9 of its 28 days
    ref = reference.load_reference(pg, ("meta", "google"), 28, TODAY)
    assert ref.unavailable["cpa"] == "Meta spend feed has 9 of 28 days" and ref.cpa_window is None


def test_a_feed_missing_up_to_the_allowed_gap_still_counts_and_one_more_does_not(pg):
    feeds(pg)
    pg.execute("DELETE FROM ad_daily WHERE day IN ('2026-10-10', '2026-10-11')")      # 26 of 28: inside the default gap of 2
    assert reference.load_reference(pg, ("meta", "google"), 28, TODAY).cpa_window is not None
    pg.execute("DELETE FROM ad_daily WHERE day = '2026-10-12'")                       # 25 of 28
    assert reference.load_reference(pg, ("meta", "google"), 28, TODAY).unavailable["cpa"] == "Meta spend feed has 25 of 28 days"


def test_the_orders_feed_needs_the_same_coverage(pg):
    feeds(pg)
    pg.execute("DELETE FROM proj_feed_orders WHERE day < '2026-10-20'")
    ref = reference.load_reference(pg, ("meta", "google"), 28, TODAY)
    assert ref.unavailable["cpa"] == "Shopify new-customer feed has 13 of 28 days" and ref.unavailable["aov"] == "Shopify new-customer feed has 13 of 28 days"


def test_a_cpa_outside_the_believable_range_is_withheld_with_the_reason(pg):
    feeds(pg, nc=2000.0)                                                        # $4.50 a customer: a feed problem, not a price
    ref = reference.load_reference(pg, ("meta", "google"), 28, TODAY)
    assert ref.cpa_window is None and ref.unavailable["cpa"] == "Realized CPA $4.50 is outside the $20 to $500 a real feed gives"
    pg.execute("DELETE FROM proj_feed_orders"); pg.execute("DELETE FROM proj_feed_orders")
    feeds_nc = [(d, 1.0) for d in days(date(2026, 8, 1), date(2026, 11, 2))]
    with pg.cursor() as cur:
        cur.executemany("INSERT INTO proj_feed_orders (day, nc_orders) VALUES (%s,%s)", feeds_nc)
    assert reference.load_reference(pg, ("meta", "google"), 28, TODAY).unavailable["cpa"] == "Realized CPA $9,000.00 is outside the $20 to $500 a real feed gives"


def test_aov_is_offered_inside_its_range_and_withheld_outside(pg):
    feeds(pg)
    pg.execute("UPDATE proj_feed_orders SET nc_rev = nc_orders * 70")
    ref = reference.load_reference(pg, ("meta", "google"), 28, TODAY)
    assert ref.aov_window["aov"] == pytest.approx(70.0) and "aov" not in ref.unavailable
    pg.execute("UPDATE proj_feed_orders SET nc_rev = nc_orders * 7")
    ref = reference.load_reference(pg, ("meta", "google"), 28, TODAY)
    assert ref.aov_window is None and ref.unavailable["aov"] == "Realized AOV $7.00 is outside the $15 to $400 a real feed gives"
    pg.execute("UPDATE proj_feed_orders SET nc_rev = 0")
    assert reference.load_reference(pg, ("meta", "google"), 28, TODAY).unavailable["aov"] == "The orders feed holds no new-customer revenue in the window"


def test_the_guard_bounds_are_levers_in_plan_settings(client, world, pg):
    feeds(pg, nc=2000.0)
    assert table(client)["reference"]["cpa"]["unavailable"].startswith("Realized CPA $4.50 is outside")
    r = client.put(f"{BASE}/levers", json={"scenario": "current", "levers": {"reference_cpa_min": 2, "reference_cpa_max": 500}})
    assert r.status_code == 200
    c = table(client)["reference"]["cpa"]
    assert c.get("unavailable") is None and c["realized"] == pytest.approx(4.5)               # the lever moved the guard; the number is offered now
    r = client.put(f"{BASE}/levers", json={"scenario": "current", "levers": {"reference_gap_days": 0}})
    assert r.status_code == 200
    pg.execute("DELETE FROM ad_daily WHERE day = '2026-10-20'")
    assert table(client)["reference"]["cpa"]["unavailable"] == "Meta spend feed has 27 of 28 days"


@pytest.mark.parametrize("levers,fragment", [({"reference_gap_days": 15}, "reference_gap_days"), ({"reference_gap_days": -1}, "reference_gap_days"),
                                             ({"reference_cpa_min": 20}, "together"), ({"reference_cpa_min": 500, "reference_cpa_max": 20}, "reference_cpa_min"),
                                             ({"reference_aov_min": 0, "reference_aov_max": 400}, "reference_aov_min"), ({"reference_aov_min": 15, "reference_aov_max": 1e9}, "reference_aov_min")])
def test_guard_levers_are_validated(client, world, levers, fragment):
    r = client.put(f"{BASE}/levers", json={"scenario": "current", "levers": levers})
    assert r.status_code == 400 and fragment in r.get_json()["error"]
