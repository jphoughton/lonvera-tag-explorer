"""Loaders for the projection module inventory_supply.

    python -m etl.projection_inventory_supply seed [--replace] [--snapshot-dir DIR]   one-time seed from the frozen sheet snapshot (idempotent)
    python -m etl.projection_inventory_supply refresh [--days 14]                     append today's Revamp stock count read from the sheet (read-only)
    python -m etl.projection_inventory_supply freshness                               print the freshness of the manual inputs

`seed` inserts what is missing and never overwrites a row (so edits made in the app survive a re-run); `--replace` first deletes the rows that came
from the sheet (those with a sheet row) and the lookup tables, then loads them again. Ids of seeded purchase orders and transfers are their sheet rows.
`refresh` is the only daily feed: Revamp (Warehance) on-hand and allocated units, read from the `Warehance Fcst Inputs` snapshot cells that the sheet's
Warehance sync writes every day (service account, read-only, env GOOGLE_SA_JSON). FBA and international 3PL counts are manual (POST .../stock).
"""
from __future__ import annotations

import argparse
import importlib
import json
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
EPOCH = date(1899, 12, 30)

# Sheet SKU -> product (Inventory Value / COGS tab).
SKU_PRODUCT = {"LV-VBULL": "bull", "LV-BLC180": "liver", "DPCD1-GN187001": "boneflex", "LV-VBELLE": "belle", "DPCD1-GN179001": "flow"}
PRODUCT_ORDER = ("bull", "liver", "boneflex", "belle", "flow")
# Vendor facts the sheet keeps outside any tab: minimum order and the location each vendor supplies (2026-10-13 rebalance).
VENDOR_FACTS = {"China": {"moq_units": 5000.0, "default_warehouse": "intl"}, "KeyStone": {"moq_units": 20000.0, "default_warehouse": "revamp"}}
FACTS_NOTE = "Minimum order and supplied location from the 2026-10-13 inventory rebalance; lead time from the sheet's Vendor COGS tab."


# ---------------------------------------------------------------------------------------------------------------- snapshot access

def snapshot_reader():
    try:
        return importlib.import_module("scripts.projection.snapshot_read")
    except Exception:
        sys.path.insert(0, str(ROOT / "scripts" / "projection"))
        return importlib.import_module("snapshot_read")


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _serial(v: Any) -> date | None:
    return EPOCH + timedelta(days=int(v)) if _is_num(v) else None


def _product(name: Any) -> str | None:
    from app.projection.inventory_supply.model import normalize_product
    return normalize_product(name)


def _yesno(v: Any) -> bool | None:
    s = str(v or "").strip().lower()
    return True if s == "yes" else False if s == "no" else None


# ---------------------------------------------------------------------------------------------------------------- seed

