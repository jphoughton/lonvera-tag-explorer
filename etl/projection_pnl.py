"""Loaders for the projection `pnl` module.

    python -m etl.projection_pnl seed [--snapshot DIR]     # QuickBooks history, driver defaults, settings, COGS inputs, QuickBooks map, vendors
                                                           # from the frozen sheet snapshot (idempotent; typed inputs carry their sheet cell as source)
    python -m etl.projection_pnl freshness                 # print the feed freshness entries

QuickBooks history is loaded by monthly upload (POST /api/projection/pnl/upload), so there is no daily refresh hook here, only `freshness`.
The seeder writes only scenario-independent data (proj_actuals 'pnl.<line>', proj_pnl_*). It never touches a scenario.
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.projection import series                                       # noqa: E402
from app.projection.interfaces import MONTHS                            # noqa: E402
from app.projection.pnl import lines as L                               # noqa: E402
from app.projection.pnl import store                                    # noqa: E402

TAB = "P&L"
FIRST_COL, LAST_QB_COL = "C", "U"                                       # Jan-25 .. Jul-26 hold typed history in the sheet
BOOKS_SOURCE = "QuickBooks values pasted in the Lonvera Projections P&L (snapshot 2026-11-03)"
TYPED_SOURCE = "typed in the Lonvera Projections P&L, Jul-26 (not a QuickBooks close)"
TYPED_MONTH = date(2026, 7, 1)


def _sr():
    for name in ("scripts.projection.snapshot_read", "snapshot_read"):
        try:
            import importlib
            return importlib.import_module(name)
        except Exception:
            continue
    sys.path.insert(0, str(ROOT / "scripts" / "projection"))
    import snapshot_read
    return snapshot_read


def _num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


# ---------------------------------------------------------------------------------------------------------------- actuals

def history_rows(sr) -> tuple[list[tuple[str, date, float]], list[tuple[str, date, float]]]:
    """(books rows, typed Jul-26 rows) of (proj_actuals key, month, value) from the sheet's typed history cells."""
    books: list[tuple[str, date, float]] = []
    typed: list[tuple[str, date, float]] = []
    months = [sr.serial_to_date(x) for x in sr.row(TAB, 8, FIRST_COL, LAST_QB_COL)]
    for key in L.QB_KEYS:
        ln = L.LINE_KEYS[key]
        if ln.row is None:
            continue
        for m, val in zip(months, sr.row(TAB, ln.row, FIRST_COL, LAST_QB_COL)):
            if not _num(val):
                continue
            (typed if m == TYPED_MONTH else books).append((L.actuals_key(key), m, float(val)))
    # Inventory shrinkage has no row of its own: the sheet adds it inside the Feb-25 total (D46 ends "+22631.4")
    f = sr.cell(TAB, "D46", "formulas")
    mt = re.search(r"\+\s*([0-9]+(?:\.[0-9]+)?)\s*$", f) if isinstance(f, str) else None
    if mt:
        books.append((L.actuals_key("inventory_shrinkage"), date(2025, 2, 1), float(mt.group(1))))
    return books, typed


# ---------------------------------------------------------------------------------------------------------------- driver defaults

def _rate(base: str, **kw) -> dict:
    return {"from": None, "mode": "rate", "base": base, "rate_kind": "window", **kw}


