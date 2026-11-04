"""Meta feeds: reach at day/week/month grain, unique outbound clicks by product, bid settings rebuilt from the activity log. Graph is faked."""
from __future__ import annotations

import datetime as dt
import json

import pytest

from etl import meta_api
from etl.feeds import meta_feeds as M
from etl.feeds.common import AuthError, FeedEmpty, FeedError, Span, make_ctx

D = dt.date
TOKEN = "EA" + "t" * 60


def ctx(today=D(2026, 11, 3), now=None):
    return make_ctx(env={"META_ACCESS_TOKEN": TOKEN}, sleep=lambda s: None, today=today, now_utc=now or dt.datetime(2026, 11, 3, 20, 0, tzinfo=dt.timezone.utc))


class FakeGraph:
    def __init__(self, handler):
        self.handler, self.calls = handler, []

    def __call__(self, url, params, what, max_retries=7, sleep=None):
        assert params["access_token"] == TOKEN
        self.calls.append((url, {k: v for k, v in params.items() if k != "access_token"}, what))
        return self.handler(url, params)


def act(url):
    return url.split("/")[-2]


def reach_row(start, stop, spend=10.0, impr=1000, reach=800, uout=30, link=40, ul=35):
    return {"date_start": start, "date_stop": stop, "spend": str(spend), "impressions": str(impr), "reach": str(reach), "frequency": "1.25",
            "inline_link_clicks": str(link), "unique_inline_link_clicks": str(ul), "unique_outbound_clicks": [{"action_type": "outbound_click", "value": str(uout)}],
            "actions": [{"action_type": "link_click", "value": str(link)}], "unique_actions": [{"action_type": "link_click", "value": str(ul)}]}


# ---------------------------------------------------------------------------------------------------------------- reach

def test_windows_are_monday_and_month_aligned_and_end_today():
    w = M.reach_windows(Span(D(2026, 10, 21), D(2026, 11, 3)))
    assert w == {"day": (D(2026, 10, 21), D(2026, 11, 3)), "week": (D(2026, 10, 19), D(2026, 11, 3)), "month": (D(2026, 10, 1), D(2026, 11, 3))}
    assert D(2026, 10, 19).weekday() == 0


def test_period_keys_and_expected_periods():
    assert M.period_key("week", D(2026, 10, 26)) == "2026-W44" and M.period_key("month", D(2026, 11, 1)) == "2026-11" and M.period_key("day", D(2026, 11, 3)) == "2026-11-03"
    assert M.expected_periods("week", D(2026, 10, 26), D(2026, 11, 3)) == [(D(2026, 10, 26), D(2026, 11, 1)), (D(2026, 11, 2), D(2026, 11, 3))]
    assert M.expected_periods("month", D(2026, 10, 1), D(2026, 11, 3)) == [(D(2026, 10, 1), D(2026, 10, 31)), (D(2026, 11, 1), D(2026, 11, 3))]


def reach_handler(url, params):
    inc = params["time_increment"]
    tr = json.loads(params["time_range"])
    a = act(url)
    main = a == "act_1671338033619223"
    if inc == 1:
        s, e = D.fromisoformat(tr["since"]), D.fromisoformat(tr["until"])
        days = [s + dt.timedelta(days=i) for i in range((e - s).days + 1)]
        return {"data": [reach_row(d.isoformat(), d.isoformat(), 10, 1000, 800, 30) for d in days if main]}
    if inc == 7:
        return {"data": [reach_row(tr["since"], "2026-10-25", 70, 7000, 3000, 210), reach_row("2026-10-26", "2026-11-01", 70, 7000, 3000, 210),
                         reach_row("2026-11-02", "2026-11-03", 20, 2000, 1500, 60)] if main else []}
    return {"data": [reach_row("2026-10-01", "2026-10-31", 310, 31000, 9000, 930), reach_row("2026-11-01", "2026-11-03", 30, 3000, 2000, 90)] if main else []}


def test_reach_sums_accounts_zero_fills_and_rounds(monkeypatch):
    g = FakeGraph(reach_handler)
    monkeypatch.setattr(meta_api, "graph_get", g)
    rows = M.pull_meta_reach(ctx(), Span(D(2026, 10, 21), D(2026, 11, 3)))
    by = {(r[0], r[1]): r for r in rows}
    assert by[("day", "2026-11-03")] == ("day", "2026-11-03", D(2026, 11, 3), D(2026, 11, 3), 1000, 800, 35, 40, 30)
    assert by[("week", "2026-W43")][2:4] == (D(2026, 10, 19), D(2026, 10, 25)) and by[("week", "2026-W43")][8] == 210
    assert by[("month", "2026-11")][3] == D(2026, 11, 3)                     # the open month ends today
    assert ("day", "2026-10-21") in by and len([k for k in by if k[0] == "day"]) == 14
    # the BackUp account returned nothing: every period still exists (zero-filled), so sums equal the Main account's
    assert all(r[4] >= 0 for r in rows)
    assert {c[1]["time_increment"] for c in g.calls} == {1, 7, "monthly"}


