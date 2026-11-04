"""Variables of module `inventory_supply`: reorder rules, planner rules, vendor lead times and prices, per-SKU reorder parameters, product costs, order rows.

The 22 scalar settings are read from the module's own SETTING_SPECS (one source); the vendor / price / reorder / product-cost cells and the
purchase-order and transfer tables are named here. Writes go through the module's overlay (params.inventory_supply), see access.py."""
from __future__ import annotations

from .build import Builder

PROD = {"bull": "Vital Bull", "liver": "Beef Liver", "boneflex": "Boneflex", "belle": "Vital Belle", "flow": "Vital Flow"}
VENDORS = (("China", 8.0, 5000.0, "intl", "H4"), ("KeyStone", 12.0, 20000.0, "revamp", "H5"))
PRICES = (("China", "bull", 2.26, 0.4, "D4"), ("China", "liver", 4.16, 0.4, "D5"), ("China", "boneflex", 2.55, 0.4, "D6"),
          ("KeyStone", "bull", 3.92, 0.5, "D7"), ("KeyStone", "boneflex", 2.5, 0.3, "D8"), ("KeyStone", "liver", 5.0, 0.3, "D9"))
REORDER = (("revamp", "bull", 90, 14, 0, 44), ("revamp", "liver", 90, 14, 0, 45), ("revamp", "boneflex", 90, 14, 0, 46), ("revamp", "belle", 90, 14, 0, 47),
           ("intl", "bull", 60, 14, 0, 45), ("intl", "liver", 60, 14, 0, 46), ("intl", "boneflex", 60, 14, 0, 47), ("intl", "flow", 60, 14, 0, 48))
LOC_NAME = {"revamp": "Revamp Utah", "intl": "International 3PL"}
LOC_TAB = {"revamp": "Warehance Fcst Inputs", "intl": "Intl Fcst Inputs"}

USED = {
    "as_of": "Pins the day the burn starts (blank = today); months before it are not burned.",
    "horizon_months": "Number of months the burn, stock-out search and averages cover.",
    "stale_stock_days": "Age after which a stock count is flagged old on the page.",
    "safety_days": "When set, replaces every SKU's safety days in the reorder and plan rules.",
    "default_safety_days": "Safety days for a SKU that has no reorder row.",
    "default_lead_days_revamp": "Lead time for a Revamp SKU that has no reorder row.",
    "default_lead_days_intl": "Lead time for an international SKU that has no reorder row.",
    "order_soon_days": "Reorder status reads ORDER SOON when the order-by date is within this many days.",
    "reorder_cover_months": "Suggested order size = this many months of average demand (or the minimum order).",
    "out_below_units": "A month reads OUT when closing stock is below this.",
    "low_cover_months": "A month reads LOW when months of cover are below this.",
    "fba_target_months": "Units to send to Amazon = target cover x next month's demand - closing stock.",
    "plan_cover_months": "The planner sizes each new order to this many months of forward demand.",
    "plan_safety_days": "Safety days the planner keeps when set; otherwise each SKU's own.",
    "po_round_units": "The planner rounds order sizes up to a multiple of this.",
    "transfer_round_units": "The planner rounds Amazon transfers up to a multiple of this.",
    "transfer_min_units": "Smallest Amazon transfer the planner proposes.",
    "fba_restore_months": "A planned transfer restores Amazon stock to this many months of cover.",
    "transfer_lag_months": "Months between a transfer leaving Revamp and arriving at Amazon.",
    "days_per_month": "Days used to turn a daily rate into a monthly one in the planner.",
    "duplicate_window_days": "Orders of the same product and size landing within this window are flagged as possible duplicates.",
    "cash_window_days": "Days ahead counted in the headline cash figure.",
}
ALT = {"as_of": "2026-12-01", "stale_stock_days": 30, "order_soon_days": 300, "safety_days": 30.0, "plan_safety_days": 30.0, "transfer_min_units": 20000}
PLAN = ("plan_cover_months", "plan_safety_days", "po_round_units", "transfer_round_units", "transfer_min_units", "fba_restore_months", "transfer_lag_months", "days_per_month")
NONE = {"default_safety_days": "Every SKU has its own reorder row today, so the default is never read.",
        "default_lead_days_revamp": "Every Revamp SKU has its own reorder row today, so the default is never read.",
        "default_lead_days_intl": "Every international SKU has its own reorder row today, so the default is never read."}
TIES = {"fba_target_months": ("Amazon Fcst Inputs", "B42"), "order_soon_days": ("Warehance Fcst Inputs", "B49"), "reorder_cover_months": ("Warehance Fcst Inputs", "B48")}


