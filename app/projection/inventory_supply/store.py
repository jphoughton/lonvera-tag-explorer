"""Writes for inventory_supply: validation plus audited inserts, updates and deletes. Every function takes a read-write connection
inside one transaction (`rwdb.get_rw()`), raises `ApiError` (400/404/409) and writes one `proj_audit` row per changed record."""
from __future__ import annotations

import math
import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from ...queries import ApiError
from .. import clock, scenarios
from . import history
from .model import Settings
from ..interfaces import AXIS_END, AXIS_START
from .model import (LOCATIONS, PO_STATUSES, PRODUCTS, SOURCES, TRANSFER_STATUSES, WAREHOUSES, normalize_location, normalize_product)

MAX_ROWS_PER_PLAN = 200
PO_FIELDS = ("po_number", "product", "vendor", "warehouse", "units", "order_date", "land_date", "status", "deposit_paid", "shipping_paid",
             "balance_paid", "deposit_pct", "unit_cost", "link", "note")
TRANSFER_FIELDS = ("transfer_no", "product", "units", "ship_date", "receive_date", "status", "note")
_CTRL = re.compile(r"[\x00-\x1f\x7f]")


# ---------------------------------------------------------------------------------------------------------------- field validators

def _text(data: dict, name: str, maxlen: int, required: bool = False) -> str | None:
    v = data.get(name)
    if v is None or (isinstance(v, str) and not v.strip()):
        if required:
            raise ApiError(400, f"{name} is required")
        return None
    if not isinstance(v, str):
        raise ApiError(400, f"{name} must be text")
    v = v.strip()
    if len(v) > maxlen or _CTRL.search(v):
        raise ApiError(400, f"{name} must be at most {maxlen} characters without control characters")
    return v


def _number(data: dict, name: str, *, required: bool = False, lo: float | None = None, hi: float | None = None, gt: float | None = None) -> float | None:
    v = data.get(name)
    if v is None or v == "":
        if required:
            raise ApiError(400, f"{name} is required")
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise ApiError(400, f"{name} must be a number")
    if gt is not None and v <= gt:
        raise ApiError(400, f"{name} must be greater than {gt:g}")
    if lo is not None and v < lo:
        raise ApiError(400, f"{name} must be at least {lo:g}")
    if hi is not None and v > hi:
        raise ApiError(400, f"{name} must be at most {hi:g}")
    return float(v)


def _date(data: dict, name: str, *, required: bool = False, window: bool = True) -> date | None:
    v = data.get(name)
    if v is None or v == "":
        if required:
            raise ApiError(400, f"{name} is required")
        return None
    if isinstance(v, date) and not isinstance(v, datetime):
        d = v
    elif isinstance(v, str):
        try:
            d = date.fromisoformat(v.strip()[:10])
        except ValueError:
            raise ApiError(400, f"{name} must be an ISO date (YYYY-MM-DD)") from None
    else:
        raise ApiError(400, f"{name} must be an ISO date (YYYY-MM-DD)")
    if window and not (AXIS_START <= d <= date(AXIS_END.year, 12, 31)):
        raise ApiError(400, f"{name} must fall between {AXIS_START.isoformat()} and {AXIS_END.year}-12-31 (the model window)")
    return d


def _bool(data: dict, name: str) -> bool | None:
    v = data.get(name)
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, str) and v.strip().lower() in ("yes", "true", "y"):
        return True
    if isinstance(v, str) and v.strip().lower() in ("no", "false", "n"):
        return False
    raise ApiError(400, f"{name} must be true or false")


def _enum(data: dict, name: str, allowed: tuple[str, ...], default: str | None = None) -> str:
    v = data.get(name, default)
    if not isinstance(v, str) or v.strip().lower() not in allowed:
        raise ApiError(400, f"{name} must be one of {', '.join(allowed)}")
    return v.strip().lower()


def _product(data: dict, name: str = "product") -> str:
    v = normalize_product(data.get(name))
    if v is None:
        raise ApiError(400, f"{name} must be one of {', '.join(PRODUCTS)}")
    return v


def _link(data: dict) -> str | None:
    v = _text(data, "link", 500)
    if v is not None and not re.match(r"^https?://", v, re.I):
        raise ApiError(400, "link must start with http:// or https://")
    return v


