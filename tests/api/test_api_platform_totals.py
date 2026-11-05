"""Marketing metrics, All views: Total spend and Total NC ROAS (app/platforms.kpis / total_series).
Total NC ROAS = actual new-customer revenue (Upstack orders, counted once) / total spend (Meta + Google) over the days Meta, Google and the orders feed all cover;
the platforms' attributed NC revenue is shown beside it with the gap. Everything is recomputed here from independent endpoints."""
import datetime as dt

import psycopg
import pytest

from .conftest import DB_URL
from .helpers import get_ok
from .test_api_google import DAYS, N, W, google  # noqa: F401  (fixture: Google tables + the Meta fixture window)
from .test_api_source import upstack  # noqa: F401  (fixture: Meta Upstack attribution for the last 3 days)

ORDERS = {d: (1000.0 + 137.0 * i * i, 10 + i) for i, d in enumerate(DAYS) if i > 0}       # the first day has no orders: it must read blank, never zero
SPEND_DAYS = [d for d in DAYS]


@pytest.fixture(scope="module")
def orders(google, upstack):                                                              # noqa: F811
    with psycopg.connect(DB_URL) as conn:
        conn.execute("TRUNCATE upstack_orders_daily")
        for d, (rev, n) in ORDERS.items():
            conn.execute("INSERT INTO upstack_orders_daily (day, nc_rev, nc_orders, total_rev, orders) VALUES (%s,%s,%s,%s,%s)", (d, rev, n, rev * 4, n * 3))
        conn.commit()
    return ORDERS


def kpi(client, extra="", w=W):
    return get_ok(client, f"/api/platforms/kpis?{w}{extra}")


def daily_spend(client, extra=""):
    """Meta and Google spend per day from the two independent day-grain trend endpoints"""
    m = {p["t"]: p["spend"] for p in get_ok(client, f"/api/campaigns/trend?{W}&grain=day{extra}")["points"]}
    g = {p["t"]: p["spend"] for p in get_ok(client, f"/api/google/trend?{W}&grain=day")["points"]}
    return m, g


def test_total_spend_is_meta_plus_google_and_ties_to_table_chart_and_strip(orders, client):
    k = kpi(client)["total"]
    table = get_ok(client, f"/api/campaigns/all?{W}")
    chart = get_ok(client, f"/api/platforms/trend?{W}&grain=day")
    m, g = daily_spend(client)
    assert k["spend"] == pytest.approx(sum(m.values()) + sum(g.values()), abs=0.005)
    assert k["meta_spend"] == pytest.approx(sum(m.values()), abs=0.005) and k["google_spend"] == pytest.approx(sum(g.values()), abs=0.005)
    assert k["spend"] == pytest.approx(round(k["meta_spend"] + k["google_spend"], 2), abs=0.005)
    assert k["spend"] == table["total"]["spend"] == pytest.approx(sum(p["spend"] for p in chart["total"]["points"] if p), abs=0.005)      # strip = table total row = chart total
    assert k["spend"] == pytest.approx(sum(pt["spend"] for pt in chart["points"]), abs=0.005)
    assert k["meta_share"] + k["google_share"] == pytest.approx(1.0, abs=0.0002) and k["meta_share"] > k["google_share"]
    assert k["spend"] > N * 200.0                                                         # Google's N x 200 plus Meta's


def test_total_nc_roas_is_actual_new_customer_revenue_over_spend_recomputed_independently(orders, client):
    k = kpi(client)["total"]
    m, g = daily_spend(client)
    covered = [d for d in DAYS if d in orders]                                           # days with Meta, Google AND orders
    assert len(covered) == len(DAYS) - 1
    rev = sum(orders[d][0] for d in covered)
    spend = sum(m[d.isoformat()] + g[d.isoformat()] for d in covered)
    nc = k["nc"]
    assert nc["available"] and nc["rev"] == pytest.approx(rev, abs=0.005) and nc["spend"] == pytest.approx(spend, abs=0.005)
    assert nc["roas"] == pytest.approx(rev / spend, abs=0.00006)
    assert nc["days"] == len(covered) and nc["from"] == str(covered[0]) and nc["to"] == str(covered[-1]) and nc["orders"] == sum(orders[d][1] for d in covered)
    assert "of" in nc["partial"]                                                          # the blank day is said, not hidden
    assert k["spend"] > nc["spend"]                                                       # the headline spend is the whole window, the ratio uses covered days only


