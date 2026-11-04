"""/api/projection/inventory-supply/*: reads from the engine result, writes through store.py (audited, validated). Auth, origin, rate limits and
JSON-only bodies are the projection core's app-level guards; nothing here handles them."""
from __future__ import annotations

from datetime import date
from typing import Any

from flask import Blueprint, jsonify, request

from ... import db
from ...queries import ApiError
from .. import engine as proj_engine
from .. import degraded, rwdb, scenarios
from . import calc, freshness as fresh, history, loaders, overlay, planner, store, tables
from .model import (LOCATIONS, LOCATION_NAMES, PO_STATUSES, PRODUCT_NAMES, PRODUCTS, TRANSFER_STATUSES, WAREHOUSES, Settings, SupplyResult, normalize_product)

bp = Blueprint("proj_inventory_supply", __name__)


# ---------------------------------------------------------------------------------------------------------------- helpers

def _body() -> dict:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ApiError(400, "JSON object body required")
    return data


def _engine():
    return proj_engine.run(request.args.get("scenario"), db.get_conn())


def _supply() -> tuple[SupplyResult, Any]:
    eng = _engine()
    degraded.ensure_ok(eng, "inventory_supply")                       # a degraded demand (or this module) is a 503 upstream_degraded, never zeros
    res = eng.results.get("inventory_supply")
    if not isinstance(res, SupplyResult):
        raise ApiError(503, "inventory_supply is not available: " + eng.errors.get("inventory_supply", "not built"))
    return res, eng


def _flags_by_id(res: SupplyResult) -> dict[tuple[str, int], list[str]]:
    out: dict[tuple[str, int], list[str]] = {}
    for w in res.warnings:
        for r in w["refs"]:
            out.setdefault((r["kind"], r["id"]), []).append(w["code"])
    return out


def _json_row(row: dict) -> dict:
    return store.jsonable(row)


# ---------------------------------------------------------------------------------------------------------------- reads

@bp.get("/table")
def table():
    res, _ = _supply()
    return jsonify(tables.burn_table(res, "total"))


@bp.get("/burn")
def burn():
    loc = request.args.get("location", "total")
    if loc not in LOCATIONS + ("total",):
        raise ApiError(400, "location must be revamp, amazon, intl or total")
    res, _ = _supply()
    return jsonify(tables.burn_table(res, loc))


@bp.get("/value")
def value():
    res, _ = _supply()
    return jsonify(tables.value_table(res))


@bp.get("/outlay")
def outlay():
    res, _ = _supply()
    return jsonify(tables.outlay_table(res))


@bp.get("/reorder")
def reorder():
    res, _ = _supply()
    rows = [{k: v for k, v in r.items() if k != "serial"} for r in res.reorder]
    for r in rows:
        r["product_name"], r["location_name"] = PRODUCT_NAMES.get(r["product"], r["product"]), LOCATION_NAMES.get(r["location"], r["location"])
    return jsonify({"reorder": rows, "today": res.today.isoformat(), "horizon": res.horizon})


@bp.get("/warnings")
def warnings():
    res, _ = _supply()
    return jsonify({"warnings": res.warnings})


@bp.get("/freshness")
def freshness_route():
    res, _ = _supply()
    return jsonify({"freshness": fresh.freshness(db.get_conn(), res.today)})


@bp.get("/summary")
def summary():
    res, eng = _supply()
    conn = db.get_conn()
    return jsonify({
        "today": res.today.isoformat(), "start": res.start.isoformat(), "horizon": res.horizon, "tldr": res.tldr,
        "reorder": [{k: v for k, v in r.items() if k != "serial"} for r in res.reorder],
        "stats": res.stats, "warnings": res.warnings, "freshness": fresh.freshness(conn, res.today),
        "outlay_total": sum(res.outlay.values()), "demand_stub": bool(eng.stubbed.get("inventory_demand")),
        "stale_stock_days": Settings.from_params(eng.scenario.params).stale_stock_days,
        "product_costs": [{"product": c.product, "sku": c.sku, "china_unit_cost": c.china_unit_cost, "source": "P&L cost model" if c.source == "pnl" else "table"}
                          for c in loaders.load_inputs(conn, eng.scenario).product_costs.values()],
        "labels": {"products": PRODUCT_NAMES, "locations": LOCATION_NAMES},
    })