def build_drivers(sr) -> list[tuple[str, list[dict], str, str]]:
    """(line key, segments, note, source) for every driven line, read from the sheet's forward columns (V = Aug-26, W = Sep-26, X = Oct-26)."""
    def cell(ref):
        v = sr.cell(TAB, ref)
        return float(v) if _num(v) else 0.0

    def fixed(key, ref, note=None):
        return (key, [{"from": None, "mode": "fixed", "value": cell(ref)}], note or "Typed in the sheet", f"P&L!{ref}")

    out: list[tuple[str, list[dict], str, str]] = []
    for key, base in (("amazon_fba_fees", "amazon_rev"), ("amazon_seller_fees", "amazon_rev"), ("amazon_inbound_transport", "amazon_rev"),
                      ("fulfillment_services", "dtc_rev"), ("shopify_subscriptions", "dtc_rev"), ("seller_fees", "dtc_rev"),
                      ("chargeback_management", "dtc_rev"), ("memberships", "dtc_rev"), ("inbound_shipping", "total_revenue"),
                      ("marketing_software", "total_revenue"), ("software_apps", "total_revenue"), ("other_business_expenses", "total_revenue")):
        row = L.LINE_KEYS[key].row
        out.append((key, [_rate(base)], "Trailing actual months, line divided by its revenue base", f"P&L!B{row}"))
    out.append(("china_cogs", [{"from": None, "mode": "rate", "base": "total_revenue", "rate_kind": "cogs_model"},
                               {"from": "2026-08-01", "mode": "rate", "base": "total_revenue", "rate_kind": "ratio_avg", "window": 3},
                               {"from": "2026-09-01", "mode": "rate", "base": "total_revenue", "rate_kind": "cogs_model"}],
                "COGS model rate; Aug-26 uses the mean monthly ratio of the last 3 actual months, as the sheet does", "P&L!B32, V32"))
    out.append(("us_cogs", [{"from": None, "mode": "fixed", "value": 0.0}], "No US supply chain cost projected", "P&L!W33"))
    out.append(("inventory_shrinkage", [{"from": None, "mode": "fixed", "value": 0.0}], "One-off in Feb-25, none projected", "P&L!D46"))
    out.append(fixed("amazon_subscriptions", "W27", "Amazon seller subscription"))
    out.append(fixed("shipping_supplies", "W42", "Discontinued"))
    out.append(("marketing_agency", [{"from": None, "mode": "carry"}], "Last month's amount carried forward", "P&L!V60"))
    out.append(fixed("marketing_services", "W61"))
    out.append(("marketing_influencers", [{"from": None, "mode": "fixed", "value": 0.0}], "None projected", "P&L!W62"))
    out.append(("headcount", [{"from": None, "mode": "headcount"}], "Formula in the settings (base, step), from the start month", "P&L!W75"))
    out.append(fixed("accounting", "W79"))
    out.append(("consulting", [{"from": None, "mode": "fixed", "value": cell("V81")}, {"from": "2026-10-01", "mode": "fixed", "value": cell("X81")}],
                "Starts Oct-26", "P&L!X81"))
    out.append(fixed("customer_service", "W82"))
    out.append(("insurance", [{"from": None, "mode": "fixed", "value": cell("V83")}, {"from": "2026-09-01", "mode": "fixed", "value": cell("W83")}],
                "Higher policy from Sep-26", "P&L!W83"))
    out.append(("interest_expense", [{"from": None, "mode": "fixed", "value": 0.0}], "None projected", "P&L!W84"))
    out.append(("legal", [{"from": None, "mode": "fixed", "value": cell("V85")}, {"from": "2026-10-01", "mode": "fixed", "value": cell("X85")}],
                "Starts Oct-26", "P&L!X85"))
    out.append(fixed("office_admin", "W87"))
    out.append(("outside_services", [{"from": None, "mode": "avg", "window": 5}, {"from": "2026-09-01", "mode": "fixed", "value": cell("W88")}],
                "Aug-26 is the mean of the previous 5 months; fixed from Sep-26", "P&L!V88, W88"))
    out.append(fixed("sales_tax_filing", "W89"))
    out.append(fixed("samples", "W90"))
    out.append(fixed("uncategorized", "W94"))
    return out


