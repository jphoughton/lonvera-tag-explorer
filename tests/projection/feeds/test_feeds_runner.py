"""Runner, modes, load() fallback, freshness hook, attempt log: against a real database with fake feeds and fake HTTP."""
from __future__ import annotations

import dataclasses
import datetime as dt

import pytest

import etl.feeds as feeds
from etl.feeds import common, upstack, warehance
from etl.feeds.common import FeedEmpty, FeedError, Span, make_ctx
from etl.feeds.feed import Feed, Metric
from feeds_helpers import FakeResponse, FakeSession, path_is, upstack_session

D = dt.date
TODAY = D(2026, 11, 3)
ENV = {"UPSTACK_API_KEY": "key-" + "a" * 40, "UPSTACK_PIXEL_ID": "pixel-" + "b" * 30, "WAREHANCE_API_KEY": "wh-" + "c" * 40}


def ctx(session=None):
    return make_ctx(session=session, sleep=lambda s: None, env=ENV, today=TODAY, now_utc=dt.datetime(2026, 11, 3, 20, tzinfo=dt.timezone.utc))


def orders_session(nc=100.0, fail=False):
    if fail:
        return FakeSession().on(lambda c: True, FakeResponse(500))
    prim = [{"dt": f"2026-11-0{d}T00:00:00", "orders.new_customer_total_revenue": nc, "orders.returning_customer_total_revenue": 50, "orders.new_customers": 3,
             "orders.total_revenue": nc + 50, "orders.count": 5} for d in (1, 2)]
    return upstack_session(query=prim, channel=[], attribution=lambda b: [])


def seed_main_orders(conn, rows):
    for d, nc in rows:
        conn.execute("INSERT INTO proj_feed_orders (day, nc_rev, rc_rev, nc_orders, total_rev, total_orders) VALUES (%s, %s, 50, 3, %s, 5)", (d, nc, nc + 50))
    conn.commit()


def test_shadow_run_writes_only_the_shadow_table_and_logs_the_attempt(conn):
    f = feeds.registry()["orders"]
    r = feeds.pull_and_write(conn, f, "shadow", 2, ctx(orders_session()))
    assert r["rows"] == 2 and r["data_through"] == D(2026, 11, 2) and r["mode"] == "shadow"
    assert conn.execute("SELECT count(*) FROM proj_feedx_orders").fetchone()[0] == 2
    assert conn.execute("SELECT count(*) FROM proj_feed_orders").fetchone()[0] == 0
    a = conn.execute("SELECT mode, source, status, rows, last_day FROM proj_feed_attempts").fetchone()
    assert a == ("shadow", "api:upstack", "ok", 2, D(2026, 11, 2))
    feeds.pull_and_write(conn, f, "shadow", 2, ctx(orders_session()))                        # idempotent
    assert conn.execute("SELECT count(*) FROM proj_feedx_orders").fetchone()[0] == 2


def test_direct_run_replaces_the_window_in_the_main_table_and_mirrors_the_run_row(conn):
    seed_main_orders(conn, [(D(2026, 10, 1), 7.0), (D(2026, 11, 1), 1.0)])
    f = feeds.registry()["orders"]
    feeds.pull_and_write(conn, f, "main", 2, ctx(orders_session(nc=100.0)))
    assert conn.execute("SELECT day, nc_rev FROM proj_feed_orders ORDER BY day").fetchall() == [(D(2026, 10, 1), 7), (D(2026, 11, 1), 100), (D(2026, 11, 2), 100)]
    assert conn.execute("SELECT source, rows_loaded, last_day FROM proj_feed_runs WHERE feed = 'orders'").fetchone() == ("api:upstack", 2, D(2026, 11, 2))


def test_failed_pull_rolls_back_logs_a_scrubbed_note_and_raises(conn):
    seed_main_orders(conn, [(D(2026, 11, 1), 1.0)])
    f = feeds.registry()["orders"]
    with pytest.raises(FeedError):
        feeds.pull_and_write(conn, f, "main", 2, ctx(orders_session(fail=True)))
    assert conn.execute("SELECT nc_rev FROM proj_feed_orders").fetchone()[0] == 1                 # untouched
    st, note = conn.execute("SELECT status, note FROM proj_feed_attempts").fetchone()
    assert st == "failed" and "gave up" in note and ENV["UPSTACK_API_KEY"] not in note


