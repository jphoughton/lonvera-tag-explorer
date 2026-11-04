"""Reconcile map for inventory_supply: burn tabs (every line, every month), Total burn, Inventory Value, Inventory Cash Outlay, the computed
columns of Purchase Orders, the reorder tabs and the bottom tables of the burn tabs. Months align on the header dates: the sheet's start month
rolls with TODAY(), so reconcile at the snapshot date (env PROJECTION_TODAY=2026-11-03; the sheet starts at Nov 2026).

Not mapped, with the reason (details in docs/projection/MODULE_inventory_supply.md "Differences vs the sheet"):
  - Intl Reorder columns B (lead time), J (order by) and K (days until order): the sheet hard-codes 30 days where its own input says 60.
  - Burn tabs' "OUT" text cells (the closing stock row carries the same information) and Vital Flow rows 18 / 27 / 36 on Inventory Value (typed zeros).
"""
from __future__ import annotations

START = "2026-11-01"
DATE_TOL = {"tol": 0.0, "abs": 0.01}            # reorder dates are Sheets serial numbers: compare to 0.01 day

# (location, tab, {product: first row}, {line: row offset from the product's first row})
_REVAMP = ("revamp", "Warehance Inventory Burn", {"bull": 6, "liver": 15, "boneflex": 24, "belle": 33},
           {"opening": 0, "demand": 1, "inbound": 2, "out": 3, "closing": 4, "cover": 5})
_INTL = ("intl", "Intl Inventory Burn", {"bull": 6, "liver": 14, "boneflex": 22, "flow": 30},
         {"opening": 0, "demand": 1, "inbound": 2, "closing": 3, "cover": 4})
_AMAZON = ("amazon", "Amazon Inventory Burn", {"bull": 6, "liver": 16, "boneflex": 26, "belle": 36},
           {"opening": 0, "demand": 1, "inbound": 2, "closing": 3, "cover": 4, "to_send": 7})
_TOTAL = ("total", "Total Inventory Burn", {"bull": 6, "liver": 14, "boneflex": 22, "belle": 30, "flow": 38},
          {"opening": 0, "demand": 1, "inbound": 2, "closing": 3, "cover": 4})


def _burn_entries() -> list[dict]:
    out = []
    for loc, tab, starts, lines in (_REVAMP, _INTL, _AMAZON, _TOTAL):
        for prod, r0 in starts.items():
            for line, off in lines.items():
                last = "AJ" if line in ("cover", "to_send") else "AK"            # the sheet's last column divides by its own demand: window-end artifact
                out.append({"key": f"{loc}_{prod}_{line}", "tab": tab, "row": r0 + off, "c0": "B", "c1": last, "month0": START,
                            "result": f"inventory.burn.{loc}|{prod}|{line}"})
    out.append({"key": "total_check", "tab": "Total Inventory Burn", "row": 57, "c0": "B", "c1": "AK", "month0": START, "result": "inventory.check"})
    return out


def _stats_entries() -> list[dict]:
    out = []
    no_cover = {("amazon", "belle"), ("intl", "flow"), ("total", "flow")}          # no demand: the sheet's cover cell is blank
    for loc, tab, rows in (("revamp", "Warehance Inventory Burn", {"bull": 45, "liver": 46, "boneflex": 47, "belle": 48}),
                           ("intl", "Intl Inventory Burn", {"bull": 41, "liver": 42, "boneflex": 43, "flow": 44}),
                           ("amazon", "Amazon Inventory Burn", {"bull": 49, "liver": 50, "boneflex": 51, "belle": 52}),
                           ("total", "Total Inventory Burn", {"bull": 51, "liver": 52, "boneflex": 53, "belle": 54, "flow": 55})):
        for prod, r in rows.items():
            for col, name in (("B", "available_today"), ("C", "avg_monthly_demand"), ("D", "months_cover_today"), ("E", "months_out")):
                if name == "months_cover_today" and (loc, prod) in no_cover:
                    continue
                out.append({"key": f"stats_{loc}_{prod}_{name}", "tab": tab, "row": r, "c0": col, "kind": "scalar",
                            "result": f"inventory.scalars.stats.{loc}.{prod}.{name}"})
    return out


