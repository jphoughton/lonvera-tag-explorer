"""Read-only loaders: the module's tables -> `Inputs`. Also the reference-day rule shared by the engine and the API."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

import psycopg

from .. import clock

from .model import PO, Inputs, ProductCost, ReorderParam, Stock, Transfer, Vendor, VendorCost, num


def resolve_today(params: dict | None = None) -> date:
    """Reference day: scenario param inventory_supply.as_of, else the projection clock (Pacific today; env PROJECTION_TODAY pins it, used for the frozen-sheet reconcile)."""
    raw = ((params or {}).get("inventory_supply") or {}).get("as_of")
    if isinstance(raw, str) and raw.strip():
        try:
            return date.fromisoformat(raw.strip()[:10])
        except ValueError:
            pass
    return clock.today_pt()


def _d(v: Any) -> date | None:
    if isinstance(v, datetime):
        return v.date()
    return v if isinstance(v, date) else None


def _po(r: dict) -> PO:
    return PO(id=int(r["id"]), product=r["product"], vendor=r["vendor"], warehouse=r["warehouse"], units=float(r["units"]),
              land_date=r["land_date"], status=r["status"], order_date=r["order_date"], deposit_pct=num(r["deposit_pct"], None),
              unit_cost=num(r["unit_cost"], None), po_number=r["po_number"], sheet_row=r["sheet_row"], deposit_paid=r["deposit_paid"],
              shipping_paid=r["shipping_paid"], balance_paid=r["balance_paid"], link=r["link"], note=r["note"], source=r["source"],
              updated_at=r["updated_at"])


def _tr(r: dict) -> Transfer:
    return Transfer(id=int(r["id"]), product=r["product"], units=float(r["units"]), ship_date=r["ship_date"], receive_date=r["receive_date"],
                    status=r["status"], transfer_no=r["transfer_no"], sheet_row=r["sheet_row"], note=r["note"], source=r["source"],
                    updated_at=r["updated_at"])


def load_inputs(conn, scn=None) -> Inputs:
    """The inputs the engine runs on: the stored rows (plan of record) with the scenario's changes applied (`scn.params` is the effective view)."""
    from . import overlay
    base = load_base(conn)
    return overlay.apply_overlay(base, getattr(scn, "params", None)) if scn is not None else base


def load_base(conn) -> Inputs:
    """The stored rows only. A database without migration 150 yields empty inputs (the engine then runs on nothing instead of failing)."""
    try:
        pos = [_po(r) for r in conn.execute("SELECT * FROM proj_po ORDER BY COALESCE(sheet_row, 100000 + id), id").fetchall()]
        trs = [_tr(r) for r in conn.execute("SELECT * FROM proj_fba_transfer ORDER BY COALESCE(sheet_row, 100000 + id), id").fetchall()]
        vendors = {r["vendor"]: Vendor(r["vendor"], float(r["lead_weeks"]), float(r["moq_units"]), r["default_warehouse"], r["note"])
                   for r in conn.execute("SELECT * FROM proj_vendor_lead").fetchall()}
        vcosts = {(r["vendor"], r["product"]): VendorCost(r["vendor"], r["product"], float(r["unit_cost"]), num(r["deposit_pct"], None), r["note"])
                  for r in conn.execute("SELECT * FROM proj_vendor_cogs").fetchall()}
        pcosts = {r["product"]: ProductCost(r["product"], float(r["china_unit_cost"]), r["sku"], num(r["us_unit_cost"], None))
                  for r in conn.execute("SELECT * FROM proj_product_cost").fetchall()}
        reorder = {(r["location"], r["product"]): ReorderParam(r["location"], r["product"], int(r["lead_days"]), int(r["safety_days"]), float(r["moq_units"]))
                   for r in conn.execute("SELECT * FROM proj_reorder_param").fetchall()}
        stock: dict[tuple[str, str], Stock] = {}
        for r in conn.execute("SELECT DISTINCT ON (location, product) * FROM proj_stock_snapshot ORDER BY location, product, as_of DESC").fetchall():
            stock[(r["location"], r["product"])] = Stock(r["location"], r["product"], r["as_of"], float(r["on_hand"]), float(r["allocated"]),
                                                          float(r["inbound_per_3pl"]), bool(r["estimate"]), r["note"])
    except psycopg.errors.UndefinedTable:
        conn.rollback()
        return Inputs()
    edits = [x.updated_at for x in pos + trs if x.updated_at]
    return Inputs(pos=pos, transfers=trs, vendors=vendors, vendor_costs=vcosts, product_costs=pcosts, reorder=reorder, stock=stock,
                  last_edit=max(edits) if edits else None)