def _unknown(data: dict, allowed: tuple[str, ...]) -> None:
    extra = sorted(set(data) - set(allowed))
    if extra:
        raise ApiError(400, f"unknown field(s): {', '.join(extra)}")


# ---------------------------------------------------------------------------------------------------------------- serialization

def jsonable(row: dict) -> dict:
    out = {}
    for k, v in row.items():
        if isinstance(v, (date, datetime)):
            out[k] = v.isoformat()
        elif isinstance(v, Decimal):
            out[k] = float(v)
        else:
            out[k] = v
    return out


def _row(conn, table: str, id_: int, lock: bool = False) -> dict:
    r = conn.execute(f"SELECT * FROM {table} WHERE id = %s" + (" FOR UPDATE" if lock else ""), (id_,)).fetchone()   # noqa: S608 (table is a constant)
    if r is None:
        raise ApiError(404, "not found")
    return r


def _audit(conn, action: str, kind: str, id_: int, old: dict | None, new: dict | None, when: date | None, note: str | None = None) -> None:
    """Log one changed record onto the change in progress (history row + audit row, written together when the change completes)."""
    history.log(kind, id_, old, new, audit_action=action, when=when, note=note)


def _settings(conn) -> Settings:
    """The plan of record's variables (defaults for rows created without a value)."""
    return Settings.from_params(scenarios.get_scenario(conn).params)


def _changed(old: dict | None, new: dict) -> bool:
    return old is None or any(old[k] != new[k] for k in new if k not in ("updated_at", "created_at"))


# ---------------------------------------------------------------------------------------------------------------- purchase orders

def clean_po(data: dict, base: dict | None = None, *, source_ok: bool = False) -> dict:
    """Validated purchase-order columns. `base` = the existing row for a partial update (the merged result is validated as a whole)."""
    if not isinstance(data, dict):
        raise ApiError(400, "JSON object body required")
    _unknown(data, PO_FIELDS + (("source",) if source_ok else ()))
    merged = {**{k: v for k, v in (base or {}).items() if k in PO_FIELDS}, **data}
    merged = {k: (v.isoformat() if isinstance(v, date) else (float(v) if isinstance(v, Decimal) else v)) for k, v in merged.items()}
    wh_raw = merged.get("warehouse", "revamp")
    wh = normalize_location(wh_raw) if isinstance(wh_raw, str) else None
    if wh not in WAREHOUSES:
        raise ApiError(400, "warehouse must be revamp or intl")
    out = {
        "po_number": _text(merged, "po_number", 60), "product": _product(merged), "vendor": _text(merged, "vendor", 60, required=True),
        "warehouse": wh, "units": _number(merged, "units", required=True, gt=0, hi=1e8),
        "order_date": _date(merged, "order_date"), "land_date": _date(merged, "land_date", required=True),
        "status": _enum(merged, "status", PO_STATUSES, "planned"),
        "deposit_paid": _bool(merged, "deposit_paid"), "shipping_paid": _bool(merged, "shipping_paid"), "balance_paid": _bool(merged, "balance_paid"),
        "deposit_pct": _number(merged, "deposit_pct", lo=0, hi=1), "unit_cost": _number(merged, "unit_cost", lo=0, hi=1e6),
        "link": _link(merged), "note": _text(merged, "note", 500),
    }
    if out["order_date"] and out["order_date"] > out["land_date"]:
        raise ApiError(400, "land_date must be on or after order_date")
    if source_ok and "source" in data:
        out["source"] = _enum(data, "source", SOURCES)
    return out


_PO_COLS = ("po_number", "product", "vendor", "warehouse", "units", "order_date", "land_date", "status", "deposit_paid", "shipping_paid",
            "balance_paid", "deposit_pct", "unit_cost", "link", "note")


@history.tracked_op("po.create")
def insert_po(conn, data: dict, *, source: str = "manual", note: str | None = None) -> dict:
    v = clean_po(data, source_ok=False)
    cols = list(_PO_COLS) + ["source"]
    row = conn.execute(f"INSERT INTO proj_po ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))}) RETURNING *",     # noqa: S608
                       [v[c] for c in _PO_COLS] + [source]).fetchone()
    _audit(conn, "inventory_supply.po.create", "po", row["id"], None, row, row["land_date"], note)
    return row