def test_reach_alignment_problem_is_an_error(monkeypatch):
    def bad(url, params):
        return {"data": [reach_row("2026-10-21", "2026-10-30")] if params["time_increment"] == 7 else []}
    monkeypatch.setattr(meta_api, "graph_get", FakeGraph(bad))
    with pytest.raises(FeedError, match="Monday"):
        M.pull_meta_reach(ctx(), Span(D(2026, 10, 21), D(2026, 11, 3)))


def test_reach_reconcile_flags_week_rows_that_do_not_match_their_days():
    days = [{"grain": "day", "account": "A", "start": D(2026, 10, 26) + dt.timedelta(days=i), "end": D(2026, 10, 26) + dt.timedelta(days=i), "spend": 10.0, "impr": 100.0} for i in range(7)]
    week = {"grain": "week", "account": "A", "start": D(2026, 10, 26), "end": D(2026, 11, 1), "spend": 70.0, "impr": 700.0}
    assert M.reconcile(days + [week], D(2026, 11, 3), D(2026, 10, 20)) == []
    assert len(M.reconcile(days + [{**week, "spend": 90.0}], D(2026, 11, 3), D(2026, 10, 20))) == 1


def test_token_rejected_is_an_auth_error(monkeypatch):
    def boom(url, params, what, **kw):
        raise meta_api.TokenRejected(f"{what}: Meta rejected the access token (code 190)")
    monkeypatch.setattr(meta_api, "graph_get", boom)
    with pytest.raises(AuthError):
        M.pull_meta_reach(ctx(), Span(D(2026, 10, 21), D(2026, 11, 3)))


def test_delete_where_covers_each_grain_from_its_own_start():
    cond, params = M.reach_delete_where(Span(D(2026, 10, 21), D(2026, 11, 3)))
    assert params == (D(2026, 10, 21), D(2026, 10, 19), D(2026, 10, 1))


# ---------------------------------------------------------------------------------------------------------------- product reach

def test_classification_matches_the_sheet_rules():
    assert M.classify("VitaBull_WW_Scaling", "x") == ("VitaBull", "campaign")
    assert M.classify("All_US_Mixed", "Beef Liver adset") == ("Beef Liver", "adset")
    assert M.classify("Raging Bull | Black Bull", None)[0] == "Other" or M.classify("Raging Bull", None)[0] == "VitaBull"
    assert M.classify("Bull and Liver", None) == ("Other", "unresolved")                # two products: unresolved
    assert M.classify("Vital Belle test", None) == ("Other", "campaign")


def product_handler(url, params):
    if "filtering" in params and "adset.id" in params["filtering"]:
        ids = json.loads(params["filtering"])[0]["value"]
        tr = json.loads(params["time_range"])
        if "time_increment" in params:
            s, e = D.fromisoformat(tr["since"]), D.fromisoformat(tr["until"])
            return {"data": [{"date_start": (s + dt.timedelta(days=i)).isoformat(), "unique_outbound_clicks": [{"action_type": "outbound_click", "value": str(10 * len(ids))}]}
                             for i in range((e - s).days + 1)]}
        return {"data": [{"date_start": tr["since"], "unique_outbound_clicks": [{"action_type": "outbound_click", "value": str(100 * len(ids))}]}]}
    if act(url) == "act_1671338033619223":
        return {"data": [{"campaign_name": "VitaBull_Scale", "adset_name": "a", "adset_id": "1", "spend": "5"},
                         {"campaign_name": "VitaBull_Scale", "adset_name": "b", "adset_id": "2", "spend": "5"},
                         {"campaign_name": "BeefLiver_Test", "adset_name": "c", "adset_id": "3", "spend": "5"},
                         {"campaign_name": "Mystery", "adset_name": "z", "adset_id": "9", "spend": "5"}]}
    return {"data": []}


def test_product_reach_filters_by_adset_ids_per_product_and_respects_the_floor(monkeypatch):
    monkeypatch.setattr(meta_api, "graph_get", FakeGraph(product_handler))
    rows = M.pull_product_reach(ctx(), Span(D(2026, 10, 25), D(2026, 11, 3)))
    day = {(r[1], r[4]): r[5] for r in rows if r[0] == "day"}
    assert day[("2026-11-03", "VitaBull")] == 20 and day[("2026-11-03", "Beef Liver")] == 10                       # 2 ad sets / 1 ad set x 10
    assert not any(r[4] == "Other" for r in rows)
    week = [r for r in rows if r[0] == "week"]
    assert week and week[0][2] == D(2026, 10, 19) and week[-1][3] == D(2026, 11, 3)                                 # whole weeks, last one open
    assert all(r[2] >= M.UOC_FLOOR[r[0]] for r in rows)


def test_product_reach_empty_is_an_error(monkeypatch):
    monkeypatch.setattr(meta_api, "graph_get", FakeGraph(lambda u, p: {"data": []}))
    with pytest.raises(FeedEmpty):
        M.pull_product_reach(ctx(), Span(D(2026, 10, 25), D(2026, 11, 3)))


