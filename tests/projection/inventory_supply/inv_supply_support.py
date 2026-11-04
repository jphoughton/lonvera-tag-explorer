"""Shared test helpers for inventory_supply: sheet-snapshot demand, snapshot-derived Inputs (no database), small hand-built fixtures."""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.projection.interfaces import DemandResult                                       # noqa: E402
from app.projection.inventory_supply.model import (PO, Inputs, ProductCost, ReorderParam, Stock, Transfer, Vendor, VendorCost)   # noqa: E402
from etl import projection_inventory_supply as etl                                        # noqa: E402

SNAPSHOT_TODAY = date(2026, 11, 3)
EPOCH = date(1899, 12, 30)


def snapshot_available() -> bool:
    try:
        sr = etl.snapshot_reader()
        sr.cell("Purchase Orders", "B2")
        return True
    except Exception:
        return False


def _serial(v) -> date:
    return EPOCH + timedelta(days=int(v))


def demand_from_snapshot() -> DemandResult:
    """The sheet's three Demand Fcst tabs as the DemandResult inventory_demand will emit: full-month units plus (loc_mtd, product) month-to-date."""
    sr = etl.snapshot_reader()
    units: dict = {}
    layout = {"revamp": ("Warehance Demand Fcst", ("bull", "liver", "boneflex", "belle")),
              "amazon": ("Amazon Demand Fcst", ("bull", "liver", "boneflex", "belle")),
              "intl": ("Intl Demand Fcst", ("bull", "liver", "boneflex", "flow"))}
    for loc, (tab, prods) in layout.items():
        months = [_serial(v) for v in sr.row(tab, 3, "B", "AK")]
        for k, prod in enumerate(prods):
            top = 11 + 5 * k
            fc, mtd = sr.row(tab, top + 3, "B", "AK"), sr.cell(tab, f"B{top + 2}")
            units[(loc, prod)] = {m: float(v or 0.0) for m, v in zip(months, fc)}
            units[(f"{loc}_mtd", prod)] = {months[0]: float(mtd or 0.0)}
    return DemandResult(units=units)


def inputs_from_snapshot() -> Inputs:
    """Inputs built straight from the frozen sheet (what etl seed writes to the database, without the database)."""
    d = etl.read_snapshot()
    inp = Inputs()
    inp.vendors = {v["vendor"]: Vendor(v["vendor"], v["lead_weeks"], etl.VENDOR_FACTS.get(v["vendor"], {}).get("moq_units", 0.0),
                                       etl.VENDOR_FACTS.get(v["vendor"], {}).get("default_warehouse", "revamp")) for v in d["vendors"]}
    dep = etl._default_deposits(d["pos"])
    inp.vendor_costs = {(c["vendor"], c["product"]): VendorCost(c["vendor"], c["product"], c["unit_cost"], dep.get((c["vendor"], c["product"])))
                        for c in d["vendor_costs"]}
    inp.product_costs = {c["product"]: ProductCost(c["product"], c["china_unit_cost"], c["sku"], c["us_unit_cost"]) for c in d["product_costs"]}
    inp.reorder = {(r["location"], r["product"]): ReorderParam(r["location"], r["product"], r["lead_days"], r["safety_days"], r["moq_units"]) for r in d["reorder"]}
    inp.stock = {(s["location"], s["product"]): Stock(s["location"], s["product"], s["as_of"], s["on_hand"], s["allocated"], s["inbound_per_3pl"], s["estimate"], s["note"])
                 for s in d["stock"]}
    inp.pos = [PO(id=p["id"], product=p["product"], vendor=p["vendor"], warehouse=p["warehouse"], units=p["units"], land_date=p["land_date"], status=p["status"],
                  order_date=p["order_date"], deposit_pct=p["deposit_pct"], unit_cost=p["unit_cost"], po_number=p["po_number"], sheet_row=p["sheet_row"],
                  deposit_paid=p["deposit_paid"], shipping_paid=p["shipping_paid"], balance_paid=p["balance_paid"], link=p["link"], note=p["note"],
                  source=p["source"]) for p in d["pos"]]
    inp.transfers = [Transfer(id=t["id"], product=t["product"], units=t["units"], ship_date=t["ship_date"], receive_date=t["receive_date"], status=t["status"],
                              transfer_no=t["transfer_no"], sheet_row=t["sheet_row"], note=t["note"], source=t["source"]) for t in d["transfers"]]
    return inp


def flat_demand(per_month: dict, months: int = 40, start: date = date(2026, 11, 1), mtd: dict | None = None) -> DemandResult:
    """DemandResult with a constant monthly figure per (location, product); `mtd` {(loc, prod): units} for the start month."""
    from app.projection.interfaces import add_months
    units: dict = {}
    for key, v in per_month.items():
        units[key] = {add_months(start, i): float(v) for i in range(months)}
        units[(f"{key[0]}_mtd", key[1])] = {start: float((mtd or {}).get(key, 0.0))}
    return DemandResult(units=units)


# ---------------------------------------------------------------------------------------------------------------- small hand-built fixture

def mini_inputs(*, stock_bull: float = 10000.0, allocated: float = 500.0) -> Inputs:
    """Two vendors, one product (bull) stocked at Revamp, FBA and the international 3PL, no orders and no transfers."""
    inp = Inputs()
    inp.vendors = {"China": Vendor("China", 8.0, 5000.0, "intl"), "KeyStone": Vendor("KeyStone", 12.0, 20000.0, "revamp")}
    inp.vendor_costs = {("China", "bull"): VendorCost("China", "bull", 2.26, 0.4), ("KeyStone", "bull"): VendorCost("KeyStone", "bull", 3.92, 0.5)}
    inp.product_costs = {"bull": ProductCost("bull", 2.26, "LV-VBULL", 4.7)}
    inp.reorder = {("revamp", "bull"): ReorderParam("revamp", "bull", 90, 14, 0.0), ("intl", "bull"): ReorderParam("intl", "bull", 60, 14, 0.0)}
    asof = date(2026, 11, 3)
    inp.stock = {("revamp", "bull"): Stock("revamp", "bull", asof, stock_bull, allocated), ("amazon", "bull"): Stock("amazon", "bull", asof, 2000.0),
                 ("intl", "bull"): Stock("intl", "bull", asof, 1000.0)}
    return inp


def po(id: int = 1, *, product="bull", vendor="China", warehouse="intl", units=10000.0, land=date(2027, 3, 1), status="planned", **kw) -> PO:
    return PO(id=id, product=product, vendor=vendor, warehouse=warehouse, units=units, land_date=land, status=status, **kw)


def transfer(id: int = 1, *, product="bull", units=2000.0, ship=date(2026, 11, 20), receive=date(2026, 12, 5), status="planned", **kw) -> Transfer:
    return Transfer(id=id, product=product, units=units, ship_date=ship, receive_date=receive, status=status, **kw)


def series_list(res, loc: str, prod: str, line: str, n: int = 6) -> list:
    return [res.burn[(loc, prod, line)].get(m) for m in res.months[:n]]
