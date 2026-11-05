"""etl.live_today: the hourly Upstack pull of today so far, on recorded-shape fixtures (no network, no credentials)."""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import psycopg
import pytest

from etl import live_today as L
from etl.feeds import upstack
from etl.feeds.common import FeedError, PT, make_ctx
from feeds_helpers import FakeResponse, FakeSession, path_is

D = dt.date
ENV = {"UPSTACK_API_KEY": "key-" + "a" * 40, "UPSTACK_PIXEL_ID": "pixel-" + "b" * 30}
ROOT = Path(__file__).resolve().parents[3]
NOW = dt.datetime(2026, 11, 5, 23, 5, 41, tzinfo=dt.timezone.utc)            # 15:05 Pacific (PST), Thursday Nov 5
TODAY = D(2026, 11, 5)


def ctx(session, now=NOW):
    return make_ctx(session=session, sleep=lambda s: None, env=ENV, now_utc=now)


def chan(source, name, day, spend):
    return {"accountName": name, "basicDetails": {"resourceSource": source}, "data": {"primary": [{"dt": f"{day}T00:00:00.000", "channel.spend": spend}]}}


def session(orders=None, channels=None):
    s = FakeSession()
    s.on(path_is("/api/query"), lambda c: FakeResponse(200, {"results": [{"data": {"primary": orders if orders is not None else []}}]}))
    s.on(path_is("/api/query-channel-details"), lambda c: FakeResponse(200, {"results": channels if channels is not None else []}))
    return s


ORDERS = [{"dt": "2026-11-05T00:00:00.000", "orders.new_customer_total_revenue": 9397.944, "orders.new_customers": 121}]
CHANNELS = [chan("facebookIntegration", "Lonvera Main", "2026-11-05", "10851.0700"), chan("facebookIntegration", "Lonvera BackUp", "2026-11-05", "0.0000"),
            chan("googleAdsIntegration", "Lonvera", "2026-11-05", "497.6800"), chan("tiktokAdsIntegration", "Lonvera TikTok", "2026-11-05", "50.25")]


def test_pull_reads_the_pacific_day_so_far_with_the_closed_day_measures():
    s = session(ORDERS, CHANNELS)
    snap = L.pull(ctx(s))
    assert snap.day == TODAY and snap.pacific_time == dt.time(15, 5) and snap.pulled_at == NOW.replace(second=0)
    assert (snap.meta_spend, snap.google_spend, snap.total_spend) == (10851.07, 497.68, 11399.0)
    assert snap.nc_rev == 9397.94 and snap.nc_orders == 121
    q = next(c for c in s.calls if c["url"].endswith("/api/query"))["json"]
    assert q["timezone"] == "America/Los_Angeles" and q["granularity"] == "day" and q["dateRange"] == {"start": "2026-11-05", "end": "2026-11-05"}
    assert [m["measure"] for m in q["measures"]] == ["orders.new_customer_total_revenue", "orders.new_customers"]     # the nc_rev / nc_orders columns of the orders feed
    assert set(["orders.new_customer_total_revenue", "orders.new_customers"]) <= set(upstack.ORDER_MEASURES)
    h = s.calls[0]["headers"]
    assert h["x-api-key"] == ENV["UPSTACK_API_KEY"] and h["x-pixel-id"] == ENV["UPSTACK_PIXEL_ID"]


def test_the_pacific_day_is_not_the_utc_day():
    late = dt.datetime(2026, 11, 6, 5, 30, tzinfo=dt.timezone.utc)                # 21:30 Pacific on Nov 5
    s = session([{"dt": "2026-11-05T00:00:00.000", "orders.new_customer_total_revenue": 5, "orders.new_customers": 1}], [])
    snap = L.pull(ctx(s, late))
    assert snap.day == TODAY and snap.pacific_time == dt.time(21, 30) and snap.pulled_at.date() == D(2026, 11, 6)
    assert s.calls[0]["json"]["dateRange"]["start"] == "2026-11-05"


def test_rows_of_other_days_are_ignored_and_meta_accounts_add_up():
    ch = [chan("facebookIntegration", "A", "2026-11-04", "99999"), chan("facebookIntegration", "A", "2026-11-05", "100"), chan("facebookIntegration", "B", "2026-11-05", "23.5")]
    snap = L.build_snapshot(NOW, TODAY, [{"dt": "2026-11-04T00:00:00.000", "orders.new_customer_total_revenue": 1e6}] + ORDERS, ch)
    assert snap.meta_spend == 123.5 and snap.google_spend == 0.0 and snap.nc_rev == 9397.94


def test_channel_pages_are_followed_until_a_short_page():
    page1 = [chan("facebookIntegration", f"acc{i}", "2026-11-05", "1") for i in range(L.PAGE)]
    page2 = [chan("googleAdsIntegration", "g", "2026-11-05", "7")]
    s = FakeSession()
    s.on(path_is("/api/query"), FakeResponse(200, {"results": [{"data": {"primary": ORDERS}}]}))
    s.on(path_is("/api/query-channel-details"), lambda c: FakeResponse(200, {"results": page1 if c["json"]["offset"] == 0 else page2}))
    snap = L.pull(ctx(s))
    assert snap.meta_spend == 20.0 and snap.google_spend == 7.0 and [c["json"]["offset"] for c in s.calls if "channel" in c["url"]] == [0, L.PAGE]