@bp.get("/po")
def po_list():
    res, _ = _supply()
    flags = _flags_by_id(res)
    rows = []
    for r in res.po_rows:
        r = dict(r)
        r["flags"] = sorted(set(flags.get(("po", r["id"]), [])))
        rows.append(r)
    return jsonify({"pos": rows, "statuses": PO_STATUSES, "warehouses": WAREHOUSES, "products": PRODUCTS, "today": res.today.isoformat(),
                    "totals": {"po_value": res.scalars.get("po.total_value", 0.0), "deposits": res.scalars.get("po.total_deposits", 0.0),
                               "balances": res.scalars.get("po.total_balances", 0.0)}})


@bp.get("/transfers")
def transfer_list():
    res, _ = _supply()
    flags = _flags_by_id(res)
    rows = []
    for r in res.transfer_rows:
        r = dict(r)
        r["flags"] = sorted(set(flags.get(("transfer", r["id"]), [])))
        rows.append(r)
    return jsonify({"transfers": rows, "statuses": TRANSFER_STATUSES, "products": PRODUCTS})


@bp.get("/stock")
def stock_list():
    conn = db.get_conn()
    rows = conn.execute("SELECT * FROM proj_stock_snapshot ORDER BY as_of DESC, location, product LIMIT 400").fetchall()
    inputs = loaders.load_inputs(conn)
    latest = [{"location": k[0], "product": k[1], "as_of": s.as_of.isoformat(), "on_hand": s.on_hand, "allocated": s.allocated,
               "inbound_per_3pl": s.inbound_per_3pl, "estimate": s.estimate, "note": s.note, "opening": s.opening} for k, s in sorted(inputs.stock.items())]
    return jsonify({"latest": latest, "history": [_json_row(r) for r in rows]})


@bp.get("/vendors")
def vendors():
    conn = db.get_conn()
    scn = scenarios.get_scenario(conn, request.args.get("scenario"))
    inputs = loaders.load_inputs(conn, scn)
    base = loaders.load_base(conn)
    ov = inputs.overridden
    return jsonify({
        "vendors": [{"vendor": v.vendor, "lead_weeks": v.lead_weeks, "moq_units": v.moq_units, "default_warehouse": v.default_warehouse, "note": v.note,
                     "overridden": list(ov.get(("vendor", v.vendor), ())), "default": ({"lead_weeks": base.vendors[v.vendor].lead_weeks, "moq_units": base.vendors[v.vendor].moq_units}
                                                                                    if v.vendor in base.vendors else None)} for v in inputs.vendors.values()],
        "vendor_costs": [{"vendor": c.vendor, "product": c.product, "unit_cost": c.unit_cost, "deposit_pct": c.deposit_pct, "note": c.note,
                          "overridden": list(ov.get(("price", f"{c.vendor}|{c.product}"), ())),
                          "default": ({"unit_cost": base.vendor_costs[(c.vendor, c.product)].unit_cost, "deposit_pct": base.vendor_costs[(c.vendor, c.product)].deposit_pct}
                                      if (c.vendor, c.product) in base.vendor_costs else None)} for c in inputs.vendor_costs.values()],
        "reorder_params": [{"location": p.location, "product": p.product, "lead_days": p.lead_days, "safety_days": p.safety_days, "moq_units": p.moq_units,
                            "overridden": list(ov.get(("reorder", f"{p.location}|{p.product}"), ())),
                            "default": ({"lead_days": base.reorder[(p.location, p.product)].lead_days, "safety_days": base.reorder[(p.location, p.product)].safety_days}
                                        if (p.location, p.product) in base.reorder else None)} for p in inputs.reorder.values()],
        "product_costs": [{"product": c.product, "sku": c.sku, "china_unit_cost": c.china_unit_cost, "us_unit_cost": c.us_unit_cost,
                           "source": "P&L cost model" if c.source == "pnl" else "table", "overridden": list(ov.get(("product_cost", c.product), ())),
                           "default": ({"china_unit_cost": base.product_costs[c.product].china_unit_cost} if c.product in base.product_costs else None)}
                          for c in inputs.product_costs.values()],
        "is_default": scn.is_default})


@bp.get("/variables")
def variables_get():
    conn = db.get_conn()
    slug = request.args.get("scenario") or scenarios.default_slug(conn)
    row = scenarios.get_row(conn, slug)
    scn = scenarios.get_scenario(conn, slug)
    cat = overlay.catalog(loaders.load_base(conn), row, scn.params)
    return jsonify({"scenario": {"slug": row["slug"], "name": row["name"], "version": row["version"], "is_default": row["is_default"], "base_slug": row["base_slug"]}, **cat})


