"""Reconcile map: engine vs the sheet's Demand Fcst tabs (36 months from the as-of month), the derived baselines and the Total Demand Fcst tab.

Run with the as-of date the snapshot was taken (the sheet's TODAY() was 2026-11-03):
    PROJECTION_TODAY=2026-11-03 python scripts/projection/reconcile.py --module inventory_demand
"""
from __future__ import annotations

M0 = "2026-11-01"          # month of column B in every Demand Fcst tab (the sheet's rolling start)

MAP: list[dict] = []

# (location, tab, input tab, rows of the product blocks, products in sheet order)
_BLOCKS = [
    ("revamp", "Warehance Demand Fcst", "Warehance Fcst Inputs", {"bull": 14, "liver": 19, "boneflex": 24, "belle": 29}),
    ("amazon", "Amazon Demand Fcst", "Amazon Fcst Inputs", {"bull": 14, "liver": 19, "boneflex": 24, "belle": 29}),
    ("intl", "Intl Demand Fcst", "Intl Fcst Inputs", {"bull": 14, "liver": 19, "boneflex": 24, "flow": 29}),
]
# Fcst Inputs rows holding window units / baseline per product, and the cell with the last complete day
_INPUT_ROWS = {"revamp": {"bull": 11, "liver": 12, "boneflex": 13, "belle": 14, "last": 5},
               "amazon": {"bull": 12, "liver": 13, "boneflex": 14, "belle": 15, "last": 6},
               "intl": {"bull": 12, "liver": 13, "boneflex": 14, "flow": 15, "last": 6}}

for loc, tab, inp, prods in _BLOCKS:
    for prod, row in prods.items():
        MAP.append({"key": f"{loc}_{prod}_units", "tab": tab, "row": row, "c0": "B", "c1": "AK", "month0": M0, "kind": "values",
                    "result": f"demand.units.{loc}|{prod}"})
        MAP.append({"key": f"{loc}_{prod}_units_per_day", "tab": tab, "row": row - 2, "c0": "B", "c1": "AK", "month0": M0, "kind": "values",
                    "result": f"demand.daily_rate.{loc}|{prod}"})
        MAP.append({"key": f"{loc}_{prod}_mtd", "tab": tab, "row": row - 1, "c0": "B", "c1": "B", "month0": M0, "kind": "values",
                    "result": f"demand.units.{loc}_mtd|{prod}"})
        MAP.append({"key": f"{loc}_{prod}_window_units", "tab": inp, "row": _INPUT_ROWS[loc][prod], "c0": "B", "kind": "scalar",
                    "result": f"demand.window_units.{loc}|{prod}"})
        MAP.append({"key": f"{loc}_{prod}_baseline", "tab": inp, "row": _INPUT_ROWS[loc][prod], "c0": "C", "kind": "scalar",
                    "result": f"demand.rates.{loc}|{prod}"})
    MAP.append({"key": f"{loc}_last_complete_day", "tab": inp, "row": _INPUT_ROWS[loc]["last"], "c0": "B", "kind": "scalar", "result": f"demand.serial.{loc}"})

# Total Demand Fcst: rows 6-8 = Vital Bull DTC / Amazon / International, 9 = total; blocks of 6 rows after that
_TOTAL_ROWS = {"bull": 9, "liver": 15, "boneflex": 21, "belle": 27, "flow": 33}
for prod, row in _TOTAL_ROWS.items():
    MAP.append({"key": f"total_{prod}", "tab": "Total Demand Fcst", "row": row, "c0": "B", "c1": "AK", "month0": M0, "kind": "values",
                "result": f"demand.product_total.{prod}"})
MAP.append({"key": "total_all", "tab": "Total Demand Fcst", "row": 35, "c0": "B", "c1": "AK", "month0": M0, "kind": "values", "result": "demand.total"})
for loc, row in (("revamp", 36), ("amazon", 37), ("intl", 38)):
    MAP.append({"key": f"share_{loc}", "tab": "Total Demand Fcst", "row": row, "c0": "B", "c1": "AK", "month0": M0, "kind": "values",
                "result": f"demand.share.{loc}", "tol": 0.0005, "abs": 0.0005})
