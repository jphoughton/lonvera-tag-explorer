"""The engine, seeded from the frozen sheet snapshot, reproduces the sheet's Demand Fcst tabs (scripts/projection/reconcile.py map), and the
DemandResult interface the inventory_supply module reads."""
from __future__ import annotations

from datetime import date

import pytest

from app.projection import engine
from etl import projection_inventory_demand as etl

try:
    from scripts.projection import reconcile as rc
    from scripts.projection import snapshot_read as sr
    sr.manifest()
    HAVE_SNAPSHOT = True
except Exception:                                                      # noqa: BLE001
    HAVE_SNAPSHOT = False

pytestmark = pytest.mark.skipif(not HAVE_SNAPSHOT, reason="sheet snapshot not available")


@pytest.fixture()
def seeded(conn, monkeypatch):
    monkeypatch.setenv("PROJECTION_TODAY", "2026-11-03")
    etl.seed(conn, etl.snapshot_reader(), amazon_source="daily-units")          # the source the sheet's own Amazon forecast reads
    return conn


def test_every_line_of_the_reconcile_map_ties_to_the_sheet(seeded):
    result = engine.run("current", seeded, strict=True)
    maps = {"inventory_demand": rc.load_map("inventory_demand")}
    reports = rc.reconcile(maps, rc.SnapshotSource(), result, 0.005, 1.0)
    bad = [(r.key, r.status, r.error, r.worst[:2]) for r in reports if r.status != "ok"]
    assert not bad, bad
    assert len(reports) >= 70 and all(r.cells >= 1 for r in reports)
    assert sum(r.cells for r in reports) > 1200                         # the map really compared cells (36 months x 31 rows plus the scalars)


def test_the_tie_is_exact_not_merely_inside_the_tolerance(seeded):
    result = engine.run("current", seeded, strict=True)
    reports = rc.reconcile({"inventory_demand": rc.load_map("inventory_demand")}, rc.SnapshotSource(), result, 0.0, 0.01)
    assert all(r.status == "ok" for r in reports), [(r.key, r.max_abs) for r in reports if r.status != "ok"]


def test_demand_result_is_what_inventory_supply_expects(seeded):
    d = engine.run("current", seeded, strict=True).demand
    nov, dec = date(2026, 11, 1), date(2026, 12, 1)
    assert isinstance(d.get("revamp", "bull"), dict) and d.get("revamp", "nope") == {}
    assert d.get("revamp", "bull")[nov] == pytest.approx(6996.0) and d.get("revamp", "bull")[dec] == pytest.approx(6966.142857, rel=1e-6)
    assert d.get("revamp_mtd", "bull") == {nov: 704.0}
    assert d.get("amazon_mtd", "bull") == {nov: 0.0}
    locs = {k[0] for k in d.units}
    assert locs == {"revamp", "amazon", "intl", "revamp_mtd", "amazon_mtd", "intl_mtd"}
    assert {k[1] for k in d.units if k[0] == "intl"} == {"bull", "liver", "boneflex", "flow"}
    assert max(d.get("revamp", "bull")) == date(2029, 12, 1)             # runs to the end of the 60-month axis
    assert all(m.day == 1 for k in d.units for m in d.units[k])


def test_actual_months_match_what_was_shipped(seeded, pg):
    d = engine.run("current", seeded, strict=True).demand
    u = pg.execute("SELECT sum(units) AS u FROM proj_units_daily WHERE location='revamp' AND product='liver' AND day >= '2026-10-01' AND day < '2026-11-01'").fetchone()["u"]
    assert d.get("revamp", "liver")[date(2026, 10, 1)] == pytest.approx(float(u))
    amz = d.get("amazon", "bull")
    assert amz[date(2025, 10, 1)] == 3595.0 and amz[date(2026, 7, 1)] == 1370.0      # monthly history, then daily sums


def test_with_amazon_read_from_product_daily_the_baseline_moves_to_the_latest_four_weeks(conn, monkeypatch):
    """Amazon - Product Daily is fresh through Nov 2 while Amazon Daily Units stops Oct 9: the Amazon baseline is the 30 days to Nov 2."""
    monkeypatch.setenv("PROJECTION_TODAY", "2026-11-03")
    etl.seed(conn, etl.snapshot_reader())
    d = engine.run("current", conn, strict=True).demand
    assert d.last_full_day["amazon"] == date(2026, 11, 2) and d.window["amazon"] == (date(2026, 10, 4), date(2026, 11, 2))
    expect = {}
    for prod, name in (("bull", "Vital Bull"), ("liver", "Beef Liver"), ("boneflex", "Boneflex")):
        total = 0.0
        for r in range(sr.load("Amazon - Product Daily")["tail_start_row"], sr.load("Amazon - Product Daily")["rows"] + 1):
            row = sr.row("Amazon - Product Daily", r, 1, 5)
            if row[1] == name and isinstance(row[0], (int, float)) and date(2026, 10, 4) <= sr.serial_to_date(row[0]) <= date(2026, 11, 2):
                total += float(row[4])
        expect[prod] = total
        assert d.window_units[("amazon", prod)] == pytest.approx(total) and d.rates[("amazon", prod)] == pytest.approx(total / 30)
    assert expect["bull"] / 30 == pytest.approx(31.4) and d.mtd[("amazon", "bull")] == 57.0           # Nov 1-2 actuals now count
    assert d.units[("amazon", "bull")][date(2026, 11, 1)] == pytest.approx(57 + 28 * 31.4)