def test_empty_pull_never_wipes_the_window(conn):
    seed_main_orders(conn, [(D(2026, 11, 1), 1.0)])
    s = upstack_session(query=[], channel=[], attribution=lambda b: [])
    with pytest.raises(FeedError):
        feeds.pull_and_write(conn, feeds.registry()["orders"], "main", 2, ctx(s))
    assert conn.execute("SELECT count(*) FROM proj_feed_orders").fetchone()[0] == 1


def test_load_by_mode(conn):
    seed_main_orders(conn, [(D(2026, 11, 1), 1.0)])
    calls = []
    fallback = lambda: (calls.append(1), {"rows": 99})[1]                                       # noqa: E731
    c = ctx(orders_session(nc=100.0))
    assert feeds.load("orders", conn, 2, fallback, c) == {"mode": "sheet", "rows": 99} and len(c.session.calls) == 0       # sheet: no API call
    common.set_mode(conn, "orders", "shadow"); conn.commit()
    r = feeds.load("orders", conn, 2, fallback, ctx(orders_session()))
    assert r["mode"] == "shadow" and r["rows"] == 99 and r["shadow"]["rows"] == 2
    r = feeds.load("orders", conn, 2, fallback, ctx(orders_session(fail=True)))                    # a shadow failure never fails the module
    assert r["rows"] == 99 and "error" in r["shadow"]
    common.set_mode(conn, "orders", "direct"); conn.commit()
    r = feeds.load("orders", conn, 2, fallback, ctx(orders_session(nc=200.0)))
    assert r["mode"] == "direct" and r["rows"] == 2 and "fallback" not in r
    assert r["data_through"] == "2026-11-02" and r["first_day"] == "2026-11-01" and r["changed"] == 2 and r["source"] == "api:upstack"
    assert conn.execute("SELECT nc_rev FROM proj_feed_orders WHERE day = '2026-11-02'").fetchone()[0] == 200


def test_direct_failure_falls_back_to_the_sheet_and_says_so(conn):
    common.set_mode(conn, "orders", "direct"); conn.commit()
    r = feeds.load("orders", conn, 2, lambda: {"rows": 5}, ctx(orders_session(fail=True)))
    assert r["fallback"] is True and r["rows"] == 5 and "gave up" in r["error"]
    assert conn.execute("SELECT status, source, note FROM proj_feed_attempts ORDER BY id DESC LIMIT 1").fetchone()[0] == "fallback"
    with pytest.raises(FeedError):
        feeds.load("orders", conn, 2, None, ctx(orders_session(fail=True)))                         # no fallback given: the error surfaces


def test_run_skips_sheet_mode_and_returns_one_when_a_pull_fails(conn):
    out = []
    assert feeds.run(conn, ["orders"], 2, ctx(orders_session()), out.append) == 0 and "mode sheet, skipped" in out[0]
    common.set_mode(conn, "orders", "shadow"); conn.commit()
    out.clear()
    assert feeds.run(conn, ["orders"], 2, ctx(orders_session()), out.append) == 0 and "shadow ok, 2 rows" in out[0]
    out.clear()
    assert feeds.run(conn, ["orders"], 2, ctx(orders_session(fail=True)), out.append) == 1 and "FAILED" in out[0]
    assert feeds.run(conn, ["nope"], 2, ctx(), out.append) == 1


def test_run_exits_one_for_a_stale_direct_feed(conn):
    conn.execute("INSERT INTO proj_feed_orders (day) VALUES ('2026-10-20')")
    common.set_mode(conn, "orders", "direct"); conn.commit()
    out = []
    # a pull that succeeds but returns old rows keeps the table stale
    s = upstack_session(query=[{"dt": "2026-10-20T00:00:00", "orders.new_customer_total_revenue": 1}], channel=[], attribution=lambda b: [])
    code = feeds.run(conn, ["orders"], 14, ctx(s), out.append)
    assert code == 1 and any("stale after 2 days" in l for l in out)