def build_settings(sr) -> list[tuple[str, dict, str]]:
    f = sr.cell(TAB, "W75", "formulas") or ""
    hc = {"base": 15000.0, "threshold": 20_000_000.0, "step": 5_000_000.0, "increment": 15000.0, "start": "2026-09-01"}
    # =15000+FLOOR(MAX(0,W22*12-20000000)/5000000)*15000
    m = re.match(r"=\s*(\d+)\s*\+\s*FLOOR\(\s*MAX\(\s*0\s*,\s*\w+\*12\s*-\s*(\d+)\s*\)\s*/\s*(\d+)\s*\)\s*\*\s*(\d+)", f, re.I)
    if m:
        hc.update(base=float(m.group(1)), threshold=float(m.group(2)), step=float(m.group(3)), increment=float(m.group(4)))
    first_hc = next((m for m, v in zip(MONTHS, sr.row(TAB, 75, "C", "BJ", "formulas")) if v), None)
    if first_hc:
        hc["start"] = first_hc.isoformat()
    cogs = {"us_share": sr.cell("COGS", "N24") if _num(sr.cell("COGS", "N24")) else 0.7,
            "price_per_bottle": sr.cell("COGS", "L38") if _num(sr.cell("COGS", "L38")) else 29.22,
            "current_price_per_bottle": sr.cell("COGS", "L37") if _num(sr.cell("COGS", "L37")) else 21.62, "mix": {}, "china_cost": {}, "us_cost": {}}
    infl = lambda ref: float(sr.cell(TAB, ref)) if _num(sr.cell(TAB, ref)) else 0.0           # noqa: E731
    mt = re.search(r"COUNT\([^)]*\)\s*=\s*(\d+)", str(sr.cell(TAB, "H145", "formulas") or ""))          # =IF(COUNT(C144:H144)=6,AVERAGE(C144:H144),"")
    trend = {"avg_window": int(mt.group(1)) if mt else 6}
    return [("engine", {"rate_window_months": 4, "opex_inflation": infl("A4"), "cogs_inflation": infl("A5")},
             "Trailing window for rate drivers; inflation (annual) applies to projected months"),
            ("headcount", hc, "P&L!W75: base plus a step each time annualised revenue passes the threshold"),
            ("cogs", cogs, "COGS tab: China/US split N24, forward price per bottle L38 (old average), current price L37"),
            ("ev", {"multiples": [3, 4, 5, 6, 7, 8], "year": 2027, "loi": 7_500_000.0}, "P&L rows 159-162; LOI per the sheet note"),
            ("trend", trend, "P&L!H145: months in the growth-rate averages (rows 145, 147, 150, 152, 155, 157)"),
            ("freshness", {"stale_after_days": 45}, "QuickBooks is late when the first month not loaded closed more than this many days ago"),
            ("upload", {"low_ratio": 0.5, "high_ratio": 2.0, "required_families": ["revenue", "cost_of_goods", "fulfillment", "marketing_spend", "operating_expenses"]},
             "Not in the sheet: account families a QuickBooks month needs to become books, and how far from its neighbours it may stray before it needs a confirmation")]


def build_cogs_sku(sr) -> list[dict]:
    out = []
    for i, r in enumerate(range(4, 16)):
        sku = sr.cell("COGS", f"A{r}")
        if not sku:
            continue
        out.append({"sku": str(sku), "label": str(sr.cell("COGS", f"B{r}") or sku), "china_unit_cost": float(sr.cell("COGS", f"D{r}") or 0),
                    "us_unit_cost": float(sr.cell("COGS", f"D{r + 28}") or 0), "sales_mix": float(sr.cell("COGS", f"F{r}") or 0),
                    "in_stock_qty": sr.cell("COGS", f"C{r}"), "sort": i, "note": "COGS tab rows 4-15 (China) and 32-43 (US)"})
    return out


def build_vendors(sr) -> list[dict]:
    out = []
    for r in range(2, 12):
        name = sr.cell("Fixed Expenses", f"B{r}")
        if not name:
            continue
        cur, prop = sr.cell("Fixed Expenses", f"C{r}"), sr.cell("Fixed Expenses", f"D{r}")
        out.append({"vendor": str(name).strip(), "current": float(cur) if _num(cur) else None, "proposed": float(prop) if _num(prop) else None,
                    "note": "Fixed Expenses tab; planning only, not wired into the P&L"})
    return out


