"""Seed from the sheet snapshot, then reconcile the engine against the sheet's P&L rows (drivers + the stub cohorts' repeat revenue)."""
from __future__ import annotations

from datetime import date

import pytest
from conftest import needs_snapshot

from app.projection import engine
from app.projection.drivers.reconcile_map import MAP
from app.projection.drivers.revenue import revenue_lines
from etl import projection_drivers as seeder
from scripts.projection import reconcile as rc

pytestmark = needs_snapshot
TODAY = date(2026, 11, 3)

# Every cell where the engine and the sheet differ by more than 0.5% (and $1 / the per-line absolute floor), with the cause (docs/projection/MODULE_drivers.md).
EXPECTED_DIFFS = {
    # Sep/Oct 2026: the sheet derives new-customer orders as spend / typed CPA; the cohort table holds the real counts for both closed months.
    "nc_orders": {"2026-09-01", "2026-10-01"},
    "nc_cpa": {"2026-09-01", "2026-10-01"},
    "nc_aov": {"2026-09-01", "2026-10-01"},
    "nc_roas": {"2026-09-01", "2026-10-01"},
    # Aug/Sep 2026: the sheet leaves the channel split blank (it types the total only); the engine splits the total by the channel-mix lever.
    "spend_amazon_ads": {"2026-08-01", "2026-09-01"},
    "spend_meta": {"2026-08-01", "2026-09-01"},
    "spend_google": {"2026-08-01", "2026-09-01"},
    "spend_tiktok": {"2026-08-01"},
}


@pytest.fixture()
def seeded(rw):
    out = seeder.seed(rw, with_nc=True)
    rw.commit()
    return out


def test_seed_counts_and_is_idempotent(rw, seeded):
    assert seeded["actuals"] > 150 and seeded["cells"] > 150 and seeded["levers"] and not seeded["skipped_plan"]
    audits = rw.execute("SELECT action FROM proj_audit WHERE actor = 'seed:drivers' ORDER BY id").fetchall()
    assert [a["action"] for a in audits] == ["actuals.load", "drivers.seed"]
    again = seeder.seed(rw, with_nc=True)
    assert again == {"actuals": 0, "cells": 0, "levers": False, "skipped_plan": True}
    assert rw.execute("SELECT count(*) AS n FROM proj_audit WHERE actor = 'seed:drivers'").fetchone()["n"] == 2
    force = seeder.seed(rw, force=True, with_nc=True)
    assert force["cells"] == 0 and force["levers"] is False and force["actuals"] > 0          # unchanged cells and levers are not rewritten


def test_the_seeded_plan_is_version_one_so_history_cannot_restore_to_an_empty_plan(rw, seeded):
    row = rw.execute("SELECT id, version FROM proj_scenarios WHERE slug = 'current'").fetchone()
    assert row["version"] == 1
    snap = rw.execute("SELECT snapshot FROM proj_scenario_history WHERE scenario_id = %s AND version = 1", (row["id"],)).fetchone()["snapshot"]
    assert sum(1 for c in snap["series"] if c[0].startswith("drivers.")) == seeded["cells"] and snap["params"]["drivers"]["sales_tax_rate"] == 0.07


def test_seeding_a_scenario_that_already_has_history_makes_the_seed_its_own_version(rw):
    from app.projection import scenarios
    scenarios.update(rw, "current", params={"cohorts": {"n_cohorts": 6}}, actor="test")           # some earlier change: version 2 exists
    rw.commit()
    out = seeder.seed(rw)
    rw.commit()
    row = rw.execute("SELECT version, params FROM proj_scenarios WHERE slug = 'current'").fetchone()
    assert out["cells"] > 150 and row["version"] == 3 and row["params"]["cohorts"] == {"n_cohorts": 6} and "drivers" in row["params"]
    assert rw.execute("SELECT action FROM proj_scenario_history WHERE version = 3").fetchone()["action"] == "seed"


