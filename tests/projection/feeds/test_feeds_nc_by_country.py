"""Feed nc_by_country_daily: new customers and their revenue by Pacific day and ship-to country from the ExpandFi warehouse. The real SQL runs against the throwaway `exwh`
schema of the ExpandFi feed tests. Rules: a customer's FIRST Shopify order ever (customer key = shopify_customer_id, else lower(email); earliest processed_at, then id),
dated on its Pacific day; country = ship-to country code, else billing country code, else 'unknown'; revenue = total_price; a first order flagged test is dropped; a
customer whose first order was before the window is not new inside it; the feed is native and non-fatal and refreshes a trailing window."""
from __future__ import annotations

import dataclasses
import datetime as dt
import json

import pytest

from etl import feeds
from etl.feeds import expandfi_feeds as E
from etl.feeds.common import FeedEmpty, Span, make_ctx
from test_feeds_expandfi_feeds import SHOP_COLS, D, put, wh                                                   # noqa: F401  (wh is a fixture)

SPAN = Span(D(2026, 10, 17), D(2026, 10, 19))


def feed():
    return next(f for f in E.FEEDS if f.key == "nc_by_country_daily")


def o(oid, cid, email, ts, total, ship=None, bill=None, test=None):
    raw = {}
    if ship is not None:
        raw["shipping_address"] = {"country_code": ship}
    if bill is not None:
        raw["billing_address"] = {"country_code": bill}
    if test is not None:
        raw["test"] = test
    return (oid, cid, email, ts, total, json.dumps(raw))


def seed(url):
    ts = dt.datetime
    put(url, "shopify_orders", [
        o(1, "1", "a@x", ts(2026, 10, 18, 3, 0), 100, ship="US"),                              # 03:00Z Oct 18 = Oct 17 20:00 Pacific
        o(2, "1", "a@x", ts(2026, 10, 19, 18, 0), 80, ship="US"),                              # a later order of the same customer: not new
        o(3, "2", "b@x", ts(2026, 10, 18, 20, 0), 60, ship="gb"),                              # lower-case code is upper-cased
        o(4, "3", "c@x", ts(2026, 10, 18, 21, 0), 50, ship="", bill="AU"),                     # empty ship-to falls back to billing
        o(5, "4", "d@x", ts(2026, 10, 18, 22, 0), 40),                                         # no address at all: unknown
        o(6, "", "E@X", ts(2026, 10, 19, 10, 0), 30, ship="CA"),                               # no customer id: keyed on lower(email)
        o(7, "", "e@x", ts(2026, 10, 19, 11, 0), 99, ship="CA"),                               # same person by email, later
        o(8, "5", "t@x", ts(2026, 10, 18, 19, 0), 70, ship="US", test="true"),                 # a test first order: dropped
        o(9, "5", "t@x", ts(2026, 10, 19, 19, 0), 70, ship="US"),                              # ... and the later real order is not a first order
        o(10, "6", "old@x", ts(2026, 9, 1, 12, 0), 55, ship="US"),                             # first order long before the window
        o(11, "6", "old@x", ts(2026, 10, 18, 12, 0), 55, ship="US"),                           # so this one is a repeat order
        o(12, "7", "f@x", ts(2026, 10, 18, 12, 0), 25, ship="US"),                             # same day and customer as order 13: lower id is the first
        o(13, "7", "f@x", ts(2026, 10, 18, 12, 0), 999, ship="GB"),
        o(14, "8", "g@x", ts(2026, 10, 18, 23, 59), 10, ship="US"),                            # 16:59 Pacific Oct 18
        o(15, "9", "h@x", ts(2026, 10, 20, 6, 0), 77, ship="US"),                              # 23:00 Pacific Oct 19 (still in the window)
        o(16, "10", "i@x", ts(2026, 10, 20, 8, 0), 88, ship="US"),                             # 01:00 Pacific Oct 20: outside
        o(17, "", "", ts(2026, 10, 18, 12, 0), 66, ship="US"),                                 # no customer key at all: skipped
    ], SHOP_COLS)


def test_registered_native_non_fatal_with_a_21_day_window_and_a_backfill_start(conn):
    f = feeds.registry()["nc_by_country_daily"]
    assert f.native and f.non_fatal and f.default_mode == "shadow" and f.source == "expandfi" and f.days == 21
    assert f.main == f.shadow == "nc_by_country_daily" and f.key_cols == ("day", "country_code") and f.history_start == D(2024, 1, 1)
    assert set(f.cols) == {"day", "country_code", "new_customers", "new_customer_revenue"}
    assert feeds.backfill_days(f, D(2026, 11, 16)) == (D(2026, 11, 15) - D(2024, 1, 1)).days + 1
    assert feeds.mode_of(conn, "nc_by_country_daily") == "shadow"


