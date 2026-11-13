"""etl.inventory_snapshots: the estimate math (pure), the never-overwrite rules and the database round trip. No network: the job reads three app tables only."""
import datetime as dt
from pathlib import Path

import pytest

from etl import inventory_snapshots as S

D = dt.date
ROOT = Path(__file__).resolve().parents[2]
TODAY = D(2026, 11, 12)


def run_compute(counts=None, units=None, latest=None, receipts=None, existing=None, today=TODAY):
    return S.compute(counts or {}, units or {}, latest if latest is not None else {}, receipts or {}, existing or {}, today)


def by_key(rows):
    return {(r[0], r[1], r[2]): r for r in rows}


# ---------------------------------------------------------------------------------------------------------------- estimate math
def test_estimate_is_count_minus_units_shipped_since():
    counts = {("intl", "bull"): [(D(2026, 11, 4), 1000.0, 7.0)]}
    units = {("intl", "bull"): {D(2026, 11, 4): 100.0, D(2026, 11, 5): 50.0, D(2026, 11, 6): 10.0}}
    rows, st = run_compute(counts, units, {"intl": D(2026, 11, 11)})
    r = by_key(rows)
    assert r[("intl", "bull", D(2026, 11, 4))][3:] == (1000.0, 7.0, "typed_count")          # the count day carries the count itself
    assert r[("intl", "bull", D(2026, 11, 5))][3:] == (900.0, 0.0, "estimate_from_count")    # day 4 shipped (start-of-day snapshot), allocated unknown -> 0
    assert r[("intl", "bull", D(2026, 11, 6))][3] == 850.0
    assert r[("intl", "bull", D(2026, 11, 7))][3] == 840.0
    assert r[("intl", "bull", D(2026, 11, 8))][3] == 840.0                                   # days with no unit row (feed loaded, nothing shipped) = 0
    assert st["typed"] == 1 and st["estimated"] == 8


def test_nothing_before_the_floor_and_no_row_without_a_count():
    counts = {("amazon", "liver"): [(D(2026, 10, 13), 500.0, 0.0)]}
    rows, _ = run_compute(counts, {("amazon", "liver"): {}}, {"amazon": D(2026, 11, 11)})
    days = sorted(r[2] for r in rows)
    assert days[0] == S.FLOOR == D(2026, 11, 3) and days[-1] == TODAY
    # the count date (Oct 13) is before the floor: no typed row, only estimates
    assert all(r[5] == "estimate_from_count" for r in rows)
    assert run_compute({}, {}, {"amazon": D(2026, 11, 11)})[0] == []


def test_units_before_the_floor_still_reduce_a_count_that_predates_it():
    counts = {("amazon", "liver"): [(D(2026, 10, 30), 500.0, 0.0)]}
    units = {("amazon", "liver"): {D(2026, 10, 30): 10.0, D(2026, 10, 31): 10.0, D(2026, 11, 1): 10.0, D(2026, 11, 2): 10.0}}
    rows, _ = run_compute(counts, units, {"amazon": D(2026, 11, 11)})
    assert by_key(rows)[("amazon", "liver", D(2026, 11, 3))][3] == 460.0


def test_floor_at_zero():
    counts = {("intl", "flow"): [(D(2026, 11, 3), 20.0, 0.0)]}
    units = {("intl", "flow"): {D(2026, 11, 3): 500.0}}
    rows, _ = run_compute(counts, units, {"intl": D(2026, 11, 11)})
    assert by_key(rows)[("intl", "flow", D(2026, 11, 4))][3] == 0.0


def test_receipts_after_the_count_add_and_earlier_ones_do_not():
    counts = {("intl", "bull"): [(D(2026, 11, 4), 100.0, 0.0)]}
    receipts = {("intl", "bull"): [(D(2026, 11, 4), 999.0), (D(2026, 11, 6), 300.0), (D(2026, 11, 12), 7.0)]}
    rows, _ = run_compute(counts, {("intl", "bull"): {}}, {"intl": D(2026, 11, 11)}, receipts)
    r = by_key(rows)
    assert r[("intl", "bull", D(2026, 11, 6))][3] == 100.0          # a receipt dated the 6th is in by the start of the 7th
    assert r[("intl", "bull", D(2026, 11, 7))][3] == 400.0
    assert r[("intl", "bull", D(2026, 11, 12))][3] == 400.0         # the receipt dated the 12th is not in at the start of the 12th
    assert r[("intl", "bull", D(2026, 11, 5))][3] == 100.0          # the receipt dated on the count day is already in the count


