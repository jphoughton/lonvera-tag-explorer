"""Trends (/api/trends): Day | Week | Month with the change vs the previous period.

Own throwaway database loaded with a small, hand-checkable account (see the generator functions below: every expected number is computed from them with plain
Python sums, independently of app/trends.py). `as_of` pins "today", so nothing here depends on the clock.

Shape of the data: days 2025-12-01 .. 2026-11-04. Meta = the sheet-fed campaign history (proj_feed_meta_day, 'sheet') for 2025-12-01 .. 2026-05-05 with a two-day
hole on Jan 10-11 2026, and ad_daily from 2026-05-06; Upstack attribution of Meta from 2026-05-06, of Google from 2026-01-15; the orders feed has no row on
2026-03-03 (a day without orders is absent, a real zero).
"""
from __future__ import annotations

import datetime as dt

import psycopg
import pytest

from app import trends as T

from . import seed_fixture as S
from .conftest import DB_NAME, PASSWORD, PG_BASE, make_app

D = dt.date
TRN_DB = f"{DB_NAME}_trends"
TRN_URL = f"{PG_BASE}/{TRN_DB}"
FIRST, LAST = D(2025, 12, 1), D(2026, 11, 4)
APP_FIRST = D(2026, 5, 6)
HOLE = (D(2026, 1, 10), D(2026, 1, 11))
G_UP_FIRST = D(2026, 1, 15)
NO_ORDERS_DAY = D(2026, 3, 3)
ANYCLICK_FIRST = D(2026, 1, 1)
TODAY = D(2026, 11, 5)
TRAFFIC_FIRST = D(2026, 1, 20)                        # the traffic feed (new visitors) starts here
TRAFFIC_HOLE = D(2026, 4, 14)                         # one day the feed lacks
ZERO_VISITORS_DAY = D(2026, 6, 9)                     # a real zero


def rng(a, b):
    return list(T.daterange(a, b))


# ----------------------------------------------------------------------------- the account (integers only, so every sum is exact)
def ms(d):   return 100 + 10 * d.weekday() + d.day % 5               # Meta spend
def ts(d):   return ms(d) // 4                                         # of which testing
def mi(d):   return ms(d) * 100                                        # impressions
def mc(d):   return ms(d) // 2                                         # link clicks
def mp(d):   return 2 + d.weekday()                                    # Meta-reported purchases
def mv(d):   return 80 * mp(d)                                         # Meta-reported value
def unc(d):  return 1 + d.weekday() % 3                                # Upstack new customers credited to the ad
def uall(d): return mp(d) + 1                                          # Upstack orders credited
def gs(d):   return 20 + d.day                                         # Google spend
def gi(d):   return gs(d) * 50
def gc(d):   return gs(d) // 2
def gp(d):   return d.day % 4
def gnc(d):  return 1 + d.day % 2 if d >= G_UP_FIRST else 0
def gall(d): return gnc(d) + 1 if d >= G_UP_FIRST else 0
def onc(d):  return 0 if d == NO_ORDERS_DAY else 3 + d.weekday()       # actual new customers
def orev(d): return 0 if d == NO_ORDERS_DAY else 300 + 10 * d.weekday()  # actual NC revenue
def orc(d):  return 0 if d == NO_ORDERS_DAY else 500 + d.day
def tnc(d):  return 1 + d.day % 3                                       # any-click new customers, Testing campaigns
def snc(d):  return 2 + d.weekday()
def ssum(f, a, b):  return sum(f(d) for d in rng(a, b))
def nv(d):   return 0 if d == ZERO_VISITORS_DAY else 1000 + 37 * d.weekday() + d.day   # tracked new visitors
def meta_present(d): return not (HOLE[0] <= d <= HOLE[1])


def week_reach(m: dt.date) -> int:    return 5000 + (m.toordinal() % 11) * 100
def month_reach(m: dt.date) -> int:   return 20000 + (m.toordinal() % 13) * 100


