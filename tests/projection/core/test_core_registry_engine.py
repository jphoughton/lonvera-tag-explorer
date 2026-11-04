from __future__ import annotations

import importlib
from datetime import date

import pytest

from app.projection import engine, registry, rwdb, scenarios, series, stubs
from app.projection.interfaces import MODULE_CATALOG, MONTHS, CohortResult
from conftest import COHORTS_MODULE, DRIVERS_MODULE, PNL_MODULE, login
from core_helpers import P, create, item, put

TODAY = date(2026, 11, 3)


@pytest.fixture()
def conn(core_db):
    with rwdb.get_rw(core_db) as c:
        yield c


# ------------------------------------------------------------------------------------------------------------ registry

def test_registry_tolerates_missing_broken_and_malformed_modules(pkg_factory):
    pkg = pkg_factory({
        "drivers/__init__.py": DRIVERS_MODULE,
        "broken/__init__.py": "raise RuntimeError('boom at import')",
        "syntax/__init__.py": "def (:",
        "nomodule/__init__.py": "X = 1",
        "cohorts/__init__.py": "from app.projection.interfaces import Module\nMODULE = Module(key='wrongkey')",
        "pnl/__init__.py": "from app.projection.interfaces import Module\nMODULE = Module('pnl', lambda c: 1, lambda s, i: 2)",     # positional misuse
        "_private/__init__.py": "raise RuntimeError('never imported')",
        "plain.py": "x = 1",
    })
    found = registry.discover(pkg)
    assert set(found) == {"drivers", "broken", "syntax", "nomodule", "cohorts", "pnl"}
    assert found["drivers"].built and found["drivers"].module.key == "drivers"
    assert not found["broken"].built and "boom at import" in found["broken"].error
    assert not found["syntax"].built and found["syntax"].error
    assert "does not export MODULE" in found["nomodule"].error
    assert "does not match" in found["cohorts"].error
    assert "keyword" in found["pnl"].error
    listing = registry.listing(pkg)
    assert [m["key"] for m in listing] == list(MODULE_CATALOG)
    by = {m["key"]: m for m in listing}
    assert by["drivers"] == {"key": "drivers", "label": "Spend plan", "group": "P1", "built": True, "ui": "drivers.js"}
    assert by["cohorts"]["built"] is False and by["cohorts"]["ui"] is None and by["financing"]["built"] is False


def test_registry_with_missing_package_returns_nothing():
    assert registry.discover("no_such_package_xyz", refresh=True) == {}
    assert all(not m["built"] for m in registry.listing("no_such_package_xyz"))


def test_modules_endpoint_lists_the_catalog_with_labels_and_groups(client):
    r = client.get(f"{P}/modules")
    assert r.status_code == 200
    mods = r.get_json()["modules"]
    assert [(m["key"], m["label"], m["group"]) for m in mods] == [(k, label, group) for k, (label, group) in MODULE_CATALOG.items()]
    assert {"cohorts", "drivers", "pnl", "inventory_demand", "inventory_supply", "financing", "pacing"} <= {m["key"] for m in mods}
    assert all(set(m) == {"key", "label", "group", "built", "ui"} and isinstance(m["built"], bool) for m in mods)


def test_a_broken_module_does_not_break_app_creation(app_factory, pkg_factory):
    pkg_factory({"broken/__init__.py": "raise ImportError('nope')", "drivers/__init__.py": DRIVERS_MODULE})
    c = login(app_factory().test_client())
    mods = {m["key"]: m for m in c.get(f"{P}/modules").get_json()["modules"]}
    assert mods["drivers"]["built"] is True and mods["pnl"]["built"] is False


# ------------------------------------------------------------------------------------------------------------ engine on stubs

def test_engine_runs_end_to_end_on_stubs(conn, pkg_factory):
    pkg_factory({})
    res = engine.run("current", conn, today=TODAY)
    assert res.scenario.slug == "current" and set(res.results) == set(engine.ORDER)
    assert all(res.stubbed[k] for k in engine.ORDER if k != "pacing") and res.results["pacing"] is None and not res.errors
    assert all(not b for b in res.built.values()) and all(isinstance(v, float) for v in res.ms.values())
    d, c, p, f = res.drivers, res.cohorts, res.pnl, res.financing
    assert all(len(getattr(d, k)) == 60 for k in ("nc_rev", "spend_total", "amazon_rev", "sales_tax"))
    assert len(c.curve_live) == 60 and len(c.curve_goal) == 60 and set(p.lines) >= {"total_revenue", "noi", "nc_rev", "repeat_rev"}
    assert set(f.lines) == set(stubs.FINANCING_KEYS) and res.demand.get("revamp", "x") == {} and res.inventory.burn == {}
    assert set(p.fy) == {2025, 2026, 2027, 2028, 2029} and len(p.ttm) == 60 and len(p.ftm) == 60
    assert res.resolve("drivers.nc_rev") is d.nc_rev and res.resolve("pnl.lines.noi") is p.lines["noi"] and res.resolve("financing.lines.ending_cash")
    with pytest.raises(KeyError):
        res.resolve("drivers.nope")
    with pytest.raises(KeyError):
        res.resolve("nothing.at.all")