# QuickBooks account names as they appear in the sheet, plus spellings seen in the sheet; revenue accounts are mapped by the user on first upload
_ALIASES: dict[str, str] = {
    "other business expenses": "other_direct", "amazon adds": "mkt_amazon_ads", "facebook adds": "mkt_facebook_ads", "google adds": "mkt_google_ads",
    "microsoft bing adds": "mkt_bing_ads", "tiktok adds": "mkt_tiktok_ads", "amazon ads": "mkt_amazon_ads", "facebook ads": "mkt_facebook_ads",
    "google ads": "mkt_google_ads", "microsoft bing ads": "mkt_bing_ads", "bing ads": "mkt_bing_ads", "tiktok ads": "mkt_tiktok_ads",
    "total dtc revenue": "dtc_rev", "amazon revenue": "amazon_rev", "tiktok revenue": "tiktok_rev",
}


def build_qb_map(sr) -> list[tuple[str, str, str]]:
    out: dict[str, tuple[str, str]] = {}
    for key in L.QB_KEYS:
        ln = L.LINE_KEYS[key]
        names = {ln.label}
        if ln.row is not None:
            raw = sr.cell(TAB, f"A{ln.row}")
            if raw:
                names.add(str(raw).strip())
        for n in names:
            out[store.norm_account(n)] = (key, "Sheet label")
    for acct, key in _ALIASES.items():
        out[store.norm_account(acct)] = (key, "Spelling variant")
    out.pop(store.norm_account("Other business expenses"), None)         # the parent name; a line with numbers under that name is the direct posting
    out[store.norm_account("Other Business Expenses")] = ("other_direct", "Direct postings to the parent account")
    return [(a, k, n) for a, (k, n) in sorted(out.items())]


# ---------------------------------------------------------------------------------------------------------------- seed

def seed(conn, sr=None) -> dict:
    """Idempotent. Returns counts."""
    sr = sr or _sr()
    books, typed = history_rows(sr)
    n_books = series.upsert_actuals(conn, books, BOOKS_SOURCE, actor="system")
    n_typed = series.upsert_actuals(conn, typed, TYPED_SOURCE, actor="system")
    n_drv = store.upsert_driver_defaults(conn, build_drivers(sr))
    n_set = store.upsert_settings(conn, build_settings(sr))
    n_sku = store.upsert_cogs_sku(conn, build_cogs_sku(sr))
    n_ven = store.upsert_vendors(conn, build_vendors(sr))
    n_map = store.upsert_qb_map(conn, build_qb_map(sr))
    return {"actuals_books": n_books, "actuals_typed": n_typed, "drivers": n_drv, "settings": n_set, "cogs_sku": n_sku, "vendors": n_ven, "qb_map": n_map}


# ---------------------------------------------------------------------------------------------------------------- freshness

def freshness(conn, today: date | None = None) -> list[dict]:
    """QuickBooks P&L feed for the shared freshness check (monthly upload; see app.projection.pnl.store.freshness_entries)."""
    return store.freshness_entries(conn, today)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="pnl module loaders")
    ap.add_argument("command", choices=("seed", "freshness"))
    ap.add_argument("--snapshot", help="snapshot directory (default: PROJECTION_SNAPSHOT_DIR or the frozen 2026-11-03 snapshot)")
    args = ap.parse_args(argv)
    import os
    if args.snapshot:
        os.environ["PROJECTION_SNAPSHOT_DIR"] = args.snapshot
    from . import db
    with db.connect() as conn:
        from psycopg.rows import dict_row
        conn.row_factory = dict_row
        if args.command == "seed":
            res = seed(conn)
            conn.commit()
            print(res)
        else:
            print(freshness(conn))
    return 0


if __name__ == "__main__":
    sys.exit(main())
