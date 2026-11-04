"""The engine against the frozen sheet (2026-11-03): the reconcile map through the foundation's own comparison code, plus the Intl reorder dates at the
sheet's hard-coded 30-day lead time."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

import inv_supply_support as S
from app.projection.engine import EngineResult
from app.projection.interfaces import Scenario
from app.projection.inventory_supply import calc
from app.projection.inventory_supply.model import Settings
from app.projection.inventory_supply.reconcile_map import MAP

sys.path.insert(0, str(S.ROOT / "scripts" / "projection"))
import reconcile as R                                                       # noqa: E402

pytestmark = pytest.mark.skipif(not S.snapshot_available(), reason="sheet snapshot not available")


def _engine(inp=None):
    inp = inp or S.inputs_from_snapshot()
    res = calc.compute_supply(inp, S.demand_from_snapshot(), S.SNAPSHOT_TODAY, Settings())
    return EngineResult(scenario=Scenario(id=1, slug="current", name="Current plan"), actual_through=None, results={"inventory_supply": res})


def test_every_mapped_cell_ties_to_the_sheet_within_tolerance():
    reports = R.reconcile({"inventory_supply": MAP}, R.SnapshotSource(), _engine(), 0.005, 1.0)
    bad = [(r.key, r.status, r.error, r.worst[:2]) for r in reports if r.status != "ok"]
    assert not bad, bad
    assert len(reports) > 250 and sum(r.cells for r in reports) > 4900
    assert all(r.skipped == 0 for r in reports)                              # no text / error cell hides a line


def test_map_covers_each_burn_tab_value_cash_and_purchase_order_column():
    keys = {m["key"] for m in MAP}
    for k in ("revamp_bull_closing", "intl_boneflex_cover", "amazon_liver_to_send", "total_flow_closing", "total_check", "value_total", "outlay_total",
              "outlay_intl", "po_po_value", "po_order_date", "reorder_revamp_bull_order_by", "reorder_intl_liver_est_stockout"):
        assert k in keys


def test_intl_reorder_order_by_matches_the_sheet_when_its_30_day_lead_time_is_used():
    inp = S.inputs_from_snapshot()
    for p in ("bull", "liver", "boneflex"):
        inp.reorder[("intl", p)].lead_days = 30                                 # the sheet's Intl Reorder column B is typed 30; its own input says 60
    eng = _engine(inp)
    extra = [{"key": f"intl_{p}_{n}", "tab": "Intl Reorder", "row": row, "c0": col, "kind": "scalar", "tol": 0.0, "abs": 0.01,
              "result": f"inventory.scalars.reorder.intl.{p}.{n}"}
             for p, row in (("bull", 4), ("liver", 5), ("boneflex", 6)) for col, n in (("B", "lead_days"), ("J", "order_by"), ("K", "days_until_order"))]
    reports = R.reconcile({"inventory_supply": extra}, R.SnapshotSource(), eng, 0.005, 1.0)
    assert all(r.status == "ok" for r in reports), [(r.key, r.worst) for r in reports if r.status != "ok"]
    # and the statuses the sheet shows (OVERDUE for bull and boneflex, OK for liver) come out the same
    status = {r["product"]: r["status"] for r in eng.results["inventory_supply"].reorder if r["location"] == "intl"}
    assert status["bull"] == "OVERDUE" and status["boneflex"] == "OVERDUE" and status["liver"] == "OK"


def test_with_the_declared_60_day_lead_time_intl_orders_are_due_30_days_earlier():
    eng = _engine()
    r = {x["product"]: x for x in eng.results["inventory_supply"].reorder if x["location"] == "intl"}
    sheet_order_by = S.etl.snapshot_reader().cell("Intl Reorder", "J5")      # liver, 30-day lead
    assert r["liver"]["serial"]["order_by"] == pytest.approx(sheet_order_by - 30)
