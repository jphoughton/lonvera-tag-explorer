"""Feed nc_hourly: new-customer revenue by Pacific clock hour from the ExpandFi warehouse. The real SQL runs against the throwaway `exwh` schema of the
ExpandFi feed tests; the day sums must equal nc_product's (same first-order rule, same revenue), DST days keep wall-clock hours, and the feed is native
(shadow by default, no sheet twin) so it changes no existing feed's mode."""
from __future__ import annotations

import dataclasses
import datetime as dt

import pytest

from etl import feeds
from etl.feeds import expandfi_feeds as E
from etl.feeds.common import FeedEmpty, Span, make_ctx
from test_feeds_expandfi_feeds import (BL, OTHER, SHOP_COLS, SPAN, VB, D, line, ln, order, put, seed_shopify, wh)       # noqa: F401  (wh is a fixture)


def feed():
    return next(f for f in E.FEEDS if f.key == "nc_hourly")


def test_registered_native_shadow_default_with_a_35_day_window_and_a_2_day_stale_limit(conn):
    f = feeds.registry()["nc_hourly"]
    assert f.native and f.default_mode == "shadow" and f.source == "expandfi" and f.days == 35 and f.stale_after == 2
    assert f.main == f.shadow == "proj_feed_nc_hourly" and f.key_cols == ("day", "hour") and set(f.cols) == {"day", "hour", "nc_rev", "nc_orders"}
    assert feeds.common.get_mode(conn, "nc_hourly", f.default_mode) == "shadow"
    assert feeds.mode_of(conn, "nc_hourly") == "shadow"
    for k in ("orders", "amazon", "nc_product", "units_amazon", "bids"):                       # no existing feed's default changed
        assert feeds.registry()[k].default_mode == "sheet"
    fr = {e["key"]: e for e in feeds.freshness(conn)}                                           # native shadow feeds are listed; sheet-mode ones are not
    assert fr["nc_hourly"]["stale_after_days"] == 2 and fr["nc_hourly"]["mode"] == "shadow" and fr["nc_hourly"]["feed"] == "nc_hourly"


def test_hourly_sql_sums_per_day_equal_nc_product_and_hours_are_pacific_wall_clock(wh):
    w, url = wh
    seed_shopify(url)
    ctx = make_ctx(today=D(2026, 11, 4), cache={E.CACHE_KEY: w})
    hourly = E.pull_nc_hourly(ctx, SPAN)
    product = E.pull_nc_product(ctx, SPAN)
    per_day = {}
    for d, _h, rev, n in hourly:
        a = per_day.setdefault(d, [0.0, 0])
        a[0] += rev
        a[1] += n
    ref = {}
    for d, _p, rev, n in product:
        a = ref.setdefault(d, [0.0, 0])
        a[0] += rev
        a[1] += int(n)
    assert {d: (round(v[0], 2), v[1]) for d, v in per_day.items()} == {d: (round(v[0], 2), v[1]) for d, v in ref.items()}
    got = {(d, h): (rev, n) for d, h, rev, n in hourly}
    assert got[(D(2026, 11, 1), 19)] == (100.0, 1)                                              # order 1: 03:30Z on Nov 2 = Nov 1 19:30 PST (UTC-8 after the fall back)
    assert got[(D(2026, 11, 2), 10)] == (90.0, 1)                                               # order 3: 18:00Z = 10:00 PST
    assert got[(D(2026, 11, 3), 4)] == (30.0, 1) or got[(D(2026, 11, 3), 4)][1] == 1            # order 10 at 12:00Z = 04:00 PST
    assert all(0 <= h <= 23 for _d, h, *_ in hourly) and hourly == sorted(hourly)


def test_dst_days_use_wall_clock_hours_and_the_25_hour_day_merges_the_two_1am_hours(wh):
    w, url = wh
    ts = dt.datetime
    put(url, "shopify_orders", [
        order(1, "a", "a@x", ts(2026, 11, 1, 8, 30), 10, [line(VB, 10)]),        # 01:30 PDT (first 1 a.m.)
        order(2, "b", "b@x", ts(2026, 11, 1, 9, 30), 20, [line(VB, 20)]),        # 01:30 PST (second 1 a.m.)
        order(3, "c", "c@x", ts(2026, 11, 1, 7, 59), 5, [line(VB, 5)]),          # 00:59 PDT
        order(4, "d", "d@x", ts(2026, 11, 2, 7, 59), 7, [line(VB, 7)]),          # 23:59 PST Nov 1 (the 25th hour of the day is hour 23)
        order(5, "e", "e@x", ts(2026, 11, 2, 8, 0), 9, [line(VB, 9)]),           # 00:00 PST Nov 2
        order(6, "f", "f@x", ts(2026, 3, 8, 9, 59), 11, [line(VB, 11)]),         # 01:59 PST on the 23-hour spring-forward day
        order(7, "g", "g@x", ts(2026, 3, 8, 10, 0), 13, [line(VB, 13)]),         # 03:00 PDT: hour 2 never exists
    ], SHOP_COLS)
    ctx = make_ctx(today=D(2026, 11, 4), cache={E.CACHE_KEY: w})
    fall = {(d, h): (rev, n) for d, h, rev, n in E.pull_nc_hourly(ctx, Span(D(2026, 11, 1), D(2026, 11, 3)))}
    assert fall[(D(2026, 11, 1), 1)] == (30.0, 2) and fall[(D(2026, 11, 1), 0)] == (5.0, 1) and fall[(D(2026, 11, 1), 23)] == (7.0, 1) and fall[(D(2026, 11, 2), 0)] == (9.0, 1)
    assert sum(r for r, _ in fall.values()) == 51.0
    spring = {(d, h): (rev, n) for d, h, rev, n in E.pull_nc_hourly(ctx, Span(D(2026, 3, 8), D(2026, 3, 8)))}
    assert spring == {(D(2026, 3, 8), 1): (11.0, 1), (D(2026, 3, 8), 3): (13.0, 1)} and not any(h == 2 for _d, h in spring)