# ---------------------------------------------------------------------------------------------------------------- bids

def event(ts, adset, old, new, old_amt=None, new_amt=None, old_fl=None, new_fl=None):
    x = {"type": "bid_type", "old_value": old, "new_value": new, "additional_value": {"old_value": old_amt, "new_value": new_amt},
         "adset_minimum_return_on_ad_spend": {"old_value": old_fl, "new_value": new_fl}}
    return {"event_type": "update_ad_set_bid_strategy", "event_time": ts, "object_id": adset, "extra_data": json.dumps(x)}


def test_state_normalisation_and_replay():
    assert M.mk_state("COST_CAP", 6500, None) == ("COST_CAP", 65.0, None)
    assert M.mk_state("MINIMUM_ROAS", 6500, 0.95) == ("LOWEST_COST_WITH_MIN_ROAS", None, 0.95)
    assert M.mk_state("LOWEST_COST_BID_STRATEGY", 100, 1) == ("LOWEST_COST_WITHOUT_CAP", None, None)
    assert M.mk_state(None, 1, 1) is None
    ev = [event("2026-11-02T10:00:00-0800", "7", "COST_CAP", "COST_CAP", 6500, 6850), event("2026-11-02T10:00:00-0800", "7", "COST_CAP", "COST_CAP", 6500, 6850)]
    tl = M.build_timelines(ev)
    assert len(tl["7"]["tl"]) == 1                                                                   # the boundary event returned twice counts once
    pt = M.ZoneInfo("America/Los_Angeles")
    before = M.state_at(tl["7"], M.end_of_day_utc(D(2026, 11, 1), pt))
    after = M.state_at(tl["7"], M.end_of_day_utc(D(2026, 11, 2), pt))
    assert before == ("COST_CAP", 65.0, None) and after == ("COST_CAP", 68.5, None)                   # a change during the day counts for that whole day


def test_bid_rows_use_end_of_day_state_current_settings_and_unknown():
    tzs = {"Main": M.ZoneInfo("America/Los_Angeles")}
    tl = M.build_timelines([event("2026-11-02T10:00:00-0800", "7", "COST_CAP", "COST_CAP", 6500, 6850)])
    cur = {"8": ("LOWEST_COST_WITH_MIN_ROAS", None, 0.95), "7": ("COST_CAP", 68.5, None)}
    rows = M.build_bid_rows([(D(2026, 11, 1), "Main", "7", 100.0), (D(2026, 11, 2), "Main", "7", 100.0), (D(2026, 11, 2), "Main", "8", 50.0),
                             (D(2026, 11, 2), "Main", "gone", 10.0)], tl, cur, tzs, dt.datetime(2026, 11, 3, tzinfo=dt.timezone.utc))
    assert rows[0][2:6] == (100.0, 6500.0, 0.0, 0.0) and rows[1][3] == 6850.0                         # cap changes between days
    assert rows[2][4:6] == (50.0, 47.5) and rows[3][6] == "UNKNOWN"


def bids_handler(url, params):
    if url.endswith("/insights"):
        tr = json.loads(params["time_range"])
        s, e = D.fromisoformat(tr["since"]), D.fromisoformat(tr["until"])
        if act(url) != "act_1671338033619223":
            return {"data": []}
        return {"data": [{"adset_id": "7", "adset_name": "n", "campaign_id": "c", "date_start": (s + dt.timedelta(days=i)).isoformat(), "spend": "100"} for i in range((e - s).days + 1)]}
    if url.endswith("/activities"):
        return {"data": []}
    if url.endswith("/campaigns"):
        return {"data": [{"id": "c", "bid_strategy": "LOWEST_COST_WITH_MIN_ROAS"}]}
    if url.endswith("/adsets"):
        return {"data": [{"id": "7", "campaign_id": "c", "bid_strategy": "COST_CAP", "bid_amount": 6500, "updated_time": "2026-10-01T00:00:00+0000"}]} if act(url) == "act_1671338033619223" else {"data": []}
    raise AssertionError(url)


def test_bids_daily_sums_with_current_settings_when_the_log_is_empty(monkeypatch):
    monkeypatch.setattr(meta_api, "graph_get", FakeGraph(bids_handler))
    rows = M.pull_bids(ctx(), Span(D(2026, 11, 1), D(2026, 11, 3)))
    assert rows[0] == (D(2026, 11, 1), 100.0, 100.0, 6500.0, 0.0, 0.0)
    assert [r[0] for r in rows] == [D(2026, 11, 1), D(2026, 11, 2), D(2026, 11, 3)]
    assert rows[1][3] == 6500.0


def test_feed_definitions_are_consistent():
    for f in M.FEEDS:
        assert set(f.key_cols) <= set(f.cols) and f.day_col in f.cols and all(m.col in f.cols for m in f.metrics)
        assert f.shadow == f.main.replace("proj_feed_", "proj_feedx_")