@bp.get("/plan/suggest")
def plan_suggest():
    eng = _engine()
    degraded.ensure_ok(eng, "inventory_supply")
    res = eng.results.get("inventory_supply")
    if not isinstance(res, SupplyResult):
        raise ApiError(503, "inventory_supply is not available")
    replace = request.args.get("replace_auto", "1") not in ("0", "false", "no")
    inputs = loaders.load_inputs(db.get_conn(), eng.scenario)
    settings = Settings.from_params(eng.scenario.params)
    plan = planner.suggest(inputs, eng.results.get("inventory_demand"), res.today, settings, replace_auto=replace)
    plan["settings"] = {k: getattr(settings, k) for k in ("plan_cover_months", "po_round_units", "fba_target_months", "fba_restore_months", "transfer_round_units")}
    return jsonify(plan)


# ---------------------------------------------------------------------------------------------------------------- writes
# A write names the scenario in `?scenario=`. For the plan of record (or no scenario) it edits the stored rows; for any other scenario it records the
# change as a scenario variable (params.inventory_supply), so the plan of record is untouched and the change is versioned, audited and restorable.

def _created(row: dict, key: str):
    return jsonify({key: _json_row(row)}), 201


def _overlay_slug(conn) -> str | None:
    slug = request.args.get("scenario")
    if not slug:
        return None
    return None if scenarios.get_row(conn, slug)["is_default"] else slug


def _ov_write(conn, slug: str, items: list[dict]) -> dict:
    return overlay.set_variables(conn, slug, items, base_inputs=loaders.load_base(conn))


def _ov_out(res: dict, **extra):
    s = res["scenario"]
    return {"scenario": {"slug": s["slug"], "version": s["version"]}, "changed": res["changed"], **extra}


def _row_ref(ref: str, db_mode: bool) -> int | str:
    if db_mode:
        if not ref.isdigit():
            raise ApiError(400, "a stored row is addressed by its number")
        return int(ref)
    return ref


def _exists(conn, slug: str, kind: str, ref: str) -> None:
    scn = scenarios.get_scenario(conn, slug)
    inputs = loaders.load_inputs(conn, scn)
    rows = inputs.pos if kind == "po" else inputs.transfers
    if not any((r.ref or str(r.id)) == ref for r in rows):
        raise ApiError(404, "not found")


def _entry(cleaned: dict) -> dict:
    return {k: (v.isoformat() if isinstance(v, date) else v) for k, v in cleaned.items() if v is not None}


@bp.post("/po")
def po_create():
    with rwdb.get_rw() as conn:
        slug = _overlay_slug(conn)
        if slug is None:
            return _created(store.insert_po(conn, _body()), "po")
        entry = _entry(store.clean_po(_body()))
        ref = f"new:{overlay.next_new_number(scenarios.get_scenario(conn, slug).params, 'pos')}"
        res = _ov_write(conn, slug, [{"kind": "po", "id": ref, "field": None, "value": entry}])
    return jsonify(_ov_out(res, ref=ref)), 201


@bp.patch("/po/<ref>")
def po_update(ref: str):
    body = _body()
    with rwdb.get_rw() as conn:
        slug = _overlay_slug(conn)
        if slug is None:
            return jsonify({"po": _json_row(store.update_po(conn, _row_ref(ref, True), body))})
        bad = sorted(set(body) - set(store.PO_FIELDS))
        if bad:
            raise ApiError(400, f"unknown field(s): {', '.join(bad)}")
        _exists(conn, slug, "po", ref)
        res = _ov_write(conn, slug, [{"kind": "po", "id": ref, "field": f, "value": "" if v is None else v} for f, v in body.items()])
    return jsonify(_ov_out(res, ref=ref))


@bp.delete("/po/<ref>")
def po_delete(ref: str):
    with rwdb.get_rw() as conn:
        slug = _overlay_slug(conn)
        if slug is None:
            id_ = _row_ref(ref, True)
            return jsonify({"deleted": id_, "po": _json_row(store.delete_po(conn, id_))})
        _exists(conn, slug, "po", ref)
        item = {"kind": "po", "id": ref, "field": None, "value": None} if ref.startswith("new:") else {"kind": "po", "id": ref, "field": "deleted", "value": True}
        res = _ov_write(conn, slug, [item])
    return jsonify(_ov_out(res, deleted=ref))


@bp.post("/transfers")
def transfer_create():
    with rwdb.get_rw() as conn:
        slug = _overlay_slug(conn)
        if slug is None:
            return _created(store.insert_transfer(conn, _body()), "transfer")
        entry = _entry(store.clean_transfer(_body()))
        ref = f"new:{overlay.next_new_number(scenarios.get_scenario(conn, slug).params, 'transfers')}"
        res = _ov_write(conn, slug, [{"kind": "transfer", "id": ref, "field": None, "value": entry}])
    return jsonify(_ov_out(res, ref=ref)), 201


