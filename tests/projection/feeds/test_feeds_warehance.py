"""Warehance feeds: Denver-dated US shipments, stock snapshot, the non-US estimate with its stored ratio levers."""
from __future__ import annotations

import datetime as dt

import pytest

from etl.feeds import warehance as W
from etl.feeds.common import FeedEmpty, FeedError, Span, make_ctx
from feeds_helpers import FakeResponse, FakeSession

D = dt.date
KEY = "wh-" + "z" * 40


def page(items, key, nxt=None):
    return FakeResponse(200, {"data": {key: items, "has_next_page": bool(nxt), "next_cursor": nxt}})


def ship(i, created, items, voided=False):
    return {"id": i, "created_at": created, "voided": voided, "shipment_parcels": [{"items": [{"sku": s, "quantity": q} for s, q in items]}]}


def ctx(session, now=None, levers=None, today=D(2026, 11, 3)):
    c = make_ctx(session=session, sleep=lambda s: None, env={"WAREHANCE_API_KEY": KEY}, today=today,
                 now_utc=now or dt.datetime(2026, 11, 3, 20, 0, tzinfo=dt.timezone.utc))
    c.levers = levers or {}
    return c


def test_shipments_are_dated_in_denver_voids_and_duplicates_dropped_and_zero_filled():
    s = FakeSession().on(lambda c: c["url"].endswith("/shipments"), [
        page([ship(1, "2026-11-01T03:30:00.123Z", [("LV-VBULL", 5), ("LV-BLC180", 2)]),             # 03:30Z = Oct 31 21:30 Denver
              ship(2, "2026-11-01T18:00:00Z", [("LV-VBULL", 3)]),
              ship(2, "2026-11-01T18:00:00Z", [("LV-VBULL", 3)]),                                    # duplicate id
              ship(3, "2026-11-01T18:00:00Z", [("LV-VBULL", 50)], voided=True),
              ship(4, "2026-11-01T19:00:00Z", [("MYSTERY-SKU", 4), ("DPCD1-GN187001", 1)])], "shipments", nxt="c2"),
        page([ship(5, "2026-11-02T20:00:00Z", [("LV-VBELLE", 6)])], "shipments")])
    c = ctx(s)
    rows = W.pull_units_revamp(c, Span(D(2026, 10, 31), D(2026, 11, 2)))
    got = {(r[2], r[1]): r[3] for r in rows}
    assert got[(D(2026, 10, 31), "bull")] == 5 and got[(D(2026, 10, 31), "liver")] == 2
    assert got[(D(2026, 11, 1), "bull")] == 3 and got[(D(2026, 11, 1), "boneflex")] == 1
    assert got[(D(2026, 11, 2), "belle")] == 6 and got[(D(2026, 11, 2), "bull")] == 0                # zero-filled
    assert len(rows) == 12 and all(r[0] == "revamp" and r[4] == "warehance" and r[5] is False for r in rows)
    assert any("MYSTERY-SKU x4" in w for w in c.warnings)
    assert s.calls[1]["params"]["cursor"] == "c2" and s.calls[0]["headers"]["X-API-KEY"] == KEY
    assert s.calls[0]["params"]["created_at_date_start"] == "2026-10-31T00:00:00Z"


def test_us_span_never_includes_the_partial_denver_day():
    now = dt.datetime(2026, 11, 3, 5, 0, tzinfo=dt.timezone.utc)                                        # 22:00 Nov 2 in Denver, 21:00 Pacific
    assert W.denver_span(ctx(None, now=now), 5).until == D(2026, 11, 1)
    assert W.denver_span(ctx(None), 5).until == D(2026, 11, 2)


def test_no_shipments_is_an_error_not_a_zero_week():
    s = FakeSession().on(lambda c: True, page([], "shipments"))
    with pytest.raises(FeedEmpty):
        W.pull_units_revamp(ctx(s), Span(D(2026, 11, 1), D(2026, 11, 2)))