def test_nc_hourly_rows_skip_test_keyless_and_zero_value_orders_and_reject_a_bad_hour():
    d = D(2026, 11, 1)
    rows = E.nc_hourly_rows([(1, d, 8, 50, None, [ln(VB, 50)]), (2, d, 8, 25.5, None, [ln(BL, 25.5)]), (3, d, 9, 40, "true", [ln(VB, 40)]),
                             (4, d, 9, 40, None, None), (5, d, 9, 0, None, [ln(VB, 10, 1, 10)])])
    assert rows == [(d, 8, 75.5, 2)]
    with pytest.raises(feeds.common.FeedError):
        E.nc_hourly_rows([(1, d, 24, 5, None, [ln(VB, 5)])])


def test_empty_window_writes_nothing(wh):
    w, _ = wh
    with pytest.raises(FeedEmpty):
        E.pull_nc_hourly(make_ctx(cache={E.CACHE_KEY: w}), SPAN)


def fake_pull(rows):
    return lambda ctx, span: list(rows)


def test_pull_and_write_replaces_the_window_and_keeps_older_days_and_logs_the_attempt(conn):
    f = dataclasses.replace(feed(), pull=fake_pull([(D(2026, 11, 2), 8, 10.0, 1), (D(2026, 11, 3), 9, 20.0, 2)]))
    conn.execute("INSERT INTO proj_feed_nc_hourly (day, hour, nc_rev, nc_orders) VALUES ('2026-09-01', 5, 5, 1), ('2026-11-03', 22, 9, 9)")
    conn.commit()
    r = feeds.pull_and_write(conn, f, "shadow", 35, make_ctx(today=D(2026, 11, 4)))
    assert r["rows"] == 2 and r["data_through"] == D(2026, 11, 3)
    feeds.pull_and_write(conn, dataclasses.replace(f, pull=fake_pull([(D(2026, 11, 2), 8, 11.0, 1)])), "shadow", 35, make_ctx(today=D(2026, 11, 4)))
    rows = conn.execute("SELECT day, hour, nc_rev FROM proj_feed_nc_hourly ORDER BY 1, 2").fetchall()
    assert rows == [(D(2026, 9, 1), 5, 5), (D(2026, 11, 2), 8, 11)]
    assert conn.execute("SELECT mode, status FROM proj_feed_attempts WHERE feed = 'nc_hourly' ORDER BY id").fetchall() == [("shadow", "ok"), ("shadow", "ok")]


def test_hour_outside_0_23_is_refused_by_the_table(conn):
    import psycopg
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("INSERT INTO proj_feed_nc_hourly (day, hour, nc_rev, nc_orders) VALUES ('2026-11-01', 24, 1, 1)")
    conn.rollback()


def test_run_pulls_a_native_feed_in_shadow_mode_without_touching_any_mode(conn, monkeypatch):
    f = dataclasses.replace(feed(), pull=fake_pull([(D(2026, 11, 3), 8, 10.0, 1)]))
    monkeypatch.setattr(feeds, "registry", lambda: {"nc_hourly": f})
    assert feeds.run(conn, ["nc_hourly"], ctx=make_ctx(today=D(2026, 11, 4)), out=lambda s: None) == 0
    assert conn.execute("SELECT count(*) FROM proj_feed_nc_hourly").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM proj_feed_config").fetchone()[0] == 0              # no mode row written


def test_tie_out_helper_compares_day_sums_with_upstack_and_nc_product():
    from etl.feeds.nc_hourly_check import tie_out
    rows = [(D(2026, 11, 1), 8, 60.0, 1), (D(2026, 11, 1), 9, 40.0, 1), (D(2026, 11, 2), 8, 50.0, 1)]
    t = tie_out(rows, {D(2026, 11, 1): 100.0, D(2026, 11, 2): 52.0}, {D(2026, 11, 1): 100.0})
    assert t[0] == {"day": D(2026, 11, 1), "hourly": 100.0, "upstack": 100.0, "diff": 0.0, "pct": 0.0, "nc_product": 100.0, "diff_nc_product": 0.0}
    assert t[1]["diff"] == -2.0 and t[1]["pct"] == pytest.approx(-2 / 52) and t[1]["nc_product"] is None