@bp.patch("/transfers/<ref>")
def transfer_update(ref: str):
    body = _body()
    with rwdb.get_rw() as conn:
        slug = _overlay_slug(conn)
        if slug is None:
            return jsonify({"transfer": _json_row(store.update_transfer(conn, _row_ref(ref, True), body))})
        bad = sorted(set(body) - set(store.TRANSFER_FIELDS))
        if bad:
            raise ApiError(400, f"unknown field(s): {', '.join(bad)}")
        _exists(conn, slug, "transfer", ref)
        res = _ov_write(conn, slug, [{"kind": "transfer", "id": ref, "field": f, "value": "" if v is None else v} for f, v in body.items()])
    return jsonify(_ov_out(res, ref=ref))


@bp.delete("/transfers/<ref>")
def transfer_delete(ref: str):
    with rwdb.get_rw() as conn:
        slug = _overlay_slug(conn)
        if slug is None:
            id_ = _row_ref(ref, True)
            return jsonify({"deleted": id_, "transfer": _json_row(store.delete_transfer(conn, id_))})
        _exists(conn, slug, "transfer", ref)
        item = {"kind": "transfer", "id": ref, "field": None, "value": None} if ref.startswith("new:") else {"kind": "transfer", "id": ref, "field": "deleted", "value": True}
        res = _ov_write(conn, slug, [item])
    return jsonify(_ov_out(res, deleted=ref))


@bp.post("/stock")
def stock_put():
    with rwdb.get_rw() as conn:
        row = store.put_stock(conn, _body(), today=loaders.resolve_today())
    return jsonify({"stock": _json_row(row)}), 201


@bp.post("/plan/accept")
def plan_accept():
    body = _body()
    with rwdb.get_rw() as conn:
        slug = _overlay_slug(conn)
        out = store.accept_plan(conn, body) if slug is None else overlay.accept_plan(conn, slug, body)
    return jsonify(out), 201


@bp.put("/vendors/<vendor>")
def vendor_put(vendor: str):
    body = _body()
    with rwdb.get_rw() as conn:
        slug = _overlay_slug(conn)
        if slug is None:
            return jsonify({"vendor": _json_row(store.put_vendor(conn, vendor, body))})
        bad = sorted(set(body) - set(overlay.VENDOR_FIELDS) - {"note"})
        if bad:
            raise ApiError(400, f"unknown field(s): {', '.join(bad)}")
        res = _ov_write(conn, slug, [{"kind": "vendor", "vendor": vendor, "field": f, "value": body[f]} for f in overlay.VENDOR_FIELDS if f in body])
    return jsonify(_ov_out(res))


@bp.put("/vendor-costs")
def vendor_cost_put():
    body = _body()
    with rwdb.get_rw() as conn:
        slug = _overlay_slug(conn)
        if slug is None:
            return jsonify({"vendor_cost": _json_row(store.put_vendor_cost(conn, body))})
        bad = sorted(set(body) - {"vendor", "product", "unit_cost", "deposit_pct", "note"})
        if bad:
            raise ApiError(400, f"unknown field(s): {', '.join(bad)}")
        res = _ov_write(conn, slug, [{"kind": "price", "vendor": body.get("vendor"), "product": body.get("product"), "field": f, "value": body[f]}
                                     for f in overlay.PRICE_FIELDS if f in body])
    return jsonify(_ov_out(res))


@bp.put("/reorder-params")
def reorder_param_put():
    body = _body()
    with rwdb.get_rw() as conn:
        slug = _overlay_slug(conn)
        if slug is None:
            return jsonify({"reorder_param": _json_row(store.put_reorder_param(conn, body))})
        bad = sorted(set(body) - {"location", "product"} - set(overlay.REORDER_FIELDS))
        if bad:
            raise ApiError(400, f"unknown field(s): {', '.join(bad)}")
        res = _ov_write(conn, slug, [{"kind": "reorder", "location": body.get("location"), "product": body.get("product"), "field": f, "value": body[f]}
                                     for f in overlay.REORDER_FIELDS if f in body])
    return jsonify(_ov_out(res))