def test_freshness_hook_contract_and_the_intl_estimate_entry(conn):
    conn.execute("INSERT INTO proj_feedx_orders (day) VALUES ('2026-11-02')")
    conn.execute("INSERT INTO proj_feedx_intl_nonus_daily (day, product, country, units_raw) VALUES ('2026-11-02', 'bull', 'GB', 10), ('2026-10-25', 'bull', 'GB', 4)")
    common.set_mode(conn, "orders", "shadow"); conn.commit()
    entries = {e["feed"]: e for e in feeds.freshness(conn, TODAY)}
    assert set(entries) == {"feeds.orders", "intl_estimate"}                                      # sheet-mode feeds are not listed; the native feed is on by default
    e = entries["intl_estimate"]
    assert (e["cadence"], e["data_through"], e["stale_after_days"], e["status"]) == ("daily", D(2026, 11, 2), 2, "fresh")
    assert {"feed", "label", "cadence", "data_through", "stale_after_days"} <= set(e)
    late = {x["feed"]: x for x in feeds.freshness(conn, D(2026, 11, 8))}
    assert late["intl_estimate"]["status"] == "stale" and late["intl_estimate"]["age_days"] == 6
    conn.execute("TRUNCATE proj_feedx_intl_nonus_daily"); conn.commit()
    assert {x["feed"]: x for x in feeds.freshness(conn, TODAY)}["intl_estimate"]["status"] == "unknown"


def test_native_intl_feed_runs_by_default_and_both_intl_feeds_share_one_pull(conn):
    orders = [{"id": 1, "order_date": "2026-11-02T18:00:00Z", "ship_to_address": {"country_code": "GB"},
               "order_items": [{"sku": "LV-VBULL", "name": "Vital Bull", "metadata": {"quantity": 10, "line_item_sku": "LV-VBULL-1"}}]},
              {"id": 2, "order_date": "2026-11-02T19:00:00Z", "ship_to_address": {"country_code": "US"}, "order_items": []}]
    s = FakeSession().on(path_is("/orders"), FakeResponse(200, {"data": {"orders": orders, "has_next_page": False}}))
    common.set_mode(conn, "units_intl", "shadow"); conn.commit()
    out = []
    code = feeds.run(conn, ["intl_estimate", "units_intl"], 3, ctx(s), out.append)
    assert code == 0, out
    assert len([c for c in s.calls if c["url"].endswith("/orders")]) == 1
    assert conn.execute("SELECT product, country, units_raw FROM proj_feedx_intl_nonus_daily").fetchall() == [("bull", "GB", 10)]
    assert conn.execute("SELECT product, units, is_estimate FROM proj_feedx_units_daily WHERE units > 0").fetchall() == [("bull", 9, True)]      # 10 x 0.905 = 9.05
    # the view applies the stored lever and flags the rows as estimated
    prod, raw, factor, units, est = conn.execute("SELECT product, units_raw, factor, units, estimated FROM proj_feedx_intl_estimate").fetchone()
    assert (prod, float(raw), float(factor), float(units), est) == ("bull", 10.0, 0.905, 9.0, True)
    common.set_lever(conn, "intl_ratio.bull", 0.5, "test"); conn.commit()
    assert conn.execute("SELECT units FROM proj_feedx_intl_estimate").fetchone()[0] == 5


def test_status_lists_every_feed_with_its_mode(conn):
    st = {r["feed"]: r for r in feeds.status(conn)}
    assert st["orders"]["mode"] == "sheet" and st["intl_estimate"]["mode"] == "shadow" and st["bids"]["source"] == "meta"
    assert set(st) == {"orders", "platform", "attribution", "traffic", "anyclick", "units_revamp", "units_intl", "intl_estimate", "stock_revamp", "meta_reach", "product_reach", "bids"}


def test_registry_is_ordered_by_source_and_unique():
    keys = list(feeds.registry())
    assert len(keys) == len(set(keys))
    order = [feeds.registry()[k].source for k in keys]
    assert order == sorted(order, key=feeds.SOURCE_ORDER.index)