def test_stub_values_come_from_the_snapshot(conn, pkg_factory):
    sr = stubs.snapshot_module()
    try:
        sr.load("P&L")
    except Exception:
        pytest.skip("projection snapshot not available")
    pkg_factory({})
    res = engine.run("current", conn, today=TODAY)
    sheet_rev = sr.row("P&L", 22, "C", "BJ")
    sheet_noi = sr.row("P&L", 100, "C", "BJ")
    for i in (0, 5, 17, 30, 59):
        assert res.pnl.lines["total_revenue"][MONTHS[i]] == pytest.approx(sheet_rev[i] or 0.0)
        assert res.pnl.lines["noi"][MONTHS[i]] == pytest.approx(sheet_noi[i] or 0.0)
    assert res.drivers.nc_rev[MONTHS[3]] == pytest.approx(sr.cell("P&L", "F12"))
    assert res.cohorts.lifetime_multiple == pytest.approx(sr.cell("Curves", "C65"))
    assert res.cohorts.curve_live[0] == pytest.approx(sr.cell("Curves", "C4"))
    assert res.drivers.actual_through == date(2026, 6, 1)                       # snapshot's last 'Actual' column
    assert res.actual_through == date(2026, 6, 1)


def test_without_a_snapshot_an_unbuilt_module_has_no_result_not_zeros(conn, pkg_factory, monkeypatch, tmp_path):
    monkeypatch.setenv("PROJECTION_SNAPSHOT_DIR", str(tmp_path / "missing"))             # production: the snapshot files do not exist
    pkg_factory({})
    res = engine.run("current", conn, today=TODAY)
    assert all(v is None for v in res.results.values()) and not any(res.stubbed.values()) and not res.errors and res.degraded == []
    assert res.actual_through is None
    j = res.summary()
    assert all(m["built"] is False and m["stub"] is False and "error" not in m for m in j["modules"].values()) and j["degraded"] == [] and j["headline"] == {}


def test_actual_through_comes_from_loaded_actuals_when_present(conn, pkg_factory):
    pkg_factory({})
    series.upsert_actuals(conn, [("qb.revenue", date(2026, 8, 1), 1.0), ("qb.revenue", date(2026, 11, 1), 1.0)], "t")
    assert engine.run("current", conn, today=TODAY).actual_through == date(2026, 8, 1)              # Nov-26 is not closed yet
    assert engine.run("current", conn, today=date(2026, 12, 2)).actual_through == date(2026, 11, 1)


def test_engine_accepts_scenario_object_slug_and_default(conn, pkg_factory):
    pkg_factory({})
    scn = scenarios.get_scenario(conn, "current")
    assert engine.run(scn, conn, today=TODAY).scenario is scn
    assert engine.run(None, conn, today=TODAY).scenario.slug == "current"
    with pytest.raises(Exception):
        engine.run("nope", conn)


# ------------------------------------------------------------------------------------------------------------ engine with real (fake) modules

def test_real_modules_replace_stubs_and_receive_the_upstream_they_name(conn, pkg_factory):
    pkg = pkg_factory({"drivers/__init__.py": DRIVERS_MODULE, "cohorts/__init__.py": COHORTS_MODULE, "pnl/__init__.py": PNL_MODULE})
    res = engine.run("current", conn, today=TODAY)
    assert res.built["drivers"] and res.built["cohorts"] and res.built["pnl"] and not res.built["financing"]
    assert not res.stubbed.get("drivers") and not res.stubbed.get("cohorts") and not res.stubbed.get("pnl") and res.stubbed["financing"]
    assert res.drivers.nc_rev == {date(2026, 11, 1): 100.0, date(2026, 12, 1): 250.0}
    assert res.actual_through == date(2026, 10, 1)                                                       # the driver module's boundary
    cohorts_seen = importlib.import_module(f"{pkg}.cohorts").SEEN
    assert cohorts_seen["drivers"] is res.drivers and cohorts_seen["nc_rev"] == res.drivers.nc_rev      # cohorts.compute(nc_rev=drivers.nc_rev)
    pnl_seen = importlib.import_module(f"{pkg}.pnl").SEEN
    assert pnl_seen["drivers"] is res.drivers and pnl_seen["cohorts"] is res.cohorts and pnl_seen["inputs"] == "inputs-without-scenario"
    assert isinstance(res.cohorts, CohortResult) and res.cohorts.lifetime_multiple == 0.75 and res.pnl.lines == {"noi": {}}
    s = res.summary()
    assert s["modules"]["drivers"]["built"] is True and s["modules"]["drivers"]["stub"] is False and s["modules"]["financing"]["stub"] is True
    assert s["actual_through"] == "2026-10-01" and s["scenario"] == "current" and s["version"] == 1