def test_days_the_units_feed_has_not_loaded_use_the_mean_of_the_last_seven_loaded_days():
    counts = {("amazon", "bull"): [(D(2026, 11, 3), 1000.0, 0.0)]}
    units = {("amazon", "bull"): {D(2026, 11, 3 + i): 10.0 * (i + 1) for i in range(7)}}       # Nov 3..9: 10..70, mean 40; feed loaded through Nov 9
    rows, st = run_compute(counts, units, {"amazon": D(2026, 11, 9)}, today=D(2026, 11, 12))
    r = by_key(rows)
    assert r[("amazon", "bull", D(2026, 11, 10))][3] == 1000.0 - 280.0       # Nov 3..9 loaded
    assert r[("amazon", "bull", D(2026, 11, 11))][3] == 1000.0 - 280.0 - 40.0   # Nov 10 not loaded -> mean 40
    assert r[("amazon", "bull", D(2026, 11, 12))][3] == 1000.0 - 280.0 - 80.0
    assert st["filled_unit_days"] == 3


def test_a_newer_typed_count_restarts_the_estimate():
    counts = {("intl", "bull"): [(D(2026, 11, 4), 1000.0, 0.0), (D(2026, 11, 8), 5000.0, 12.0)]}
    units = {("intl", "bull"): {D(2026, 11, 4): 100.0, D(2026, 11, 5): 100.0, D(2026, 11, 8): 40.0}}
    rows, _ = run_compute(counts, units, {"intl": D(2026, 11, 11)})
    r = by_key(rows)
    assert r[("intl", "bull", D(2026, 11, 7))][3] == 800.0            # still from the first count
    assert r[("intl", "bull", D(2026, 11, 8))][3:] == (5000.0, 12.0, "typed_count")
    assert r[("intl", "bull", D(2026, 11, 9))][3] == 4960.0


def test_no_units_feed_for_a_location_skips_it_and_says_so():
    counts = {("intl", "bull"): [(D(2026, 11, 4), 10.0, 0.0)]}
    rows, st = run_compute(counts, {}, {})
    assert rows == [] and st["skipped_no_units"] == ["intl"]


# ---------------------------------------------------------------------------------------------------------------- never overwrite
def test_real_rows_are_never_recomputed_or_replaced():
    counts = {("intl", "bull"): [(D(2026, 11, 4), 1000.0, 0.0)]}
    existing = {("intl", "bull", D(2026, 11, 6)): "warehance", ("intl", "bull", D(2026, 11, 7)): "typed_count"}
    rows, st = run_compute(counts, {("intl", "bull"): {}}, {"intl": D(2026, 11, 11)}, existing=existing)
    days = {r[2] for r in rows}
    assert D(2026, 11, 6) not in days and D(2026, 11, 7) not in days and D(2026, 11, 5) in days
    assert st["kept_real"] == 2


def test_typed_count_day_replaces_an_estimate_but_not_a_real_row():
    counts = {("intl", "bull"): [(D(2026, 11, 6), 700.0, 3.0)]}
    rows, _ = run_compute(counts, {("intl", "bull"): {}}, {"intl": D(2026, 11, 11)}, existing={("intl", "bull", D(2026, 11, 6)): "estimate_from_count"})
    assert by_key(rows)[("intl", "bull", D(2026, 11, 6))][5] == "typed_count"
    rows, _ = run_compute(counts, {("intl", "bull"): {}}, {"intl": D(2026, 11, 11)}, existing={("intl", "bull", D(2026, 11, 6)): "warehance"})
    assert D(2026, 11, 6) not in {r[2] for r in rows}


def test_stored_estimates_older_than_two_weeks_are_kept_and_recent_ones_refreshed():
    counts = {("intl", "bull"): [(D(2026, 11, 3), 1000.0, 0.0)]}
    today = D(2026, 11, 30)
    existing = {("intl", "bull", D(2026, 11, 10)): "estimate_from_count", ("intl", "bull", D(2026, 11, 25)): "estimate_from_count"}
    rows, st = run_compute(counts, {("intl", "bull"): {}}, {"intl": D(2026, 11, 29)}, existing=existing, today=today)
    days = {r[2] for r in rows}
    assert D(2026, 11, 10) not in days and D(2026, 11, 25) in days and D(2026, 11, 11) in days     # a missing old day is still appended
    assert st["kept_old_estimate"] == 1


# ---------------------------------------------------------------------------------------------------------------- database round trip
@pytest.fixture
def db(conn):
    for t in ("proj_demand_snapshot", "proj_stock_snapshot", "proj_units_daily", "proj_fba_transfer", "proj_po"):
        conn.execute(f"TRUNCATE {t} CASCADE")
    conn.commit()
    return conn


