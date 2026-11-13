"""Email (/api/email/*): flows, campaigns and over-time results from klaviyo_daily.

Own throwaway database loaded with a small, hand-checkable account (the generator below: integers and halves only, so every sum is exact). Every expected number
is computed from the generated rows with plain Python loops, independently of app/email_view.py.

Shape: loaded days 2026-07-15 (a Wednesday) .. 2026-10-28 (a Wednesday), 106 days. Two flows (F1 Billing Reminder: two email messages; F2 Welcome Text: one text
message, no unsubscribes because Klaviyo does not report them), four campaigns (C1 email, C2 an A/B email with two messages, C3 text message with an unsubscribe row that
has no message, C4 sent before the 30 day window and no longer listed by Klaviyo), copy for most messages (fm2 has no row, cm2b has a row with no text).
"""
from __future__ import annotations

import datetime as dt

import psycopg
import pytest

from app import email_view as E

from . import seed_fixture as S
from .conftest import DB_NAME, PASSWORD, PG_BASE, make_app

D = dt.date
EM_DB = f"{DB_NAME}_email"
EM_URL = f"{PG_BASE}/{EM_DB}"
FIRST, THROUGH = D(2026, 7, 15), D(2026, 10, 28)
DAYS = [FIRST + dt.timedelta(days=i) for i in range((THROUGH - FIRST).days + 1)]
FIELDS = ("sends", "delivered", "opens", "unique_opens", "clicks", "unique_clicks", "unsubscribes", "orders", "revenue")


def row(day, source, channel, pid, mid, name, sends, delivered, opens, uo, clicks, uc, unsub, orders, revenue, bounced=None):
    return dict(day=day, source=source, channel=channel, parent_id=pid, message_id=mid, parent_name=name, sends=sends, delivered=delivered,
                bounced=sends - delivered if bounced is None else bounced, opens=opens, unique_opens=uo, clicks=clicks, unique_clicks=uc, unsubscribes=unsub,
                orders=orders, revenue=revenue)


