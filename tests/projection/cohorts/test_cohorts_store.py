"""Cohort tables: loaders, upserts that never erase, the Goal curve snapshots, the seeder, and feed freshness."""
from __future__ import annotations

from datetime import date

import psycopg
import pytest
from conftest import DB_URL, build_history, make_cohort, seed_cohorts

from app.projection import freshness as shared_freshness
from app.projection import rwdb
from app.projection.cohorts import store
from etl import projection_cohorts as etl_cohorts

D = date


@pytest.fixture()
def rw(cohorts_db):
    with rwdb.get_rw(cohorts_db) as conn:
        yield conn


def test_upsert_and_load_roundtrip(rw):
    c = make_cohort(D(2026, 8, 1), n=8417, first=63.47, ratios=[0.2, 0.3], repeat_90d=0.61, cac=95.9, payback_months=2.5, nc_rev_reference=515139.33)
    seed_cohorts(rw, [c])
    (got,) = store.load_cohorts(rw)
    assert got.month == c.month and got.new_customers == 8417 and got.first_order == pytest.approx(63.47)
    assert got.cum[:2] == pytest.approx(c.cum[:2]) and got.cum[2:] == (None,) * 34
    assert (got.repeat_90d, got.cac, got.payback_months, got.nc_rev_reference) == (pytest.approx(0.61), pytest.approx(95.9), pytest.approx(2.5), 515139.33)


def test_per_customer_values_keep_six_decimals(rw):
    seed_cohorts(rw, [make_cohort(D(2026, 8, 1), first=63.4650065343947, ratios=[0.0123456789])])
    (got,) = store.load_cohorts(rw)
    assert got.first_order == pytest.approx(63.465007, abs=1e-6) and got.cum[0] == pytest.approx(63.4650065343947 * 1.0123456789, abs=1e-6)


def test_upsert_revises_and_adds_but_never_erases_stored_cells(rw):
    seed_cohorts(rw, [make_cohort(D(2026, 8, 1), n=100, first=60.0, ratios=[0.2, 0.1], cac=90.0)])
    store.upsert_cohorts(rw, [store.CohortRow(month=D(2026, 8, 1), new_customers=110, first_order=None, cum={1: 75.0})], "again")
    rw.commit()
    (got,) = store.load_cohorts(rw)
    assert got.new_customers == 110 and got.first_order == pytest.approx(60.0) and got.cac == pytest.approx(90.0)     # None keeps the stored head value
    assert got.cum[0] == 75.0 and got.cum[1] == pytest.approx(60.0 + 12.0 + 6.0)                                      # M1 revised, M2 kept


def test_goal_curve_returns_the_newest_snapshot_and_zeros_when_none(rw):
    assert store.load_goal(rw) == ([0.0] * 60, None)
    store.replace_goal(rw, D(2026, 9, 29), [0.2] * 60)
    store.replace_goal(rw, D(2026, 11, 3), [0.3] * 60)
    store.replace_goal(rw, D(2026, 11, 3), [0.4] * 59)                      # same day again replaces that snapshot (59 ages -> last age 0)
    rw.commit()
    goal, snap = store.load_goal(rw)
    assert snap == D(2026, 11, 3) and goal[0] == 0.4 and goal[58] == 0.4 and goal[59] == 0.0
    assert rw.execute("SELECT count(*) AS n FROM proj_curve_goal").fetchone()["n"] == 60 + 59      # the older snapshot is kept


def test_downstream_helpers_return_first_order_revenue_and_new_customers(rw):
    seed_cohorts(rw, [make_cohort(D(2026, 9, 1), n=200, first=70.0), make_cohort(D(2026, 10, 1), n=300, first=80.0)])
    assert store.first_order_revenue(rw) == {D(2026, 9, 1): pytest.approx(14000.0), D(2026, 10, 1): pytest.approx(24000.0)}
    assert store.new_customers(rw) == {D(2026, 9, 1): 200.0, D(2026, 10, 1): 300.0}