def read_snapshot(sr=None) -> dict:
    """Everything seed needs, as plain python, from the frozen sheet snapshot."""
    sr = sr or snapshot_reader()
    out: dict[str, Any] = {}
    # vendors and price list
    vend = []
    for r in range(4, 12):
        v, w = sr.cell("Vendor COGS", f"G{r}"), sr.cell("Vendor COGS", f"H{r}")
        if isinstance(v, str) and v.strip() and _is_num(w):
            vend.append({"vendor": v.strip(), "lead_weeks": float(w)})
    out["vendors"] = vend
    costs = []
    for r in range(4, 60):
        vendor, prod, cost = sr.cell("Vendor COGS", f"B{r}"), _product(sr.cell("Vendor COGS", f"C{r}")), sr.cell("Vendor COGS", f"D{r}")
        if isinstance(vendor, str) and prod and _is_num(cost):
            costs.append({"vendor": vendor.strip(), "product": prod, "unit_cost": float(cost)})
    out["vendor_costs"] = costs
    # unit costs used to value stock (COGS tab, by SKU)
    pcs, seen = [], set()
    for r in range(4, 40):
        sku = sr.cell("COGS", f"A{r}")
        prod = SKU_PRODUCT.get(str(sku).strip()) if sku else None
        if prod and prod not in seen:                                  # the first row of a SKU wins, as the sheet's MATCH does
            seen.add(prod)
            c, us = sr.cell("COGS", f"D{r}"), sr.cell("COGS", f"H{r}")
            pcs.append({"product": prod, "sku": str(sku).strip(), "china_unit_cost": float(c) if _is_num(c) else 0.0,
                        "us_unit_cost": float(us) if _is_num(us) else None})
    out["product_costs"] = pcs
    # reorder parameters (lead time, safety stock, minimum order per SKU)
    rp = []
    for loc, tab, first, n in (("revamp", "Warehance Fcst Inputs", 44, 4), ("intl", "Intl Fcst Inputs", 45, 4)):
        for r in range(first, first + n):
            prod = _product(sr.cell(tab, f"A{r}"))
            b, c, d = sr.cell(tab, f"B{r}"), sr.cell(tab, f"C{r}"), sr.cell(tab, f"D{r}")
            if prod and _is_num(b):
                rp.append({"location": loc, "product": prod, "lead_days": int(b), "safety_days": int(c) if _is_num(c) else 14,
                           "moq_units": float(d) if _is_num(d) else 0.0})
    out["reorder"] = rp
    # stock counts
    stock = []
    d = _serial(sr.cell("Warehance Fcst Inputs", "B39"))
    for r in range(35, 39):
        prod = _product(sr.cell("Warehance Fcst Inputs", f"A{r}"))
        oh, al = sr.cell("Warehance Fcst Inputs", f"B{r}"), sr.cell("Warehance Fcst Inputs", f"C{r}")
        if prod and d and _is_num(oh):
            stock.append({"location": "revamp", "product": prod, "as_of": d, "on_hand": float(oh), "allocated": float(al) if _is_num(al) else 0.0,
                          "inbound_per_3pl": 0.0, "estimate": False, "note": "Warehance on hand and allocated, from Warehance Fcst Inputs."})
    d = _serial(sr.cell("Amazon Fcst Inputs", "B40"))
    for r in range(36, 40):
        prod, oh = _product(sr.cell("Amazon Fcst Inputs", f"A{r}")), sr.cell("Amazon Fcst Inputs", f"B{r}")
        if prod and d and _is_num(oh):
            stock.append({"location": "amazon", "product": prod, "as_of": d, "on_hand": float(oh), "allocated": 0.0, "inbound_per_3pl": 0.0,
                          "estimate": False, "note": str(sr.cell("Amazon Fcst Inputs", "C40") or "")[:480] or None})
    d = _serial(sr.cell("Intl Fcst Inputs", "B40"))
    note = str(sr.cell("Intl Fcst Inputs", "C40") or "")[:480] or None
    for r in range(36, 40):
        prod, oh = _product(sr.cell("Intl Fcst Inputs", f"A{r}")), sr.cell("Intl Fcst Inputs", f"B{r}")
        if prod and d and _is_num(oh):
            pend, inb = sr.cell("Intl Fcst Inputs", f"C{r}"), sr.cell("Intl Fcst Inputs", f"D{r}")
            stock.append({"location": "intl", "product": prod, "as_of": d, "on_hand": float(oh), "allocated": float(pend) if _is_num(pend) else 0.0,
                          "inbound_per_3pl": float(inb) if _is_num(inb) else 0.0, "estimate": True, "note": note})
    out["stock"] = stock
    # purchase orders
    pos = []
    lead = {v["vendor"]: v["lead_weeks"] for v in vend}
    price = {(c["vendor"], c["product"]): c["unit_cost"] for c in costs}
    n_rows = sr.load("Purchase Orders")["rows"]
    for r in range(2, n_rows + 1):
        prod, units, land = _product(sr.cell("Purchase Orders", f"B{r}")), sr.cell("Purchase Orders", f"E{r}"), _serial(sr.cell("Purchase Orders", f"G{r}"))
        if not prod or not _is_num(units) or not land:
            continue
        vendor = str(sr.cell("Purchase Orders", f"C{r}") or "").strip()
        wh = "intl" if str(sr.cell("Purchase Orders", f"D{r}") or "").strip() == "International 3PL" else "revamp"
        order = _serial(sr.cell("Purchase Orders", f"F{r}"))
        derived = land - timedelta(days=7 * lead[vendor]) if vendor in lead else None
        po_no = sr.cell("Purchase Orders", f"A{r}")
        cost = sr.cell("Purchase Orders", f"L{r}")
        dep = sr.cell("Purchase Orders", f"P{r}")
        note = sr.cell("Purchase Orders", f"O{r}")
        link = sr.cell("Purchase Orders", f"R{r}")
        pos.append({"id": r, "sheet_row": r, "po_number": str(po_no) if po_no is not None else None, "product": prod, "vendor": vendor,
                    "warehouse": wh, "units": float(units), "order_date": None if order == derived else order, "land_date": land,
                    "status": str(sr.cell("Purchase Orders", f"H{r}") or "planned").strip().lower(),
                    "deposit_paid": _yesno(sr.cell("Purchase Orders", f"I{r}")), "shipping_paid": _yesno(sr.cell("Purchase Orders", f"J{r}")),
                    "balance_paid": _yesno(sr.cell("Purchase Orders", f"K{r}")), "deposit_pct": float(dep) if _is_num(dep) else None,
                    "unit_cost": float(cost) if _is_num(cost) and abs(float(cost) - price.get((vendor, prod), -1)) > 1e-9 else None,
                    "link": str(link) if link else None, "note": str(note) if note else None,
                    "source": "auto-plan" if str(note or "").startswith("Auto-plan") else "manual"})
    out["pos"] = pos
    # FBA transfers
    trs = []
    n_rows = sr.load("FBA Transfers")["rows"]
    for r in range(4, n_rows + 1):
        prod, units = _product(sr.cell("FBA Transfers", f"B{r}")), sr.cell("FBA Transfers", f"C{r}")
        ship, recv = _serial(sr.cell("FBA Transfers", f"D{r}")), _serial(sr.cell("FBA Transfers", f"E{r}"))
        if not prod or not _is_num(units) or not ship or not recv:
            continue
        no, note = sr.cell("FBA Transfers", f"A{r}"), sr.cell("FBA Transfers", f"G{r}")
        trs.append({"id": r, "sheet_row": r, "transfer_no": str(no) if no else None, "product": prod, "units": float(units), "ship_date": ship,
                    "receive_date": recv, "status": str(sr.cell("FBA Transfers", f"F{r}") or "planned").strip().lower(),
                    "note": str(note) if note else None, "source": "auto-plan" if str(note or "").startswith("Auto-plan") else "manual"})
    out["transfers"] = trs
    return out