def build_rows():
    out = []
    for i, d in enumerate(DAYS):
        for mid, extra in (("fm1", 0), ("fm2", 100)):
            s = 200 + i % 5 * 10 + extra
            dl = s - 3
            uo = dl // 3
            if mid == "fm1" and i == len(DAYS) - 11:                  # one huge, poorly opened day: the ratio of sums and the average of daily ratios part ways
                s, dl, uo = 5000, 4990, 100
            o = i % 3 + (1 if extra else 0)
            out.append(row(d, "flow", "email", "F1", mid, "Billing Reminder", s, dl, dl // 2, uo, uo // 4, uo // 10, 1 if i % 4 == 0 else 0, o, o * 40 + (i % 2) * 2.5))
        s = 50 + i % 3
        o = i % 2
        out.append(row(d, "flow", "sms", "F2", "fm3", "Welcome Text", s, s - 2, 0, 0, 5 + i % 4, 4, 0, o, o * 25.0))
    # campaigns: a send day, then a short tail of late opens, clicks and orders
    def camp(pid, mid, name, channel, send, size, tail, rev_each, orders_each):
        for k in range(tail):
            d = send + dt.timedelta(days=k)
            if d > THROUGH:
                break
            first = k == 0
            sd = size if first else 0
            dl = sd - 5 if first else 0
            uo = (size // 2 if first else size // (4 * (k + 1))) if channel == "email" else 0
            uc = size // (20 * (k + 1))
            out.append(row(d, "campaign", channel, pid, mid, name, sd, dl, uo * 2, uo, uc * 2, uc, 3 if (first and channel == "email") else 0, orders_each // (k + 1), round(rev_each / (k + 1), 2)))
    camp("C4", "cm4", "Early Bird", "email", FIRST + dt.timedelta(days=2), 1000, 4, 120.0, 4)       # entirely before the 30 day window
    camp("C1", "cm1", "Spring Sale", "email", FIRST + dt.timedelta(days=10), 2000, 5, 400.0, 8)
    camp("C2", "cm2a", "Test Offer", "email", FIRST + dt.timedelta(days=60), 1200, 3, 160.0, 4)
    camp("C2", "cm2b", "Test Offer", "email", FIRST + dt.timedelta(days=60), 800, 3, 90.0, 2)
    camp("C3", "cm3", "Flash Text", "sms", THROUGH - dt.timedelta(days=3), 900, 4, 70.0, 2)
    out.append(row(THROUGH - dt.timedelta(days=2), "campaign", "sms", "C3", "", "Flash Text", 0, 0, 0, 0, 0, 0, 7, 0, 0.0))        # a text unsubscribe matched by name, no message
    return out


ROWS = build_rows()
BODY = ("Hello {{ first_name }}, your billing reminder is here. " * 30).strip()          # longer than the 600 character preview


# ----------------------------------------------------------------------------- the expected numbers, plain Python
def pick(rows, start=None, end=None, source=None, channel=None, pid=None, mid=None):
    return [r for r in rows if (start is None or r["day"] >= start) and (end is None or r["day"] <= end) and (source is None or r["source"] == source)
            and (channel is None or r["channel"] == channel) and (pid is None or r["parent_id"] == pid) and (mid is None or r["message_id"] == mid)]


def expect(rows):
    t = {f: sum(r[f] for r in rows) for f in FIELDS}
    d_email = sum(r["delivered"] for r in rows if r["channel"] == "email")
    uc_email = sum(r["unique_clicks"] for r in rows if r["channel"] == "email")
    d_unsub = sum(r["delivered"] for r in rows if not (r["source"] == "flow" and r["channel"] == "sms"))
    t["open_rate"] = t["unique_opens"] / d_email if d_email else None
    t["click_rate"] = t["unique_clicks"] / t["delivered"] if t["delivered"] else None
    t["click_to_open"] = uc_email / t["unique_opens"] if t["unique_opens"] else None
    t["rev_per_delivered"] = t["revenue"] / t["delivered"] if t["delivered"] else None
    t["orders_per_delivered"] = t["orders"] / t["delivered"] if t["delivered"] else None
    t["unsub_rate"] = t["unsubscribes"] / d_unsub if d_unsub else None
    return t


def near(a, b, tol=1e-9):
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) <= tol


def check_totals(m, e):
    assert m["sends"] == e["sends"] and m["delivered"] == e["delivered"] and m["opens"] == e["unique_opens"] and m["orders"] == e["orders"]
    assert m["revenue"] == pytest.approx(e["revenue"], abs=0.005)
    for k in ("open_rate", "click_rate", "click_to_open", "orders_per_delivered", "unsub_rate"):
        assert near(m[k], e[k]), (k, m[k], e[k])
    assert near(m["rev_per_delivered"], e["rev_per_delivered"], 1e-9)


# ----------------------------------------------------------------------------- the database and app
@pytest.fixture(scope="module")
def edb():
    try:
        S.recreate_db(f"{PG_BASE}/postgres", EM_DB)
        conn = psycopg.connect(EM_URL)
    except psycopg.OperationalError as e:
        pytest.skip(f"Postgres not available for API tests: {e}")
    with conn:
        S.apply_migrations(conn)
        with conn.cursor() as cur:
            cur.executemany("""INSERT INTO klaviyo_daily (day, source, parent_id, message_id, channel, parent_name, sends, delivered, bounced, opens, unique_opens, clicks, unique_clicks,
                               unsubscribes, orders, revenue) VALUES (%(day)s,%(source)s,%(parent_id)s,%(message_id)s,%(channel)s,%(parent_name)s,%(sends)s,%(delivered)s,%(bounced)s,
                               %(opens)s,%(unique_opens)s,%(clicks)s,%(unique_clicks)s,%(unsubscribes)s,%(orders)s,%(revenue)s)""", ROWS)
            cur.executemany("INSERT INTO klaviyo_flows (id, name, status) VALUES (%s,%s,%s)", [("F1", "Billing Reminder Flow", "live"), ("F2", "Welcome Text Flow", "draft")])
            cur.executemany("INSERT INTO klaviyo_flow_messages (id, flow_id, name, channel, subject) VALUES (%s,%s,%s,%s,%s)",
                            [("fm1", "F1", "Reminder 1", "email", "Your plan renews soon"), ("fm2", "F1", "Reminder 2", "email", "Last reminder"), ("fm3", "F2", "Text 1", "sms", None)])
            send = lambda d: f"{d.isoformat()} 10:00:00-07"        # (October days are still PDT; July too: -07 all through)
            cur.executemany("INSERT INTO klaviyo_campaigns (id, name, channel, status, send_time, subject) VALUES (%s,%s,%s,'Sent',%s,%s)",
                            [("C1", "Spring Sale Campaign", "email", send(FIRST + dt.timedelta(days=10)), "Spring savings inside"),
                             ("C2", "Test Offer Campaign", "email", send(FIRST + dt.timedelta(days=60)), "Offer A"),
                             ("C3", "Flash Text Campaign", "sms", send(THROUGH - dt.timedelta(days=3)), None)])
            cur.executemany("""INSERT INTO klaviyo_message_content (message_id, source, parent_id, channel, subject, preview_text, from_name, body_text, image_count, link_urls, fetched_at)
                               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                            [("fm1", "flow", "F1", "email", "Your plan renews soon", "Renewal notice", "Lonvera", BODY, 2, ["https://a.example/1", "https://a.example/2"], "2026-11-09 18:00:00+00"),
                             ("cm1", "campaign", "C1", "email", "Spring savings inside", "Preview of spring", "Lonvera", "Short spring copy.", 0, ["https://a.example/s"], "2026-11-09 18:00:00+00"),
                             ("cm2a", "campaign", "C2", "email", "Offer A", None, "Lonvera", "Offer A body.", 1, [], "2026-11-09 18:00:00+00"),
                             ("cm2b", "campaign", "C2", "email", "Offer B", None, "Lonvera", None, 3, [], "2026-11-09 18:00:00+00"),
                             ("cm3", "campaign", "C3", "sms", None, None, None, "Flash sale: 20 percent off today", 1, [], "2026-11-09 18:00:00+00")])
        conn.commit()
    return True


@pytest.fixture(scope="module")
def eapp(edb):
    a = make_app(DATABASE_URL=EM_URL)
    yield a
    a.extensions["pool"].close()


@pytest.fixture()
def ec(eapp):
    c = eapp.test_client()
    assert c.post("/api/login", json={"username": "JoshHoughton", "password": PASSWORD}).status_code == 204
    return c


def get(c, path, **kw):
    r = c.get(path, query_string=kw)
    assert r.status_code == 200, r.get_data(as_text=True)[:400]
    return r.get_json()


def win(n):
    return THROUGH - dt.timedelta(days=n - 1), THROUGH


# ----------------------------------------------------------------------------- summary
def test_summary_ties_to_plain_python_sums(ec):
    d = get(ec, "/api/email/summary", window="30d")
    s, e = win(30)
    assert d["window"]["from"] == s.isoformat() and d["window"]["to"] == e.isoformat() and d["window"]["through"] == THROUGH.isoformat() and d["window"]["days"] == 30
    check_totals(d["totals"], expect(pick(ROWS, s, e)))
    assert [m["key"] for m in d["metrics"]][:3] == ["sends", "delivered", "opens"] and d["tiles"][0] == "sends"
    assert d["flags"]["sms_unsub_incomplete"] is True


def test_rates_are_ratios_of_sums_not_averages_of_daily_rates(ec):
    s, e = win(30)
    rows = pick(ROWS, s, e)
    daily = {}
    for r in rows:
        if r["channel"] == "email":
            a = daily.setdefault(r["day"], [0, 0])
            a[0] += r["unique_opens"]
            a[1] += r["delivered"]
    avg_of_daily = sum(a / b for a, b in daily.values() if b) / len(daily)
    got = get(ec, "/api/email/summary", window="30d")["totals"]["open_rate"]
    assert got == pytest.approx(sum(r["unique_opens"] for r in rows if r["channel"] == "email") / sum(r["delivered"] for r in rows if r["channel"] == "email"))
    assert abs(got - avg_of_daily) > 1e-4                      # the fixture makes the two differ, so the test can tell them apart


def test_text_messages_stay_out_of_the_open_rate_and_flow_text_out_of_the_unsubscribe_rate(ec):
    s, e = win(30)
    both = get(ec, "/api/email/summary", window="30d")["totals"]
    em = get(ec, "/api/email/summary", window="30d", channel="email")["totals"]
    sms = get(ec, "/api/email/summary", window="30d", channel="sms")
    assert both["open_rate"] == pytest.approx(em["open_rate"])                              # sms adds delivered, not opens: the denominator is email only
    assert sms["totals"]["open_rate"] is None and sms["totals"]["click_to_open"] is None   # no opens on text messages: a dash, not 0
    flow_sms = pick(ROWS, s, e, source="flow", channel="sms")
    assert sum(r["delivered"] for r in flow_sms) > 0
    only_flow_sms = get(ec, "/api/email/summary", window="30d", kind="flows", channel="sms")["totals"]
    assert only_flow_sms["unsub_rate"] is None                                              # Klaviyo does not report them: never a 0
    assert sms["flags"]["sms_unsub_incomplete"] is True
    assert get(ec, "/api/email/summary", window="30d", channel="email")["flags"]["sms_unsub_incomplete"] is False


def test_filters_kind_and_channel(ec):
    s, e = win(30)
    for kind, src in (("flows", "flow"), ("campaigns", "campaign")):
        for channel, ch in (("all", None), ("email", "email"), ("sms", "sms")):
            d = get(ec, "/api/email/summary", window="30d", kind=kind, channel=channel)
            check_totals(d["totals"], expect(pick(ROWS, s, e, source=src, channel=ch)))
    assert get(ec, "/api/email/summary", window="30d", kind="all")["totals"]["sends"] == expect(pick(ROWS, s, e))["sends"]


def test_prior_period_is_shown_only_when_fully_loaded(ec):
    for n in (7, 30):
        d = get(ec, "/api/email/summary", window=f"{n}d")
        s, e = win(n)
        ps, pe = s - dt.timedelta(days=n), s - dt.timedelta(days=1)
        assert d["window"]["prior"] == {"from": ps.isoformat(), "to": pe.isoformat()}
        before = expect(pick(ROWS, ps, pe))
        check_totals(d["prior_totals"], before)
        now = expect(pick(ROWS, s, e))
        assert d["change"]["revenue"]["t"] == "pct" and d["change"]["revenue"]["v"] == pytest.approx(now["revenue"] / before["revenue"] - 1, abs=1e-5)
        assert d["change"]["sends"]["v"] == pytest.approx(now["sends"] / before["sends"] - 1, abs=1e-5)
        assert d["change"]["click_rate"]["t"] == "pp" and d["change"]["click_rate"]["v"] == pytest.approx(now["click_rate"] - before["click_rate"], abs=1e-5)
        assert d["change"]["unsub_rate"]["t"] == "pp"
    # 90 days needs 180 days of data, only 106 are loaded; all loaded days has nothing before it
    for w in ("90d", "all"):
        d = get(ec, "/api/email/summary", window=w)
        assert d["window"]["prior"] is None and d["prior_totals"] is None and d["change"] == {} and d["window"]["prior_why"]
    allw = get(ec, "/api/email/summary", window="all")
    assert allw["window"]["from"] == FIRST.isoformat() and allw["window"]["days"] == 106
    check_totals(allw["totals"], expect(ROWS))
    check_totals(get(ec, "/api/email/summary", window="90d")["totals"], expect(pick(ROWS, *win(90))))


def test_default_window_is_30_days_and_bad_parameters_are_400(ec):
    assert get(ec, "/api/email/summary")["window"]["name"] == "30d"
    for q in ({"window": "1y"}, {"kind": "x"}, {"channel": "push"}):
        assert ec.get("/api/email/summary", query_string=q).status_code == 400
    assert ec.get("/api/email/timeseries", query_string={"grain": "year"}).status_code == 400


def test_endpoints_need_a_login(eapp):
    a = eapp.test_client()
    for u in ("/api/email/summary", "/api/email/timeseries", "/api/email/flows", "/api/email/campaigns", "/api/email/message/cm1", "/api/email/flows/F1/messages"):
        assert a.get(u).status_code == 401, u


# ----------------------------------------------------------------------------- over time
def test_day_buckets_tie_and_zero_fill(ec):
    d = get(ec, "/api/email/timeseries", window="30d", grain="day")
    s, e = win(30)
    assert len(d["buckets"]) == 30 and d["buckets"][0]["start"] == s.isoformat() and d["buckets"][-1]["start"] == e.isoformat()
    assert not any(b["partial"] for b in d["buckets"])
    for b in d["buckets"]:
        day = D.fromisoformat(b["start"])
        check_totals(b["v"], expect(pick(ROWS, day, day)))
    assert sum(b["v"]["sends"] for b in d["buckets"]) == expect(pick(ROWS, s, e))["sends"]


def test_week_buckets_are_iso_weeks_and_the_ends_are_partial(ec):
    d = get(ec, "/api/email/timeseries", window="all", grain="week")
    bks = d["buckets"]
    assert all(D.fromisoformat(b["start"]).weekday() == 0 for b in bks)
    # first loaded day is a Wednesday: the first week starts before the data. The last loaded day is a Wednesday: the last week runs past it.
    assert bks[0]["start"] == "2026-07-13" and bks[0]["partial"] and bks[0]["days_cov"] == 5 and "before the first" in bks[0]["partial_why"]
    assert bks[-1]["start"] == "2026-10-26" and bks[-1]["partial"] and bks[-1]["days_cov"] == 3 and "past the last" in bks[-1]["partial_why"]
    assert not any(b["partial"] for b in bks[1:-1]) and bks[1]["label"].startswith("W")
    for b in bks:
        s, e = D.fromisoformat(b["start"]), D.fromisoformat(b["end"])
        check_totals(b["v"], expect(pick(ROWS, s, e)))
    assert sum(b["v"]["revenue"] for b in bks) == pytest.approx(expect(ROWS)["revenue"], abs=0.01)


def test_month_buckets_and_the_current_month_is_partial(ec):
    d = get(ec, "/api/email/timeseries", window="all", grain="month")
    bks = d["buckets"]
    assert [b["start"] for b in bks] == ["2026-07-01", "2026-08-01", "2026-09-01", "2026-10-01"]
    assert [b["partial"] for b in bks] == [True, False, False, True]              # July starts before the data (Jul 15), October runs past Oct 28
    assert bks[-1]["days"] == 31 and bks[-1]["days_cov"] == 28 and bks[0]["days_cov"] == 17
    for b in bks:
        s, e = D.fromisoformat(b["start"]), D.fromisoformat(b["end"])
        check_totals(b["v"], expect(pick(ROWS, s, min(e, THROUGH))))


def test_split_flows_and_campaigns_adds_up_to_the_total(ec):
    d = get(ec, "/api/email/timeseries", window="30d", grain="week", split="1")
    assert d["split"] is True
    for b in d["buckets"]:
        for k in ("sends", "delivered", "orders"):
            assert b["split"]["flows"][k] + b["split"]["campaigns"][k] == b["v"][k]
        assert b["split"]["flows"]["revenue"] + b["split"]["campaigns"]["revenue"] == pytest.approx(b["v"]["revenue"], abs=0.005)
    assert get(ec, "/api/email/timeseries", window="30d", grain="week", split="1", kind="flows")["split"] is False        # split only means something on All
    assert "split" not in get(ec, "/api/email/timeseries", window="30d", grain="week")["buckets"][0]
    only_email = get(ec, "/api/email/timeseries", window="30d", grain="week", channel="email")
    for b in only_email["buckets"]:
        s, e = D.fromisoformat(b["start"]), D.fromisoformat(b["end"])
        check_totals(b["v"], expect(pick(ROWS, max(s, FIRST), min(e, THROUGH), channel="email")))


def test_one_response_holds_a_bar_and_a_line_measure_per_bucket_from_sums(ec):
    """The two-axis chart (bars = sends, line = click rate) needs no second request: each bucket's `v` carries every measure, rates from that bucket's sums."""
    for grain, window in (("day", "30d"), ("week", "all"), ("month", "all")):
        d = get(ec, "/api/email/timeseries", window=window, grain=grain, split="1")
        assert d["split"] is True
        for b in d["buckets"]:
            s, e = D.fromisoformat(b["start"]), D.fromisoformat(b["end"])
            rows = pick(ROWS, max(s, FIRST), min(e, THROUGH))
            bar, line = sum(r["sends"] for r in rows), (sum(r["unique_clicks"] for r in rows) / sum(r["delivered"] for r in rows) if rows and sum(r["delivered"] for r in rows) else None)
            assert b["v"]["sends"] == bar and near(b["v"]["click_rate"], line), (grain, b["start"])
            # with the split on the line is still the combined total: flows plus campaigns sums, not either alone
            fl, ca = (pick(ROWS, max(s, FIRST), min(e, THROUGH), source=k) for k in ("flow", "campaign"))
            dl = sum(r["delivered"] for r in fl + ca)
            assert near(b["v"]["click_rate"], sum(r["unique_clicks"] for r in fl + ca) / dl if dl else None)
    # a ratio of sums is not an average of the daily ratios: the month's click rate differs from the mean of its days' rates
    m = get(ec, "/api/email/timeseries", window="all", grain="month")["buckets"][2]
    days = [pick(ROWS, x, x) for x in (D(2026, 9, 1) + dt.timedelta(days=i) for i in range(30))]
    mean_daily = sum(sum(r["unique_clicks"] for r in x) / sum(r["delivered"] for r in x) for x in days) / len(days)
    assert m["v"]["click_rate"] != pytest.approx(mean_daily, abs=1e-12)
    # partial bucket flags ride along with the same payload (the line's open marker reads them)
    wk = get(ec, "/api/email/timeseries", window="all", grain="week")["buckets"]
    assert wk[0]["partial"] and wk[-1]["partial"] and wk[0]["v"]["click_rate"] is not None


# ----------------------------------------------------------------------------- flows
def test_flows_table_ties_and_sorts_by_revenue(ec):
    d = get(ec, "/api/email/flows", window="30d")
    s, e = win(30)
    rows = d["rows"]
    assert [r["id"] for r in rows] == sorted([r["id"] for r in rows], key=lambda i: -expect(pick(ROWS, s, e, source="flow", pid=i))["revenue"])
    for r in rows:
        check_totals(r["m"], expect(pick(ROWS, s, e, source="flow", pid=r["id"])))
    f1 = next(r for r in rows if r["id"] == "F1")
    assert f1["name"] == "Billing Reminder Flow" and f1["messages"] == 2 and f1["status"] == "live" and f1["channels"] == ["email"]
    f2 = next(r for r in rows if r["id"] == "F2")
    assert f2["name"] == "Welcome Text Flow" and f2["status"] == "draft" and f2["m"]["open_rate"] is None
    assert sum(r["share"] for r in rows) == pytest.approx(1.0) and f1["share"] == pytest.approx(f1["m"]["revenue"] / sum(r["m"]["revenue"] for r in rows))
    check_totals(d["total"], expect(pick(ROWS, s, e, source="flow")))
    assert [r["id"] for r in get(ec, "/api/email/flows", window="30d", channel="sms")["rows"]] == ["F2"]


def test_flow_messages(ec):
    d = get(ec, "/api/email/flows/F1/messages", window="30d")
    s, e = win(30)
    assert d["flow"] == {"id": "F1", "name": "Billing Reminder Flow", "status": "live"}
    by = {r["id"]: r for r in d["rows"]}
    assert set(by) == {"fm1", "fm2"}
    for mid, r in by.items():
        check_totals(r["m"], expect(pick(ROWS, s, e, mid=mid)))
    assert by["fm1"]["has_copy"] is True and by["fm1"]["subject"] == "Your plan renews soon" and by["fm1"]["name"] == "Reminder 1"
    assert by["fm2"]["has_copy"] is False and by["fm2"]["subject"] == "Last reminder"              # no content row: the flow message's own subject, and 'copy not available'
    check_totals(d["total"], expect(pick(ROWS, s, e, pid="F1")))
    assert ec.get("/api/email/flows/NOPE/messages").status_code == 404


# ----------------------------------------------------------------------------- campaigns
def test_campaigns_table(ec):
    d = get(ec, "/api/email/campaigns", window="90d")
    s, e = win(90)
    rows = d["rows"]
    ids = [r["id"] for r in rows]
    assert set(ids) == {"C2", "C3"}                                      # C1 and C4 sent and finished before the 90 day window opens
    assert [r["send_date"] for r in rows] == sorted((r["send_date"] for r in rows), reverse=True)           # newest send first
    for r in rows:
        check_totals(r["m"], expect(pick(ROWS, s, e, source="campaign", pid=r["id"])))
    c3 = next(r for r in rows if r["id"] == "C3")
    assert c3["send_date"] == (THROUGH - dt.timedelta(days=3)).isoformat() and c3["channel"] == "sms" and c3["message_id"] == "cm3" and c3["sent_in_window"] is True
    assert c3["m"]["unsub_rate"] is not None and c3["m"]["open_rate"] is None
    c2 = next(r for r in rows if r["id"] == "C2")
    assert c2["messages"] == 2 and c2["message_id"] == "cm2a" and c2["subject"] == "Offer A" and c2["name"] == "Test Offer Campaign"        # the A/B message with more delivered
    check_totals(d["total"], expect(pick(ROWS, s, e, source="campaign")))
    # a campaign Klaviyo no longer lists keeps the name it was loaded with and its first send day
    wide = get(ec, "/api/email/campaigns", window="all")
    c4 = next(r for r in wide["rows"] if r["id"] == "C4")
    assert c4["name"] == "Early Bird" and c4["send_date"] == (FIRST + dt.timedelta(days=2)).isoformat() and c4["subject"] is None
    assert len(wide["rows"]) == 4
    assert [r["id"] for r in get(ec, "/api/email/campaigns", window="all", channel="sms")["rows"]] == ["C3"]


def test_campaign_search_and_a_campaign_sent_before_the_window(ec):
    d = get(ec, "/api/email/campaigns", window="all", q="spring")
    assert [r["id"] for r in d["rows"]] == ["C1"]
    assert [r["id"] for r in get(ec, "/api/email/campaigns", window="all", q="OFFER b")["rows"]] == []                 # subject is the first message's (Offer A)
    assert [r["id"] for r in get(ec, "/api/email/campaigns", window="all", q="offer a")["rows"]] == ["C2"]
    d7 = get(ec, "/api/email/campaigns", window="7d")
    assert [r["id"] for r in d7["rows"]] == ["C3"]
    check_totals(d7["total"], expect(pick(ROWS, *win(7), source="campaign")))


# ----------------------------------------------------------------------------- one message
def test_message_with_copy(ec):
    d = get(ec, "/api/email/message/fm1", window="30d")
    s, e = win(30)
    m = d["message"]
    assert m["source"] == "flow" and m["parent_id"] == "F1" and m["parent_name"] == "Billing Reminder Flow" and m["name"] == "Reminder 1" and m["channel"] == "email"
    assert m["subject"] == "Your plan renews soon" and m["preview_text"] == "Renewal notice" and m["from_name"] == "Lonvera"
    assert m["has_copy"] is True and m["body_chars"] == len(BODY) > 600 and m["body_text"] == BODY and m["link_count"] == 2 and m["image_count"] == 2
    assert dt.datetime.fromisoformat(m["copy_fetched_at"]) == dt.datetime(2026, 11, 9, 18, 0, tzinfo=dt.timezone.utc)
    check_totals(d["totals"], expect(pick(ROWS, s, e, mid="fm1")))
    assert len(d["series"]) == 30 and d["series"][0]["day"] == s.isoformat()
    for x in d["series"]:
        day = D.fromisoformat(x["day"])
        ex = expect(pick(ROWS, day, day, mid="fm1"))
        assert x["sends"] == ex["sends"] and x["clicks"] == ex["unique_clicks"] and x["revenue"] == pytest.approx(ex["revenue"], abs=0.005) and x["orders"] == ex["orders"]


def test_message_without_copy_and_with_images_only(ec):
    nocopy = get(ec, "/api/email/message/fm2", window="30d")["message"]
    assert nocopy["has_copy"] is False and nocopy["body_text"] is None and nocopy["copy_fetched_at"] is None and nocopy["subject"] == "Last reminder"
    images = get(ec, "/api/email/message/cm2b", window="all")["message"]
    assert images["has_copy"] is False and images["copy_fetched_at"] is not None and images["image_count"] == 3
    sms = get(ec, "/api/email/message/cm3", window="30d")["message"]
    assert sms["channel"] == "sms" and sms["body_text"] == "Flash sale: 20 percent off today" and sms["parent_name"] == "Flash Text Campaign"
    assert sms["subject"] is None and sms["preview_text"] is None and sms["from_name"] is None      # real JSON nulls: the page must render nothing for them
    assert ec.get("/api/email/message/does-not-exist").status_code == 404


# ----------------------------------------------------------------------------- pure functions
class MemSource:
    """The same interface as DbSource over a list of rows: what the UI mock uses too."""
    def __init__(self, rows):
        self._rows = rows

    def bounds(self):
        return (min(r["day"] for r in self._rows), max(r["day"] for r in self._rows)) if self._rows else (None, None)

    def rows(self, start, end, kind, channel, parent_id=None, message_id=None):
        src = {"flows": "flow", "campaigns": "campaign"}.get(kind)
        return pick(self._rows, start, end, source=src, channel=None if channel == "all" else channel, pid=parent_id, mid=message_id)

    def flows(self): return {}
    def campaigns(self): return {}
    def flow_messages(self, flow_id=None): return {}
    def content(self, ids): return {}


def test_memsource_gives_the_same_answers_as_the_database(ec):
    p = E.parse_params({"window": "30d"})
    mem = E.summary(MemSource(ROWS), p)
    db = get(ec, "/api/email/summary", window="30d")
    assert mem["totals"] == db["totals"] and mem["change"] == db["change"]


def test_change_of_polarity_and_missing_values():
    assert E.change_of("revenue", 110.0, 100.0) == {"v": 0.1, "t": "pct"}
    assert E.change_of("revenue", 110.0, 0) is None and E.change_of("revenue", None, 5) is None
    assert E.change_of("click_rate", 0.03, 0.02) == {"v": 0.01, "t": "pp"}
    assert E.change_of("click_rate", 0.03, 0.0) == {"v": 0.03, "t": "pp"}           # from a zero rate the points change is still a fact


def test_empty_table_answers_empty_not_an_error(edb):
    class Nothing(MemSource):
        pass
    s = Nothing([])
    p = E.parse_params({})
    assert E.summary(s, p)["empty"] is True and E.timeseries(s, p, "week", False)["buckets"] == []
    assert E.flows(s, p)["rows"] == [] and E.campaigns(s, p)["rows"] == []


def test_a_window_with_no_rows_shows_zero_counts_and_dashes_for_rates(ec):
    d = get(ec, "/api/email/summary", window="7d", kind="campaigns", channel="email")
    s, e = win(7)
    assert expect(pick(ROWS, s, e, source="campaign", channel="email"))["sends"] == 0
    t = d["totals"]
    assert t["sends"] == 0 and t["revenue"] == 0 and t["open_rate"] is None and t["click_rate"] is None and t["rev_per_delivered"] is None and t["unsub_rate"] is None


def test_the_last_test_empties_the_table_over_http(eapp, edb):
    conn = psycopg.connect(EM_URL)
    conn.execute("TRUNCATE klaviyo_daily")
    conn.commit()
    conn.close()
    c = eapp.test_client()
    assert c.post("/api/login", json={"username": "JoshHoughton", "password": PASSWORD}).status_code == 204
    for u in ("/api/email/summary", "/api/email/timeseries", "/api/email/flows", "/api/email/campaigns"):
        d = c.get(u).get_json()
        assert d["empty"] is True, u
    assert c.get("/api/email/message/fm1").status_code == 404