def test_database_rejects_bad_rows(cohorts_db):
    with psycopg.connect(cohorts_db) as c:
        with pytest.raises(psycopg.errors.CheckViolation):
            c.execute("INSERT INTO proj_cohorts (cohort_month, new_customers) VALUES ('2026-08-15', 10)")
    with psycopg.connect(cohorts_db) as c:
        c.execute("INSERT INTO proj_cohorts (cohort_month, new_customers) VALUES ('2026-08-01', 10)")
        with pytest.raises(psycopg.errors.CheckViolation):
            c.execute("INSERT INTO proj_cohort_cum (cohort_month, age, cum_per_customer) VALUES ('2026-08-01', 37, 5)")


# ---------------------------------------------------------------------------------------------------------------- seeder

def test_seeder_loads_the_snapshot_and_is_idempotent(rw):
    try:
        rows, goal, snap = etl_cohorts.read_snapshot()
    except Exception as e:
        pytest.skip(f"sheet snapshot not available: {e}")
    first = etl_cohorts.seed(rw, rows, goal, snap)
    rw.commit()
    counts = lambda: [rw.execute(f"SELECT count(*) AS n FROM {t}").fetchone()["n"] for t in ("proj_cohorts", "proj_cohort_cum", "proj_curve_goal")]   # noqa: E731
    before = counts()
    second = etl_cohorts.seed(rw, rows, goal, snap)
    rw.commit()
    assert first == second and counts() == before == [33, 522, 60]
    cohorts = store.load_cohorts(rw)
    assert cohorts[0].month == D(2024, 2, 1) and cohorts[-1].month == D(2026, 10, 1) and cohorts[0].nc_rev_reference == pytest.approx(1598.36)
    audit = rw.execute("SELECT count(*) AS n FROM proj_audit WHERE action = 'cohorts.seed'").fetchone()["n"]
    assert audit == 2


# ---------------------------------------------------------------------------------------------------------------- freshness

def test_freshness_reports_a_monthly_upload_feed_relative_to_today(rw):
    """Loaded through Oct 2026: fresh until Dec 15 (45 days after Oct 31), behind from Dec 1 (Nov is closed and not loaded), stale from Dec 16."""
    seed_cohorts(rw, build_history(first=D(2025, 8, 1), last=D(2026, 10, 1)))
    (f,) = etl_cohorts.freshness(rw, today=D(2026, 11, 3))
    assert f["feed"] == "cohort_export" and f["label"] == "Cohort export" and f["cadence"] == "monthly-upload" and f["stale_after_days"] == 45
    assert f["data_through"] == D(2026, 10, 31) and f["data_through_month"] == D(2026, 10, 1) and f["observed_through"] == D(2026, 10, 1)
    assert f["behind"] is False and f["stale"] is False and f["age_days"] == 3 and f["next_close"] == D(2026, 12, 1)

    (f,) = etl_cohorts.freshness(rw, today=D(2026, 12, 5))
    assert f["behind"] is True and f["stale"] is False and f["last_closed_month"] == D(2026, 11, 1)          # Nov closed, not loaded, still inside the grace
    (f,) = etl_cohorts.freshness(rw, today=D(2026, 12, 16))
    assert f["behind"] is True and f["stale"] is True and f["age_days"] == 46

    # the shared check (used by the daily runner) reads the same entry
    entry = etl_cohorts.freshness(rw, today=D(2026, 12, 16))[0]
    assert shared_freshness.evaluate("cohorts", entry, D(2026, 12, 16))["status"] == "stale"
    assert shared_freshness.evaluate("cohorts", etl_cohorts.freshness(rw, today=D(2026, 11, 3))[0], D(2026, 11, 3))["status"] == "fresh"
    assert shared_freshness.hook_module("cohorts") is etl_cohorts


def test_freshness_with_nothing_loaded_is_stale_and_unknown_to_the_shared_check(rw):
    (f,) = etl_cohorts.freshness(rw, today=D(2026, 11, 3))
    assert f["data_through"] is None and f["behind"] is True and f["stale"] is True and f["cohorts"] == 0
    assert shared_freshness.evaluate("cohorts", f, D(2026, 11, 3))["status"] == "unknown"
