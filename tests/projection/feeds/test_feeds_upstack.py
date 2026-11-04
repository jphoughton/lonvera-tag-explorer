"""Upstack feeds on recorded-shape fixtures (no network, no credentials)."""
from __future__ import annotations

import datetime as dt

import pytest

from etl.feeds import upstack
from etl.feeds.common import AuthError, FeedEmpty, FeedError, Span, make_ctx
from feeds_helpers import FakeResponse, FakeSession, path_is, upstack_session

D = dt.date
ENV = {"UPSTACK_API_KEY": "key-" + "a" * 40, "UPSTACK_PIXEL_ID": "pixel-" + "b" * 30}


def ctx(session):
    return make_ctx(session=session, sleep=lambda s: None, env=ENV, today=D(2026, 11, 3))


def feed(key):
    return next(f for f in upstack.FEEDS if f.key == key)


def test_orders_maps_the_seven_measures_in_sheet_column_order():
    prim = [{"dt": "2026-11-01T00:00:00", "orders.new_customer_total_revenue": "1000.456", "orders.returning_customer_total_revenue": 500,
             "orders.new_customers": 12, "orders.total_revenue": 1500.46, "orders.count": 30, "orders.tax": 70.1, "orders.refund_amount": 5},
            {"dt": "2026-11-02T00:00:00", "orders.new_customer_total_revenue": 10}]
    s = upstack_session(query=prim, channel=[], attribution=lambda b: [])
    rows = upstack.pull_orders(ctx(s), Span(D(2026, 11, 1), D(2026, 11, 2)))
    assert rows[0] == (D(2026, 11, 1), 1000.46, 500.0, 12.0, 1500.46, 30.0, 70.1, 5.0)
    assert rows[1][0] == D(2026, 11, 2) and rows[1][4] == 0.0                         # missing measures read as 0
    body = s.calls[0]["json"]
    assert body["timezone"] == "America/Los_Angeles" and body["dateRange"] == {"start": "2026-11-01", "end": "2026-11-02"}
    assert [m["measure"] for m in body["measures"]] == upstack.ORDER_MEASURES
    h = s.calls[0]["headers"]
    assert h["x-api-key"] == ENV["UPSTACK_API_KEY"] and h["x-pixel-id"] == ENV["UPSTACK_PIXEL_ID"] and h["x-upstack-cli-version"] == "1.0.7"


def test_orders_chunks_long_windows_into_60_day_calls():
    s = upstack_session(query=lambda b: [{"dt": b["dateRange"]["start"] + "T00:00:00"}], channel=[], attribution=lambda b: [])
    rows = upstack.pull_orders(ctx(s), Span(D(2026, 7, 1), D(2026, 11, 2)))
    assert len(s.calls) == 3 and [r[0] for r in rows] == [D(2026, 7, 1), D(2026, 8, 30), D(2026, 10, 29)]


def test_empty_pull_raises_and_auth_failure_is_clear_and_secret_free():
    s = upstack_session(query=[], channel=[], attribution=lambda b: [])
    with pytest.raises(FeedEmpty):
        upstack.pull_orders(ctx(s), Span(D(2026, 11, 1), D(2026, 11, 1)))
    bad = FakeSession().on(lambda c: True, FakeResponse(401))
    with pytest.raises(AuthError) as e:
        upstack.pull_orders(ctx(bad), Span(D(2026, 11, 1), D(2026, 11, 1)))
    assert ENV["UPSTACK_API_KEY"] not in str(e.value)


def test_missing_credentials_name_the_variable():
    with pytest.raises(FeedError, match="UPSTACK_API_KEY"):
        upstack.pull_orders(make_ctx(env={}, today=D(2026, 11, 3)), Span(D(2026, 11, 1), D(2026, 11, 1)))