def test_load_inputs_gets_the_scenario_when_it_can_take_it(conn, pkg_factory):
    pkg_factory({"drivers/__init__.py": DRIVERS_MODULE})
    seen = {}
    mod = registry.get_module("drivers")
    orig = mod.compute
    mod.compute = lambda scn, inputs: (seen.update(inputs=inputs), orig(scn, inputs))[1]
    engine.run("current", conn, today=TODAY)
    assert seen["inputs"] == {"slug": "current"}


FAILING_PNL = {"pnl/__init__.py": """
    from app.projection.interfaces import Module
    def compute(scn, inputs):
        raise ValueError("model bug")
    MODULE = Module(key="pnl", compute=compute)
"""}


def test_a_failing_module_is_degraded_with_no_result_and_no_stand_in(conn, pkg_factory):
    pkg_factory(FAILING_PNL)
    res = engine.run("current", conn, today=TODAY)
    assert res.pnl is None and res.results["pnl"] is None and not res.stubbed.get("pnl")                    # not the snapshot stub, not zeros
    assert res.built["pnl"] is True and res.errors["pnl"] == "ValueError: model bug"
    assert [d["module"] for d in res.degraded] == ["pnl", "financing", "pacing"]                              # and everything that reads the P&L
    assert res.errors["financing"] == "upstream degraded: pnl" and res.financing is None
    j = res.summary()
    assert j["degraded"] == res.degraded
    assert j["modules"]["pnl"] == {"built": True, "stub": False, "ms": j["modules"]["pnl"]["ms"], "error": "ValueError: model bug"}
    assert "fy" not in j["headline"] and "lowest_ending_cash" not in j["headline"]                            # nothing plausible is reported for the broken chain
    with pytest.raises(ValueError):
        engine.run("current", conn, strict=True, today=TODAY)


def test_a_degraded_module_in_production_leaves_nothing_that_looks_like_data(conn, pkg_factory, monkeypatch, tmp_path):
    monkeypatch.setenv("PROJECTION_SNAPSHOT_DIR", str(tmp_path / "missing"))
    pkg_factory({**FAILING_PNL, "drivers/__init__.py": DRIVERS_MODULE})
    res = engine.run("current", conn, today=TODAY)
    assert res.drivers is not None and res.pnl is None and res.financing is None and res.inventory is None and res.demand is None
    assert [d["module"] for d in res.degraded] == ["pnl", "financing", "pacing"] and res.summary()["headline"] == {}


def test_a_dependent_of_a_degraded_module_is_degraded_even_when_its_upstream_is_optional(conn, pkg_factory):
    pkg = pkg_factory({"drivers/__init__.py": """
        from app.projection.interfaces import Module
        def compute(scn, inputs):
            raise RuntimeError("no feed")
        MODULE = Module(key="drivers", compute=compute)
    """, "cohorts/__init__.py": """
        from app.projection.interfaces import CohortResult, Module
        RAN = []
        def compute(scn, inputs, drivers):
            RAN.append(1)
            return CohortResult(lifetime_multiple=drivers.nc_rev.get(1, 0.0))
        MODULE = Module(key="cohorts", compute=compute)
    """, "pnl/__init__.py": """
        from app.projection.interfaces import Module, PnlResult
        RAN = []
        def compute(scn, inputs, drivers=None, cohorts=None):
            RAN.append(1)
            return PnlResult(lines={"noi": {}})
        MODULE = Module(key="pnl", compute=compute)
    """})
    res = engine.run("current", conn, today=TODAY)
    assert importlib.import_module(f"{pkg}.cohorts").RAN == [] and importlib.import_module(f"{pkg}.pnl").RAN == []          # neither ran
    assert res.errors["drivers"] == "RuntimeError: no feed" and res.errors["cohorts"] == "upstream degraded: drivers"
    assert res.errors["pnl"] == "upstream degraded: drivers, cohorts" and res.pnl is None
    assert [d["module"] for d in res.degraded][:3] == ["drivers", "cohorts", "pnl"]
    with pytest.raises(RuntimeError, match="no feed"):
        engine.run("current", conn, strict=True, today=TODAY)


def test_a_module_missing_a_required_upstream_that_is_simply_not_built_is_degraded(conn, pkg_factory, monkeypatch, tmp_path):
    monkeypatch.setenv("PROJECTION_SNAPSHOT_DIR", str(tmp_path / "missing"))               # production: no stub for the unbuilt drivers
    pkg_factory({"cohorts/__init__.py": """
        from app.projection.interfaces import CohortResult, Module
        def compute(scn, inputs, drivers):
            return CohortResult()
        MODULE = Module(key="cohorts", compute=compute)
    """})
    res = engine.run("current", conn, today=TODAY)
    assert res.cohorts is None and res.errors == {"cohorts": "upstream unavailable: drivers", "pnl": "upstream degraded: cohorts",
                                                   "financing": "upstream degraded: pnl", "pacing": "upstream degraded: pnl, cohorts"}