def test_seeded_plan_content(rw, seeded):
    cells = {(r["line_key"], r["month"]): (float(r["value"]), r["kind"]) for r in rw.execute(
        "SELECT line_key, month, value, kind FROM proj_series s JOIN proj_scenarios p ON p.id = s.scenario_id WHERE p.slug = 'current'").fetchall()}
    assert cells[("drivers.spend_total", date(2026, 10, 1))] == (350000.0, "input")
    assert cells[("drivers.spend_total", date(2026, 8, 1))] == (847183.0, "input")
    assert cells[("drivers.nc_cpa", date(2026, 9, 1))] == (95.0, "input") and cells[("drivers.nc_aov", date(2026, 9, 1))] == (72.0, "input")
    assert ("drivers.nc_aov", date(2026, 10, 1)) not in cells                       # a formula in the sheet (= September): carried, not typed
    assert cells[("drivers.nc_rev", date(2026, 10, 1))] == (240354.88, "override")
    assert cells[("drivers.repeat_rev", date(2026, 8, 1))][1] == "override"
    assert cells[("drivers.tiktok_rev", date(2027, 6, 1))] == (0.0, "input")
    assert cells[("drivers.ad_spend", date(2026, 7, 1))] == (794965.0, "override")
    params = rw.execute("SELECT params FROM proj_scenarios WHERE slug = 'current'").fetchone()["params"]["drivers"]
    assert params["channel_share"]["meta"] == pytest.approx(0.8971495015118965) and params["sales_tax_rate"] == 0.07


def run_engine(rw, drivers_only):
    res = engine.run("current", rw, strict=True, package=drivers_only, today=TODAY)
    rl = revenue_lines(res.drivers, res.cohorts.repeat_rev)                      # what the pnl module does with the cohorts' repeat revenue
    res.pnl.lines["dtc_rev"], res.pnl.lines["sales_tax"], res.pnl.lines["total_revenue"] = rl.dtc_rev, rl.sales_tax, rl.total_revenue
    res.stubbed["pnl"] = False
    return res


def test_engine_reproduces_the_sheet_and_every_difference_is_known(rw, seeded, drivers_only):
    res = run_engine(rw, drivers_only)
    assert res.actual_through == date(2026, 7, 1) and res.built["drivers"] and not res.stubbed.get("drivers")
    src = rc.SnapshotSource()
    got = {}
    for entry in MAP:
        rep = rc.reconcile_entry("drivers", entry, src, res, 0.005, 1.0)
        assert rep.status in ("ok", "FAIL"), (entry["key"], rep.error)
        assert rep.cells == 60 and rep.skipped == 0, entry["key"]
        got[entry["key"]] = {w["at"] for w in rep.worst} if rep.over else set()
        assert rep.over == len(got[entry["key"]]), (entry["key"], rep.over)
    assert {k: v for k, v in got.items() if v} == EXPECTED_DIFFS


def test_rows_without_a_known_difference_tie_to_the_cent(rw, seeded, drivers_only):
    res = run_engine(rw, drivers_only)
    src = rc.SnapshotSource()
    for entry in MAP:
        if entry["key"] in EXPECTED_DIFFS:
            continue
        rep = rc.reconcile_entry("drivers", entry, src, res, 1e-9, 0.01)
        assert rep.over == 0, (entry["key"], rep.worst[:2])


def test_the_sheets_typed_overrides_are_what_holds_the_august_to_october_numbers(rw, seeded, drivers_only):
    res = run_engine(rw, drivers_only)
    d = res.drivers
    assert d.nc_rev[date(2026, 8, 1)] == 515139.33 and d.nc_orders[date(2026, 8, 1)] == 8375 and d.ad_spend[date(2026, 8, 1)] == 847183.0
    assert d.nc_rev_cohort[date(2026, 8, 1)] == pytest.approx(8417 * 63.4650065343947, rel=1e-6)                 # what the cohort table would say
    assert d.repeat_override[date(2026, 8, 1)] == pytest.approx(984286.67, abs=0.01)