def test_platform_sums_accounts_and_names_platforms():
    def res(src, account, rows):
        return {"basicDetails": {"resourceSource": src, "accountName": account}, "data": {"primary": rows}}
    d = {"dt": "2026-11-01T00:00:00", "channel.spend": 100.0, "channel.purchases": 4, "channel.purchases_value": 300, "channel.new_customer_purchases": 2,
         "channel.new_customer_purchases_value": 150, "channel.link_clicks": 50, "channel.clicks": 70}
    s = upstack_session(query=[], channel=[res("googleAdsIntegration", "a", [d]), res("googleAdsIntegration", "b", [d]), res("facebookIntegration", "m", [d]),
                                          res("somethingNew", "x", [d])], attribution=lambda b: [])
    rows = upstack.pull_platform(ctx(s), Span(D(2026, 11, 1), D(2026, 11, 1)))
    by = {r[1]: r for r in rows}
    assert by["Google Ads"][2] == 200.0 and by["Google Ads"][3] == 8 and by["Google Ads"][8] == 140
    assert set(by) == {"Google Ads", "Meta (Facebook/Instagram)", "somethingNew"}


def test_attribution_one_call_per_day_last_click_only_known_sources():
    def prim(body):
        assert body["attributionModel"] == "last_click" and body["attributionWindow"] == 7 and body["dimension"] == "ad_source"
        day = body["dateRange"]["start"]
        return [{"utm_source": "Meta Ads", "orders.new_customers": 3, "orders.new_customer_total_revenue": 200, "orders.count": 5, "orders.total_revenue": 350},
                {"utm_source": "Meta Ads", "orders.new_customers": 1, "orders.new_customer_total_revenue": 50, "orders.count": 1, "orders.total_revenue": 50},
                {"utm_source": "Bing", "orders.new_customers": 9}, {"utm_source": "Google Ads", "orders.count": 2, "orders.total_revenue": 80}]
    s = upstack_session(query=[], channel=[], attribution=prim)
    rows = upstack.pull_attribution(ctx(s), Span(D(2026, 11, 1), D(2026, 11, 2)))
    assert len(s.calls) == 2 and len(rows) == 4
    meta = next(r for r in rows if r[0] == D(2026, 11, 1) and r[1] == "Meta Ads")
    assert meta == (D(2026, 11, 1), "Meta Ads", 4.0, 250.0, 6.0, 400.0)                # duplicates for one source are added


def test_traffic_and_anyclick():
    s = upstack_session(query=[{"dt": "2026-11-03T00:00:00", "tracked.new_visitors": 1483, "tracked.visitors": "3113", "tracked.sessions": 4000}], channel=[],
                        attribution=lambda b: [{"utm_campaign": "VitaBull_Test_CBO", "orders.new_customers": 5, "orders.new_customer_total_revenue": 300.456},
                                               {"utm_campaign": "BeefLiver_Scale", "orders.new_customers": 7, "orders.new_customer_total_revenue": 400},
                                               {"utm_campaign": "Empty", "orders.new_customers": 0}])
    assert upstack.pull_traffic(ctx(s), Span(D(2026, 11, 3), D(2026, 11, 3))) == [(D(2026, 11, 3), 1483, 3113, 4000)]
    rows = upstack.pull_anyclick(ctx(s), Span(D(2026, 11, 1), D(2026, 11, 1)))
    assert rows == [(D(2026, 11, 1), "Scaling", 7.0, 400.0), (D(2026, 11, 1), "Testing", 5.0, 300.46)]
    body = next(c for c in s.calls if c["json"] and c["json"].get("attributionModel") == "any_click")["json"]
    assert body["attributionWindow"] == 30 and body["dimension"] == "utm_campaign" and body["filters"] == upstack.ANYCLICK_FILTER


def test_spans_match_the_sheet_conventions():
    c = ctx(None)
    assert feed("orders").span(c, 14) == Span(D(2026, 10, 20), D(2026, 11, 2))          # through yesterday
    assert feed("traffic").span(c, 3) == Span(D(2026, 11, 1), D(2026, 11, 3))           # the sheet's traffic tab includes today
    assert feed("anyclick").days == 7


def test_feed_definitions_are_consistent():
    for f in upstack.FEEDS:
        assert set(f.key_cols) <= set(f.cols) and f.day_col in f.cols
        assert all(m.col in f.cols for m in f.metrics)
        assert f.shadow == f.main.replace("proj_feed_", "proj_feedx_")