def test_early_morning_with_no_rows_is_zero_but_later_it_is_an_error():
    early = dt.datetime(2026, 11, 5, 8, 10, tzinfo=dt.timezone.utc)               # 00:10 Pacific
    snap = L.pull(ctx(session([], []), early))
    assert (snap.nc_rev, snap.nc_orders, snap.meta_spend, snap.total_spend) == (0.0, 0, 0.0, 0.0)
    with pytest.raises(FeedError, match="no sales row and no spend"):
        L.pull(ctx(session([], [])))                                              # 15:05 Pacific: Upstack returning nothing at all means a broken response


def test_garbage_is_refused():
    with pytest.raises(FeedError, match="negative"):
        L.build_snapshot(NOW, TODAY, [{"dt": "2026-11-05T00:00:00.000", "orders.new_customer_total_revenue": -5}], CHANNELS)
    with pytest.raises(FeedError, match="2 rows"):
        L.build_snapshot(NOW, TODAY, ORDERS * 2, CHANNELS)
    with pytest.raises(FeedError, match="moved on"):
        L.build_snapshot(dt.datetime(2026, 11, 6, 8, 5, tzinfo=dt.timezone.utc), TODAY, ORDERS, CHANNELS)      # midnight passed between choosing the day and building


# ---------------------------------------------------------------------------------------------------------------- the database side

@pytest.fixture()
def snaps(feeds_db):
    with psycopg.connect(feeds_db) as c:
        c.execute((ROOT / "migrations" / "192_proj_live_today.sql").read_text())
        c.execute("TRUNCATE proj_live_today_snapshots")
        c.commit()
    c = psycopg.connect(feeds_db)
    yield c
    c.rollback()
    c.execute("TRUNCATE proj_live_today_snapshots")
    c.commit()
    c.close()


def rows(c):
    return c.execute("SELECT pulled_at, day, pacific_time, meta_spend, google_spend, total_spend, nc_rev, nc_orders FROM proj_live_today_snapshots ORDER BY pulled_at").fetchall()


def test_save_is_idempotent_inside_the_minute_and_keeps_history(snaps):
    s1 = L.build_snapshot(NOW, TODAY, ORDERS, CHANNELS)
    L.save(snaps, s1); L.save(snaps, s1)
    assert len(rows(snaps)) == 1
    s1b = L.build_snapshot(NOW.replace(second=59), TODAY, [{"dt": "2026-11-05T00:00:00.000", "orders.new_customer_total_revenue": 9500, "orders.new_customers": 122}], CHANNELS)
    L.save(snaps, s1b)
    r = rows(snaps)
    assert len(r) == 1 and float(r[0][6]) == 9500.0 and r[0][7] == 122                # same minute: replaced, not duplicated
    L.save(snaps, L.build_snapshot(NOW + dt.timedelta(hours=1), TODAY, ORDERS, CHANNELS))
    r = rows(snaps)
    assert len(r) == 2 and r[0][2] == dt.time(15, 5) and r[1][2] == dt.time(16, 5)


def test_save_prunes_old_days_only(snaps):
    old_now = NOW - dt.timedelta(days=100)
    old_day = old_now.astimezone(PT).date()
    L.save(snaps, L.build_snapshot(old_now, old_day, [{"dt": f"{old_day}T00:00:00.000", "orders.new_customer_total_revenue": 1, "orders.new_customers": 1}], []))
    n = L.save(snaps, L.build_snapshot(NOW, TODAY, ORDERS, CHANNELS))
    assert n == 1 and len(rows(snaps)) == 1


def test_main_writes_one_snapshot_and_exits_0(snaps):
    assert L.main([], ctx=ctx(session(ORDERS, CHANNELS)), conn=snaps) == 0
    r = rows(snaps)
    assert len(r) == 1 and float(r[0][3]) == 10851.07 and float(r[0][6]) == 9397.94


def test_an_upstack_error_writes_nothing_keeps_the_last_good_snapshot_and_exits_1(snaps, capsys):
    L.save(snaps, L.build_snapshot(NOW - dt.timedelta(hours=1), TODAY, ORDERS, CHANNELS)); snaps.commit()
    bad = FakeSession()
    bad.on(path_is("/api/query"), FakeResponse(500, text="boom " + ENV["UPSTACK_API_KEY"]))
    c = ctx(bad)
    assert L.main([], ctx=c, conn=snaps) == 1
    assert len(rows(snaps)) == 1                                                  # the earlier pull is still the latest
    err = capsys.readouterr().err
    assert "live_today" in err and ENV["UPSTACK_API_KEY"] not in err


def test_an_auth_failure_and_an_unusable_response_both_exit_1_without_writing(snaps):
    denied = FakeSession(); denied.on(lambda c: True, FakeResponse(401, text="no"))
    assert L.main([], ctx=ctx(denied), conn=snaps) == 1
    assert L.main([], ctx=ctx(session([], [])), conn=snaps) == 1
    assert rows(snaps) == []


def test_dry_run_never_touches_the_database(capsys):
    assert L.main(["--dry-run"], ctx=ctx(session(ORDERS, CHANNELS)), conn=object()) == 0          # an object() as the connection would blow up on any use
    assert "dry run: nothing written" in capsys.readouterr().out


def test_the_deploy_script_migrates_then_runs_the_live_pull():
    text = (ROOT / "scripts/deploy/run_live.sh").read_text()
    assert "python -m etl.migrate" in text and "python -m etl.live_today" in text and text.index("etl.migrate") < text.index("etl.live_today")
    assert (ROOT / "scripts/deploy/run_live.sh").stat().st_mode & 0o111