def specs():
    from ..inventory_supply.model import SETTING_SPECS
    b = Builder("inventory_supply", "inventory-supply")
    for s in SETTING_SPECS:
        vt = {"num": "number", "opt_num": "number", "int": "integer", "date": "date"}[s.kind]
        unit = s.unit
        b.lever(s.key, s.label, group=s.group, unit=unit, vt=vt, default=s.default, path=("inventory_supply", s.key), lo=s.minimum, hi=s.maximum,
                step=1 if s.kind == "int" else None, sheet=s.source, used=USED.get(s.key, s.label + "."), sheet_cells=(TIES[s.key],) if s.key in TIES else (),
                sens=s.key not in ("as_of",), probe=("plan" if s.key in PLAN else "none" if s.key in NONE else "engine"), notes=NONE.get(s.key, ""),
                extra=({"alt": ALT[s.key]} if s.key in ALT else None))
    G = "Vendors"
    for v, weeks, moq, wh, cell in VENDORS:
        b.lever(f"vendor.{v.lower()}.lead_weeks", f"{v}: lead time", group=G, unit="weeks", vt="number", default=weeks, path=("inventory_supply", "vendors", v, "lead_weeks"), lo=0.1, hi=104, step=1,
                binding="supply", sheet=f"Vendor COGS!{cell}", sheet_cells=(("Vendor COGS", cell),), used="Order date = land date minus lead time; also the earliest an order placed today can land.")
        b.lever(f"vendor.{v.lower()}.moq_units", f"{v}: minimum order", group=G, unit="units", vt="number", default=moq, path=("inventory_supply", "vendors", v, "moq_units"), lo=0, hi=1e9, step=1000,
                binding="supply", sheet="2026-10-13 rebalance (5,000 China, 20,000 KeyStone); no sheet cell", used="Smallest order the planner buys from this vendor.", probe="plan", extra={"alt": 200000.0})
        b.lever(f"vendor.{v.lower()}.default_warehouse", f"{v}: ships to", group=G, unit="location", vt="choice", default=wh, path=("inventory_supply", "vendors", v, "default_warehouse"),
                choices=("revamp", "intl"), binding="supply", sheet="Purchase Orders column D, by vendor", used="Which warehouse the planner buys this vendor's orders for.", sens=False, probe="plan")
    G = "Prices"
    for v, p, cost, dep, cell in PRICES:
        key = f"{v}|{p}"
        b.lever(f"price.{v.lower()}.{p}.unit_cost", f"{v}, {PROD[p]}: unit cost", group=G, unit="dollars per bottle", vt="money", default=cost, path=("inventory_supply", "prices", key, "unit_cost"),
                lo=0, hi=1e6, step=0.05, binding="supply", sheet=f"Vendor COGS!{cell}", sheet_cells=(("Vendor COGS", cell),), used="Order value, down payment and balance of every order for this vendor and product.")
        b.lever(f"price.{v.lower()}.{p}.deposit_pct", f"{v}, {PROD[p]}: deposit share of the order", group=G, unit="fraction", vt="fraction", default=dep,
                path=("inventory_supply", "prices", key, "deposit_pct"), lo=0, hi=1, step=0.05, binding="supply", sheet="Purchase Orders column P (most common value for this vendor and product)",
                used="Deposit paid in the order month; the balance is paid when the order lands.", probe="none",
                notes="Every order today carries its own deposit share, so the vendor default is never read.")
    G = "Reorder rules by warehouse and product"
    for loc, p, lead, safety, moq, row in REORDER:
        key = f"{loc}|{p}"
        tab = LOC_TAB[loc]
        n = f"{LOC_NAME[loc]}, {PROD[p]}"
        b.lever(f"reorder.{loc}.{p}.lead_days", f"{n}: lead time", group=G, unit="days", vt="integer", default=lead, path=("inventory_supply", "reorder", key, "lead_days"), lo=0, hi=730, step=1,
                binding="supply", sheet=f"{tab}!B{row}", sheet_cells=((tab, f"B{row}"),), used="Order-by date = must-land date minus lead time.")
        b.lever(f"reorder.{loc}.{p}.safety_days", f"{n}: safety stock", group=G, unit="days of demand", vt="integer", default=safety, path=("inventory_supply", "reorder", key, "safety_days"), lo=0, hi=365, step=1,
                binding="supply", sheet=f"{tab}!C{row}", sheet_cells=((tab, f"C{row}"),), used="Must-land date = estimated stock-out date minus safety days.")
        b.lever(f"reorder.{loc}.{p}.moq_units", f"{n}: minimum order", group=G, unit="units", vt="number", default=moq, path=("inventory_supply", "reorder", key, "moq_units"), lo=0, hi=1e9, step=1000,
                binding="supply", sheet=f"{tab}!D{row}", sheet_cells=((tab, f"D{row}"),), used="Suggested order is at least this many units.", sens=False, probe="engine")
    G = "Orders and counts"
    b.table("purchase_orders", "Purchase orders (units, land date, status, deposit, price per order)", group=G, sheet="Purchase Orders A2:R120 (74 rows, 205 yellow input cells)",
            used="Inbound units by landing month at each warehouse and cash by order and landing month.", unit="rows", scope="scenario")
    b.table("fba_transfers", "Amazon transfers (units, ship date, receive date)", group=G, sheet="FBA Transfers A4:G47 (44 rows, 350 yellow input cells)",
            used="Units leaving Revamp and arriving at Amazon stock.", unit="rows", scope="scenario")
    b.table("stock_counts", "Stock counts by warehouse and product", group=G, sheet="Warehance Fcst Inputs A35:D39, Amazon Fcst Inputs A36:B40, Intl Fcst Inputs A36:D40", unit="rows",
            used="Opening stock of each burn table (dated measurements).", scope="all")
    b.missing("po_land_date_offsets", "Land dates typed as an offset from another order's date", group=G, unit="days", vt="integer", default=30,
              sheet="Purchase Orders!G13, G17:G38, G40 (=G9+30, =G14+31, =G28+120 chains)", used="Chained land dates in the sheet.",
              notes="Stored as plain dates per order; the chain is not a variable.", scope="scenario")
    return b.specs
