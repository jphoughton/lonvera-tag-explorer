from __future__ import annotations

import json
import sys
from datetime import date

import pytest

from app.projection.engine import EngineResult
from app.projection.interfaces import CohortResult, DriverResult, PnlResult, Scenario
from conftest import DRIVERS_MODULE
from scripts.projection import reconcile as R

SCN = Scenario(1, "current", "Current plan", {})


def result(stubbed=None, **kw) -> EngineResult:
    return EngineResult(scenario=SCN, actual_through=None, results=kw, stubbed=stubbed or {})


def month(i: int) -> date:
    return date(2025 + i // 12, i % 12 + 1, 1)


# P&L row 12 holds 100, 200, 300, ... in columns C.. (Jan-25 onward); row 13 has a text cell, a blank and a formula error
PNL_GRID = [[None] * 3 for _ in range(11)] + [["NC-Rev", "", 100, 200, 300, 400], ["NC-Orders", "", 1, "n/a", "", "#REF!", 5],
                                               ["", "", 0.5, 0.25]]
SRC = R.DictSource({"P&L": PNL_GRID, "Curves": [["h"], ["h"], ["h"], ["Age", "Goal", "Live"], [1, 0.2, 0.19], [2, 0.3, 0.29], [3, 0.1, 0.09], ["", "", ""], ["Lifetime", 1.88, 1.58]]})


def entry(**kw):
    return {"key": "nc_rev", "tab": "P&L", "row": 12, "c0": "C", "c1": "F", "kind": "values", "result": "drivers.nc_rev", **kw}


def drivers(**series):
    return result(drivers=DriverResult(**series))


# ------------------------------------------------------------------------------------------------------------ cell comparison

def test_exact_match_passes():
    rep = R.reconcile_entry("drivers", entry(), SRC, drivers(nc_rev={month(0): 100.0, month(1): 200.0, month(2): 300.0, month(3): 400.0}), 0.005, 1.0)
    assert (rep.status, rep.cells, rep.over, rep.max_abs, rep.max_rel) == ("ok", 4, 0, 0.0, 0.0)


def test_relative_tolerance_and_absolute_tolerance():
    got = drivers(nc_rev={month(0): 100.4, month(1): 200.0, month(2): 300.0, month(3): 400.0})
    assert R.reconcile_entry("m", entry(), SRC, got, 0.005, 0.0).status == "ok"                  # 0.4% < 0.5%
    got = drivers(nc_rev={month(0): 100.6, month(1): 200.0, month(2): 300.0, month(3): 400.0})
    rep = R.reconcile_entry("m", entry(), SRC, got, 0.005, 0.0)
    assert rep.status == "FAIL" and rep.over == 1 and rep.worst[0]["at"] == "2025-01-01" and rep.max_abs == pytest.approx(0.6)
    assert R.reconcile_entry("m", entry(), SRC, got, 0.005, 1.0).status == "ok"                  # within $1 passes even though 0.6% > 0.5%
    far = drivers(nc_rev={month(0): 130.0, month(1): 200.0, month(2): 300.0, month(3): 400.0})
    rep = R.reconcile_entry("m", entry(), SRC, far, 0.005, 1.0)
    assert rep.over == 1 and rep.max_abs == pytest.approx(30.0) and rep.max_rel == pytest.approx(30 / 130)


def test_missing_engine_month_counts_as_zero_and_blank_sheet_cell_as_zero():
    rep = R.reconcile_entry("m", entry(), SRC, drivers(nc_rev={month(0): 100.0}), 0.005, 1.0)
    assert rep.status == "FAIL" and rep.over == 3 and rep.cells == 4
    rep = R.reconcile_entry("m", entry(row=14, c0="C", c1="D"), SRC, drivers(nc_rev={}), 0.005, 0.001)       # sheet 0.5, 0.25 vs engine 0
    assert rep.over == 2
    rep = R.reconcile_entry("m", entry(c0="G", c1="H"), SRC, drivers(nc_rev={}), 0.005, 1.0)                  # beyond the used range: blank = 0 = engine
    assert rep.status == "ok" and rep.cells == 2


def test_text_and_error_cells_are_skipped_not_compared():
    rep = R.reconcile_entry("m", entry(row=13, key="orders", result="drivers.nc_orders"), SRC, drivers(nc_orders={month(0): 1.0, month(1): 99.0, month(3): 99.0}), 0.005, 1.0)
    assert rep.skipped == 2 and rep.cells == 2 and rep.status == "ok"                                        # 'n/a' and '#REF!' skipped; blank = 0 vs 0


def test_overrides_and_scale():
    got = drivers(nc_rev={month(0): 101.0, month(1): 200.0, month(2): 300.0, month(3): 400.0})
    assert R.reconcile_entry("m", entry(tol=0.02), SRC, got, 0.001, 0.0).status == "ok"
    assert R.reconcile_entry("m", entry(**{"abs": 5}), SRC, got, 0.001, 0.0).status == "ok"
    scaled = drivers(nc_rev={month(0): 1.0, month(1): 2.0, month(2): 3.0, month(3): 4.0})
    assert R.reconcile_entry("m", entry(scale=0.01), SRC, scaled, 0.005, 0.0001).status == "ok"
    assert R.reconcile_entry("m", entry(month0="2026-01-01"), SRC, drivers(nc_rev={month(12): 100.0, month(13): 200.0, month(14): 300.0, month(15): 400.0}),
                             0.005, 1.0).status == "ok"                                                      # month0 shifts the axis


def test_column_and_scalar_kinds_and_list_results():
    col = {"key": "curve", "tab": "Curves", "kind": "column", "c0": "C", "r0": 5, "r1": 7, "result": "cohorts.curve_live"}
    ok = result(cohorts=CohortResult(curve_live=[0.19, 0.29, 0.09], lifetime_multiple=1.58))
    assert R.reconcile_entry("cohorts", col, SRC, ok, 0.005, 0.0).status == "ok"
    bad = result(cohorts=CohortResult(curve_live=[0.19, 0.30, 0.09]))
    rep = R.reconcile_entry("cohorts", col, SRC, bad, 0.005, 0.0)
    assert rep.over == 1 and rep.worst[0]["at"] == "row 6"
    scalar = {"key": "life", "tab": "Curves", "kind": "scalar", "row": 9, "c0": "C", "result": "cohorts.lifetime_multiple"}
    assert R.reconcile_entry("cohorts", scalar, SRC, ok, 0.005, 0.0).status == "ok"
    assert R.reconcile_entry("cohorts", scalar, SRC, result(cohorts=CohortResult(lifetime_multiple=1.9)), 0.005, 0.0).status == "FAIL"


def test_dict_result_paths_with_dotted_keys_and_tuple_keys():
    pnl = PnlResult(lines={"amazon.fees": {month(0): 100.0, month(1): 200.0, month(2): 300.0, month(3): 400.0}})
    assert R.reconcile_entry("pnl", entry(result="pnl.lines.amazon.fees"), SRC, result(pnl=pnl), 0.005, 1.0).status == "ok"
    from app.projection.interfaces import DemandResult
    dem = DemandResult(units={("revamp", "bull"): {month(0): 100.0, month(1): 200.0, month(2): 300.0, month(3): 400.0}})
    assert R.reconcile_entry("d", entry(result="demand.units.revamp|bull"), SRC, result(inventory_demand=dem), 0.005, 1.0).status == "ok"


def test_unresolvable_path_or_tab_is_an_error_not_a_crash():
    rep = R.reconcile_entry("m", entry(result="drivers.no_such_field"), SRC, drivers(), 0.005, 1.0)
    assert rep.status == "ERROR" and "no_such_field" in rep.error
    rep = R.reconcile_entry("m", entry(tab="Nope"), SRC, drivers(), 0.005, 1.0)
    assert rep.status == "ERROR" and "Nope" in rep.error
    rep = R.reconcile_entry("m", entry(kind="weird"), SRC, drivers(), 0.005, 1.0)
    assert rep.status == "ERROR"


def test_a_stubbed_module_is_flagged_not_passed():
    got = result(stubbed={"drivers": True}, drivers=DriverResult(nc_rev={month(0): 100.0, month(1): 200.0, month(2): 300.0, month(3): 400.0}))
    reps = R.reconcile({"drivers": [entry()]}, SRC, got)
    assert [r.status for r in reps] == ["stub"]
    got.stubbed["demand"] = True        # an alias is mapped back to its module key; unrelated stubs do not matter
    assert R.reconcile({"drivers": [entry()]}, SRC, result(drivers=got.drivers))[0].status == "ok"


# ------------------------------------------------------------------------------------------------------------ output

def test_table_and_json_rendering():
    ok = result(drivers=DriverResult(nc_rev={month(0): 100.0, month(1): 200.0, month(2): 300.0, month(3): 380.0}))
    reps = R.reconcile({"drivers": [entry(), entry(key="orders", row=13, result="drivers.nc_orders")]}, SRC, ok)
    text = R.render_table(reps, ["pnl"])
    assert "max abs diff" in text and "nc_rev" in text and "FAIL" in text and "no reconcile_map: pnl" in text and "2025-04-01" in text
    assert json.loads(json.dumps([r.to_dict() for r in reps], default=str))[0]["worst"][0]["at"] == "2025-04-01"


def test_load_map_reads_a_module_package(pkg_factory):
    pkg = pkg_factory({"drivers/__init__.py": DRIVERS_MODULE, "drivers/reconcile_map.py": "MAP = [{'key': 'a', 'tab': 'P&L', 'row': 1, 'c0': 'C', 'c1': 'D', 'result': 'drivers.nc_rev'}]",
                       "pnl/__init__.py": ""})
    assert R.load_map("drivers", pkg)[0]["key"] == "a"
    assert R.load_map("pnl", pkg) is None                   # package without a map
    assert R.load_map("financing", pkg) is None             # no package at all


# ------------------------------------------------------------------------------------------------------------ CLI

@pytest.fixture()
def cli(core_db, pkg_factory, monkeypatch):
    pkg_factory({"drivers/__init__.py": DRIVERS_MODULE})        # real drivers: nc_rev = {2026-11: 100, 2026-12: 250}
    monkeypatch.setenv("DATABASE_URL", core_db)
    grid = [[None] * 3 for _ in range(11)] + [["NC-Rev"] + [""] * 21 + [100, 250]]       # row 12, columns W (Nov-26) and X (Dec-26)
    state = {"grid": grid}
    monkeypatch.setattr(R, "SnapshotSource", lambda: R.DictSource({"P&L": state["grid"]}))
    maps = {"drivers": [{"key": "nc_rev", "tab": "P&L", "row": 12, "c0": "W", "c1": "X", "month0": "2026-11-01", "result": "drivers.nc_rev"}]}
    monkeypatch.setattr(R, "load_map", lambda m, package="app.projection": maps.get(m))
    return state


def test_cli_pass_fail_json_and_exit_codes(cli, capsys):
    assert R.main(["--module", "drivers"]) == 0
    out = capsys.readouterr().out
    assert "nc_rev" in out and "ok" in out and "1 lines: 1 ok" in out
    cli["grid"][11][23] = 300                                           # Dec-26 now 300 vs engine 250
    assert R.main(["--module", "drivers", "--format", "json"]) == 1
    j = json.loads(capsys.readouterr().out)
    assert j["lines"][0]["status"] == "FAIL" and j["lines"][0]["over"] == 1 and j["source"] == "snapshot" and j["tolerance"] == 0.005
    assert R.main(["--module", "drivers", "--tolerance", "0.5"]) == 0                    # loosened
    assert R.main(["--module", "drivers", "--abs", "100"]) == 0
    assert R.main(["--module", "all"]) == 1                                              # other modules have no map: listed, not failed; drivers still fails
    assert "no reconcile_map" in capsys.readouterr().out
    assert R.main(["--module", "pnl"]) == 2                                              # explicit module without a map
    assert R.main(["--module", "bogus"]) == 2


def test_cli_requires_a_built_module_and_a_database(cli, core_db, pkg_factory, monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL")
    assert R.main(["--module", "drivers"]) == 2
    monkeypatch.setenv("DATABASE_URL", core_db)
    pkg_factory({})                                                                      # drivers not built -> engine uses the stub
    assert R.main(["--module", "drivers"]) == 2
    assert "not built" in capsys.readouterr().err


def test_cli_script_runs_directly_with_help():
    import subprocess
    from conftest import ROOT
    p = subprocess.run([sys.executable, str(ROOT / "scripts" / "projection" / "reconcile.py"), "--help"], capture_output=True, text=True, cwd="/tmp")
    assert p.returncode == 0 and "--module" in p.stdout and "--tolerance" in p.stdout


def test_snapshot_source_reads_the_real_snapshot():
    try:
        src = R.SnapshotSource()
        row = src.row("P&L", 8, "C", "E")
    except Exception:
        pytest.skip("projection snapshot not available")
    assert row == [45658, 45689, 45717]                      # Jan/Feb/Mar-2025 serials
    assert R.col_idx("BJ") == 62 and R.col_letters(62) == "BJ"