@history.tracked_op("po.update")
def update_po(conn, id_: int, patch: dict) -> dict:
    old = _row(conn, "proj_po", id_, lock=True)
    v = clean_po(patch, base=old)
    changed = {c: v[c] for c in _PO_COLS if v[c] != (float(old[c]) if isinstance(old[c], Decimal) else old[c])}
    if not changed:
        return old
    sets = ", ".join(f"{c} = %s" for c in changed)
    row = conn.execute(f"UPDATE proj_po SET {sets}, updated_at = now() WHERE id = %s RETURNING *", [*changed.values(), id_]).fetchone()   # noqa: S608
    _audit(conn, "inventory_supply.po.update", "po", id_, {c: old[c] for c in changed}, {c: row[c] for c in changed}, row["land_date"])
    return row


@history.tracked_op("po.delete")
def delete_po(conn, id_: int) -> dict:
    old = _row(conn, "proj_po", id_, lock=True)
    conn.execute("DELETE FROM proj_po WHERE id = %s", (id_,))
    _audit(conn, "inventory_supply.po.delete", "po", id_, old, None, old["land_date"])
    return old


# ---------------------------------------------------------------------------------------------------------------- transfers

def clean_transfer(data: dict, base: dict | None = None, *, source_ok: bool = False) -> dict:
    if not isinstance(data, dict):
        raise ApiError(400, "JSON object body required")
    _unknown(data, TRANSFER_FIELDS + (("source",) if source_ok else ()))
    merged = {**{k: v for k, v in (base or {}).items() if k in TRANSFER_FIELDS}, **data}
    merged = {k: (v.isoformat() if isinstance(v, date) else (float(v) if isinstance(v, Decimal) else v)) for k, v in merged.items()}
    out = {"transfer_no": _text(merged, "transfer_no", 60), "product": _product(merged), "units": _number(merged, "units", required=True, gt=0, hi=1e8),
           "ship_date": _date(merged, "ship_date", required=True), "receive_date": _date(merged, "receive_date", required=True),
           "status": _enum(merged, "status", TRANSFER_STATUSES, "planned"), "note": _text(merged, "note", 500)}
    if out["receive_date"] < out["ship_date"]:
        raise ApiError(400, "receive_date must be on or after ship_date")
    return out


_TR_COLS = TRANSFER_FIELDS


@history.tracked_op("transfer.create")
def insert_transfer(conn, data: dict, *, source: str = "manual", note: str | None = None) -> dict:
    v = clean_transfer(data)
    cols = list(_TR_COLS) + ["source"]
    row = conn.execute(f"INSERT INTO proj_fba_transfer ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))}) RETURNING *",     # noqa: S608
                       [v[c] for c in _TR_COLS] + [source]).fetchone()
    _audit(conn, "inventory_supply.transfer.create", "transfer", row["id"], None, row, row["ship_date"], note)
    return row


@history.tracked_op("transfer.update")
def update_transfer(conn, id_: int, patch: dict) -> dict:
    old = _row(conn, "proj_fba_transfer", id_, lock=True)
    v = clean_transfer(patch, base=old)
    changed = {c: v[c] for c in _TR_COLS if v[c] != (float(old[c]) if isinstance(old[c], Decimal) else old[c])}
    if not changed:
        return old
    sets = ", ".join(f"{c} = %s" for c in changed)
    row = conn.execute(f"UPDATE proj_fba_transfer SET {sets}, updated_at = now() WHERE id = %s RETURNING *", [*changed.values(), id_]).fetchone()   # noqa: S608
    _audit(conn, "inventory_supply.transfer.update", "transfer", id_, {c: old[c] for c in changed}, {c: row[c] for c in changed}, row["ship_date"])
    return row


@history.tracked_op("transfer.delete")
def delete_transfer(conn, id_: int) -> dict:
    old = _row(conn, "proj_fba_transfer", id_, lock=True)
    conn.execute("DELETE FROM proj_fba_transfer WHERE id = %s", (id_,))
    _audit(conn, "inventory_supply.transfer.delete", "transfer", id_, old, None, old["ship_date"])
    return old


