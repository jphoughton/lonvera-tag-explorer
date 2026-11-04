"""Variables of module `pnl`: cost-line drivers, headcount, the COGS model, inflation, the value multiples and the history tables (sheet P&L rows 24-175, COGS)."""
from __future__ import annotations

from .build import Builder

# key, kind, base, sheet cell, default. kind: rate (share of a revenue base, from the trailing window unless typed), fixed (dollars a month, long-run step), carry, model
DRIVERS = (
    ("amazon_fba_fees", "rate", "amazon_rev", "P&L!B25", None), ("amazon_seller_fees", "rate", "amazon_rev", "P&L!B26", None),
    ("amazon_inbound_transport", "rate", "amazon_rev", "P&L!B28", None), ("amazon_subscriptions", "fixed", None, "P&L!W27:BJ27", 39.99),
    ("china_cogs", "model", "total_revenue", "P&L!B32 (=COGS!L44) and V32", None), ("us_cogs", "fixed", None, "P&L!W33", 0.0),
    ("inventory_shrinkage", "fixed", None, "P&L!D46 (literal 22,631.4 in Feb-25)", 0.0),
    ("fulfillment_services", "rate", "dtc_rev", "P&L!B37", None), ("inbound_shipping", "rate", "total_revenue", "P&L!B38", None),
    ("shipping_supplies", "fixed", None, "P&L!W42:BJ42 (discontinued)", 0.0), ("shopify_subscriptions", "rate", "dtc_rev", "P&L!B43", None),
    ("seller_fees", "rate", "dtc_rev", "P&L!B44", None),
    ("marketing_agency", "carry", None, "P&L!V60 (carried forward)", None), ("marketing_services", "fixed", None, "P&L!W61:BJ61", 22000.0),
    ("marketing_influencers", "fixed", None, "P&L!W62", 0.0), ("marketing_software", "rate", "total_revenue", "P&L!B63", None),
    ("other_business_expenses", "rate", "total_revenue", "P&L!B66", None),
    ("accounting", "fixed", None, "P&L!W79:BJ79", 2500.0), ("chargeback_management", "rate", "dtc_rev", "P&L!B80", None),
    ("consulting", "fixed", None, "P&L!X81:BJ81 (from Oct-26)", 8000.0), ("customer_service", "fixed", None, "P&L!W82:BJ82", 8500.0),
    ("insurance", "fixed", None, "P&L!W83:BJ83 (from Sep-26)", 1200.0), ("interest_expense", "fixed", None, "P&L!W84", 0.0),
    ("legal", "fixed", None, "P&L!X85:BJ85 (from Oct-26)", 2500.0), ("memberships", "rate", "dtc_rev", "P&L!B86", None),
    ("office_admin", "fixed", None, "P&L!W87:BJ87", 544.4733333333334), ("outside_services", "fixed", None, "P&L!W88:BJ88 (from Sep-26)", 5584.300833333334),
    ("sales_tax_filing", "fixed", None, "P&L!W89:BJ89", 1112.5), ("samples", "fixed", None, "P&L!W90:BJ90", 549.71),
    ("software_apps", "rate", "total_revenue", "P&L!B91", None), ("uncategorized", "fixed", None, "P&L!W94:BJ94", 0.0),
)
BASE_WORDS = {"amazon_rev": "Amazon revenue", "dtc_rev": "DTC revenue", "total_revenue": "total company revenue"}

SKUS = (("LV-VBULL", "Vital Bull", 4, 32, 2.26, 3.92, 0.5), ("DPCD1-GN187001", "Boneflex", 5, 33, 2.59, 2.5, 0.25),
        ("LV-BLC180", "Beef Liver", 6, 34, 4.16, 5.0, 0.25), ("LV-VBELLE", "Vital Belle", 7, 35, 2.65, 2.65, 0.0))


