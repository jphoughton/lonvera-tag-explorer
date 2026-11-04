"""Daily hooks in etl/projection_drivers.py: freshness(conn) and refresh(conn, days)."""
from __future__ import annotations

from datetime import date, timedelta

from conftest import put_actuals

from app.projection import freshness as core_freshness
from app.projection import scenarios
from app.projection.drivers import loaders, model
from etl import projection_drivers as hooks

TODAY = date(2026, 11, 3)


def M(y, m):
    return date(y, m, 1)


def fill_ad_daily(pg, start: date, end: date, per_day: float = 100.0, skip: set[date] | None = None):
    d = start
    rows = []
    while d <= end:
        if not skip or d not in skip:
            rows.append(("ad1", d, "fam", "Fam", per_day))
        d += timedelta(days=1)
    with pg.cursor() as cur:
        cur.executemany("INSERT INTO ad_daily (ad_id, day, meta_family_key, meta_family, spend, source) VALUES (%s,%s,%s,%s,%s,'meta_api')", rows)


def feed(pg):
    return {r["month"]: float(r["value"]) for r in pg.execute("SELECT month, value FROM proj_actuals WHERE line_key = 'spend.meta_feed'").fetchall()}


def test_refresh_loads_complete_closed_months_only(pg):
    fill_ad_daily(pg, date(2026, 9, 1), date(2026, 11, 2))               # Sep, Oct complete; Nov partial
    out = hooks.refresh(pg, days=14, today=TODAY)
    assert out == {"rows": 2, "data_through": "2026-11-02"}
    assert feed(pg) == {M(2026, 9): 3000.0, M(2026, 10): 3100.0}


def test_refresh_skips_a_month_with_a_missing_day(pg):
    fill_ad_daily(pg, date(2026, 9, 1), date(2026, 10, 31), skip={date(2026, 10, 15)})
    hooks.refresh(pg, today=TODAY)
    assert feed(pg) == {M(2026, 9): 3000.0}


def test_refresh_is_idempotent_and_writes_nothing_when_nothing_changed(pg):
    fill_ad_daily(pg, date(2026, 9, 1), date(2026, 10, 31))
    assert hooks.refresh(pg, today=TODAY)["rows"] == 2
    audit_before = pg.execute("SELECT count(*) AS n FROM proj_audit").fetchone()["n"]
    again = hooks.refresh(pg, today=TODAY)
    assert again["rows"] == 0 and pg.execute("SELECT count(*) AS n FROM proj_audit").fetchone()["n"] == audit_before
    assert feed(pg) == {M(2026, 9): 3000.0, M(2026, 10): 3100.0}


def test_refresh_picks_up_a_restated_recent_month_but_leaves_old_closed_months_alone(pg):
    fill_ad_daily(pg, date(2026, 8, 1), date(2026, 10, 31))
    hooks.refresh(pg, today=TODAY, days=400)
    pg.execute("UPDATE ad_daily SET spend = spend + 1 WHERE day = '2026-10-31'")          # Meta restated the last day of October
    pg.execute("UPDATE ad_daily SET spend = spend + 1 WHERE day = '2026-08-05'")          # ...and one in August
    out = hooks.refresh(pg, days=14, today=TODAY)
    assert out["rows"] == 1 and feed(pg)[M(2026, 10)] == 3101.0 and feed(pg)[M(2026, 8)] == 3100.0       # window = Oct 20 onward
    assert hooks.refresh(pg, days=400, today=TODAY)["rows"] == 1 and feed(pg)[M(2026, 8)] == 3101.0


def test_refresh_with_no_ad_data(pg):
    assert hooks.refresh(pg, today=TODAY) == {"rows": 0, "data_through": None}


def test_a_refreshed_month_becomes_a_feeds_month_in_the_engine(pg, rw):
    fill_ad_daily(pg, date(2026, 10, 1), date(2026, 10, 31), per_day=10000.0)
    hooks.refresh(pg, today=TODAY)
    rw.commit()
    inp = loaders.load_inputs(rw, scenarios.get_scenario(rw, "current"), TODAY)
    d = model.compute_drivers({}, inp)
    assert d.source[M(2026, 10)] == "meta_feed" and d.spend_meta[M(2026, 10)] == 310000.0
    assert d.month_status[M(2026, 10)] == "feeds" and d.feeds_through == M(2026, 10) and d.books_through is None and d.actual_through is None


def test_freshness_reports_the_daily_feed_and_the_monthly_books(pg):
    fill_ad_daily(pg, date(2026, 10, 25), date(2026, 11, 2))
    put_actuals(pg, "spend.meta", {M(2026, 6): 1.0, M(2026, 7): 1.0})
    put_actuals(pg, "amazon_rev", {M(2026, 8): 1.0})
    feeds = hooks.freshness(pg)
    by = {f["feed"]: f for f in feeds}
    assert by["drivers.meta_spend"]["cadence"] == "daily" and by["drivers.meta_spend"]["stale_after_days"] == 2
    assert by["drivers.meta_spend"]["data_through"] == date(2026, 11, 2)
    assert by["drivers.books"]["cadence"] == "monthly-upload" and by["drivers.books"]["stale_after_days"] == 45
    assert by["drivers.books"]["data_through"] == date(2026, 8, 31)          # last day of the last month with books data
    assert all(set(f) == {"feed", "label", "cadence", "data_through", "stale_after_days"} for f in feeds)


def test_freshness_with_nothing_loaded_is_unknown_not_an_error(pg):
    feeds = hooks.freshness(pg)
    assert [f["data_through"] for f in feeds] == [None, None]


def test_the_core_freshness_evaluator_accepts_the_entries(pg):
    fill_ad_daily(pg, date(2026, 10, 25), date(2026, 10, 30))
    put_actuals(pg, "pnl.mkt_google_ads", {M(2026, 5): 1.0})
    ev = [core_freshness.evaluate("drivers", e, TODAY) for e in hooks.freshness(pg)]
    by = {e["feed"]: e for e in ev}
    assert by["drivers.meta_spend"]["status"] == "stale" and by["drivers.meta_spend"]["age_days"] == 4
    assert by["drivers.books"]["status"] == "stale"                            # May 31 is more than 45 days before Nov 3
    hooks_by_key, problems = core_freshness.load_hooks(["drivers"])
    assert not problems and hooks_by_key["drivers"] is hooks


def test_the_meta_monthly_view_sums_ad_daily_and_the_migration_can_run_twice(pg):
    fill_ad_daily(pg, date(2026, 9, 1), date(2026, 9, 30), per_day=10.0)
    fill_ad_daily(pg, date(2026, 10, 1), date(2026, 10, 3), per_day=5.0)
    rows = {r["month"]: r for r in pg.execute("SELECT * FROM proj_meta_spend_monthly").fetchall()}
    assert float(rows[M(2026, 9)]["spend"]) == 300.0 and rows[M(2026, 9)]["days"] == 30 and rows[M(2026, 10)]["days"] == 3
    assert rows[M(2026, 10)]["last_day"] == date(2026, 10, 3)
    from pathlib import Path
    pg.execute((Path(__file__).resolve().parents[3] / "migrations" / "120_proj_drivers.sql").read_text())           # idempotent