# ---------------------------------------------------------------------------------------------------------------- plan accept

@history.tracked_op("plan.accept")
def accept_plan(conn, body: dict) -> dict:
    """Write an accepted plan: optionally replace the auto-plan rows, insert the proposed rows (source auto-plan). One transaction, all audited.

    body: {pos:[...], transfers:[...], replace_auto: bool, expect_replaced: {pos:int, transfers:int}}  (expect_replaced is required with replace_auto,
    so a plan computed against older data cannot silently delete rows that appeared since)."""
    if not isinstance(body, dict):
        raise ApiError(400, "JSON object body required")
    _unknown(body, ("pos", "transfers", "replace_auto", "expect_replaced"))
    pos, trs = body.get("pos", []), body.get("transfers", [])
    if not isinstance(pos, list) or not isinstance(trs, list):
        raise ApiError(400, "pos and transfers must be lists")
    if len(pos) > MAX_ROWS_PER_PLAN or len(trs) > MAX_ROWS_PER_PLAN:
        raise ApiError(400, f"at most {MAX_ROWS_PER_PLAN} rows of each kind per plan")
    if not pos and not trs:
        raise ApiError(400, "the plan has no rows")
    replace = body.get("replace_auto", False)
    if not isinstance(replace, bool):
        raise ApiError(400, "replace_auto must be true or false")
    removed_pos: list[dict] = []
    removed_tr: list[dict] = []
    if replace:
        exp = body.get("expect_replaced")
        if not isinstance(exp, dict) or not all(isinstance(exp.get(k), int) and not isinstance(exp.get(k), bool) for k in ("pos", "transfers")):
            raise ApiError(400, "expect_replaced {pos, transfers} (integers) is required with replace_auto")
        removed_pos = conn.execute("SELECT * FROM proj_po WHERE source = 'auto-plan' ORDER BY id FOR UPDATE").fetchall()
        removed_tr = conn.execute("SELECT * FROM proj_fba_transfer WHERE source = 'auto-plan' ORDER BY id FOR UPDATE").fetchall()
        if (len(removed_pos), len(removed_tr)) != (exp["pos"], exp["transfers"]):
            raise ApiError(409, f"plan_stale: {len(removed_pos)} auto-plan orders and {len(removed_tr)} transfers exist now, the plan was made against "
                                f"{exp['pos']} and {exp['transfers']}. Suggest the plan again.")
    cleaned_pos = [clean_po({k: v for k, v in p.items() if k in PO_FIELDS}) for p in pos]
    cleaned_tr = [clean_transfer({k: v for k, v in t.items() if k in TRANSFER_FIELDS}) for t in trs]
    for r in removed_pos:
        conn.execute("DELETE FROM proj_po WHERE id = %s", (r["id"],))
        _audit(conn, "inventory_supply.po.delete", "po", r["id"], r, None, r["land_date"], "plan accept: replaced auto-plan row")
    for r in removed_tr:
        conn.execute("DELETE FROM proj_fba_transfer WHERE id = %s", (r["id"],))
        _audit(conn, "inventory_supply.transfer.delete", "transfer", r["id"], r, None, r["ship_date"], "plan accept: replaced auto-plan row")
    new_pos = [insert_po(conn, p, source="auto-plan", note="plan accept") for p in cleaned_pos]
    new_tr = [insert_transfer(conn, t, source="auto-plan", note="plan accept") for t in cleaned_tr]
    history.current().audit_only("inventory_supply.plan.accept", "inventory_supply.plan",
                                 {"added_pos": len(new_pos), "added_transfers": len(new_tr), "removed_pos": len(removed_pos), "removed_transfers": len(removed_tr)})
    return {"added_pos": [r["id"] for r in new_pos], "added_transfers": [r["id"] for r in new_tr],
            "removed_pos": len(removed_pos), "removed_transfers": len(removed_tr)}


# ---------------------------------------------------------------------------------------------------------------- stock snapshots and price lists