def specs():
    from ..pnl import lines as L
    b = Builder("pnl", "pnl")
    G = "Cost-line drivers"
    for key, kind, base, cell, default in DRIVERS:
        label = L.LINE_KEYS[key].label
        if kind == "rate":
            parent = key == "other_business_expenses"
            b.lever(f"driver.{key}", f"{label}: share of {BASE_WORDS[base]}", group=G, unit="fraction", vt="fraction", default=None, path=("pnl", "drivers", key),
                    lo=-5, hi=5, step=0.001, binding="pnl_driver", extra={"mode": "rate", "base": base, "readonly": parent}, sheet=cell + " (trailing-window rate formula)",
                    used=f"Projected {label.lower()} = this share x {BASE_WORDS[base]} in each projected month; default is the average of the last closed months (window setting).",
                    status="PARTIAL" if parent else "IMPLEMENTED", ticket="pnl#parent-driver-not-editable" if parent else None,
                    notes=("The module cannot save a new driver for this parent line today: its editor rejects it." if parent else
                           "The default is computed from the books, not typed; the value shown is today's trailing rate. Typing a value fixes the rate for the scenario."))
        elif kind == "model":
            b.lever(f"driver.{key}", f"{label}: share of total company revenue", group=G, unit="fraction", vt="fraction", default=None, path=("pnl", "drivers", key),
                    lo=-5, hi=5, step=0.001, binding="pnl_driver", extra={"mode": "rate", "base": base}, sheet=cell,
                    used="Projected China COGS = this share x total revenue; default is the COGS model (blended landed cost per bottle / price per bottle).",
                    notes="Typing a value overrides the COGS model for the scenario.")
        elif kind == "carry":
            b.lever(f"driver.{key}", f"{label}: dollars per month", group=G, unit="dollars per month", vt="money", default=None, path=("pnl", "drivers", key),
                    lo=-1e9, hi=1e9, step=100, binding="pnl_driver", extra={"mode": "fixed", "base": None}, sheet=cell,
                    used="Projected agency cost; default carries the last month forward. Typing a value fixes it.")
        else:
            b.lever(f"driver.{key}", f"{label}: dollars per month", group=G, unit="dollars per month", vt="money", default=default, path=("pnl", "drivers", key),
                    lo=-1e9, hi=1e9, step=100, binding="pnl_driver", extra={"mode": "fixed", "base": None}, sheet=cell,
                    used=f"Projected {label.lower()} each month once the last step starts.")
    b.table("monthly_overrides", "Typed monthly values over any cost or revenue line", group=G, sheet="P&L forward cells typed per month (rows 27, 42, 61, 79, 81-83, 85, 87-90, 94)",
            used="A typed month replaces the driver for that line and month; saved as scenario cells.", unit="cells")

    G = "Engine"
    b.lever("engine.rate_window_months", "Closed months averaged into every trailing rate", group=G, unit="months", vt="integer", default=4, path=("pnl", "engine", "rate_window_months"),
            lo=1, hi=36, step=1, sheet="P&L column B formulas sum(R:U) / sum(R:U) (the last four closed months)", used="Window behind every rate driver: line total / revenue base over these months.")
    b.lever("engine.opex_inflation", "Operating-expense inflation per year", group=G, unit="fraction a year", vt="fraction", default=0.0, path=("pnl", "engine", "opex_inflation"),
            lo=-0.5, hi=1, step=0.005, sheet="P&L!A4 (Opex Inflation)", sheet_cells=(("P&L", "A4"),), used="Compounds every non-COGS projected line from the first projected month.")
    b.lever("engine.cogs_inflation", "Cost-of-goods inflation per year", group=G, unit="fraction a year", vt="fraction", default=0.0, path=("pnl", "engine", "cogs_inflation"),
            lo=-0.5, hi=1, step=0.005, sheet="P&L!A5 (COGS Inflation)", sheet_cells=(("P&L", "A5"),), used="Compounds China COGS, US COGS and shrinkage from the first projected month.")
    b.lever("trend.avg_window", "Months in the growth-rate averages", group=G, unit="months", vt="integer", default=6, path=("pnl", "trend", "avg_window"), lo=1, hi=24, step=1,
            sheet="Formula literal 6 in P&L!H145:BJ157 (COUNT(...)=6)", used="Rolling average length of the month-over-month and year-over-year rows.", sens=False)

    G = "Headcount"
    b.lever("headcount.base", "Headcount cost per month at the start", group=G, unit="dollars per month", vt="money", default=15000.0, path=("pnl", "headcount", "base"), lo=0, hi=1e8, step=500,
            sheet="Formula literal 15000 in P&L!W75:BJ75", used="Fixed headcount cost before any revenue step.")
    b.lever("headcount.threshold", "Annualised revenue where headcount steps start", group=G, unit="dollars a year", vt="money", default=20000000.0, path=("pnl", "headcount", "threshold"),
            lo=0, hi=1e10, step=1000000, sheet="Formula literal 20000000 in P&L!W75:BJ75", used="Headcount adds a step for each whole step of annualised revenue above this amount.")
    b.lever("headcount.step", "Annualised revenue per headcount step", group=G, unit="dollars a year", vt="money", default=5000000.0, path=("pnl", "headcount", "step"), lo=1, hi=1e10, step=500000,
            sheet="Formula literal 5000000 in P&L!W75:BJ75", used="Width of one revenue step above the threshold.")
    b.lever("headcount.increment", "Headcount cost added per step", group=G, unit="dollars per month", vt="money", default=15000.0, path=("pnl", "headcount", "increment"), lo=0, hi=1e8, step=500,
            sheet="Formula literal 15000 (second) in P&L!W75:BJ75", used="Monthly cost added at each step.")
    b.lever("headcount.start", "First month headcount is charged", group=G, unit="month", vt="month", default="2026-09-01", path=("pnl", "headcount", "start"),
            sheet="P&L!W75 (first month with a formula; the deal closes Sep-26)", used="Months before this carry no headcount cost.", sens=False)

    G = "Cost of goods model"
    b.lever("cogs.us_share", "Share of bottles from the US supply chain", group=G, unit="fraction", vt="fraction", default=0.7, path=("pnl", "cogs", "us_share"), lo=0, hi=1, step=0.05,
            sheet="COGS!N24", sheet_cells=(("COGS", "N24"),), used="Blended landed cost = (1 - share) x China cost + share x US cost, per bottle.")
    b.lever("cogs.price_per_bottle", "Average selling price per bottle for the forward COGS rate", group=G, unit="dollars", vt="money", default=29.22, path=("pnl", "cogs", "price_per_bottle"),
            lo=0.01, hi=1000, step=0.5, sheet="COGS!L38 (Old Avg Sale Price Per Bottle; L36 forward price points to it)", sheet_cells=(("COGS", "L38"),),
            used="Forward China COGS rate = blended landed cost per bottle / this price.", notes="Today's price is 21.62 (COGS!L37); at that price the rate is 16.3% instead of 12.1%.")
    b.lever("cogs.current_price_per_bottle", "Current average selling price per bottle", group=G, unit="dollars", vt="money", default=21.62, path=("pnl", "cogs", "current_price_per_bottle"),
            lo=0.01, hi=1000, step=0.5, sheet="COGS!L37", sheet_cells=(("COGS", "L37"),), used="Shown beside the forward rate as the cost rate at today's price; does not drive the P&L.",
            status="PARTIAL", probe="view", sens=False)
    for sku, name, r1, r2, china, us, mix in SKUS:
        slug = sku.lower().replace("-", "_")
        b.lever(f"cogs.mix.{slug}", f"{name}: share of bottle sales", group=G, unit="fraction", vt="fraction", default=mix, path=("pnl", "cogs", "mix", sku), lo=0, hi=1, step=0.05,
                sheet=f"COGS!F{r1}", sheet_cells=(("COGS", f"F{r1}"),) if mix else (), used="Weights this SKU's landed costs in the blended cost per bottle.")
        b.lever(f"cogs.china_cost.{slug}", f"{name}: China landed cost per bottle", group=G, unit="dollars", vt="money", default=china, path=("pnl", "cogs", "china_cost", sku),
                lo=0, hi=1000, step=0.05, sheet=f"COGS!D{r1}", sheet_cells=(("COGS", f"D{r1}"),), also_used_by=("inventory_supply",),
                used="China side of the blended landed cost per bottle (the P&L's cost of goods rate) and the unit value of this product's stock (inventory value).")
        b.lever(f"cogs.us_cost.{slug}", f"{name}: US landed cost per bottle", group=G, unit="dollars", vt="money", default=us, path=("pnl", "cogs", "us_cost", sku),
                lo=0, hi=1000, step=0.05, sheet=f"COGS!D{r2}", sheet_cells=(("COGS", f"D{r2}"),), used="US side of the blended landed cost per bottle.")
    b.lever("cogs.china_cost.dpcd1_gn179001", "Vital Flow: China landed cost per bottle", group=G, unit="dollars", vt="money", default=0.0, path=("pnl", "cogs", "china_cost", "DPCD1-GN179001"),
            lo=0, hi=1000, step=0.05, sheet="COGS!D13", sheet_cells=(("COGS", "D13"),), also_used_by=("inventory_supply",),
            used="Unit value of Vital Flow stock (inventory value); Vital Flow carries no sales mix, so the P&L's cost rate does not move.")
    b.table("cogs_sku", "Warehouse SKU table (12 SKUs: quantity, China cost, mix, US cost)", group=G, sheet="COGS!A4:H15 and A32:H43", unit="rows",
            used="Starting values for the per-SKU costs and mix above; the other eight SKUs carry no sales mix.", scope="all")

    G = "Value at exit"
    b.lever("ev.year", "Year whose net operating income sets enterprise value", group=G, unit="year", vt="integer", default=2027, path=("pnl", "ev", "year"), lo=2025, hi=2029, step=1,
            sheet="P&L!C162:H162 refer to $BN$100 (FY2027 net operating income)", used="Implied enterprise value = that year's net operating income x each multiple.", sens=False)
    b.lever("ev.loi", "Offer (letter of intent) price", group=G, unit="dollars", vt="money", default=7500000.0, path=("pnl", "ev", "loi"), lo=0, hi=1e10, step=100000,
            sheet="P&L!A160 note ($7.5M: $6.0M cash + $1.5M seller note)", used="Compared with the implied value range on the P&L and the headline.", sens=False, probe="engine")
    b.lever("ev.multiples", "Multiples applied to net operating income", group=G, unit="multiples", vt="list", default=[3, 4, 5, 6, 7, 8], path=("pnl", "ev", "multiples"),
            sheet="Formula literals 3 to 8 in P&L!C162:H162", used="Columns of the implied enterprise value table.", sens=False, extra={"alt": [3, 5, 7]})

    G = "History and reference"
    b.table("qb_actuals", "QuickBooks history (Profit and Loss by month)", group=G, sheet="P&L!C:U pasted QuickBooks values (Jan-25 to Jul-26)",
            used="Actual months: every cost line is its loaded value; trailing rates read these months.", scope="all", unit="cells")
    b.table("freshness.stale_after_days", "QuickBooks counts as late after (days past the first unloaded month's end)", group=G, sheet="No sheet cell", unit="days", scope="all",
            used="The page and the daily check call the QuickBooks load late when the first month not loaded closed more than this many days ago (default 45).")
    b.table("vendor_retainers", "Vendor retainers, current and proposed", group=G, sheet="Fixed Expenses!B2:D11 (10 vendors; current $50,270, proposed $58,770 a month)",
            used="Reference only: no projected line reads it.", status="PARTIAL", unit="rows", scope="all", ticket="pnl#vendor-retainers-unwired",
            notes="The sheet does not wire it into any formula either.")

    G = "Sheet items the app does not carry"
    b.missing("payment_processing_rate", "Payment processing fee rate", group=G, unit="fraction", default=0.041, sheet="P&L!B40 (typed 0.041)", sheet_cells=(("P&L", "B40"),),
              used="Nothing: P&L row 40 sums shipping supplies, Shopify subscriptions and seller fees; the typed rate is not read.", notes="Unused in the sheet; the app's three payment lines are drivers.")
    b.missing("cogs_adjustments", "Sales and COGS percent by channel and month", group=G, unit="rows", vt="list", sheet="COGS Adjustments!B2:BJ13 (hidden, typed)",
              used="Nothing: no projected cell reads it.", notes="Superseded by revenue from the spend plan.", scope="all")
    b.missing("cogs_old_cost_per_bottle", "Old average cost per bottle (US supply chain)", group=G, unit="dollars", vt="money", default=4.46, sheet="COGS!L42 (typed), L46 (=L42/L38)",
              sheet_cells=(("COGS", "L42"),), used="Only the 'Old COGS % Rev' display cell, which no P&L line reads.", scope="all")
    b.missing("marketing_software_jul26", "Marketing software, July 2026 typed amount", group=G, unit="dollars", vt="money", default=7143.5, sheet="P&L!U63 (=4143.5+3000, two literals)",
              used="July 2026 history cell; loaded as an actual.", notes="History, not a driver.", scope="all")
    return b.specs
