"""Upstack source: the pure parts (row shaping, utm -> Meta ad mapping, order allocation, table renaming). No database, no network."""
import datetime as dt

import pytest

from etl import build_metrics, upstack_api as api
from etl.upstack_build import Mapper, _split, allocate

D1, D2 = dt.date(2026, 10, 30), dt.date(2026, 10, 31)


def test_split_sums_exactly_and_remainder_goes_to_the_largest():
    for total, w in [(10.0, [1, 1, 1]), (7.0, [3, 1]), (0.07, [1, 2, 4]), (5.0, [0, 0]), (3.33, [2.5, 2.5, 1])]:
        parts = _split(total, w)
        assert round(sum(parts), 2) == round(total, 2), (total, w, parts)
        assert all(p >= 0 for p in parts)
    assert _split(7.0, [3, 1]) == [5.25, 1.75]
    assert _split(10.0, [0, 0]) == [5.0, 5.0]                 # no weights at all -> equal split


CAMPS = [("100", "VB_Scaling_US"), ("200", "VB_Testing_Cambium"), ("300", "BL Scaling")]
ADS = [("a1", "Bull-1", "100", "bull-1"), ("a2", "Bull-1", "100", "bull-1"),               # same name twice in one campaign (two ad sets)
       ("a3", "Bull-1 - Copy 2", "200", "bull-1"), ("b1", "Liver-1", "300", "liver-1"), ("z9", "Renamed-Elsewhere", "300", "renamed-elsewhere")]


def mapper():
    return Mapper(CAMPS, ADS)


@pytest.mark.parametrize("camp,ad,ids,rule", [
    ("VB_Scaling_US", "Bull-1", ["a1", "a2"], "exact"),
    ("vb_scaling_us", " bull-1 ", ["a1", "a2"], "exact"),                       # case and padding do not matter
    ("200", "Bull-1 - Copy 2", ["a3"], "exact"),                                 # utm campaign can be a raw campaign id
    ("VB_Testing_Cambium", "Bull-1", ["a3"], "family"),                          # same ad family inside the campaign (copy suffix)
    ("(not set)", "Renamed-Elsewhere", ["z9"], "name_any"),                      # campaign unknown, exact ad name found elsewhere
    ("BL Scaling", "Bull-1", ["a1", "a2"], "name_any"),                    # campaign known but the ad is not in it
    ("BL Scaling", "No-Such-Ad", [], "no_ad"),
    ("(not set)", "No-Such-Ad", [], "no_campaign"),
    ("VB_Scaling_US", "(not set)", [], "not_identified"),
    ("VB_Scaling_US", "(ad not identified)", [], "not_identified"),
])
def test_mapper_rules(camp, ad, ids, rule):
    got, r = mapper().candidates(camp, ad)
    assert (sorted(got), r) == (ids, rule)


def test_allocate_conserves_every_measure_and_reports_unmapped():
    m = mapper()
    raw = [(D1, "VB_Scaling_US", "Bull-1", 3, 300.0, 5, 500.0),             # twins a1/a2
           (D1, "BL Scaling", "Liver-1", 1, 100.0, 2, 210.0),
           (D1, "(not set)", "(not set)", 2, 90.0, 2, 90.0),                 # unmapped
           (D2, "VB_Scaling_US", "Bull-1", 1, 55.5, 1, 55.5)]
    spend = {("a1", D1): 300.0, ("a2", D1): 100.0}                           # D2: no spend that day -> lifetime weights
    life = {"a1": 10.0, "a2": 30.0}
    out, un = allocate(raw, m, spend, life)
    assert out[("a1", D1)][2] == 3.75 and out[("a2", D1)][2] == 1.25          # orders split 3:1 by that day's Meta spend
    assert out[("a1", D2)][2] == 0.25 and out[("a2", D2)][2] == 0.75          # 1:3 by lifetime spend
    for j in range(4):                                                        # nc, nc_rev, orders, rev: nothing lost, nothing invented
        mapped = sum(v[j] for v in out.values())
        raw_total = sum(r[3 + j] for r in raw if r[1] != "(not set)")
        assert round(mapped, 2) == round(raw_total, 2)
    assert [(u[1], u[3]) for u in un] == [("(not set)", "not_identified")]
    assert m.stats["exact"] == 3 and m.stats["not_identified"] == 1