def _default_deposits(pos: list[dict]) -> dict[tuple[str, str], float]:
    """Deposit percentage new orders default to: the most common value on that vendor x product's orders."""
    seen: dict[tuple[str, str], Counter] = {}
    for p in pos:
        if p["deposit_pct"] is not None and p["status"] != "cancelled":
            seen.setdefault((p["vendor"], p["product"]), Counter())[p["deposit_pct"]] += 1
    return {k: c.most_common(1)[0][0] for k, c in seen.items()}


def seed(conn, snapshot: dict | None = None, *, replace: bool = False) -> dict:
    """Load the sheet's facts. Returns inserted counts. Existing rows are left alone unless `replace`."""
    from psycopg.types.json import Jsonb  # noqa: F401  (kept for parity with other loaders)
    data = snapshot if snapshot is not None else read_snapshot()
    if replace:
        for t in ("proj_po", "proj_fba_transfer"):
            conn.execute(f"DELETE FROM {t} WHERE sheet_row IS NOT NULL")                     # noqa: S608 (constant table names)
        for t in ("proj_vendor_cogs", "proj_vendor_lead", "proj_product_cost", "proj_reorder_param", "proj_stock_snapshot"):
            conn.execute(f"DELETE FROM {t}")                                                  # noqa: S608
    n = Counter()

    def ins(sql: str, params: tuple, key: str) -> None:
        cur = conn.execute(sql, params)
        n[key] += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0

    for v in data["vendors"]:
        facts = VENDOR_FACTS.get(v["vendor"], {})
        ins("INSERT INTO proj_vendor_lead (vendor, lead_weeks, moq_units, default_warehouse, note) VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            (v["vendor"], v["lead_weeks"], facts.get("moq_units", 0.0), facts.get("default_warehouse", "revamp"), FACTS_NOTE), "vendors")
    dep = _default_deposits(data["pos"])
    for c in data["vendor_costs"]:
        ins("INSERT INTO proj_vendor_cogs (vendor, product, unit_cost, deposit_pct, note) VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            (c["vendor"], c["product"], c["unit_cost"], dep.get((c["vendor"], c["product"])), "Sheet Vendor COGS tab; deposit default = most common on its orders."),
            "vendor_costs")
    for c in data["product_costs"]:
        ins("INSERT INTO proj_product_cost (product, sku, china_unit_cost, us_unit_cost, note) VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            (c["product"], c["sku"], c["china_unit_cost"], c["us_unit_cost"], "Sheet COGS tab, China landed cost by SKU."), "product_costs")
    for r in data["reorder"]:
        ins("INSERT INTO proj_reorder_param (location, product, lead_days, safety_days, moq_units) VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            (r["location"], r["product"], r["lead_days"], r["safety_days"], r["moq_units"]), "reorder")
    for s in data["stock"]:
        ins("INSERT INTO proj_stock_snapshot (location, product, as_of, on_hand, allocated, inbound_per_3pl, estimate, note) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            (s["location"], s["product"], s["as_of"], s["on_hand"], s["allocated"], s["inbound_per_3pl"], s["estimate"], s["note"]), "stock")
    for p in data["pos"]:
        ins("INSERT INTO proj_po (id, sheet_row, po_number, product, vendor, warehouse, units, order_date, land_date, status, deposit_paid, shipping_paid, "
            "balance_paid, deposit_pct, unit_cost, link, note, source) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            (p["id"], p["sheet_row"], p["po_number"], p["product"], p["vendor"], p["warehouse"], p["units"], p["order_date"], p["land_date"], p["status"],
             p["deposit_paid"], p["shipping_paid"], p["balance_paid"], p["deposit_pct"], p["unit_cost"], p["link"], p["note"], p["source"]), "pos")
    for t in data["transfers"]:
        ins("INSERT INTO proj_fba_transfer (id, sheet_row, transfer_no, product, units, ship_date, receive_date, status, note, source) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            (t["id"], t["sheet_row"], t["transfer_no"], t["product"], t["units"], t["ship_date"], t["receive_date"], t["status"], t["note"], t["source"]),
            "transfers")
    from app.projection import audit
    if sum(n.values()):
        audit.record(conn, actor="system", scenario=None, action="inventory_supply.seed", line_key="inventory_supply", new=dict(n),
                     note="seeded from the frozen sheet snapshot" + (" (replace)" if replace else ""))
    return dict(n)


# ---------------------------------------------------------------------------------------------------------------- daily refresh (Revamp stock)

def read_revamp_cells() -> list[list]:
    """`Warehance Fcst Inputs`!A33:D39 from the live sheet, read-only (UNFORMATTED_VALUE, serial dates)."""
    from etl import sheets_io
    res = sheets_io._service().spreadsheets().values().get(
        spreadsheetId=sheets_io.PROJ_SHEET, range="'Warehance Fcst Inputs'!A33:D39", valueRenderOption="UNFORMATTED_VALUE",
        dateTimeRenderOption="SERIAL_NUMBER").execute()
    return res.get("values", [])


def refresh(conn, days: int = 14, *, today: date | None = None, reader: Callable[[], list[list]] | None = None) -> dict:
    """Append the Revamp (Warehance) on-hand and allocated counts as a new dated row per product.

    The sheet holds one current count (rows 35-38 for Vital Bull, Beef Liver, Boneflex, Vital Belle; snapshot date in B39). A count older than `days` is not
    loaded. A row for that day that already exists with the same values is skipped; the same day with different values is replaced (the sync rewrites
    the cells during the day). Returns {"rows": rows written, "data_through": the snapshot date}."""
    today = today or date.today()
    grid = (reader or read_revamp_cells)()
    by_label = {}
    for line in grid:
        if line and isinstance(line[0], str):
            prod = _product(line[0])
            if prod and len(line) >= 3 and _is_num(line[1]):
                by_label[prod] = (float(line[1]), float(line[2]) if _is_num(line[2]) else 0.0)
    as_of = None
    for line in grid:
        if line and isinstance(line[0], str) and line[0].strip().lower().startswith("snapshot date") and len(line) > 1:
            as_of = _serial(line[1])
    if not as_of or not by_label:
        raise RuntimeError("Warehance Fcst Inputs: stock block not found (expected rows labelled by product and a 'Snapshot date' row)")
    if (today - as_of).days > days:
        return {"rows": 0, "data_through": as_of.isoformat()}
    from app.projection import audit
    written = 0
    for prod, (on_hand, allocated) in by_label.items():
        old = conn.execute("SELECT on_hand, allocated FROM proj_stock_snapshot WHERE location = 'revamp' AND product = %s AND as_of = %s",
                           (prod, as_of)).fetchone()
        if old is not None and float(old["on_hand"]) == on_hand and float(old["allocated"]) == allocated:
            continue
        conn.execute("INSERT INTO proj_stock_snapshot (location, product, as_of, on_hand, allocated, inbound_per_3pl, estimate, note) "
                     "VALUES ('revamp', %s, %s, %s, %s, 0, false, 'Warehance on hand and allocated, read from Warehance Fcst Inputs.') "
                     "ON CONFLICT (location, product, as_of) DO UPDATE SET on_hand = EXCLUDED.on_hand, allocated = EXCLUDED.allocated",
                     (prod, as_of, on_hand, allocated))
        audit.record(conn, actor="system", scenario=None, action="inventory_supply.stock.refresh", line_key=f"inventory_supply.stock:revamp.{prod}",
                     month=as_of.replace(day=1), old={k: float(v) for k, v in old.items()} if old else None, new={"on_hand": on_hand, "allocated": allocated, "as_of": as_of.isoformat()})
        written += 1
    return {"rows": written, "data_through": as_of.isoformat()}


# ---------------------------------------------------------------------------------------------------------------- freshness

def freshness(conn, today: date | None = None) -> list[dict]:
    """The hook entries app/projection/freshness.py evaluates: purchase_orders (monthly-upload, 30 days), stock_revamp (daily, 2), stock_fba and stock_intl
    (counts entered by hand, so monthly-upload for the runner, which treats them as reported-never-fatal; 14 days). `input` carries the real cadence
    ('manual') for the page; `data_through` is the date of the last purchase-order / transfer edit, or the latest count date."""
    from app.projection.inventory_supply import freshness as f
    return [{"feed": e["feed"], "label": e["label"], "cadence": "daily" if e["cadence"] == "daily" else "monthly-upload", "input": e["cadence"],
             "data_through": e["data_through"], "stale_after_days": e["stale_after_days"]} for e in f.freshness(conn, today)]


# ---------------------------------------------------------------------------------------------------------------- CLI

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("seed")
    s.add_argument("--replace", action="store_true")
    s.add_argument("--snapshot-dir")
    r = sub.add_parser("refresh")
    r.add_argument("--days", type=int, default=14)
    sub.add_parser("freshness")
    args = ap.parse_args(argv)
    import os
    import psycopg
    from psycopg.rows import dict_row
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2
    if getattr(args, "snapshot_dir", None):
        os.environ["PROJECTION_SNAPSHOT_DIR"] = args.snapshot_dir
    sys.path.insert(0, str(ROOT))
    with psycopg.connect(url, row_factory=dict_row) as conn:
        if args.cmd == "seed":
            print(json.dumps(seed(conn, replace=args.replace)))
        elif args.cmd == "refresh":
            print(json.dumps(refresh(conn, args.days)))
        else:
            print(json.dumps(freshness(conn), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
