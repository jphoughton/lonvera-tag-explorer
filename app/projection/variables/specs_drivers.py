"""Variables of module `drivers` (page Spend plan): the plan lines, the channel mix, the sales-tax rate and the new-customer ROAS slide."""
from __future__ import annotations

from .build import Builder

CHANNELS = (("amazon", "Amazon ads", "P&L!B52", 0.04920832435847688), ("meta", "Facebook (Meta) ads", "P&L!B53", 0.8971495015118965),
            ("google", "Google ads", "P&L!B54", 0.048089806249984754), ("bing", "Microsoft Bing ads", "P&L!B55", 0.0), ("tiktok", "TikTok ads", "P&L!B56", 0.005552367879641713))


SLIDE = {"drivers.roas_slide.enabled": True, "drivers.roas_slide.from": "2026-10-01", "drivers.roas_slide.to": "2027-06-01", "drivers.roas_slide.start": 0.6, "drivers.roas_slide.end": 0.5}


def specs():
    b = Builder("drivers", "drivers")
    G = "Monthly plan"
    b.series("spend_total", "Marketing spend", group=G, unit="dollars per month", key="drivers.spend_total", cells=40,
             sheet="P&L!W51:BJ51 (typed 350,000 from Oct-26)", used="Total marketing spend for planned months; split over channels by the channel mix; ad spend divided by cost per new customer gives new-customer orders.", also_used_by=("modeler",))
    b.series("nc_cpa", "Cost per new customer", group=G, unit="dollars per month", key="drivers.nc_cpa", cells=40, sheet="P&L!W14:BJ14 (typed 100, then 95)",
             used="Planned new-customer orders = ad spend / this cost; carried forward when a month has no value.", also_used_by=("modeler",))
    b.series("nc_aov", "New-customer average order value", group=G, unit="dollars per month", key="drivers.nc_aov", cells=39, sheet="P&L!X15:BJ15 (typed 69)",
             used="Planned new-customer revenue = orders x this value; carried forward when a month has no value.", also_used_by=("modeler",))
    b.series("amazon_rev", "Amazon revenue", group=G, unit="dollars per month", key="drivers.amazon_rev", cells=42, sheet="P&L!U19:BJ19 (typed; 175,000 from Oct-26)",
             used="Amazon revenue for planned months; feeds total revenue, Amazon fees and every revenue-based cost.")
    b.series("tiktok_rev", "TikTok Shop revenue", group=G, unit="dollars per month", key="drivers.tiktok_rev", cells=41, sheet="P&L row 20 (typed Jun-26 and Jul-26; blank after)",
             used="TikTok revenue for planned months; feeds total revenue.")
    b.series("ad_spend_override", "Ad spend typed over the computed value", group="Typed overrides", unit="dollars per month", key="drivers.ad_spend", cells=3,
             sheet="P&L!U11:V11 (typed Jul-26, Aug-26)", used="Replaces Meta plus Google ad spend (the cost-per-customer denominator) for that month.")
    b.series("nc_orders_override", "New-customer orders typed over the computed value", group="Typed overrides", unit="orders per month", key="drivers.nc_orders", cells=1,
             sheet="P&L!V13 (typed Aug-26)", used="Replaces planned or cohort-table new-customer orders for that month.", vt="number")
    b.series("nc_rev_override", "New-customer revenue typed over the computed value", group="Typed overrides", unit="dollars per month", key="drivers.nc_rev", cells=3,
             sheet="P&L!V12:X12 (typed Aug-Oct 26)", used="Replaces planned or cohort-table new-customer revenue for that month.")
    b.series("repeat_rev_override", "Repeat revenue typed over the waterfall", group="Typed overrides", unit="dollars per month", key="drivers.repeat_rev", cells=2,
             sheet="P&L!U17 (typed Jul-26) and V17 (=1499426-V12)", used="Replaces the cohort waterfall's repeat revenue for that month.")

    G = "Channel mix"
    for key, label, cell, val in CHANNELS:
        b.lever(f"channel_share.{key}", f"{label}: share of marketing spend", group=G, unit="fraction", vt="fraction", default=val, path=("drivers", "channel_share", key),
                lo=0, hi=1, step=0.005, sheet=f"{cell} (trailing actual mix)", sheet_cells=(("P&L", cell.split("!")[1]),),
                used="Splits planned marketing spend into channel lines; Meta plus Google (by default) is the ad spend behind cost per new customer.",
                notes=("The sheet's forward formula is =Facebook spend x 0.005 (P&L!V56:BJ56); the app uses this trailing mix instead." if key == "tiktok" else ""),
                ticket=("drivers#tiktok-ads-formula" if key == "tiktok" else None))
    b.lever("ad_spend_channels", "Channels counted as ad spend for cost per new customer", group=G, unit="channels", vt="list", default=["meta", "google"],
            path=("drivers", "ad_spend_channels"), choices=("amazon", "meta", "google", "bing", "tiktok"), sheet="P&L!X11 =sum(X53:X54) (Facebook + Google)",
            used="Planned new-customer orders = (ad spend in these channels) / cost per new customer.", sens=False, extra={"alt": ["meta"]})

    G = "Revenue rules"
    b.lever("sales_tax_rate", "Sales-tax estimate, share of DTC revenue", group=G, unit="fraction", vt="fraction", default=0.07, path=("drivers", "sales_tax_rate"), lo=0, hi=0.5, step=0.005,
            sheet="Formula literal 0.07 in P&L!V21:BJ21 (=-(DTC revenue x 0.07))", used="Deducted from DTC revenue in planned months to reach total company revenue; also the pacing revenue basis.",
            also_used_by=("pacing", "pnl"))

    G = "New-customer ROAS slide"
    b.lever("roas_slide.enabled", "Use the new-customer ROAS slide", group=G, unit="on or off", vt="bool", default=False, path=("drivers", "roas_slide", "enabled"),
            sheet="Retired Assumptions tab B8/B37/B38 (slide from 0.81x to 0.70x, set flat 0.70x on 2026-07-24)", used="When on, cost per new customer = order value / ROAS for months without a typed cost.", sens=False, extra={"setup": {k: v for k, v in SLIDE.items() if k != "drivers.roas_slide.enabled"}})
    b.lever("roas_slide.from", "ROAS slide: first month", group=G, unit="month", vt="month", default=None, path=("drivers", "roas_slide", "from"), sheet="Retired Assumptions tab (start month)",
            used="First month the slide governs.", sens=False, extra={"setup": SLIDE, "alt": "2026-12-01"})
    b.lever("roas_slide.to", "ROAS slide: last month", group=G, unit="month", vt="month", default=None, path=("drivers", "roas_slide", "to"), sheet="Retired Assumptions tab (end month)",
            used="Month the slide reaches its end value, then holds it.", sens=False, extra={"setup": SLIDE, "alt": "2027-12-01"})
    b.lever("roas_slide.start", "ROAS slide: starting new-customer ROAS", group=G, unit="multiple", vt="number", default=None, path=("drivers", "roas_slide", "start"), lo=0.05, hi=10, step=0.01,
            sheet="Retired Assumptions tab B8 (0.70x)", used="New-customer revenue per ad dollar in the first month of the slide.", sens=False, extra={"setup": SLIDE, "alt": 0.8})
    b.lever("roas_slide.end", "ROAS slide: ending new-customer ROAS", group=G, unit="multiple", vt="number", default=None, path=("drivers", "roas_slide", "end"), lo=0.05, hi=10, step=0.01,
            sheet="Retired Assumptions tab B37/B38 (0.70x)", used="New-customer revenue per ad dollar in the last month of the slide and after it.", sens=False, extra={"setup": SLIDE, "alt": 0.9})

    G = "Payback inputs"
    b.lever("payback.product_cost_pct", "Product cost, share of order value (payback)", group=G, unit="fraction", vt="fraction", default=0.1726,
            path=("drivers", "payback", "product_cost_pct"), lo=0, hi=0.99, step=0.005, sheet="Payback!D11", sheet_cells=(("Payback", "D11"),),
            used="Contribution per order = order value x (1 - this) - fulfillment: the Spend plan hint, the Cash and debt payback and the pacing payback box all read it.",
            also_used_by=("financing", "pacing"), sens=False)
    b.lever("payback.fulfillment_per_order", "Fulfillment cost per order (payback)", group=G, unit="dollars", vt="money", default=5.53,
            path=("drivers", "payback", "fulfillment_per_order"), lo=0, hi=1000, step=0.01, sheet="Payback!B12", sheet_cells=(("Payback", "B12"),),
            used="Contribution per order = order value x (1 - product cost) - this; read by the Spend plan hint, the Cash and debt payback and the pacing payback box.",
            also_used_by=("financing", "pacing"), sens=False)
    b.lever("payback.horizon_months", "Payback horizon", group=G, unit="months", vt="integer", default=36, path=("drivers", "payback", "horizon_months"), lo=1, hi=60, step=1,
            sheet="Payback!E20:E56 (36-month table)", used="Months of repeat curve counted before payback reads 'not within the horizon'; read by the Spend plan hint, the Cash and debt payback and the pacing payback box.",
            also_used_by=("financing", "pacing"), sens=False)

    G = "Page rules"
    b.lever("posture_band", "Growth or harvest band", group=G, unit="fraction", vt="fraction", default=0.10, path=("drivers", "posture_band"), lo=0.01, hi=1, step=0.01,
            sheet="No sheet cell (app rule)", used="Next-window spend more than this share above the window before reads as growth, below it as harvest.", status="PARTIAL", probe="view", sens=False,
            notes="Changes the sentence on the spend-plan page only.")
    b.lever("summary_months", "Months in the spend-plan summary windows", group=G, unit="months", vt="integer", default=12, path=("drivers", "summary_months"), lo=1, hi=36, step=1,
            sheet="No sheet cell (app rule)", used="Length of the two windows the spend-plan page compares.", status="PARTIAL", probe="view", sens=False, notes="Changes the spend-plan page summary only.")

    G = "Realized references (what the feeds say lately)"
    REF = "No sheet cell (app rule for the read-only feed references beside the plan)"
    b.lever("reference_days", "Days in the realized-reference window", group=G, unit="days", vt="integer", default=28, path=("drivers", "reference_days"), lo=7, hi=90, step=1, sheet=REF,
            used="Realized cost per new customer, order value and the Amazon run-rate are read over this many complete days.", sens=False, probe="view",
            notes="Moves the references shown beside the plan and the payback defaults, not the plan itself.")
    b.lever("reference_tolerance", "How far a typed plan may sit from its reference before it gets a note", group=G, unit="fraction", vt="fraction", default=0.05,
            path=("drivers", "reference_tolerance"), lo=0.01, hi=1, step=0.01, sheet=REF, used="A typed cost per customer or order value further than this from the realized figure gets a warning chip.", sens=False, probe="view")
    b.lever("reference_gap_days", "Feed days that may be missing from the reference window", group=G, unit="days", vt="integer", default=2, path=("drivers", "reference_gap_days"), lo=0, hi=14, step=1, sheet=REF,
            used="A realized figure is offered only when every counted feed has at least the window's days minus this many; otherwise it is withheld with the reason.", sens=False, probe="view",
            also_used_by=("pacing", "financing"))
    for key, label, default, lo, hi, step, sib, unit, vt in (
            ("reference_cpa_min", "Lowest believable realized cost per new customer", 20.0, 0.01, 99999, 5, ("drivers", "reference_cpa_max"), "dollars", "money"),
            ("reference_cpa_max", "Highest believable realized cost per new customer", 500.0, 0.01, 100000, 10, ("drivers", "reference_cpa_min"), "dollars", "money"),
            ("reference_aov_min", "Lowest believable realized new-customer order value", 15.0, 0.01, 99999, 1, ("drivers", "reference_aov_max"), "dollars", "money"),
            ("reference_aov_max", "Highest believable realized new-customer order value", 400.0, 0.01, 100000, 5, ("drivers", "reference_aov_min"), "dollars", "money")):
        sib_default = {"reference_cpa_min": 20.0, "reference_cpa_max": 500.0, "reference_aov_min": 15.0, "reference_aov_max": 400.0}[sib[1]]
        b.lever(key, label, group=G, unit=unit, vt=vt, default=default, path=("drivers", key), lo=lo, hi=hi, step=step, sheet=REF,
                used="A realized figure outside the believable range is treated as a feed problem and withheld with the reason, so a bad feed cannot set a default.", sens=False, probe="view",
                also_used_by=("pacing", "financing"), extra={"pair": (sib, sib_default)},
                notes="Saved together with its partner bound (the module accepts the two as a pair); resetting one resets both.")

    G = "Sheet items the app does not carry"
    b.missing("pacing_blend", "Share of month-to-date pacing blended into the current month's repeat revenue", group=G, unit="fraction", default=0.5,
              sheet="P&L!W17 (0.5 x waterfall + 0.5 x pacing projection, one cell, Sep-26)", used="Blends the waterfall with the pacing projection for one month.",
              notes="Wired to a single sheet column, so it goes stale the next month. The app uses the waterfall for planned months and loaded actuals for closed ones.")
    b.missing("amazon_scenario_haircut", "Amazon scenario haircut", group=G, unit="fraction", default=None, sheet="P&L!A6 (reads the deleted 'Assumptions - DEAD' tab)",
              used="Scaled Amazon revenue by scenario in the old model.", notes="Dead in the sheet (the scenario selector was removed); scenarios are named copies now.")
    return b.specs