@pytest.fixture(scope="module")
def tdb():
    try:
        S.recreate_db(f"{PG_BASE}/postgres", TRN_DB)
        conn = psycopg.connect(TRN_URL)
    except psycopg.OperationalError as e:
        pytest.skip(f"Postgres not available for API tests: {e}")
    with conn:
        S.apply_migrations(conn)
        conn.execute("INSERT INTO meta_campaigns (campaign_id, account, name, stage) VALUES ('c-test','Main','VB_Test_1','testing'),('c-scale','Main','VB_Scaling_1','scaling')")
        days = rng(FIRST, LAST)
        sheet_days = [d for d in rng(FIRST, APP_FIRST - dt.timedelta(days=1)) if meta_present(d)]
        app_days = rng(APP_FIRST, LAST)
        with conn.cursor() as cur:
            cur.executemany("""INSERT INTO proj_feed_meta_day (day, campaign_id, source, campaign_name, product, stage, spend, impressions, link_clicks, purchases, purchase_value)
                               VALUES (%s,%s,'sheet',%s,'VitaBull',%s,%s,%s,%s,%s,%s)""",
                            [row for d in sheet_days for row in (
                                (d, "c-test", "VB_Test_1", "Testing", ts(d), 0, 0, 0, 0),
                                (d, "c-scale", "VB_Scaling_1", "Scaling", ms(d) - ts(d), mi(d), mc(d), mp(d), mv(d)))])
            # a stray 'sheet' row on an app day must be ignored (ad_daily is the source from its first day on)
            cur.execute("""INSERT INTO proj_feed_meta_day (day, campaign_id, source, campaign_name, product, stage, spend, impressions, link_clicks, purchases, purchase_value)
                           VALUES (%s,'c-scale','sheet','VB_Scaling_1','VitaBull','Scaling',99999,1,1,1,1)""", (APP_FIRST,))
            cur.executemany("""INSERT INTO ad_daily (ad_id, day, meta_family_key, meta_family, campaign_id, spend, purchases, conv_value, link_clicks, impressions, source)
                               VALUES (%s,%s,'f','F',%s,%s,%s,%s,%s,%s,'meta_api')""",
                            [row for d in app_days for row in (
                                ("a-test", d, "c-test", ts(d), 0, 0, 0, 0),
                                ("a-scale", d, "c-scale", ms(d) - ts(d), mp(d), mv(d), mc(d), mi(d)))])
            cur.executemany("""INSERT INTO ad_daily_upstack (ad_id, day, meta_family_key, meta_family, campaign_id, spend, purchases, conv_value, nc_purchases, nc_conv_value, link_clicks)
                               VALUES ('a-scale',%s,'f','F','c-scale',999,%s,%s,%s,%s,0)""",
                            [(d, uall(d), 70 * uall(d), unc(d), 50 * unc(d)) for d in app_days])
            cur.executemany("""INSERT INTO google_daily (day, campaign_key, campaign_name, campaign_type, in_feed, spend, impressions, clicks, purchases, purchases_value, nc, nc_rev, orders, rev)
                               VALUES (%s,'g1','G One','Search',true,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                            [(d, gs(d), gi(d), gc(d), gp(d), 90 * gp(d), gnc(d), 40 * gnc(d), gall(d), 60 * gall(d)) for d in days])
            cur.executemany("INSERT INTO upstack_orders_daily (day, nc_rev, rc_rev, nc_orders, total_rev, orders) VALUES (%s,%s,%s,%s,%s,%s)",
                            [(d, orev(d), orc(d), onc(d), orev(d) + orc(d), onc(d) + 5) for d in days if d != NO_ORDERS_DAY])
            prods = [("VitaBull", 2), ("Beef Liver", 4), ("Boneflex", 8)]
            cur.executemany("INSERT INTO proj_feed_meta_product (day, product, source, spend, link_clicks) VALUES (%s,%s,%s,%s,0)",
                            [(d, "VitaBull", "sheet", 77777) for d in (APP_FIRST, APP_FIRST + dt.timedelta(days=1))]            # ignored: those days have 'app' rows
                            + [(d, name, "sheet" if d < APP_FIRST else "app", ms(d) // div) for d in days if meta_present(d) for name, div in prods]
                            + [(d, "Other", "sheet" if d < APP_FIRST else "app", ms(d) - ms(d) // 2 - ms(d) // 4 - ms(d) // 8) for d in days if meta_present(d)])
            cur.executemany("INSERT INTO proj_feed_nc_product (day, product, nc_rev, nc_orders) VALUES (%s,%s,%s,%s)",
                            [(d, name, orev(d) // div, onc(d) // div) for d in days if d != NO_ORDERS_DAY for name, div in prods])
            # traffic feed: Jan 20 on, one missing day, and a partial row for today (999999) that must never be read
            cur.executemany("INSERT INTO proj_feed_traffic (day, new_visitors, visitors, sessions) VALUES (%s,%s,%s,%s)",
                            [(d, nv(d), nv(d) * 2, nv(d) * 3) for d in rng(TRAFFIC_FIRST, LAST) if d != TRAFFIC_HOLE] + [(TODAY, 999999, 999999, 999999)])
            cur.executemany("INSERT INTO proj_feed_anyclick (day, stage, nc, nc_rev) VALUES (%s,%s,%s,0)",
                            [(d, st, f(d)) for d in rng(ANYCLICK_FIRST, LAST) for st, f in (("Testing", tnc), ("Scaling", snc))])
            # reach feed: day rows for the last 60 days, week rows (ISO, Monday start), month rows; the current periods run through today (partial)
            reach_rows = []
            for d in rng(TODAY - dt.timedelta(days=60), TODAY):
                reach_rows.append(("day", d.isoformat(), d, d, 1000 + d.day, 700 + d.day, 5 + d.day))
            m = D(2025, 12, 1)
            while m <= TODAY:
                end = min(m + dt.timedelta(days=6), TODAY)
                if m == D(2026, 5, 11):
                    end = D(2026, 5, 15)             # an incomplete closed week: its reach must show a dash
                imp = ssum(mi, m, min(end, LAST))
                reach_rows.append(("week", f"{m.isocalendar()[0]}-W{m.isocalendar()[1]:02d}", m, end, imp, week_reach(m), 300 + m.toordinal() % 7))
                m += dt.timedelta(days=7)
            mm = D(2025, 12, 1)
            while mm <= TODAY:
                end = min(T.month_end(mm), TODAY)
                reach_rows.append(("month", mm.strftime("%Y-%m"), mm, end, ssum(mi, mm, min(end, LAST)), month_reach(mm), 1000 + mm.month))
                mm = T.month_end(mm) + dt.timedelta(days=1)
            cur.executemany("INSERT INTO proj_feed_meta_reach (grain, period_key, period_start, period_end, impressions, reach, unique_outbound_clicks) VALUES (%s,%s,%s,%s,%s,%s,%s)", reach_rows)
        conn.execute("INSERT INTO app_meta (key, value) VALUES ('google_upstack_first_day', %s), ('daily_start', %s)", (G_UP_FIRST.isoformat(), APP_FIRST.isoformat()))
        conn.commit()
    return True


@pytest.fixture(scope="module")
def tapp(tdb):
    a = make_app(DATABASE_URL=TRN_URL)
    yield a
    a.extensions["pool"].close()


@pytest.fixture()
def tc(tapp):
    c = tapp.test_client()
    assert c.post("/api/login", json={"username": "JoshHoughton", "password": PASSWORD}).status_code == 204
    return c


def get(c, **kw):
    kw.setdefault("as_of", TODAY.isoformat())
    r = c.get("/api/trends", query_string=kw)
    assert r.status_code == 200, r.get_data(as_text=True)[:400]
    return r.get_json()


def by_start(d, start):
    return next(r for r in d["rows"] if r["start"] == start.isoformat())


def approx(a, b, tol=0.006):
    return a is not None and abs(a - b) <= tol


# ----------------------------------------------------------------------------- pure functions (no database)
def test_pacific_today_is_dst_aware():
    utc = dt.timezone.utc
    assert T.pacific_today(dt.datetime(2026, 11, 2, 7, 30, tzinfo=utc)) == D(2026, 11, 1)      # 23:30 PST on the 25-hour day
    assert T.pacific_today(dt.datetime(2026, 11, 1, 8, 30, tzinfo=utc)) == D(2026, 11, 1)      # 01:30 PDT (first 1 a.m.)
    assert T.pacific_today(dt.datetime(2026, 11, 1, 9, 30, tzinfo=utc)) == D(2026, 11, 1)      # 01:30 PST (second 1 a.m.)
    assert T.pacific_today(dt.datetime(2026, 11, 1, 6, 59, tzinfo=utc)) == D(2026, 10, 31)     # 23:59 PDT
    assert T.pacific_today(dt.datetime(2026, 3, 8, 10, 30, tzinfo=utc)) == D(2026, 3, 8)       # 03:30 PDT after the 23-hour spring day starts
    assert T.pacific_today(dt.datetime(2026, 3, 9, 7, 5, tzinfo=utc)) == D(2026, 3, 9)         # 00:05 PDT


def test_iso_weeks_and_month_spans():
    assert T.span_of("week", D(2026, 1, 1)) == (D(2025, 12, 29), D(2026, 1, 4))        # ISO 2026-W01 starts in 2025
    assert T.label_of("week", D(2025, 12, 29), D(2026, 1, 4)) == ("W01", "Dec 29, 2025 – Jan 4, 2026")
    assert T.span_of("week", D(2026, 11, 1)) == (D(2026, 10, 26), D(2026, 11, 1))      # Sunday belongs to the week that began Monday
    assert T.span_of("month", D(2028, 2, 10)) == (D(2028, 2, 1), D(2028, 2, 29))
    assert T.previous_span("month", D(2026, 3, 1)) == (D(2026, 2, 1), D(2026, 2, 28))
    assert T.previous_span("month", D(2026, 1, 1)) == (D(2025, 12, 1), D(2025, 12, 31))


def test_change_of_polarity_free_math():
    assert T.change_of("money", 120.0, 100.0) == {"v": 0.2, "t": "pct"}
    assert T.change_of("money", 120.0, 0.0) is None and T.change_of("money", None, 5.0) is None and T.change_of("roas", 1.0, None) is None
    assert T.change_of("rate", 0.031, 0.020) == {"v": 0.011, "t": "pp"}
    assert T.change_of("rate", 0.031, 0.0) == {"v": 0.031, "t": "pp"}                 # a rate may change from zero: points, not percent


def test_build_periods_like_for_like_rules():
    through = D(2026, 11, 4)
    mon = T.build_periods("month", D(2026, 11, 5), through, 3)
    assert [p["label"] for p in mon] == ["Nov 2026", "Oct 2026", "Sep 2026"]
    nov = mon[0]
    assert nov["state"] == "open" and nov["days_cov"] == 4 and nov["compare"]["kind"] == "like_for_like"
    assert (nov["compare"]["start"], nov["compare"]["end"]) == (D(2026, 10, 1), D(2026, 10, 4))
    assert mon[1]["compare"]["kind"] == "previous" and (mon[1]["compare"]["start"], mon[1]["compare"]["end"]) == (D(2026, 9, 1), D(2026, 9, 30))
    wk = T.build_periods("week", D(2026, 11, 5), through, 2)
    assert wk[0]["days_cov"] == 3 and (wk[0]["compare"]["start"], wk[0]["compare"]["end"]) == (D(2026, 10, 26), D(2026, 10, 28))     # Mon-Wed of last week
    day = T.build_periods("day", D(2026, 11, 5), through, 3)
    assert [p["state"] for p in day] == ["not_loaded", "closed", "closed", "closed"] and day[0]["compare"]["kind"] == "none"
    assert day[1]["compare"]["kind"] == "previous" and day[1]["compare"]["start"] == D(2026, 11, 3)


def test_like_for_like_never_compares_a_longer_partial_with_a_shorter_month():
    # March 30 days elapsed against a 28-day February: no base, a stated reason instead of a misleading change
    mon = T.build_periods("month", D(2026, 3, 31), D(2026, 3, 30), 2)
    assert mon[0]["state"] == "open" and mon[0]["compare"]["kind"] == "none" and "28 days" in mon[0]["compare"]["why"]
    ok = T.build_periods("month", D(2026, 3, 29), D(2026, 3, 28), 2)
    assert ok[0]["compare"]["kind"] == "like_for_like" and ok[0]["compare"]["end"] == D(2026, 2, 28)


# ----------------------------------------------------------------------------- API
def test_requires_login(tapp):
    assert tapp.test_client().get("/api/trends").status_code == 401


def test_parameter_validation(tc):
    for qs in ({"interval": "year"}, {"periods": "1"}, {"periods": "x"}, {"interval": "month", "periods": "37"}, {"interval": "day", "periods": "121"}, {"numbers": "bogus"}, {"as_of": "nope"}):
        r = tc.get("/api/trends", query_string=qs)
        assert r.status_code == 400, qs
        assert r.get_json()["error"]


def test_default_spans_and_row_counts(tc):
    d = get(tc, interval="day")
    assert d["periods"] == 14 and len(d["rows"]) == 15 and d["rows"][0]["state"] == "not_loaded"      # 14 loaded days + today's placeholder
    assert get(tc)["interval"] == "week" and len(get(tc)["rows"]) == 12
    m = get(tc, interval="month")
    assert m["periods"] == 12 and len(m["rows"]) == 12 and m["rows"][0]["state"] == "open"
    assert [r["label"] for r in m["rows"]][:2] == ["Nov 2026", "Oct 2026"]


def test_week_values_equal_the_oracle_and_ratios_are_ratios_of_sums(tc):
    d = get(tc, interval="week", periods=20)
    for r in d["rows"]:
        if r["state"] != "closed":
            continue
        a, b = D.fromisoformat(r["start"]), D.fromisoformat(r["end"])
        if any(not meta_present(x) for x in rng(a, b)):
            continue
        spend = ssum(ms, a, b) + ssum(gs, a, b)
        assert approx(r["v"]["meta_spend"], ssum(ms, a, b)) and approx(r["v"]["google_spend"], ssum(gs, a, b)) and approx(r["v"]["total_spend"], spend), r["label"]
        assert approx(r["v"]["nc_rev"], ssum(orev, a, b)) and approx(r["v"]["nc_orders"], ssum(onc, a, b))
        assert approx(r["v"]["nc_roas"], ssum(orev, a, b) / spend, 1e-5), r["label"]
        assert approx(r["v"]["nc_cpa"], spend / ssum(onc, a, b), 1e-4)
        assert approx(r["v"]["rc_rev"], ssum(orc, a, b)) and approx(r["v"]["total_rev"], ssum(orev, a, b) + ssum(orc, a, b))
        assert approx(r["v"]["m_cpm"], ssum(ms, a, b) / ssum(mi, a, b) * 1000, 1e-4) and approx(r["v"]["m_ctr"], ssum(mc, a, b) / ssum(mi, a, b), 1e-6)
        assert approx(r["v"]["m_cpc"], ssum(ms, a, b) / ssum(mc, a, b), 1e-5)
    # the average of daily ROAS differs from the ratio of sums: the page must show the latter
    w = by_start(d, D(2026, 10, 19))
    a, b = D(2026, 10, 19), D(2026, 10, 25)
    mean_of_ratios = sum(orev(x) / (ms(x) + gs(x)) for x in rng(a, b)) / 7
    assert abs(mean_of_ratios - w["v"]["nc_roas"]) > 0.005
    assert approx(w["v"]["nc_roas"], ssum(orev, a, b) / (ssum(ms, a, b) + ssum(gs, a, b)), 1e-5)


def test_change_vs_previous_period_closed_week(tc):
    d = get(tc, interval="week")
    w = by_start(d, D(2026, 10, 19))
    a, b = D(2026, 10, 19), D(2026, 10, 25)
    pa, pb = D(2026, 10, 12), D(2026, 10, 18)
    assert w["compare"]["kind"] == "previous" and (w["compare"]["start"], w["compare"]["end"]) == (pa.isoformat(), pb.isoformat())
    want = (ssum(ms, a, b) + ssum(gs, a, b)) / (ssum(ms, pa, pb) + ssum(gs, pa, pb)) - 1
    assert w["d"]["total_spend"]["t"] == "pct" and approx(w["d"]["total_spend"]["v"], want, 1e-5)
    roas = lambda x, y: ssum(orev, x, y) / (ssum(ms, x, y) + ssum(gs, x, y))
    assert approx(w["d"]["nc_roas"]["v"], roas(a, b) / roas(pa, pb) - 1, 1e-5)
    ctr = lambda x, y: ssum(mc, x, y) / ssum(mi, x, y)
    assert w["d"]["m_ctr"]["t"] == "pp" and approx(w["d"]["m_ctr"]["v"], ctr(a, b) - ctr(pa, pb), 1e-6)


def test_open_week_compares_the_same_weekdays(tc):
    d = get(tc, interval="week")
    w = d["rows"][0]
    assert w["state"] == "open" and w["label"] == "W45" and w["days_cov"] == 3 and w["covered_end"] == "2026-11-04"      # Mon Nov 2 - Wed Nov 4
    assert w["compare"]["kind"] == "like_for_like" and (w["compare"]["start"], w["compare"]["end"]) == ("2026-10-26", "2026-10-28")
    a, b = D(2026, 11, 2), D(2026, 11, 4)
    assert approx(w["v"]["total_spend"], ssum(ms, a, b) + ssum(gs, a, b))
    pa, pb = D(2026, 10, 26), D(2026, 10, 28)
    full = ssum(ms, D(2026, 10, 26), D(2026, 11, 1))
    want = ssum(ms, a, b) / ssum(ms, pa, pb) - 1
    assert approx(w["d"]["meta_spend"]["v"], want, 1e-5)
    assert abs(want - (ssum(ms, a, b) / full - 1)) > 0.3        # against the FULL previous week it would read about -60%: that is what must never be shown


def test_open_month_is_month_to_date_against_the_same_first_days(tc):
    d = get(tc, interval="month")
    nov = d["rows"][0]
    assert nov["state"] == "open" and nov["days_cov"] == 4 and (nov["compare"]["start"], nov["compare"]["end"]) == ("2026-10-01", "2026-10-04")
    a, b = D(2026, 11, 1), D(2026, 11, 4)
    assert approx(nov["v"]["nc_rev"], ssum(orev, a, b))
    assert approx(nov["d"]["nc_rev"]["v"], ssum(orev, a, b) / ssum(orev, D(2026, 10, 1), D(2026, 10, 4)) - 1, 1e-5)
    octo = d["rows"][1]
    assert octo["state"] == "closed" and octo["days_cov"] == 31 and octo["compare"]["kind"] == "previous" and octo["compare"]["end"] == "2026-09-30"
    assert approx(octo["v"]["total_spend"], ssum(ms, D(2026, 10, 1), D(2026, 10, 31)) + ssum(gs, D(2026, 10, 1), D(2026, 10, 31)))


def test_iso_week_year_boundary(tc):
    d = get(tc, interval="week", as_of="2026-01-07", periods=4)           # a Wednesday: Jan 5-6 loaded
    labels = [r["label"] for r in d["rows"]]
    assert labels[:3] == ["W02", "W01", "W52"]
    w1 = by_start(d, D(2025, 12, 29))
    assert w1["sub"] == "Dec 29, 2025 – Jan 4, 2026" and w1["state"] == "closed" and w1["days_cov"] == 7
    assert approx(w1["v"]["nc_rev"], ssum(orev, D(2025, 12, 29), D(2026, 1, 4)))
    w2 = by_start(d, D(2026, 1, 5))
    assert w2["state"] == "open" and w2["days_cov"] == 2 and w2["compare"]["kind"] == "like_for_like"
    assert (w2["compare"]["start"], w2["compare"]["end"]) == ("2025-12-29", "2025-12-30")


def test_monday_the_new_week_and_month_are_placeholders(tc):
    d = get(tc, interval="week", as_of="2026-11-02", periods=3)          # Monday: Sunday Nov 1 is the last loaded day
    assert [r["state"] for r in d["rows"]] == ["not_loaded", "closed", "closed", "closed"]
    assert d["rows"][0]["label"] == "W45" and all(v is None for v in d["rows"][0]["v"].values()) and d["rows"][0]["compare"]["kind"] == "none"
    w44 = d["rows"][1]
    assert w44["end"] == "2026-11-01" and w44["days_cov"] == 7 and w44["compare"]["kind"] == "previous"
    m = get(tc, interval="month", as_of="2026-11-02", periods=2)
    assert m["rows"][0]["state"] == "open" and m["rows"][0]["days_cov"] == 1 and m["rows"][0]["compare"]["end"] == "2026-10-01"      # Nov 1 against Oct 1


def test_dst_day_is_one_pacific_day(tc):
    d = get(tc, interval="day", as_of="2026-11-03", periods=5)            # Nov 1 2026 is the 25-hour day
    assert [r["start"] for r in d["rows"]] == ["2026-11-03", "2026-11-02", "2026-11-01", "2026-10-31", "2026-10-30", "2026-10-29"]      # Nov 3 is today: a placeholder
    nov1 = by_start(d, D(2026, 11, 1))
    assert nov1["days"] == 1 and nov1["state"] == "closed" and approx(nov1["v"]["meta_spend"], ms(D(2026, 11, 1)))
    assert nov1["compare"]["start"] == "2026-10-31" and approx(nov1["d"]["meta_spend"]["v"], ms(D(2026, 11, 1)) / ms(D(2026, 10, 31)) - 1, 1e-5)
    w = get(tc, interval="week", as_of="2026-11-03", periods=3)           # the week holding the 25-hour day still sums 7 Pacific days
    w44 = by_start(w, D(2026, 10, 26))
    assert w44["days"] == 7 and approx(w44["v"]["meta_spend"], ssum(ms, D(2026, 10, 26), D(2026, 11, 1)))


def test_today_has_no_numbers_and_says_so(tc):
    d = get(tc, interval="day", periods=3)
    t = d["rows"][0]
    assert t["start"] == "2026-11-05" and t["state"] == "not_loaded" and t["days_cov"] == 0 and t["open"]
    assert all(v is None for v in t["v"].values()) and t["d"] == {} and "nothing is loaded" in t["compare"]["why"].lower()
    y = d["rows"][1]
    assert y["state"] == "closed" and y["compare"]["start"] == "2026-11-03" and approx(y["v"]["total_spend"], ms(D(2026, 11, 4)) + gs(D(2026, 11, 4)))


def test_missing_meta_days_give_dashes_not_partial_sums(tc):
    d = get(tc, interval="week", as_of="2026-02-02", periods=8)
    w = by_start(d, D(2026, 1, 5))                                         # contains the Jan 10-11 hole
    assert w["state"] == "closed"
    for k in ("meta_spend", "total_spend", "nc_roas", "nc_cpa", "m_cpm", "test_share", "p_vitabull_spend"):
        assert w["v"][k] is None, k
        assert "no data for Jan 10, 2026" in w["why"][k] or "Meta" in w["why"][k], (k, w["why"][k])
    assert approx(w["v"]["google_spend"], ssum(gs, D(2026, 1, 5), D(2026, 1, 11)))      # the other sources are not affected
    assert approx(w["v"]["nc_rev"], ssum(orev, D(2026, 1, 5), D(2026, 1, 11)))
    nxt = by_start(d, D(2026, 1, 12))                                      # the week after: its base (the hole week) is missing, so no change
    assert nxt["v"]["total_spend"] is not None and "total_spend" not in nxt["d"] and "no data" in nxt["why"]["total_spend:d"]


def test_sources_that_start_later_dash_the_earlier_periods(tc):
    d = get(tc, interval="week", as_of="2026-01-14", periods=3)
    w = by_start(d, D(2026, 1, 12))                                        # Mon Jan 12: Google attribution starts Jan 15
    assert w["v"]["g_roas"] is not None                                    # own reporting is covered
    d2 = get(tc, interval="week", as_of="2026-02-02", periods=3, numbers="nc")
    w2 = by_start(d2, D(2026, 1, 19))
    assert w2["v"]["g_roas"] is not None and w2["v"]["m_roas"] is None and "starts" in w2["why"]["m_roas"]      # Meta Upstack attribution starts May 6
    d3 = get(tc, interval="week", as_of="2026-01-20", periods=3, numbers="nc")
    assert by_start(d3, D(2026, 1, 12))["v"]["g_roas"] is None and "starts Jan 15, 2026" in by_start(d3, D(2026, 1, 12))["why"]["g_roas"]
    far = get(tc, interval="week", periods=104)                            # weeks before the first loaded day
    first = far["rows"][-1]
    assert first["v"]["total_spend"] is None and first["v"]["nc_rev"] is None and first["why"]["nc_rev"]
    assert [r for r in far["rows"] if r["v"]["total_spend"] is not None]    # and later ones are filled


def test_basis_switch_changes_meta_and_google_purchases_not_spend(tc):
    a, b = D(2026, 10, 19), D(2026, 10, 25)
    own, nc, al = (by_start(get(tc, interval="week", numbers=n), a) for n in ("own", "nc", "all"))
    assert approx(own["v"]["m_purch"], ssum(mp, a, b)) and approx(own["v"]["m_rev"], ssum(mv, a, b)) and approx(own["v"]["m_roas"], ssum(mv, a, b) / ssum(ms, a, b), 1e-5)
    assert approx(nc["v"]["m_purch"], ssum(unc, a, b)) and approx(nc["v"]["m_rev"], 50 * ssum(unc, a, b)) and approx(nc["v"]["m_cpp"], ssum(ms, a, b) / ssum(unc, a, b), 1e-4)
    assert approx(al["v"]["m_purch"], ssum(uall, a, b)) and approx(al["v"]["m_rev"], 70 * ssum(uall, a, b))
    assert approx(own["v"]["g_purch"], ssum(gp, a, b)) and approx(nc["v"]["g_purch"], ssum(gnc, a, b)) and approx(al["v"]["g_purch"], ssum(gall, a, b))
    for r in (own, nc, al):
        assert approx(r["v"]["meta_spend"], ssum(ms, a, b)), "spend is the platform's on every basis (never read from the Upstack twin table)"
    labels = {n: {m["key"]: m["label"] for m in get(tc, interval="week", numbers=n)["metrics"]} for n in ("own", "nc", "all")}
    assert labels["own"]["m_roas"] == "Meta ROAS" and "NC ROAS" in labels["nc"]["m_roas"] and "(Upstack)" in labels["all"]["m_rev"]


def test_meta_history_before_ad_daily_comes_from_the_sheet_feed_and_stray_rows_are_ignored(tc):
    d = get(tc, interval="week", as_of="2026-05-12", periods=3)
    w = by_start(d, D(2026, 5, 4))                                         # May 4-5 sheet era, May 6-10 ad_daily era (the stray 99999 'sheet' row must not count)
    assert approx(w["v"]["meta_spend"], ssum(ms, D(2026, 5, 4), D(2026, 5, 10)))
    assert approx(w["v"]["p_vitabull_spend"], ssum(lambda x: ms(x) // 2, D(2026, 5, 4), D(2026, 5, 10)))      # 77777 stray product rows ignored too
    assert approx(w["v"]["m_impr"], ssum(mi, D(2026, 5, 4), D(2026, 5, 10)))


def test_testing_vs_scaling_and_products(tc):
    d = get(tc, interval="month")
    o = by_start(d, D(2026, 10, 1))
    a, b = D(2026, 10, 1), D(2026, 10, 31)
    assert approx(o["v"]["test_spend"], ssum(ts, a, b)) and approx(o["v"]["scale_spend"], ssum(ms, a, b) - ssum(ts, a, b))
    assert approx(o["v"]["test_spend"] + o["v"]["scale_spend"], o["v"]["meta_spend"])
    assert approx(o["v"]["test_share"], ssum(ts, a, b) / ssum(ms, a, b), 1e-6)
    assert approx(o["v"]["test_nc"], ssum(tnc, a, b)) and approx(o["v"]["test_cpa"], ssum(ts, a, b) / ssum(tnc, a, b), 1e-4)
    assert approx(o["v"]["scale_cpa"], (ssum(ms, a, b) - ssum(ts, a, b)) / ssum(snc, a, b), 1e-4)
    assert approx(o["v"]["p_vitabull_spend"], ssum(lambda x: ms(x) // 2, a, b)) and approx(o["v"]["p_beefliver_nc_rev"], ssum(lambda x: orev(x) // 4, a, b))
    assert approx(o["v"]["p_boneflex_nc_roas"], ssum(lambda x: orev(x) // 8, a, b) / ssum(lambda x: ms(x) // 8, a, b), 1e-5)
    assert approx(o["v"]["p_vitabull_nc_cpa"], ssum(lambda x: ms(x) // 2, a, b) / ssum(lambda x: onc(x) // 2, a, b), 1e-4)


def test_zero_order_day_inside_the_feed_is_a_real_zero(tc):
    d = get(tc, interval="day", as_of="2026-03-05", periods=3)
    z = by_start(d, NO_ORDERS_DAY)
    assert z["v"]["nc_rev"] == 0 and z["v"]["nc_orders"] == 0 and z["v"]["nc_roas"] == 0 and z["v"]["nc_cpa"] is None and "denominator is zero" in z["why"]["nc_cpa"]
    nxt = by_start(d, D(2026, 3, 4))
    assert "nc_rev" not in nxt["d"] and "zero" in nxt["why"]["nc_rev:d"].lower()


def test_reach_comes_from_the_feed_rows_never_from_summed_days(tc):
    d = get(tc, interval="week")
    w = by_start(d, D(2026, 10, 19))
    assert w["v"]["reach"] == week_reach(D(2026, 10, 19)) and w["v"]["uoc"] == 300 + D(2026, 10, 19).toordinal() % 7
    assert approx(w["v"]["frequency"], ssum(mi, D(2026, 10, 19), D(2026, 10, 25)) / week_reach(D(2026, 10, 19)), 1e-4)
    assert approx(w["v"]["cost_1k"], ssum(ms, D(2026, 10, 19), D(2026, 10, 25)) / week_reach(D(2026, 10, 19)) * 1000, 1e-3)
    assert approx(w["d"]["reach"]["v"], week_reach(D(2026, 10, 19)) / week_reach(D(2026, 10, 12)) - 1, 1e-5)
    day_sum = sum(1000 + x.day for x in rng(D(2026, 10, 19), D(2026, 10, 25)))
    assert w["v"]["reach"] != day_sum
    o = d["rows"][0]                                                       # the open week: the feed's partial row, no like-for-like change
    assert o["state"] == "open" and o["v"]["reach"] == week_reach(D(2026, 11, 2)) and "reach" not in o["d"] and "like-for-like" in o["why"]["reach:d"]
    assert o["v"]["cost_1k"] is None and o["reach_through"] == "2026-11-05"      # reach runs a day past the loaded spend: no cost per 1,000 until the week closes
    m = get(tc, interval="month")
    assert by_start(m, D(2026, 10, 1))["v"]["reach"] == month_reach(D(2026, 10, 1))
    dd = get(tc, interval="day", periods=3)
    assert dd["rows"][1]["v"]["reach"] == 700 + 4 and dd["rows"][1]["v"]["uoc"] == 5 + 4


def test_reach_dash_for_an_incomplete_closed_period_and_for_periods_without_rows(tc):
    d = get(tc, interval="week", as_of="2026-05-20", periods=3)
    w = by_start(d, D(2026, 5, 11))
    assert w["v"]["reach"] is None and "incomplete" in w["why"]["reach"]
    assert d["rows"][0]["state"] == "open"
    far = get(tc, interval="week", periods=104)
    assert far["rows"][-1]["v"]["reach"] is None and far["rows"][-1]["why"]["reach"]
    day = get(tc, interval="day", periods=120)
    assert day["rows"][-1]["v"]["reach"] is None                          # day rows exist for 60 days only


def test_freshness_chips_and_staleness(tc):
    d = get(tc)
    f = {x["key"]: x for x in d["freshness"]}
    assert f["meta"]["last"] == "2026-11-04" and not f["meta"]["stale"] and f["orders"]["first"] == "2025-12-01" and f["meta_up"]["first"] == "2026-05-06"
    assert f["google_up"]["first"] == "2026-01-15" and f["reach"]["last"] == "2026-11-04"
    stale = get(tc, as_of="2026-11-09")                                    # four days after the last loaded day
    assert {x["key"]: x for x in stale["freshness"]}["meta"]["stale"] is True and stale["data_through"] == "2026-11-04"
    assert d["data_through"] == "2026-11-04" and d["today"] == "2026-11-05"


def test_groups_and_catalog(tc):
    """Changed with the sheet layout: the chips are the sheet's bands (DTC / Shopify, Meta, Google, By product, Testing vs scaling, Change; Plan on the month grain), and
    each group lists the columns of its bands in the sheet's order."""
    d = get(tc)
    keys = {m["key"] for m in d["metrics"]}
    g = {x["key"]: x["metrics"] for x in d["groups"]}
    assert list(g) == ["totals", "meta", "google", "products", "stage", "change"]
    assert g["totals"][:5] == ["total_rev", "nc_rev", "rc_rev", "nc_orders", "nc_aov"] and "nc_roas" in g["totals"] and "nc_cpa" in g["totals"]
    assert {"m_impr", "m_cpm", "m_ctr", "m_cpc", "m_roas", "reach", "frequency", "meta_spend"} <= set(g["meta"]) and {"g_clicks", "g_cpc", "g_roas", "google_spend"} <= set(g["google"])
    for slug in ("vitabull", "beefliver", "boneflex"):
        assert {f"p_{slug}_spend", f"p_{slug}_nc_rev", f"p_{slug}_nc_roas", f"p_{slug}_nc_cpa", f"p_{slug}_nc_orders"} <= set(g["products"])
    assert set(sum(g.values(), [])) <= keys
    assert [x["key"] for x in get(tc, interval="month")["groups"]][0] == "plan"                  # the plan block exists on the month grain only
    pol = {m["key"]: m["pol"] for m in d["metrics"]}
    assert pol["total_spend"] == "neutral" and pol["nc_cpa"] == "down_good" and pol["nc_roas"] == "up_good" and pol["m_cpm"] == "down_good"


def test_layout_is_the_sheets_bands_in_the_sheets_order(tc):
    d = get(tc)
    assert [b["title"] for b in d["layout"]] == ["DTC / SHOPIFY", "META", "SPEND", "NC REVENUE", "NC ROAS", "NC ORDERS", "NC CPA", "SPEND BY STAGE", "TESTING % OF SPEND",
                                                 "NC (UPSTACK ANY-CLICK)", "NC CPA BY STAGE", "GOOGLE", "WoW Δ"]
    cols = {b["key"]: [c["label"] for c in b["cols"]] for b in d["layout"]}
    assert cols["dtc"] == ["Total Revenue", "NC Revenue", "Repeat Revenue", "NC Orders", "NC AOV", "Ad Spend", "NC CPA", "NC ROAS", "MER", "NC Conversion", "Repeat Share %", "New Visitors", "Cost per New Visitor"]
    assert cols["meta"][:9] == ["Spend", "Link Clicks", "CPC", "Purchases", "Purchase Value", "CVR", "Platform ROAS", "Cost per Purchase", "Impressions"]
    assert cols["prod_nc_roas"] == ["VitaBull", "Beef Liver", "Boneflex", "Total"]
    assert [x["title"] for x in get(tc, interval="month")["layout"]][0] == "Projected* | Target | % Diff" and get(tc, interval="month")["layout"][-1]["title"] == "MoM Δ"
    assert get(tc, interval="day")["layout"][-1]["title"] == "DoD Δ"
    nc = {b["key"]: [c["label"] for c in b["cols"]] for b in get(tc, numbers="nc")["layout"]}
    assert nc["meta"][3:5] == ["New customers (Upstack)", "NC revenue (Upstack)"] and nc["meta"][6] == "NC ROAS (Upstack)"
    every = {m["key"] for m in d["metrics"]}
    placed = {c["key"] for b in d["layout"] for c in b["cols"]}
    assert every <= placed, every - placed                                                       # no metric is lost by the layout
    assert placed <= every


def test_every_interval_answers_quickly_on_the_fixture(tc):
    import time
    for iv, n in (("day", 120), ("week", 104), ("month", 36)):
        t = time.time()
        get(tc, interval=iv, periods=n)
        assert time.time() - t < 2.0, iv


def test_query_timeout_is_a_clear_503(tapp, monkeypatch):
    def boom(*a, **k):
        raise psycopg.errors.QueryCanceled("canceling statement due to statement timeout")
    monkeypatch.setattr(T, "coverage", boom)
    c = tapp.test_client()
    assert c.post("/api/login", json={"username": "JoshHoughton", "password": PASSWORD}).status_code == 204
    r = c.get("/api/trends", query_string={"as_of": TODAY.isoformat()})
    assert r.status_code == 503 and r.get_json()["error"] == "query timed out"


def test_sheet_parity_ratios_nc_aov_mer_repeat_share_conversion_first_impressions(tc):
    a, b = D(2026, 10, 19), D(2026, 10, 25)
    w = by_start(get(tc, interval="week"), a)
    spend = ssum(ms, a, b) + ssum(gs, a, b)
    assert approx(w["v"]["nc_aov"], ssum(orev, a, b) / ssum(onc, a, b), 1e-4)
    assert approx(w["v"]["mer"], (ssum(orev, a, b) + ssum(orc, a, b)) / spend, 1e-5)
    assert approx(w["v"]["repeat_share"], ssum(orc, a, b) / (ssum(orev, a, b) + ssum(orc, a, b)), 1e-6)
    assert approx(w["v"]["nc_cvr"], ssum(onc, a, b) / (ssum(mc, a, b) + ssum(gc, a, b)), 1e-6)
    assert approx(w["v"]["m_cvr"], ssum(mp, a, b) / ssum(mc, a, b), 1e-6) and approx(w["v"]["g_cvr"], ssum(gp, a, b) / ssum(gc, a, b), 1e-6)
    assert approx(w["v"]["first_imp"], week_reach(a) / ssum(mi, a, b), 1e-6)


# ----------------------------------------------------------------------------- new visitors and cost per new visitor (Upstack traffic feed)
def test_new_visitors_and_cost_per_new_visitor_are_sums_and_ratios_of_sums(tc):
    for interval, n in (("week", 30), ("month", 10), ("day", 60)):
        d = get(tc, interval=interval, periods=n)
        checked = 0
        for r in d["rows"]:
            if r["state"] != "closed":
                continue
            a, b = D.fromisoformat(r["start"]), D.fromisoformat(r["end"])
            if a < TRAFFIC_FIRST or any(not meta_present(x) for x in rng(a, b)) or TRAFFIC_HOLE in rng(a, b):
                continue
            visitors, spend = ssum(nv, a, b), ssum(ms, a, b) + ssum(gs, a, b)
            assert r["v"]["new_visitors"] == visitors, (interval, r["label"])
            if visitors:
                assert approx(r["v"]["cost_nv"], spend / visitors, 1e-5), (interval, r["label"])
            checked += 1
        assert checked >= 5, interval
    w = by_start(get(tc, interval="week", periods=20), D(2026, 10, 19))
    a, b = D(2026, 10, 19), D(2026, 10, 25)
    mean_of_ratios = sum((ms(x) + gs(x)) / nv(x) for x in rng(a, b)) / 7
    assert abs(mean_of_ratios - w["v"]["cost_nv"]) / w["v"]["cost_nv"] > 0.002              # the page shows the ratio of sums
    assert approx(w["v"]["cost_nv"], (ssum(ms, a, b) + ssum(gs, a, b)) / ssum(nv, a, b), 1e-6)


def test_cost_per_new_visitor_uses_the_same_total_spend_as_the_ad_spend_column(tc):
    r = by_start(get(tc, interval="week", periods=20), D(2026, 10, 19))
    assert approx(r["v"]["cost_nv"] * r["v"]["new_visitors"], r["v"]["total_spend"], 0.05)


def test_new_visitors_dash_before_the_feed_starts_and_when_a_period_straddles_the_first_day(tc):
    d = get(tc, interval="week", as_of="2026-02-04", periods=4)
    before = by_start(d, D(2026, 1, 12))                                       # Jan 12-18: entirely before Jan 20
    straddle = by_start(d, D(2026, 1, 19))                                     # Jan 19-25: starts a day before the feed
    for r in (before, straddle):
        assert r["v"]["new_visitors"] is None and r["v"]["cost_nv"] is None, r["label"]
        assert "starts Jan 20, 2026" in r["why"]["new_visitors"] and "starts Jan 20, 2026" in r["why"]["cost_nv"]
        assert r["v"]["total_spend"] is not None                                # the other columns are untouched
        assert "new_visitors" not in r["d"] and "cost_nv" not in r["d"]
    ok = by_start(d, D(2026, 1, 26))
    assert ok["v"]["new_visitors"] == ssum(nv, D(2026, 1, 26), D(2026, 2, 1))
    assert "new_visitors" not in ok["d"] and "starts" in ok["why"]["new_visitors:d"] or "no data" in ok["why"]["new_visitors:d"]
    m = get(tc, interval="month", as_of="2026-02-10", periods=3)
    jan = by_start(m, D(2026, 1, 1))
    assert jan["v"]["new_visitors"] is None and jan["v"]["cost_nv"] is None and "starts Jan 20, 2026" in jan["why"]["cost_nv"]
    assert by_start(m, D(2026, 2, 1))["v"]["new_visitors"] == ssum(nv, D(2026, 2, 1), D(2026, 2, 9))        # the open month: through yesterday


def test_new_visitors_dash_for_a_period_with_a_missing_traffic_day(tc):
    d = get(tc, interval="week", as_of="2026-04-27", periods=4)
    w = by_start(d, D(2026, 4, 13))                                            # Apr 13-19 holds the missing Apr 14
    assert w["v"]["new_visitors"] is None and w["v"]["cost_nv"] is None
    assert "no data for Apr 14, 2026" in w["why"]["new_visitors"] and "no data for Apr 14, 2026" in w["why"]["cost_nv"]
    assert w["v"]["total_spend"] is not None
    nxt = by_start(d, D(2026, 4, 20))
    assert nxt["v"]["new_visitors"] == ssum(nv, D(2026, 4, 20), D(2026, 4, 26))
    assert "new_visitors" not in nxt["d"] and "no data" in nxt["why"]["new_visitors:d"]               # the base week is a dash
    day = by_start(get(tc, interval="day", as_of="2026-04-16", periods=3), D(2026, 4, 14))
    assert day["v"]["new_visitors"] is None and "no data for Apr 14, 2026" in day["why"]["new_visitors"]
    mo = by_start(get(tc, interval="month", as_of="2026-05-10", periods=3), D(2026, 4, 1))
    assert mo["v"]["new_visitors"] is None and "no data for Apr 14, 2026" in mo["why"]["cost_nv"]


def test_zero_visitor_day_is_a_real_zero_and_cost_is_undefined_not_zero(tc):
    day = by_start(get(tc, interval="day", as_of="2026-06-12", periods=5), ZERO_VISITORS_DAY)
    assert day["v"]["new_visitors"] == 0 and day["v"]["cost_nv"] is None
    assert "zero" in day["why"]["cost_nv"]
    wk = by_start(get(tc, interval="week", as_of="2026-06-17", periods=4), D(2026, 6, 8))              # the week around it still sums (the zero day adds nothing)
    assert wk["v"]["new_visitors"] == ssum(nv, D(2026, 6, 8), D(2026, 6, 14))
    assert approx(wk["v"]["cost_nv"], (ssum(ms, D(2026, 6, 8), D(2026, 6, 14)) + ssum(gs, D(2026, 6, 8), D(2026, 6, 14))) / wk["v"]["new_visitors"], 1e-5)


def test_changes_for_new_visitors_and_cost_closed_open_and_polarity(tc):
    d = get(tc, interval="week", periods=8)
    cur, prev = by_start(d, D(2026, 10, 19)), by_start(d, D(2026, 10, 12))
    exp = ssum(nv, D(2026, 10, 19), D(2026, 10, 25)) / ssum(nv, D(2026, 10, 12), D(2026, 10, 18)) - 1
    assert approx(cur["d"]["new_visitors"]["v"], exp, 1e-5) and cur["d"]["new_visitors"]["t"] == "pct"
    c_now = (ssum(ms, D(2026, 10, 19), D(2026, 10, 25)) + ssum(gs, D(2026, 10, 19), D(2026, 10, 25))) / ssum(nv, D(2026, 10, 19), D(2026, 10, 25))
    c_prev = (ssum(ms, D(2026, 10, 12), D(2026, 10, 18)) + ssum(gs, D(2026, 10, 12), D(2026, 10, 18))) / ssum(nv, D(2026, 10, 12), D(2026, 10, 18))
    assert approx(cur["d"]["cost_nv"]["v"], c_now / c_prev - 1, 1e-5)
    pol = {m["key"]: m["pol"] for m in d["metrics"]}
    kind = {m["key"]: m["kind"] for m in d["metrics"]}
    assert pol["new_visitors"] == "up_good" and pol["cost_nv"] == "down_good" and kind["new_visitors"] == "int" and kind["cost_nv"] == "money2"
    # the open week (Nov 2-4 loaded) against the same weekdays of the last week; today's partial traffic row (999999) is never read
    o = by_start(d, D(2026, 11, 2))
    assert o["open"] and o["v"]["new_visitors"] == ssum(nv, D(2026, 11, 2), D(2026, 11, 4))
    assert approx(o["d"]["new_visitors"]["v"], ssum(nv, D(2026, 11, 2), D(2026, 11, 4)) / ssum(nv, D(2026, 10, 26), D(2026, 10, 28)) - 1, 1e-5)
    om = by_start(get(tc, interval="month", periods=3), D(2026, 11, 1))
    assert om["v"]["new_visitors"] == ssum(nv, D(2026, 11, 1), D(2026, 11, 4))
    assert approx(om["d"]["new_visitors"]["v"], ssum(nv, D(2026, 11, 1), D(2026, 11, 4)) / ssum(nv, D(2026, 10, 1), D(2026, 10, 4)) - 1, 1e-5)


def test_new_visitor_columns_sit_in_the_dtc_band_the_change_block_and_the_freshness_chips(tc):
    d = get(tc)
    dtc = next(b for b in d["layout"] if b["key"] == "dtc")
    assert [c["key"] for c in dtc["cols"]][-3:] == ["repeat_share", "new_visitors", "cost_nv"]               # the sheet's position: after Repeat Share %
    assert [c["label"] for c in dtc["cols"]][-2:] == ["New Visitors", "Cost per New Visitor"]
    chg = next(b for b in d["layout"] if b["key"] == "change")
    assert {"new_visitors", "cost_nv"} <= {c["key"] for c in chg["cols"]}
    g = {x["key"]: x["metrics"] for x in d["groups"]}
    assert {"new_visitors", "cost_nv"} <= set(g["totals"]) and not ({"new_visitors", "cost_nv"} & set(g["meta"]))
    f = {x["key"]: x for x in d["freshness"]}
    assert f["traffic"]["first"] == "2026-01-20" and f["traffic"]["last"] == "2026-11-04" and not f["traffic"]["stale"]    # today's partial row is not counted


def test_a_week_across_the_dst_change_counts_seven_days_of_visitors(tc):
    w = by_start(get(tc, interval="week", as_of="2026-11-11", periods=4), D(2026, 10, 26))                  # Nov 1 2026 is the end of daylight time
    assert w["days"] == 7 and w["v"]["new_visitors"] == sum(nv(x) for x in rng(D(2026, 10, 26), D(2026, 11, 1)))