def test_attributed_sum_and_the_gap_are_shown_next_to_actual_nc_revenue(orders, client):
    nc = kpi(client)["total"]["nc"]
    covered = [d.isoformat() for d in DAYS if d in orders]
    meta_up = {p["t"]: p["revenue"] for p in get_ok(client, f"/api/campaigns/trend?{W}&grain=day&source=upstack&basis=nc")["points"]}
    google_up = {p["t"]: p["revenue"] for p in get_ok(client, f"/api/google/trend?{W}&grain=day&source=upstack&basis=nc")["points"]}
    a = nc["attributed"]
    assert a["meta"] == pytest.approx(sum(meta_up.get(d, 0) for d in covered), abs=0.005) and a["google"] == pytest.approx(sum(google_up.get(d, 0) for d in covered), abs=0.005)
    assert a["sum"] == pytest.approx(a["meta"] + a["google"], abs=0.005)
    assert a["gap"] == pytest.approx(a["sum"] - nc["rev"], abs=0.005) and a["gap_pct"] == pytest.approx(a["gap"] / nc["rev"], abs=0.0002)
    assert a["meta"] > 0 and a["google"] > 0


@pytest.mark.parametrize("grain", ["day", "week", "month"])
def test_chart_total_nc_roas_is_a_ratio_of_sums_in_every_grain(orders, client, grain):
    t = get_ok(client, f"/api/platforms/trend?{W}&grain={grain}")
    tot = t["total"]
    assert tot["available"] and len(tot["points"]) == len(t["labels"])
    m, g = daily_spend(client)
    for lab, pt in zip(t["labels"], tot["points"]):
        d0 = dt.date.fromisoformat(lab)
        key = (lambda d: d) if grain == "day" else (lambda d: d - dt.timedelta(days=d.weekday())) if grain == "week" else (lambda d: d.replace(day=1))
        days = [d for d in DAYS if key(d) == d0]
        cov = [d for d in days if d in orders]
        want_sp = sum(m[d.isoformat()] + g[d.isoformat()] for d in days)
        assert pt["spend"] == pytest.approx(want_sp, abs=0.01), (grain, lab)
        if cov:
            rev = sum(orders[d][0] for d in cov)
            sp = sum(m[d.isoformat()] + g[d.isoformat()] for d in cov)
            assert pt["nc_roas"] == pytest.approx(rev / sp, abs=0.00006), (grain, lab)
            assert pt["nc_days"] == len(cov) and pt["meta_spend"] + pt["google_spend"] == pytest.approx(pt["spend"], abs=0.01)
        else:
            assert pt["nc_roas"] is None and pt["nc_rev"] is None                          # no order data that day: blank, not zero
    # whatever the grain, the buckets weigh back to the strip's ratio
    k = kpi(client)["total"]["nc"]
    revs = sum(p["nc_rev"] for p in tot["points"] if p and p["nc_rev"] is not None)
    sps = sum(p["nc_spend"] for p in tot["points"] if p and p["nc_spend"] is not None)
    assert revs / sps == pytest.approx(k["roas"], abs=0.00006)


def test_a_week_is_not_the_mean_of_its_daily_ratios(orders, client):
    day = get_ok(client, f"/api/platforms/trend?{W}&grain=day")["total"]["points"]
    week = get_ok(client, f"/api/platforms/trend?{W}&grain=week")["total"]["points"]
    ratios = [p["nc_roas"] for p in day if p and p["nc_roas"] is not None]
    if len({round(r, 4) for r in ratios}) > 1:
        mean = sum(ratios) / len(ratios)
        whole = sum(p["nc_rev"] for p in week if p and p["nc_rev"]) / sum(p["nc_spend"] for p in week if p and p["nc_spend"])
        assert abs(whole - mean) > 1e-5 or len(week) == 1                                   # the fixture's daily ratios differ, so the two definitions differ


def test_the_day_without_orders_is_blank_in_the_chart_and_kept_in_spend(orders, client):
    t = get_ok(client, f"/api/platforms/trend?{W}&grain=day")
    first = t["total"]["points"][t["labels"].index(str(DAYS[0]))]
    assert first["nc_roas"] is None and first["nc_rev"] is None and first["spend"] > 0 and first["nc_days"] == 0


BASES = {"own": "", "nc": "&source=upstack&basis=nc", "all": "&source=upstack&basis=all"}


