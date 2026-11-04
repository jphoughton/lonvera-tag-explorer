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


def test_without_feeds_there_are_no_reference_rows_and_no_chip(client, world):
    t = table(client)
    assert row(t, "ref_cpa") is None and row(t, "ref_amazon") is None and t["reference"]["cpa"] is None and t["reference"]["amazon"] is None


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
    pg.execute("DROP TABLE proj_feed_platform")
    try:
        ref = reference.load_reference(pg, ("meta", "google"), 28, TODAY)
        assert ref.cpa_by_month == {} and ref.cpa_window is None
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
