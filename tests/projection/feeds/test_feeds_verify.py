"""Verification: pure comparison, per-feed tolerances, soft recent days, sheet-behind days, schema drift, consecutive clean days."""
from __future__ import annotations

import datetime as dt

import pytest

import etl.feeds as feeds
from etl.feeds import common, verify
from etl.feeds.verify import compare_rows

D = dt.date
TODAY = D(2026, 11, 10)


def orders_row(day, nc=100.0, rc=50.0, ncn=3.0, tot=None, tn=5.0, tax=7.0, ref=0.0):
    return (day, nc, rc, ncn, tot if tot is not None else nc + rc, tn, tax, ref)


F = feeds.registry()["orders"]


def test_identical_rows_are_ok_and_money_tolerance_is_a_dollar_or_a_tenth_of_a_percent():
    a = [orders_row(D(2026, 11, 1), nc=100.0), orders_row(D(2026, 11, 2), nc=10000.0)]
    b = [orders_row(D(2026, 11, 1), nc=100.9), orders_row(D(2026, 11, 2), nc=10009.0)]            # $0.90 and 0.09%
    res = compare_rows(F, a, b, TODAY)
    assert {d: r.status for d, r in res.items()} == {D(2026, 11, 1): "ok", D(2026, 11, 2): "ok"}


def test_out_of_tolerance_fails_with_the_worst_column_and_counts_are_exact():
    a = [orders_row(D(2026, 11, 1), nc=100.0, ncn=3.0)]
    b = [orders_row(D(2026, 11, 1), nc=150.0, ncn=4.0)]
    r = compare_rows(F, a, b, TODAY)[D(2026, 11, 1)]
    assert r.status == "fail" and r.n_bad == 1 and r.worst_col in ("nc_rev", "total_rev") and r.worst_abs == pytest.approx(50.0)
    assert "nc_orders" in r.detail


def test_recent_days_are_soft_at_three_times_the_tolerance_and_never_fail():
    a = [orders_row(D(2026, 11, 9), nc=100.0)]
    assert compare_rows(F, a, [orders_row(D(2026, 11, 9), nc=102.5)], TODAY)[D(2026, 11, 9)].status == "ok"      # within 3x of $1
    assert compare_rows(F, a, [orders_row(D(2026, 11, 9), nc=130.0)], TODAY)[D(2026, 11, 9)].status == "soft"
    assert compare_rows(F, a, [orders_row(D(2026, 11, 1), nc=130.0)], TODAY).get(D(2026, 11, 9)) is not None