def test_api_errors_payload_and_missing_key():
    s = FakeSession().on(lambda c: True, FakeResponse(200, {"errors": ["nope"], "data": None}))
    with pytest.raises(FeedError, match="errors"):
        W.pull_units_revamp(ctx(s), Span(D(2026, 11, 1), D(2026, 11, 2)))
    with pytest.raises(FeedError, match="WAREHANCE_API_KEY"):
        W.pull_units_revamp(make_ctx(env={}, today=D(2026, 11, 3)), Span(D(2026, 11, 1), D(2026, 11, 2)))


def test_stock_snapshot_uses_the_denver_date_and_requires_every_sku():
    prods = [{"sku": "LV-VBULL", "on_hand": 100, "allocated": 5}, {"sku": "LV-BLC180", "on_hand": 200, "allocated": 0},
             {"sku": "DPCD1-GN187001", "on_hand": 300, "allocated": 7}, {"sku": "LV-VBELLE", "on_hand": 40, "allocated": 1},
             {"sku": "LV-VBULL", "is_bundle": True, "on_hand": 9999, "allocated": 0}, {"sku": "OTHER", "on_hand": 1, "allocated": 1}]
    s = FakeSession().on(lambda c: c["url"].endswith("/products"), page(prods, "products"))
    c = ctx(s)
    rows = W.pull_stock_revamp(c, W.stock_span(c, 1))
    assert sorted(rows) == [("revamp", "belle", D(2026, 11, 3), 40.0, 1.0, "warehance"), ("revamp", "boneflex", D(2026, 11, 3), 300.0, 7.0, "warehance"),
                            ("revamp", "bull", D(2026, 11, 3), 100.0, 5.0, "warehance"), ("revamp", "liver", D(2026, 11, 3), 200.0, 0.0, "warehance")]
    s2 = FakeSession().on(lambda c: True, page(prods[:2], "products"))
    with pytest.raises(FeedError, match="no SKU"):
        W.pull_stock_revamp(ctx(s2), W.stock_span(ctx(s2), 1))


# ---------------------------------------------------------------------------------------------------------------- international estimate

def order(i, when, country, items, status="cancelled"):
    return {"id": i, "order_date": when, "status": status, "ship_to_address": {"country_code": country},
            "order_items": [{"sku": s, "name": n, "metadata": {"quantity": q, "line_item_sku": ls}} for s, n, q, ls in items]}


ORDERS = [
    order(1, "2026-10-20T18:00:00Z", "GB", [("LV-VBULL", "Vital Bull", 2, "LV-VBULL-3")]),            # 2 x bundle of 3 = 6 bottles
    order(2, "2026-10-20T19:00:00Z", "AU", [("LV-BLC180", "Beef Liver", 1, "LV-BLC180-2"), ("DPCD1-GN187001", "Boneflex", 4, "DPCD1-GN187001")]),
    order(3, "2026-10-20T20:00:00Z", "US", [("LV-VBULL", "Vital Bull", 9, "LV-VBULL-1")]),               # US: excluded
    order(4, "2026-10-21T03:00:00Z", "GB", [("LV-VBULL", "Vital Bull", 1, "LV-VBULL-1")], status="Orders with Issues"),    # 03:00Z = Oct 20 20:00 Pacific
    order(5, "2026-10-21T18:00:00Z", "", [("LV-VBULL", "Vital Bull", 5, "LV-VBULL-1")]),                # no country: counted as an order, skipped for units
    order(1, "2026-10-20T18:00:00Z", "GB", [("LV-VBULL", "Vital Bull", 2, "LV-VBULL-3")]),             # duplicate id
]