def table_sub(client, extra, pf_key, w=W):
    t = get_ok(client, f"/api/campaigns/all?{w}{extra}")
    return t, t["subtotals"][pf_key]["m"]


def test_blended_view_is_labelled_all_and_has_no_platform_block(orders, client):
    k = kpi(client)
    assert k["view"] == "all" and "m" not in k["total"] and k["total"]["nc"]["available"]


@pytest.mark.parametrize("basis", ["own", "nc", "all"])
@pytest.mark.parametrize("key", ["meta", "google"])
def test_platform_filter_shows_that_platforms_own_figures_equal_to_the_table_subtotal(orders, client, key, basis):
    extra = BASES[basis]
    k = get_ok(client, f"/api/platforms/kpis?{W}&only={key}{extra}")
    t, sub = table_sub(client, f"&only={key}{extra}", key)
    tot = k["total"]
    assert k["view"] == key and tot["view"] == key and tot["name"] == key.capitalize() and tot["platform"] == key.capitalize() and tot["numbers"] == basis
    assert tot["spend"] == sub["spend"] == t["total"]["spend"] and tot[f"{key}_spend"] == tot["spend"]       # KPI spend = the table's subtotal = the table's total row
    for m in ("purchases", "revenue", "roas", "cpa"):
        assert tot["m"][m] == sub[m], (key, basis, m)                                                     # revenue and ROAS on the basis in force: the platform-attributed numbers
    assert tot["basis"] == t["basis"][key]
    assert tot["labels"] == {"own": {"revenue": "Revenue", "roas": "ROAS"}, "nc": {"revenue": "NC revenue", "roas": "NC ROAS"}, "all": {"revenue": "Revenue", "roas": "ROAS"}}[basis]
    other = "google" if key == "meta" else "meta"
    assert tot[f"{other}_spend"] == 0.0 and tot[f"{key}_share"] == 1.0                                       # no blended total, no other platform
    assert "nc" not in tot


def test_platform_only_spend_equals_the_all_view_subtotals_and_they_add_to_the_blended_total(orders, client):
    t = get_ok(client, f"/api/campaigns/all?{W}")                                                             # Platform = All: both subtotals in one table
    meta_k = get_ok(client, f"/api/platforms/kpis?{W}&only=meta")["total"]
    google_k = get_ok(client, f"/api/platforms/kpis?{W}&only=google")["total"]
    blended = kpi(client)["total"]
    assert meta_k["spend"] == t["subtotals"]["meta"]["m"]["spend"] and google_k["spend"] == t["subtotals"]["google"]["m"]["spend"]
    assert round(meta_k["spend"] + google_k["spend"], 2) == blended["spend"] == t["total"]["spend"]
    assert blended["meta_spend"] == meta_k["spend"] and blended["google_spend"] == google_k["spend"]


def test_product_and_type_filters_belong_to_one_platform_and_show_its_own_figures(orders, client):
    k = get_ok(client, f"/api/platforms/kpis?{W}&product=vita-bull{BASES['nc']}")
    t, sub = table_sub(client, f"&product=vita-bull{BASES['nc']}", "meta")
    assert k["view"] == "meta" and k["total"]["spend"] == sub["spend"] == t["total"]["spend"] > 0 and k["total"]["m"]["roas"] == sub["roas"]
    assert k["total"]["name"] == "Meta · Vita Bull" and k["total"]["platform"] == "Meta"                       # the label says which slice of the platform it is
    k = get_ok(client, f"/api/platforms/kpis?{W}&type=Search")
    t, sub = table_sub(client, "&type=Search", "google")
    assert k["view"] == "google" and k["total"]["spend"] == sub["spend"] == t["total"]["spend"] > 0 and k["total"]["m"]["revenue"] == sub["revenue"]
    assert k["total"]["name"] == "Google · Search"


@pytest.mark.parametrize("extra", ["&only=meta", "&only=google", "&product=vita-bull", "&type=Search"])
def test_a_filtered_chart_follows_the_same_platform_and_its_spend_ties_to_the_strip(orders, client, extra):
    k = get_ok(client, f"/api/platforms/kpis?{W}{extra}")
    chart = get_ok(client, f"/api/platforms/trend?{W}{extra}")
    assert chart["total"]["available"] is False and all(p is None or p["nc_roas"] is None for p in chart["total"]["points"])      # no blended ratio on a platform view
    assert round(sum(p["spend"] for p in chart["total"]["points"] if p), 2) == pytest.approx(k["total"]["spend"], abs=0.01)
    assert {g["key"] for g in chart["groups"]} == {k["view"]}                                                 # one platform's bars and lines


