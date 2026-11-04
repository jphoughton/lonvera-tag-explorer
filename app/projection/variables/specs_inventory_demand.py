"""Variables of module `inventory_demand`: baseline window, growth, seasonality and per-product rates for each shipping location (sheet *Fcst Inputs tabs)."""
from __future__ import annotations

from .build import Builder

LOCS = (("revamp", "US direct (Revamp, Utah)", "Warehance Fcst Inputs", 28, "B4", "B7", ("bull", "liver", "boneflex", "belle")),
        ("amazon", "Amazon", "Amazon Fcst Inputs", 30, "B4", "B8", ("bull", "liver", "boneflex", "belle")),
        ("intl", "International", "Intl Fcst Inputs", 30, "B4", "B8", ("bull", "liver", "boneflex", "flow")))
PRODUCTS = {"bull": "Vital Bull", "liver": "Beef Liver", "boneflex": "Boneflex", "belle": "Vital Belle", "flow": "Vital Flow"}


def _season_validator(v):
    from ..inventory_demand import levers
    return levers._check_season(v, "seasonality")


def specs():
    b = Builder("inventory_demand", "inventory-demand")
    b.lever("as_of", "Forecast date", group="Timing", unit="date", vt="date", default=None, path=("inventory_demand", "as_of"), sheet="TODAY() in every Fcst Inputs tab",
            used="Pins the month the forecast starts in (blank = today, Pacific time); months before it use loaded actuals.", sens=False, extra={"alt": "2026-12-01"})
    for loc, name, tab, days, c_days, c_growth, prods in LOCS:
        G = name
        b.lever(f"{loc}.baseline_days", f"{name}: days averaged into the baseline", group=G, unit="days", vt="integer", default=days, path=("inventory_demand", "locations", loc, "baseline_days"),
                lo=1, hi=365, step=1, sheet=f"{tab}!{c_days}", sheet_cells=((tab, c_days),), used="Units per day = units shipped in the last N complete days / N; every forecast month starts from it.")
        b.lever(f"{loc}.lag_days", f"{name}: extra newest days ignored", group=G, unit="days", vt="integer", default=0, path=("inventory_demand", "locations", loc, "lag_days"), lo=0, hi=60, step=1,
                sheet=f"{tab}!B5 (reporting lag; Amazon types 2, the others 0 or 1)" if loc != "revamp" else "Warehance Fcst Inputs!B5 (=MAX(date)-1)",
                used="Drops this many of the newest complete days from the baseline window (feeds revise late).",
                notes="The sheet's own lag is now the loader's partial-day rule (one day for Warehance and Amazon), so the default here is 0.")
        b.lever(f"{loc}.monthly_growth", f"{name}: monthly growth in units", group=G, unit="fraction a month", vt="fraction", default=0.0, path=("inventory_demand", "locations", loc, "monthly_growth"),
                lo=-0.5, hi=1, step=0.005, sheet=f"{tab}!{c_growth}", sheet_cells=((tab, c_growth),), used="Compounds the baseline each month after the forecast month.")
        b.lever(f"{loc}.seasonality", f"{name}: seasonality index by calendar month", group=G, unit="12 numbers, January to December", vt="list", default=[1.0] * 12,
                path=("inventory_demand", "locations", loc, "seasonality"), sheet=f"{tab}!B19:B30 (or B20:B31)" , used="Multiplies the baseline in each calendar month; 1.00 means no adjustment.",
                validator=_season_validator, sens=False, extra={"alt": [1.0] * 9 + [1.0, 1.2, 1.2]})
        b.lever(f"{loc}.tie_to_dtc_revenue", f"{name}: scale units with projected DTC revenue", group=G, unit="on or off", vt="bool", default=False,
                path=("inventory_demand", "locations", loc, "tie_to_dtc_revenue"), sheet="No sheet cell (added)", used="When on, forward units follow projected new-customer plus repeat revenue per day against the baseline window.",
                sens=False)
        for p in prods:
            b.lever(f"{loc}.{p}.rate", f"{name}, {PRODUCTS[p]}: units per day typed over the baseline", group=G, unit="units per day", vt="number", default=None,
                    path=("inventory_demand", "locations", loc, "products", p, "rate"), lo=0, hi=100000, step=1, sheet=f"{tab} baseline units/day (computed from the daily feed)",
                    used="Replaces the trailing baseline for this product; blank keeps the computed average.", extra={"computed": "demand_rate", "loc": loc, "product": p})
            b.lever(f"{loc}.{p}.monthly_growth", f"{name}, {PRODUCTS[p]}: monthly growth typed over the location's", group=G, unit="fraction a month", vt="fraction", default=None,
                    path=("inventory_demand", "locations", loc, "products", p, "monthly_growth"), lo=-0.5, hi=1, step=0.005, sheet="No sheet cell (added)",
                    used="Growth for this product only; blank uses the location's growth.", sens=False,
                    extra={"alt": 0.05, "setup": {f"inventory_demand.{loc}.{p}.rate": 10.0}})
    G = "Stock and feeds"
    b.table("stock_counts", "Units on hand at each warehouse (dated counts)", group=G, sheet="Warehance Fcst Inputs!A35:D39, Amazon Fcst Inputs!A36:B40, Intl Fcst Inputs!A36:D40", scope="all",
            used="Opening stock for the burn tables (owned by inventory supply).", unit="counts", page="inventory-supply")
    for loc, name in (("revamp", "US direct"), ("amazon", "Amazon"), ("intl", "International")):
        tail = {"revamp": 1, "amazon": 1, "intl": 0}[loc]
        b.missing(f"feed.partial_tail_days.{loc}", f"{name}: newest days held back as not final", group=G, unit="days", vt="integer", default=tail, scope="feed",
                  sheet={"revamp": "Warehance Fcst Inputs!B5 (=MAX(date)-1)", "amazon": "Amazon Fcst Inputs!B5 (typed 2)", "intl": "Intl Fcst Inputs!B5 (typed 0)"}[loc],
                  used="Loader rule: the newest days of a feed are not stored until complete.", ticket="inventory_demand#feed-rules-constants",
                  notes="Hard-coded in constants.PARTIAL_TAIL_DAYS; not editable.")
    b.missing("feed.stale_after_days", "Feed counts as stale after (US direct 2 days, Amazon 2, International 4)", group=G, unit="days", vt="integer", default=2, scope="feed",
              sheet="No sheet cell (the sheet showed no feed age)", used="Age at which a shipping feed turns amber and then fails the daily check.", ticket="inventory_demand#feed-rules-constants",
              notes="Hard-coded in constants.STALE_AFTER_DAYS; not editable.")
    b.missing("feed.intl_estimate_factor", "International units as a share of Warehance non-US orders", group=G, unit="fraction", default=0.89, scope="feed",
              sheet="Estimate rule in the sheet's refresh job (Warehance non-US orders x 0.89)", used="Models international shipping days no feed reports yet.",
              ticket="inventory_demand#feed-rules-constants", notes="Computed outside the app; the app only flags estimated days.")
    return b.specs