def test_day_record_keeps_campaign_totals_when_the_ad_split_is_empty():
    camps = [["C1", 4.0, 400.0, 6.0, 600.0], ["C2", 0.0, 0.0, 0.0, 0.0], ["C3", 1.0, 80.0, 1.0, 80.0]]
    rec = api.day_record(camps, {"C1": [["C1", "Ad A", 3.0, 300.0, 5.0, 500.0]], "C3": []})
    assert rec["unidentified"] == ["C3"]                                       # C3 had orders but no ad rows
    assert ["C3", api.NOT_IDENTIFIED, 1.0, 80.0, 1.0, 80.0] in rec["ads"]
    assert rec["nc_campaigns"] == 5.0 and rec["nc_ads"] == 4.0                 # the gap (1 new customer in C1) is real and is reported, not filled


def test_attribution_row_shaping_sums_duplicates_and_drops_empty_rows():
    prim = [{"utm_campaign": "C1", "orders.new_customers": "2", "orders.new_customer_total_revenue": "100", "orders.count": "3", "orders.total_revenue": "150"},
            {"utm_campaign": "C1", "orders.new_customers": 1, "orders.new_customer_total_revenue": 50, "orders.count": 1, "orders.total_revenue": 60},
            {"utm_campaign": "C2", "orders.new_customers": 0, "orders.count": 0}]
    assert api.campaign_rows(prim) == [["C1", 3.0, 150.0, 4.0, 210.0]]
    assert api.ad_rows("C1", [{"utm_ad": None, "orders.count": 1, "orders.total_revenue": 9}]) == [["C1", "(not set)", 0.0, 0.0, 1.0, 9.0]]


def test_channel_rows_keep_meta_campaigns_only():
    res = [{"campaign": "120", "campaignName": "VB", "basicDetails": {"resourceSource": "facebookIntegration"},
            "data": {"primary": [{"dt": "2026-10-30T00:00:00Z", "channel.spend": "10.5", "channel.purchases": 2}, {"dt": "2026-10-31", "channel.spend": 0}]}},
           {"campaign": "g1", "campaignName": "G", "basicDetails": {"resourceSource": "googleAdsIntegration"}, "data": {"primary": [{"dt": "2026-10-30", "channel.spend": 99}]}}]
    rows = api.channel_rows(res)
    assert rows == [(D1, "120", "VB", 10.5, 2.0, 0.0, 0.0, 0.0, 0.0)]


@pytest.mark.parametrize("sql,out", [
    ("SELECT * FROM family_daily", "SELECT * FROM family_daily_upstack_nc"),
    ("FROM family_daily_active x JOIN family_daily y", "FROM family_daily_active_upstack_nc x JOIN family_daily_upstack_nc y"),
    ("FROM ad_daily ad JOIN meta_ad_lifetime ml ON ml.ad_id = ad.ad_id LEFT JOIN ad_metrics am", "FROM ad_daily_upstack_nc ad JOIN meta_ad_lifetime_upstack_nc ml ON ml.ad_id = ad.ad_id LEFT JOIN ad_metrics_upstack_nc am"),
    ("FROM unmatched_meta u JOIN meta_ads a", "FROM unmatched_meta_upstack_nc u JOIN meta_ads a"),          # meta_ads, products, tracker_rows are shared
    ("SELECT spend_launch_active FROM ad_metrics", "SELECT spend_launch_active FROM ad_metrics_upstack_nc"),  # column names are never touched
])
def test_rename_tables_swaps_whole_tokens_only(sql, out):
    assert build_metrics.rename_tables(sql, "_upstack_nc") == out
    assert build_metrics.rename_tables(out, "") == out                                                         # no suffix = untouched