def test_missing_key_fails_unless_all_zero_and_sheet_behind_is_its_own_status():
    zero = (D(2026, 11, 1), 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    assert compare_rows(F, [orders_row(D(2026, 11, 1))], [], TODAY, b_through=D(2026, 11, 5))[D(2026, 11, 1)].status == "fail"
    assert compare_rows(F, [zero], [], TODAY, b_through=D(2026, 11, 5))[D(2026, 11, 1)].status == "ok"
    res = compare_rows(F, [orders_row(D(2026, 11, 6))], [], TODAY, b_through=D(2026, 11, 5))
    assert res[D(2026, 11, 6)].status == "sheet_stale"
    assert verify.summarize(res) == {"checked": 0, "failed": 0, "soft": 0, "stale": 1, "ok": 0}


def test_open_periods_are_not_compared():
    reach = feeds.registry()["meta_reach"]
    open_week = ("week", "2026-W45", D(2026, 11, 9), D(2026, 11, 10), 1.0, 1.0, 1.0, 1.0, 1.0)
    closed_week = ("week", "2026-W44", D(2026, 11, 2), D(2026, 11, 8), 100.0, 80.0, 5.0, 6.0, 4.0)
    res = compare_rows(reach, [open_week, closed_week], [("week", "2026-W45", D(2026, 11, 9), D(2026, 11, 10), 9.0, 9.0, 9.0, 9.0, 9.0), closed_week], TODAY)
    assert list(res) == [D(2026, 11, 2)] and res[D(2026, 11, 2)].status == "ok"


def test_units_compare_by_location_product_day_and_estimate_tolerance():
    f = feeds.registry()["units_intl"]
    a = [("intl", "bull", D(2026, 11, 1), 230.0, "estimate", True)]
    assert compare_rows(f, a, [("intl", "bull", D(2026, 11, 1), 233.0, "estimate", True)], TODAY)[D(2026, 11, 1)].status == "ok"          # 3 units
    assert compare_rows(f, a, [("intl", "bull", D(2026, 11, 1), 260.0, "estimate", True)], TODAY)[D(2026, 11, 1)].status == "fail"


# ---------------------------------------------------------------------------------------------------------------- database side

def put(conn, table, rows):
    for r in rows:
        conn.execute(f"INSERT INTO {table} (day, nc_rev, rc_rev, nc_orders, total_rev, total_orders, tax, refunds) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)", r)


def test_verify_feed_records_days_and_ignores_days_before_the_shadow_began(conn):
    put(conn, "proj_feed_orders", [orders_row(D(2026, 10, d)) for d in range(20, 31)])
    put(conn, "proj_feedx_orders", [orders_row(D(2026, 10, d)) for d in range(25, 31)] + [orders_row(D(2026, 10, 29), nc=999.0)][:0])
    conn.execute("UPDATE proj_feedx_orders SET nc_rev = 500 WHERE day = '2026-10-27'")
    r = verify.verify_feed(conn, F, 30, TODAY)
    assert r["status"] == "fail" and r["failing_days"] == [D(2026, 10, 27)] and r["checked"] == 6 and r["window"][0] == D(2026, 10, 25)
    row = conn.execute("SELECT status, n_bad, worst_col FROM proj_feed_verify WHERE day = '2026-10-27'").fetchone()
    assert row[0] == "fail" and row[1] == 1
    assert conn.execute("SELECT days_checked, days_failed FROM proj_feed_verify_runs").fetchone() == (6, 1)


def test_verify_with_no_shadow_data_and_sheet_behind(conn):
    assert verify.verify_feed(conn, F, 30, TODAY)["status"] == "no shadow data yet"
    put(conn, "proj_feedx_orders", [orders_row(D(2026, 11, 8))])
    r = verify.verify_feed(conn, F, 30, TODAY)
    assert r["stale"] == 1 and r["failed"] == 0 and r["status"] == "ok"                       # the sheet-fed table is empty: that is the sheet being behind


def test_schema_drift_is_reported(conn):
    assert verify.schema_drift(conn, F) == []
    conn.execute("ALTER TABLE proj_feed_orders ADD COLUMN extra_col numeric")
    put(conn, "proj_feedx_orders", [orders_row(D(2026, 11, 1))])
    try:
        assert verify.schema_drift(conn, F) == ["extra_col: missing in proj_feedx_orders"]
        assert verify.verify_feed(conn, F, 30, TODAY)["status"] == "fail"
    finally:
        conn.rollback()
    conn.execute("ALTER TABLE proj_feed_orders DROP COLUMN IF EXISTS extra_col")
    conn.commit()
    for f in feeds.registry().values():
        if not f.native:
            assert verify.schema_drift(conn, f) == [], f.key                                 # every shadow table matches the sheet-fed table it mirrors


def test_clean_streak_counts_consecutive_pacific_dates(conn):
    def run_on(day, failed=0, att="ok"):
        ts = dt.datetime.combine(day, dt.time(12), tzinfo=common.PT)
        conn.execute("INSERT INTO proj_feed_verify_runs (at, feed, days_checked, days_failed, schema_ok) VALUES (%s, 'orders', 5, %s, true)", (ts, failed))
        conn.execute("INSERT INTO proj_feed_attempts (feed, mode, source, started_at, finished_at, status) VALUES ('orders', 'shadow', 'api:upstack', %s, %s, %s)", (ts, ts, att))
    for d in (7, 8, 9, 10):
        run_on(D(2026, 11, d))
    assert verify.clean_streak(conn, "orders", TODAY) == 4
    run_on(D(2026, 11, 6), failed=2)
    run_on(D(2026, 11, 5))
    assert verify.clean_streak(conn, "orders", TODAY) == 4                                    # the failed 6th breaks the run
    run_on(D(2026, 11, 10), att="failed")                                                     # the last attempt of today failed: today is not clean
    assert verify.clean_streak(conn, "orders", TODAY) == 3
    assert verify.clean_streak(conn, "orders", D(2026, 11, 11)) == 0                           # tomorrow: today's failed attempt is the latest


def test_run_verify_prints_the_streak_and_returns_exit_codes(conn):
    put(conn, "proj_feed_orders", [orders_row(D(2026, 11, 1))])
    put(conn, "proj_feedx_orders", [orders_row(D(2026, 11, 1))])
    common.set_mode(conn, "orders", "shadow"); conn.commit()
    out = []
    assert verify.run_verify(conn, [F], 30, 7, today=TODAY, out=out.append) == 0
    assert "consecutive clean days 0 (need 7)" in out[0] and "ok" in out[0]
    conn.execute("UPDATE proj_feedx_orders SET nc_rev = 1"); conn.commit()
    out.clear()
    assert verify.run_verify(conn, [F], 30, 7, today=TODAY, out=out.append) == 1 and "FAIL" in out[0]
    common.set_mode(conn, "orders", "direct"); conn.commit()
    out.clear()
    assert verify.run_verify(conn, [F], 30, 7, today=TODAY, out=out.append) == 0 and "mode is direct" in out[0]
    out.clear()
    assert verify.run_verify(conn, [feeds.registry()["intl_estimate"]], 30, 7, today=TODAY, out=out.append) == 0 and "native" in out[0]