def test_aggregate_intl_pacific_days_any_status_bundle_multiplier_and_country():
    raw, per_day, unknown = W.aggregate_intl(ORDERS)
    assert raw[(D(2026, 10, 20), "bull", "GB")] == 7                    # 6 + 1 (the second GB order is dated Oct 20 Pacific)
    assert raw[(D(2026, 10, 20), "liver", "AU")] == 2 and raw[(D(2026, 10, 20), "boneflex", "AU")] == 4
    assert (D(2026, 10, 20), "bull", "US") not in raw and unknown == 1
    assert per_day == {D(2026, 10, 20): 4, D(2026, 10, 21): 1}


def intl_session(orders):
    return FakeSession().on(lambda c: c["url"].endswith("/orders"), page(orders, "orders"))


def test_raw_feed_keeps_country_and_skips_days_without_orders():
    rows = W.pull_intl_estimate(ctx(intl_session(ORDERS)), Span(D(2026, 10, 19), D(2026, 10, 21)))
    assert rows == [(D(2026, 10, 20), "boneflex", "AU", 4.0), (D(2026, 10, 20), "bull", "GB", 7.0), (D(2026, 10, 20), "liver", "AU", 2.0)]


def test_calibrated_feed_uses_the_stored_levers_not_constants():
    span = Span(D(2026, 10, 19), D(2026, 10, 21))
    base = {r[1]: r[3] for r in W.pull_units_intl(ctx(intl_session(ORDERS)), span) if r[2] == D(2026, 10, 20)}
    assert base == {"bull": 6.0, "liver": 2.0, "boneflex": 3.0, "flow": 0.0}                           # 7*.905=6.3, 2*.895=1.79, 4*.868=3.47 (fallbacks)
    moved = ctx(intl_session(ORDERS), levers={"intl_ratio.bull": 0.5, "intl_ratio.liver": 1.0, "intl_ratio.boneflex": 1.0})
    got = {r[1]: r[3] for r in W.pull_units_intl(moved, span) if r[2] == D(2026, 10, 20)}
    assert got == {"bull": 4.0, "liver": 2.0, "boneflex": 4.0, "flow": 0.0}                            # 3.5 rounds half-to-even to 4
    rows = W.pull_units_intl(ctx(intl_session(ORDERS)), span)
    assert all(r[0] == "intl" and r[4] == "estimate" and r[5] is True for r in rows)


def test_one_orders_pull_serves_both_intl_feeds():
    s = intl_session(ORDERS)
    c = ctx(s)
    span = Span(D(2026, 10, 19), D(2026, 10, 21))
    W.pull_intl_estimate(c, span)
    W.pull_units_intl(c, span)
    assert len([x for x in s.calls if x["url"].endswith("/orders")]) == 1


def test_screen_refuses_a_zero_or_partial_day():
    ok = {D(2026, 10, d): 100.0 for d in range(1, 8)}
    assert W.screen(ok) == {}
    assert W.screen({**ok, D(2026, 10, 8): 0.0}).keys() == {D(2026, 10, 8)}
    assert W.screen({**ok, D(2026, 10, 8): 20.0}).keys() == {D(2026, 10, 8)}
    assert W.screen({**ok, D(2026, 10, 8): 500.0}).keys() == {D(2026, 10, 8)}


def test_intl_span_never_starts_before_the_estimate_start_and_empty_window_is_an_error():
    c = ctx(None, today=D(2026, 9, 20))
    assert W.intl_span(c, 21).since == D(2026, 9, 16)
    early = ctx(intl_session(ORDERS), today=D(2026, 9, 16))
    with pytest.raises(FeedEmpty):
        W.pull_units_intl(early, W.intl_span(early, 21))


def test_feed_definitions_are_consistent():
    for f in W.FEEDS:
        assert set(f.key_cols) <= set(f.cols) and f.day_col in f.cols and all(m.col in f.cols for m in f.metrics)
    est = next(f for f in W.FEEDS if f.key == "intl_estimate")
    assert est.native and est.stale_after == 2 and est.main == est.shadow == "proj_feedx_intl_nonus_daily"