def test_a_module_that_returns_nothing_is_degraded(conn, pkg_factory):
    pkg_factory({"drivers/__init__.py": "from app.projection.interfaces import Module\nMODULE = Module(key='drivers', compute=lambda scn, inputs: None)"})
    res = engine.run("current", conn, today=TODAY)
    assert res.drivers is None and res.errors["drivers"] == "the module returned no result"


def test_a_module_that_breaks_the_transaction_does_not_poison_the_modules_after_it(conn, pkg_factory):
    pkg_factory({"drivers/__init__.py": """
        from app.projection.interfaces import Module
        def load_inputs(conn, scn):
            conn.execute("SELECT * FROM table_that_does_not_exist")
        def compute(scn, inputs):
            return None
        MODULE = Module(key="drivers", load_inputs=load_inputs, compute=compute)
    """, "inventory_demand/__init__.py": """
        from app.projection.interfaces import DemandResult, Module
        def load_inputs(conn, scn):
            return conn.execute("SELECT count(*) AS n FROM proj_scenarios").fetchone()["n"]
        def compute(scn, inputs):
            return DemandResult(units={("revamp", "x"): {}, ("n", str(inputs)): {}})
        MODULE = Module(key="inventory_demand", load_inputs=load_inputs, compute=compute)
    """})
    res = engine.run("current", conn, today=TODAY)
    assert "UndefinedTable" in res.errors["drivers"] and res.drivers is None
    assert res.demand is not None and ("n", "1") in res.demand.units                                          # ran on a clean transaction after drivers broke it


def test_module_with_no_compute_is_built_but_stubbed(conn, pkg_factory):
    pkg_factory({"cohorts/__init__.py": "from app.projection.interfaces import Module\nMODULE = Module(key='cohorts', ui='cohorts.js')"})
    res = engine.run("current", conn, today=TODAY)
    assert res.built["cohorts"] and res.stubbed["cohorts"] and not res.errors                              # (needs the snapshot, like every stub)


def test_engine_uses_the_effective_params_of_the_scenario(client, conn, pkg_factory):
    pkg_factory({"cohorts/__init__.py": """
        from app.projection.interfaces import CohortResult, Module
        def compute(scn, inputs):
            return CohortResult(lifetime_multiple=scn.params["cohorts"]["tail"])
        MODULE = Module(key="cohorts", compute=compute)
    """})
    client.patch(f"{P}/scenarios/current", json={"params": {"cohorts": {"tail": 0.9}}})
    create(client, "Kid")
    client.patch(f"{P}/scenarios/kid", json={"params": {"cohorts": {"tail": 0.8}}})
    client.patch(f"{P}/scenarios/current", json={"params": {"cohorts": {"other": 1}}})
    assert engine.run("current", conn, today=TODAY).cohorts.lifetime_multiple == 0.9
    assert engine.run("kid", conn, today=TODAY).cohorts.lifetime_multiple == 0.8


# ------------------------------------------------------------------------------------------------------------ /run endpoint

def test_run_endpoint_summary_shape(client, pkg_factory):
    pkg_factory({"drivers/__init__.py": DRIVERS_MODULE})
    put(client, "current", item())
    r = client.get(f"{P}/run?scenario=current")
    assert r.status_code == 200
    j = r.get_json()
    assert set(j) == {"scenario", "version", "actual_through", "modules", "degraded", "headline"} and j["scenario"] == "current" and j["version"] == 2
    assert j["degraded"] == []
    assert list(j["modules"]) == list(engine.ORDER)
    assert j["modules"]["drivers"]["built"] is True and j["modules"]["pnl"]["built"] is False and isinstance(j["modules"]["pnl"]["ms"], float)
    assert set(j["headline"]["fy"]) == {"2025", "2026", "2027", "2028", "2029"} and set(j["headline"]["fy"]["2027"]) == {"revenue", "noi"}
    assert client.get(f"{P}/run").status_code == 200                      # default scenario
    assert client.get(f"{P}/run?scenario=nope").status_code == 404


def test_run_endpoint_never_writes(client, pg):
    q = ("SELECT (SELECT count(*) FROM proj_audit) a, (SELECT count(*) FROM proj_series) s, "
         "(SELECT version FROM proj_scenarios WHERE slug='current') v")
    before = pg.execute(q).fetchone()
    assert client.get(f"{P}/run").status_code == 200
    assert pg.execute(q).fetchone() == before