def seed(conn):
    conn.execute("INSERT INTO proj_stock_snapshot (location, product, as_of, on_hand, allocated) VALUES ('intl','bull','2026-11-04',1000,5), ('amazon','bull','2026-10-13',400,0), "
                 "('intl','liver','2026-11-04',50,0)")
    conn.execute("INSERT INTO proj_stock_snapshot (location, product, as_of, on_hand, estimate) VALUES ('intl','boneflex','2026-11-04',9999,true)")      # an estimated count is not a count
    conn.execute("INSERT INTO proj_units_daily (location, product, day, units, source) SELECT 'intl', 'bull', d::date, 10, 'estimate' FROM generate_series('2026-11-04'::date, '2026-11-11', '1 day') d")
    conn.execute("INSERT INTO proj_units_daily (location, product, day, units, source) SELECT 'amazon', 'bull', d::date, 2, 'expandfi' FROM generate_series('2026-10-14'::date, '2026-11-10', '1 day') d")
    conn.execute("INSERT INTO proj_units_daily (location, product, day, units, source) SELECT 'intl', 'liver', d::date, 1, 'estimate' FROM generate_series('2026-11-04'::date, '2026-11-11', '1 day') d")
    conn.execute("INSERT INTO proj_demand_snapshot (location, product, as_of, on_hand, allocated, source) VALUES ('revamp','bull','2026-11-03',111,0,'warehance')")
    conn.execute("INSERT INTO proj_po (product, vendor, warehouse, units, land_date, status) VALUES ('bull','china','intl',300,'2026-11-06','landed'), ('bull','china','intl',9000,'2026-11-06','planned')")
    conn.execute("INSERT INTO proj_fba_transfer (product, units, ship_date, receive_date, status) VALUES ('bull', 50, '2026-10-20', '2026-10-25', 'received'), ('bull', 70, '2026-11-01', '2026-11-05', 'shipped')")
    conn.commit()


def snap(conn, loc, prod):
    return {r[0]: (float(r[1]), float(r[2]), r[3]) for r in conn.execute("SELECT as_of, on_hand, allocated, source FROM proj_demand_snapshot WHERE location=%s AND product=%s", (loc, prod))}


def test_run_writes_estimates_and_is_idempotent(db):
    seed(db)
    st = S.run(db, TODAY)
    i = snap(db, "intl", "bull")
    assert i[D(2026, 11, 4)] == (1000.0, 5.0, "typed_count")
    assert i[D(2026, 11, 5)] == (990.0, 0.0, "estimate_from_count")
    assert i[D(2026, 11, 7)] == (1000.0 - 30 + 300, 0.0, "estimate_from_count")      # landed PO of 300 (the planned one never counts)
    assert D(2026, 11, 3) not in i and max(i) == TODAY
    a = snap(db, "amazon", "bull")
    # count Oct 13 = 400; Oct 14..Nov 2 = 20 days x 2 = 40 shipped; 50 received Oct 25; the 'shipped' transfer does not count
    assert a[D(2026, 11, 3)] == (400.0 - 2 * 20 + 50, 0.0, "estimate_from_count")
    assert snap(db, "intl", "boneflex") == {}                                                                              # an estimated count is not a count
    assert snap(db, "revamp", "bull") == {D(2026, 11, 3): (111.0, 0.0, "warehance")}                                       # revamp untouched
    before = db.execute("SELECT count(*), max(loaded_at) FROM proj_demand_snapshot").fetchone()
    S.run(db, TODAY)
    assert snap(db, "intl", "bull") == i
    assert db.execute("SELECT count(*) FROM proj_demand_snapshot").fetchone()[0] == before[0]
    assert st["rows"] > 0


def test_real_row_in_the_table_survives_a_run_and_a_new_count_restarts(db):
    seed(db)
    db.execute("INSERT INTO proj_demand_snapshot (location, product, as_of, on_hand, allocated, source) VALUES ('intl','bull','2026-11-06',1,1,'warehance')")
    db.commit()
    S.run(db, TODAY)
    assert snap(db, "intl", "bull")[D(2026, 11, 6)] == (1.0, 1.0, "warehance")
    db.execute("INSERT INTO proj_stock_snapshot (location, product, as_of, on_hand, allocated) VALUES ('intl','bull','2026-11-09',2000,0)")
    db.commit()
    S.run(db, TODAY)
    i = snap(db, "intl", "bull")
    assert i[D(2026, 11, 9)] == (2000.0, 0.0, "typed_count")
    assert i[D(2026, 11, 10)] == (1990.0, 0.0, "estimate_from_count")          # restarted from the new count (day 9 shipped 10)
    assert i[D(2026, 11, 8)] == (1000.0 - 40 + 300, 0.0, "estimate_from_count")             # days before the new count keep the first count's path


def test_dry_run_writes_nothing(db):
    seed(db)
    n = db.execute("SELECT count(*) FROM proj_demand_snapshot").fetchone()[0]
    st = S.run(db, TODAY, dry_run=True)
    assert st["rows"] > 0 and db.execute("SELECT count(*) FROM proj_demand_snapshot").fetchone()[0] == n


def test_run_etl_sh_has_nonfatal_snapshot_step():
    s = (ROOT / "scripts" / "deploy" / "run_etl.sh").read_text()
    line = next(x for x in s.splitlines() if "python -m etl.inventory_snapshots" in x and "||" in x)
    assert "|| echo" in line and "non-fatal" in line
    assert s.index("python -m etl.amazon_mtd ||") < s.index("python -m etl.inventory_snapshots ||") < s.index("python -m etl.auto_tag ||")
