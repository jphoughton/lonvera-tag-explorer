"""Degraded must be loud: dependents of a failed module answer 503 upstream_degraded, never 200 with zeros."""
from __future__ import annotations

import pytest

from app.projection import degraded
from conftest import login
from core_helpers import P

MODULES = {
    "pnl/__init__.py": """
        from app.projection.interfaces import Module
        def compute(scn, inputs):
            raise ValueError("model bug")
        MODULE = Module(key="pnl", compute=compute)
    """,
    "financing/__init__.py": """
        from flask import Blueprint, jsonify
        from app import db
        from app.projection import engine
        from app.projection.interfaces import FinancingResult, Module, zero_series
        bp = Blueprint("fake_financing", __name__)

        def compute(scn, inputs, pnl=None):
            return FinancingResult(lines={"ending_cash": zero_series()})

        @bp.get("/table")
        def table():
            res = engine.run(None, db.get_conn())
            return jsonify(rows=[{"key": "ending_cash", "values": [0.0] * 60}], pnl_is_none=res.pnl is None)        # a page that never checks

        @bp.post("/save")
        def save():
            engine.run(None, db.get_conn())
            return jsonify(saved=True)

        MODULE = Module(key="financing", compute=compute, blueprint=bp)
    """,
    "inventory_demand/__init__.py": """
        from flask import Blueprint, jsonify
        from app import db
        from app.projection import engine
        from app.projection.interfaces import DemandResult, Module
        bp = Blueprint("fake_demand", __name__)

        def compute(scn, inputs):
            return DemandResult(units={("revamp", "bull"): {}})

        @bp.get("/table")
        def table():
            engine.run(None, db.get_conn())
            return jsonify(ok=True)

        MODULE = Module(key="inventory_demand", compute=compute, blueprint=bp)
    """,
    "pacing/__init__.py": """
        from flask import Blueprint, jsonify
        from app import db
        from app.projection import degraded, engine
        from app.projection.interfaces import Module
        bp = Blueprint("fake_pacing", __name__)

        @bp.get("/month")
        def month():
            res = engine.run(None, db.get_conn())
            degraded.ensure_ok(res, "pacing")                                                                          # the explicit helper
            return jsonify(ok=True)

        @bp.get("/own500")
        def own500():
            engine.run(None, db.get_conn())
            return jsonify(error="pacing exploded"), 500

        MODULE = Module(key="pacing", blueprint=bp)
    """,
}


@pytest.fixture()
def c(app_factory, pkg_factory):
    pkg_factory(MODULES)
    return login(app_factory().test_client())


def test_dependents_of_a_degraded_module_answer_503_with_the_modules_and_errors(c):
    r = c.get(f"{P}/financing/table")
    assert r.status_code == 503
    j = r.get_json()
    assert j["error"] == "upstream_degraded"
    assert j["modules"][0] == {"module": "pnl", "error": "ValueError: model bug"}
    assert {"module": "financing", "error": "upstream degraded: pnl"} in j["modules"]
    assert "rows" not in j and "pnl_is_none" not in j                                  # nothing the page built reaches the client


def test_the_explicit_helper_and_a_modules_own_500_are_both_503(c):
    r = c.get(f"{P}/pacing/month")
    assert r.status_code == 503 and r.get_json()["error"] == "upstream_degraded" and [m["module"] for m in r.get_json()["modules"]][0] == "pnl"
    r = c.get(f"{P}/pacing/own500")
    assert r.status_code == 503 and r.get_json()["error"] == "upstream_degraded"


def test_an_independent_module_and_the_core_endpoints_are_unaffected(c):
    assert c.get(f"{P}/inventory-demand/table").get_json() == {"ok": True}
    j = c.get(f"{P}/run").get_json()
    assert [d["module"] for d in j["degraded"]] == ["pnl", "financing", "pacing"] and "fy" not in j["headline"] and "lowest_ending_cash" not in j["headline"]
    assert c.get(f"{P}/scenarios").status_code == 200 and c.get(f"{P}/modules").status_code == 200 and c.get(f"{P}/freshness").status_code == 200


def test_writes_are_not_rewritten_after_they_committed(c):
    assert c.post(f"{P}/financing/save", json={}).status_code == 200                  # a 503 here would tell the user a saved change was lost


def test_ensure_ok_names_the_closure_only():
    class Res:
        errors = {"drivers": "x", "inventory_demand": "y"}

    with pytest.raises(degraded.UpstreamDegraded) as e:
        degraded.ensure_ok(Res, "cohorts")
    assert e.value.modules == [{"module": "drivers", "error": "x"}]
    with pytest.raises(degraded.UpstreamDegraded):
        degraded.ensure_ok(Res, "financing")                                           # financing <- inventory_supply <- inventory_demand
    class Fine:
        errors: dict = {}
    degraded.ensure_ok(Fine, "pacing")
    assert degraded.closure("financing") == ["financing", "pnl", "inventory_supply", "drivers", "cohorts", "inventory_demand"]