@bp.put("/product-costs")
def product_cost_put():
    """The China landed cost of a SKU has one source: the scenario param `pnl.cogs.china_cost.<sku>` (the P&L reads and writes the same key). A product with a
    SKU is written there for the scenario in `?scenario=` (the plan of record when absent; value null = back to the stored seed); the stored table row is the
    seed and fallback. A product without a SKU keeps this module's own storage."""
    body = _body()
    bad = sorted(set(body) - {"product", "china_unit_cost", "us_unit_cost", "sku", "note"})
    if bad:
        raise ApiError(400, f"unknown field(s): {', '.join(bad)}")
    with rwdb.get_rw() as conn:
        prod = normalize_product(body.get("product"))
        if prod is None:
            raise ApiError(400, f"product must be one of {', '.join(PRODUCTS)}")
        base = loaders.load_base(conn)
        pc = base.product_costs.get(prod)
        slug = request.args.get("scenario") or scenarios.default_slug(conn)
        is_plan = scenarios.get_row(conn, slug)["is_default"]
        if pc is not None and pc.sku and "china_unit_cost" in body:
            res = _ov_write(conn, slug, [{"kind": "product_cost", "product": prod, "field": "china_unit_cost", "value": body["china_unit_cost"]}])
            table_fields = {k: v for k, v in body.items() if k in ("us_unit_cost", "sku", "note")}
            if table_fields and is_plan:                                      # the seed table's other columns; its China cost stays as it is
                store.put_product_cost(conn, {"product": prod, "china_unit_cost": pc.china_unit_cost, **table_fields})
            return jsonify(_ov_out(res, sku=pc.sku, source="P&L cost model"))
        if is_plan:
            return jsonify({"product_cost": _json_row(store.put_product_cost(conn, body))})
        bad = sorted(set(body) - {"product", "china_unit_cost"})
        if bad:
            raise ApiError(400, f"unknown field(s): {', '.join(bad)}")
        res = _ov_write(conn, slug, [{"kind": "product_cost", "product": prod, "field": "china_unit_cost", "value": body.get("china_unit_cost")}] if "china_unit_cost" in body else [])
    return jsonify(_ov_out(res))


@bp.put("/variables")
def variables_put():
    """Change named variables of a scenario: {scenario?, items:[{kind, ..., value}], expected_version?}. value null = back to the default."""
    body = _body()
    bad = sorted(set(body) - {"scenario", "items", "expected_version"})
    if bad:
        raise ApiError(400, f"unknown field(s): {', '.join(bad)}")
    ev = body.get("expected_version")
    if ev is not None and (isinstance(ev, bool) or not isinstance(ev, int)):
        raise ApiError(400, "expected_version must be an integer")
    with rwdb.get_rw() as conn:
        slug = body.get("scenario") or request.args.get("scenario") or scenarios.default_slug(conn)
        if not isinstance(slug, str):
            raise ApiError(400, "scenario must be a slug")
        res = overlay.set_variables(conn, slug, body.get("items"), base_inputs=loaders.load_base(conn), expected_version=ev)
    return jsonify(_ov_out(res))


# ---------------------------------------------------------------------------------------------------------------- history of the plan of record's supply data

@bp.get("/history")
def history_list():
    try:
        limit = int(request.args.get("limit", 50))
    except ValueError:
        raise ApiError(400, "limit must be an integer") from None
    return jsonify({"changes": history.list_changes(db.get_conn(), limit)})


@bp.post("/history/revert")
def history_revert():
    body = _body()
    bad = sorted(set(body) - {"version"})
    v = body.get("version")
    if bad or isinstance(v, bool) or not isinstance(v, int):
        raise ApiError(400, "send {version: <integer>}")
    with rwdb.get_rw() as conn:
        return jsonify(history.revert_change(conn, v))


@bp.post("/history/restore")
def history_restore():
    """Restore orders, transfers, counts and prices to a version or time ({to_version} or {at}): undoes every later change still in force."""
    from datetime import datetime
    body = _body()
    bad = sorted(set(body) - {"to_version", "at"})
    if bad:
        raise ApiError(400, f"unknown field(s): {', '.join(bad)}")
    v, at = body.get("to_version"), body.get("at")
    if v is not None and (isinstance(v, bool) or not isinstance(v, int)):
        raise ApiError(400, "to_version must be an integer")
    when = None
    if at is not None:
        try:
            when = datetime.fromisoformat(str(at).replace("Z", "+00:00"))
        except ValueError:
            raise ApiError(400, "at must be an ISO date-time") from None
        if when.tzinfo is None:
            raise ApiError(400, "at must include a time zone (for example 2026-11-03T20:00:00-08:00)")
    with rwdb.get_rw() as conn:
        return jsonify(history.restore_to(conn, to_version=v, at=when))