def test_compare_window_gets_its_own_totals_and_deltas(orders, client):
    d_a, d_b = DAYS[-1], DAYS[-2]                                                         # both days are inside Meta's fixture data and Google's
    k = get_ok(client, f"/api/platforms/kpis?from={d_a}&to={d_a}&compare=prev")
    assert k["compare"] is not None and k["compare_info"]["from"] == k["compare_info"]["to"] == str(d_b)
    m = {p["t"]: p["spend"] for p in get_ok(client, f"/api/campaigns/trend?from={d_b}&to={d_b}&grain=day")["points"]}
    g = {p["t"]: p["spend"] for p in get_ok(client, f"/api/google/trend?from={d_b}&to={d_b}&grain=day")["points"]}
    assert k["compare"]["spend"] == pytest.approx(sum(m.values()) + sum(g.values()), abs=0.005)
    assert k["compare"]["nc"]["rev"] == pytest.approx(orders[d_b][0], abs=0.005)
    assert k["delta"]["spend_pct"] == pytest.approx((k["total"]["spend"] - k["compare"]["spend"]) / k["compare"]["spend"], abs=0.0002)
    assert k["delta"]["nc_rev_pct"] == pytest.approx((orders[d_a][0] - orders[d_b][0]) / orders[d_b][0], abs=0.0002)
    assert get_ok(client, f"/api/platforms/kpis?{W}")["compare"] is None
    assert get_ok(client, "/api/platforms/kpis?window=launch")["compare"] is None


def test_compare_window_of_a_platform_view_is_that_platforms_window_b(orders, client):
    d_a, d_b = DAYS[-1], DAYS[-2]
    k = get_ok(client, f"/api/platforms/kpis?from={d_a}&to={d_a}&compare=prev&only=meta{BASES['nc']}")
    b = get_ok(client, f"/api/campaigns/all?from={d_b}&to={d_b}&only=meta{BASES['nc']}")["subtotals"]["meta"]["m"]
    assert k["view"] == "meta" and k["compare"]["spend"] == b["spend"] and k["compare"]["m"]["roas"] == b["roas"] and k["compare_info"]["from"] == str(d_b)
    assert k["delta"]["spend_pct"] == pytest.approx((k["total"]["spend"] - b["spend"]) / b["spend"], abs=0.0002)


def test_a_compare_window_before_a_platforms_data_says_so_instead_of_failing(orders, client):
    # window B (the four days before DAYS[3]) reaches back past where Meta's fixture data starts
    k = get_ok(client, f"/api/platforms/kpis?from={DAYS[3]}&to={DAYS[-1]}&compare=prev")
    assert k["compare"] is None and k["delta"] is None and k["total"]["spend"] > 0
    assert any("comparison window starts before" in n for n in k["notes"])


def test_since_launch_is_the_days_every_platform_has_and_names_it(orders, client):
    k = get_ok(client, "/api/platforms/kpis?window=launch")
    assert "every platform has data" in k["window_info"]["note"] and k["total"]["window"]["from"] == str(DAYS[0]) and k["total"]["window"]["to"] == str(DAYS[-1])
    assert k["platform_windows"]["meta"]["from"] == k["platform_windows"]["google"]["from"] == str(DAYS[0])


def test_missing_order_data_hides_the_ratio_and_keeps_spend(google, client):
    with psycopg.connect(DB_URL) as conn:
        conn.execute("TRUNCATE upstack_orders_daily")
        conn.commit()
    k = kpi(client)["total"]
    assert k["nc"]["available"] is False and "not loaded yet" in k["nc"]["reason"] and k["spend"] > 0
    t = get_ok(client, f"/api/platforms/trend?{W}")
    assert t["total"]["available"] is False and all(p is None or p["nc_roas"] is None for p in t["total"]["points"])
    assert get_ok(client, f"/api/platforms/kpis?{W}&only=bogus".replace("only=bogus", "only=meta"))["total"]["spend"] > 0
    assert client.get(f"/api/platforms/kpis?{W}&only=tiktok").status_code == 400
    assert client.get(f"/api/platforms/kpis?{W}&product=vita-bull&type=Search").status_code == 400