@history.tracked_op("stock.put")
def put_stock(conn, data: dict, *, actor_note: str | None = None, today: date | None = None) -> dict:
    """Append a stock count (location, product, as_of); a second count for the same day replaces that day's row. Returns the stored row."""
    if not isinstance(data, dict):
        raise ApiError(400, "JSON object body required")
    _unknown(data, ("location", "product", "as_of", "on_hand", "allocated", "inbound_per_3pl", "estimate", "note"))
    loc = normalize_location(data.get("location")) if data.get("location") else None
    if loc not in LOCATIONS:
        raise ApiError(400, f"location must be one of {', '.join(LOCATIONS)}")
    prod = _product(data)
    as_of = _date(data, "as_of", required=True, window=False)
    if as_of > (today or clock.today_pt()) + timedelta(days=31):
        raise ApiError(400, "as_of is too far in the future")
    on_hand = _number(data, "on_hand", required=True, lo=-1e8, hi=1e9)
    allocated = _number(data, "allocated", lo=0, hi=1e9) or 0.0
    inbound = _number(data, "inbound_per_3pl", lo=0, hi=1e9) or 0.0
    est = data.get("estimate", False)
    if not isinstance(est, bool):
        raise ApiError(400, "estimate must be true or false")
    note = _text(data, "note", 500)
    old = conn.execute("SELECT * FROM proj_stock_snapshot WHERE location = %s AND product = %s AND as_of = %s FOR UPDATE", (loc, prod, as_of)).fetchone()
    row = conn.execute(
        "INSERT INTO proj_stock_snapshot (location, product, as_of, on_hand, allocated, inbound_per_3pl, estimate, note) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT (location, product, as_of) DO UPDATE SET on_hand = EXCLUDED.on_hand, allocated = EXCLUDED.allocated, "
        "inbound_per_3pl = EXCLUDED.inbound_per_3pl, estimate = EXCLUDED.estimate, note = EXCLUDED.note RETURNING *",
        (loc, prod, as_of, on_hand, allocated, inbound, est, note)).fetchone()
    if old is None or any(old[k] != row[k] for k in ("on_hand", "allocated", "inbound_per_3pl", "estimate", "note")):
        history.log("stock", f"{loc}|{prod}|{as_of.isoformat()}", old, row, audit_action="inventory_supply.stock.put", line_key=f"inventory_supply.stock:{loc}.{prod}",
                    when=as_of, op="put", note=actor_note)
    return row


@history.tracked_op("vendor.put")
def put_vendor(conn, vendor: str, data: dict) -> dict:
    if not isinstance(vendor, str) or not vendor.strip() or len(vendor) > 60:
        raise ApiError(400, "vendor must be 1-60 characters")
    _unknown(data, ("lead_weeks", "moq_units", "default_warehouse", "note"))
    old = conn.execute("SELECT * FROM proj_vendor_lead WHERE vendor = %s FOR UPDATE", (vendor,)).fetchone()
    lead = _number(data, "lead_weeks", required=old is None, gt=0, hi=104)
    moq = _number(data, "moq_units", lo=0, hi=1e9)
    wh = _enum(data, "default_warehouse", WAREHOUSES, old["default_warehouse"] if old else "revamp")
    row = conn.execute(
        "INSERT INTO proj_vendor_lead (vendor, lead_weeks, moq_units, default_warehouse, note) VALUES (%s,%s,%s,%s,%s) "
        "ON CONFLICT (vendor) DO UPDATE SET lead_weeks = EXCLUDED.lead_weeks, moq_units = EXCLUDED.moq_units, "
        "default_warehouse = EXCLUDED.default_warehouse, note = EXCLUDED.note, updated_at = now() RETURNING *",
        (vendor, lead if lead is not None else float(old["lead_weeks"]), moq if moq is not None else (float(old["moq_units"]) if old else 0.0), wh,
         _text(data, "note", 500) if "note" in data else (old["note"] if old else None))).fetchone()
    if _changed(old, row):
        history.log("vendor", vendor, old, row, audit_action="inventory_supply.vendor.put", line_key=f"inventory_supply.vendor:{vendor}", op="put")
    return row