def _value_entries() -> list[dict]:
    out = []
    blocks = (("revamp", {"bull": 14, "liver": 15, "boneflex": 16, "belle": 17}), ("amazon", {"bull": 23, "liver": 24, "boneflex": 25, "belle": 26}),
              ("intl", {"bull": 32, "liver": 33, "boneflex": 34, "flow": 36}))
    for loc, rows in blocks:
        for prod, r in rows.items():
            out.append({"key": f"value_{loc}_{prod}", "tab": "Inventory Value", "row": r, "c0": "B", "c1": "AK", "month0": START,
                        "result": f"inventory.value_by_location.{loc}|{prod}"})
    for prod, r in {"bull": 41, "liver": 42, "boneflex": 43, "belle": 44, "flow": 45}.items():
        out.append({"key": f"value_total_{prod}", "tab": "Inventory Value", "row": r, "c0": "B", "c1": "AK", "month0": START,
                    "result": f"inventory.burn.total|{prod}|value"})
    out.append({"key": "value_total", "tab": "Inventory Value", "row": 46, "c0": "B", "c1": "AK", "month0": START, "result": "inventory.value"})
    for loc, col in (("revamp", "H"), ("amazon", "I"), ("intl", "J")):                  # stock on hand today x unit cost (top table, Total row)
        out.append({"key": f"value_now_{loc}", "tab": "Inventory Value", "row": 9, "c0": col, "kind": "scalar", "result": f"inventory.scalars.value_now.{loc}"})
    return out


def _outlay_entries() -> list[dict]:
    rows = {"deposits": (5, "inventory.deposits"), "landing_value": (6, "inventory.landing_value"), "landing_deposits": (7, "inventory.landing_deposits"),
            "balances": (8, "inventory.balances"), "total": (9, "inventory.outlay"), "intl": (10, "inventory.intl_outlay"),
            "cumulative": (11, "inventory.outlay_cumulative")}
    out = [{"key": f"outlay_{k}", "tab": "Inventory Cash Outlay", "row": r, "c0": "C", "c1": "BJ", "month0": "2025-01-01", "result": path}
           for k, (r, path) in rows.items()]
    for k, r, path in (("total_po_value", 14, "po.total_value"), ("total_deposits", 15, "po.total_deposits"),
                       ("total_balances", 16, "po.total_balances"), ("total_scheduled", 17, "outlay.total_scheduled")):
        out.append({"key": f"outlay_{k}", "tab": "Inventory Cash Outlay", "row": r, "c0": "B", "kind": "scalar", "result": f"inventory.scalars.{path}"})
    return out


def _po_entries() -> list[dict]:
    return [{"key": f"po_{k}", "tab": "Purchase Orders", "kind": "column", "c0": col, "r0": 2, "r1": 78, "result": f"inventory.po_cells.{k}"}
            for k, col in (("unit_cost", "L"), ("po_value", "M"), ("down_payment", "N"), ("balance", "Q"), ("order_date", "F"))]


def _reorder_entries() -> list[dict]:
    out = []
    cols = (("D", "stockout_month", True), ("E", "opening_that_month", False), ("F", "demand_that_month", False), ("G", "days_in_month", False),
            ("H", "est_stockout", True), ("I", "must_land_by", True), ("M", "avg_monthly_demand", False), ("N", "suggested_po_units", False))
    lead_cols = (("B", "lead_days", False), ("C", "safety_days", False), ("J", "order_by", True), ("K", "days_until_order", True))
    for loc, tab, rows in (("revamp", "Warehance Reorder", {"bull": 4, "liver": 5, "boneflex": 6, "belle": 7}),
                           ("intl", "Intl Reorder", {"bull": 4, "liver": 5, "boneflex": 6, "flow": 7})):
        for prod, r in rows.items():
            cc = list(cols) + ([] if loc == "intl" else list(lead_cols)) + ([("C", "safety_days", False)] if loc == "intl" else [])
            for col, name, is_date in cc:
                if loc == "intl" and prod == "flow" and name not in ("avg_monthly_demand", "suggested_po_units", "safety_days"):
                    continue                                    # no stockout in the window: blank cells on the sheet
                e = {"key": f"reorder_{loc}_{prod}_{name}", "tab": tab, "row": r, "c0": col, "kind": "scalar",
                     "result": f"inventory.scalars.reorder.{loc}.{prod}.{name}"}
                if is_date:
                    e.update(DATE_TOL)
                out.append(e)
    return out


MAP: list[dict] = _burn_entries() + _stats_entries() + _value_entries() + _outlay_entries() + _po_entries() + _reorder_entries()