def test_sql_applies_the_first_order_country_and_pacific_day_rules(wh):
    w, url = wh
    seed(url)
    rows = E.pull_nc_by_country(make_ctx(today=D(2026, 11, 4), cache={E.CACHE_KEY: w}), SPAN)
    got = {(d, c): (n, rev) for d, c, n, rev in rows}
    assert got == {
        (D(2026, 10, 17), "US"): (1, 100.0),                                                   # order 1 only
        (D(2026, 10, 18), "GB"): (1, 60.0),
        (D(2026, 10, 18), "AU"): (1, 50.0),
        (D(2026, 10, 18), "unknown"): (1, 40.0),
        (D(2026, 10, 18), "US"): (2, 35.0),                                                    # orders 12 (id tie) and 14: 25 + 10; order 11 is a repeat, order 8 a dropped test
        (D(2026, 10, 19), "CA"): (1, 30.0),                                                    # order 6 (email key); 7 is not first
        (D(2026, 10, 19), "US"): (1, 77.0),                                                    # order 15 at 23:00 Pacific
    }, got
    assert rows == sorted(rows, key=lambda r: (r[0], r[1])) and all(isinstance(r[2], int) for r in rows)


def test_a_window_before_the_first_orders_returns_only_its_own_days_and_an_empty_window_writes_nothing(wh):
    w, url = wh
    seed(url)
    ctx = make_ctx(today=D(2026, 11, 4), cache={E.CACHE_KEY: w})
    sept = E.pull_nc_by_country(ctx, Span(D(2026, 9, 1), D(2026, 9, 1)))
    assert sept == [(D(2026, 9, 1), "US", 1, 55.0)]
    with pytest.raises(FeedEmpty):
        E.pull_nc_by_country(ctx, Span(D(2026, 8, 1), D(2026, 8, 5)))


def test_a_long_span_is_cut_into_90_day_windows_and_stitched(wh, monkeypatch):
    w, url = wh
    seed(url)
    calls = []
    real = w.query

    def spy(stmt, params=None, **kw):
        calls.append((params["since"], params["until"]))
        return real(stmt, params, **kw)
    monkeypatch.setattr(w, "query", spy)
    rows = E.pull_nc_by_country(make_ctx(today=D(2026, 11, 4), cache={E.CACHE_KEY: w}), Span(D(2026, 1, 1), D(2026, 10, 31)))
    assert len(calls) == 4 and calls[0] == (D(2026, 1, 1), D(2026, 3, 31)) and calls[-1][1] == D(2026, 10, 31)
    assert all(b - a <= dt.timedelta(days=89) for a, b in calls) and all(calls[i][1] + dt.timedelta(days=1) == calls[i + 1][0] for i in range(3))
    assert sum(r[2] for r in rows) == 10                                                                 # every first order of the year except the dropped test order: customers 1-4, 6-10 and the Sep 1 customer
    assert {r[0] for r in rows} >= {D(2026, 9, 1), D(2026, 10, 20)}


def fake_pull(rows):
    return lambda ctx, span: list(rows)


def test_pull_and_write_replaces_the_window_keeps_older_days_and_logs_the_attempt(conn):
    f = dataclasses.replace(feed(), pull=fake_pull([(D(2026, 11, 2), "US", 5, 300.0), (D(2026, 11, 3), "GB", 2, 120.0)]))
    conn.execute("INSERT INTO nc_by_country_daily (day, country_code, new_customers, new_customer_revenue) VALUES ('2026-01-05', 'US', 9, 9), ('2026-11-03', 'US', 8, 8)")
    conn.commit()
    r = feeds.pull_and_write(conn, f, "shadow", 21, make_ctx(today=D(2026, 11, 4)))
    assert r["rows"] == 2 and r["data_through"] == D(2026, 11, 3)
    feeds.pull_and_write(conn, dataclasses.replace(f, pull=fake_pull([(D(2026, 11, 2), "US", 6, 310.0)])), "shadow", 21, make_ctx(today=D(2026, 11, 4)))
    rows = conn.execute("SELECT day, country_code, new_customers, new_customer_revenue FROM nc_by_country_daily ORDER BY 1, 2").fetchall()
    assert [(d, c, n, float(v)) for d, c, n, v in rows] == [(D(2026, 1, 5), "US", 9, 9.0), (D(2026, 11, 2), "US", 6, 310.0)]
    assert conn.execute("SELECT mode, status FROM proj_feed_attempts WHERE feed = 'nc_by_country_daily' ORDER BY id").fetchall() == [("shadow", "ok"), ("shadow", "ok")]


def test_a_failed_pull_is_non_fatal_for_the_run(conn):
    out = []
    bad = dataclasses.replace(feed(), pull=lambda ctx, span: (_ for _ in ()).throw(FeedEmpty("nc_by_country_daily: nothing")))
    import etl.feeds as F
    orig = F.registry
    F.registry = lambda: {"nc_by_country_daily": bad}
    try:
        code = F.run(conn, ["nc_by_country_daily"], 21, make_ctx(today=D(2026, 11, 4)), out.append)
    finally:
        F.registry = orig
    assert code == 0 and any("WARNING (non-fatal)" in line for line in out), out