@history.tracked_op("vendor_cost.put")
def put_vendor_cost(conn, data: dict) -> dict:
    _unknown(data, ("vendor", "product", "unit_cost", "deposit_pct", "note"))
    vendor = _text(data, "vendor", 60, required=True)
    prod = _product(data)
    cost = _number(data, "unit_cost", required=True, lo=0, hi=1e6)
    dep = _number(data, "deposit_pct", lo=0, hi=1)
    if conn.execute("SELECT 1 FROM proj_vendor_lead WHERE vendor = %s", (vendor,)).fetchone() is None:
        raise ApiError(400, f"unknown vendor {vendor!r}: add its lead time first")
    old = conn.execute("SELECT * FROM proj_vendor_cogs WHERE vendor = %s AND product = %s FOR UPDATE", (vendor, prod)).fetchone()
    row = conn.execute(
        "INSERT INTO proj_vendor_cogs (vendor, product, unit_cost, deposit_pct, note) VALUES (%s,%s,%s,%s,%s) "
        "ON CONFLICT (vendor, product) DO UPDATE SET unit_cost = EXCLUDED.unit_cost, deposit_pct = EXCLUDED.deposit_pct, note = EXCLUDED.note, "
        "updated_at = now() RETURNING *", (vendor, prod, cost, dep, _text(data, "note", 500))).fetchone()
    if _changed(old, row):
        history.log("vendor_cost", f"{vendor}|{prod}", old, row, audit_action="inventory_supply.vendor_cost.put", line_key=f"inventory_supply.vendor_cost:{vendor}.{prod}", op="put")
    return row


@history.tracked_op("reorder_param.put")
def put_reorder_param(conn, data: dict) -> dict:
    _unknown(data, ("location", "product", "lead_days", "safety_days", "moq_units"))
    loc = normalize_location(data.get("location")) if data.get("location") else None
    if loc not in WAREHOUSES:
        raise ApiError(400, "location must be revamp or intl")
    prod = _product(data)
    old = conn.execute("SELECT * FROM proj_reorder_param WHERE location = %s AND product = %s FOR UPDATE", (loc, prod)).fetchone()
    lead = _number(data, "lead_days", required=old is None, lo=0, hi=730)
    safety = _number(data, "safety_days", lo=0, hi=365)
    moq = _number(data, "moq_units", lo=0, hi=1e9)
    row = conn.execute(
        "INSERT INTO proj_reorder_param (location, product, lead_days, safety_days, moq_units) VALUES (%s,%s,%s,%s,%s) "
        "ON CONFLICT (location, product) DO UPDATE SET lead_days = EXCLUDED.lead_days, safety_days = EXCLUDED.safety_days, "
        "moq_units = EXCLUDED.moq_units, updated_at = now() RETURNING *",
        (loc, prod, int(lead if lead is not None else old["lead_days"]), int(safety if safety is not None else (old["safety_days"] if old else round(_settings(conn).default_safety_days))),
         moq if moq is not None else (float(old["moq_units"]) if old else 0.0))).fetchone()
    if _changed(old, row):
        history.log("reorder_param", f"{loc}|{prod}", old, row, audit_action="inventory_supply.reorder_param.put", line_key=f"inventory_supply.reorder:{loc}.{prod}", op="put")
    return row


@history.tracked_op("product_cost.put")
def put_product_cost(conn, data: dict) -> dict:
    _unknown(data, ("product", "china_unit_cost", "us_unit_cost", "sku", "note"))
    prod = _product(data)
    cost = _number(data, "china_unit_cost", required=True, lo=0, hi=1e6)
    us = _number(data, "us_unit_cost", lo=0, hi=1e6)
    old = conn.execute("SELECT * FROM proj_product_cost WHERE product = %s FOR UPDATE", (prod,)).fetchone()
    row = conn.execute(
        "INSERT INTO proj_product_cost (product, sku, china_unit_cost, us_unit_cost, note) VALUES (%s,%s,%s,%s,%s) "
        "ON CONFLICT (product) DO UPDATE SET sku = EXCLUDED.sku, china_unit_cost = EXCLUDED.china_unit_cost, us_unit_cost = EXCLUDED.us_unit_cost, "
        "note = EXCLUDED.note, updated_at = now() RETURNING *",
        (prod, _text(data, "sku", 60) or (old["sku"] if old else None), cost, us, _text(data, "note", 500))).fetchone()
    if _changed(old, row):
        history.log("product_cost", prod, old, row, audit_action="inventory_supply.product_cost.put", line_key=f"inventory_supply.product_cost:{prod}", op="put")
    return row
