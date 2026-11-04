"""Scenario overlay: every input of the module is a named variable that a scenario can change without touching the stored defaults.

Stored rows (purchase orders, transfers, vendors, prices, reorder parameters, product costs) are the plan of record's values, seeded from the sheet.
A scenario keeps its own changes in `params["inventory_supply"]` (so they are versioned, audited and restorable by the core):

    <scalar variable>                 SETTING_SPECS keys: fba_target_months, po_round_units, ...
    vendors[<vendor>]                 {lead_weeks, moq_units, default_warehouse}
    prices["<vendor>|<product>"]      {unit_cost, deposit_pct}
    reorder["<location>|<product>"]   {lead_days, safety_days, moq_units}
    product_costs[<product>]          {china_unit_cost}
    pos["<id>"]                       field overrides of a stored order, or {"deleted": true}
    pos["new:<n>"]                    an order that exists only in the scenario (all its fields)
    transfers[...]                    the same for FBA transfers

`apply_overlay` turns stored inputs plus the effective params into the inputs the engine runs on. `set_variables` validates a change list and writes it.
"""
from __future__ import annotations

import copy
import re
from datetime import date
from typing import Any

from ...queries import ApiError
from .. import scenarios
from . import store
from .model import (PO, PRODUCTS, SETTING_SPECS, SPEC_BY_KEY, WAREHOUSES, Inputs, ProductCost, ReorderParam, Settings, Transfer, Vendor, VendorCost, normalize_product)

NS = "inventory_supply"
TABLE_KEYS = ("vendors", "prices", "reorder", "product_costs", "pos", "transfers")
VENDOR_FIELDS = ("lead_weeks", "moq_units", "default_warehouse")
PRICE_FIELDS = ("unit_cost", "deposit_pct")
REORDER_FIELDS = ("lead_days", "safety_days", "moq_units")
PCOST_FIELDS = ("china_unit_cost",)
PO_ENTRY_FIELDS = store.PO_FIELDS + ("source",)
TR_ENTRY_FIELDS = store.TRANSFER_FIELDS + ("source",)
_NEW = re.compile(r"^new:([1-9][0-9]{0,5})$")
_BASE_ID = re.compile(r"^[1-9][0-9]{0,11}$")


# ---------------------------------------------------------------------------------------------------------------- reading the overlay

PNL_NS = "pnl"
SHARED_COST_MAX = 1000.0                                                   # the P&L's own bound for a China cost per unit


def shared_china_costs(params: dict | None) -> dict[str, float]:
    """{sku: dollars per unit} from the scenario params the P&L writes (`pnl.cogs.china_cost.<sku>`): the single source of the China landed cost.
    Read from params only (no import of the P&L engine). Missing, null or mistyped entries are ignored."""
    cogs = ((params or {}).get(PNL_NS) or {}).get("cogs") if isinstance((params or {}).get(PNL_NS), dict) else None
    china = cogs.get("china_cost") if isinstance(cogs, dict) else None
    return {str(k): float(v) for k, v in (china or {}).items() if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and 0 <= v <= SHARED_COST_MAX}

def _ns(params: dict | None) -> dict:
    ns = (params or {}).get(NS)
    return ns if isinstance(ns, dict) else {}


def _dict_items(v: Any):
    return list(v.items()) if isinstance(v, dict) else []


def _po_dict(p: PO) -> dict:
    return {k: getattr(p, k) for k in store.PO_FIELDS}


def _tr_dict(t: Transfer) -> dict:
    return {k: getattr(t, k) for k in store.TRANSFER_FIELDS}


def _ref_parts(ref: str) -> tuple[str, int]:
    """('new', n) for 'new:n'; ('base', id) for a stored row id. ValueError otherwise."""
    m = _NEW.match(ref)
    if m:
        return "new", int(m.group(1))
    if _BASE_ID.match(ref):
        return "base", int(ref)
    raise ValueError(f"bad row reference {ref!r}")


def apply_overlay(inputs: Inputs, params: dict | None, *, strict: bool = False) -> Inputs:
    """Stored inputs with the scenario's changes applied. A change that no longer fits (a deleted row, an invalid value) is skipped and listed in
    `Inputs.notes` (shown as a data check); with `strict` it raises ApiError(400) instead (used when a change is being written)."""
    ns = _ns(params)
    shared = shared_china_costs(params)
    if not any(ns.get(k) for k in TABLE_KEYS) and not any(pc.sku in shared for pc in inputs.product_costs.values()):
        return inputs
    out = copy.deepcopy(inputs)
    settings = Settings.from_params(params)
    out.notes = list(out.notes)
    out.overridden = dict(out.overridden)

    def bad(msg: str) -> None:
        if strict:
            raise ApiError(400, msg)
        out.notes.append(msg)

    for name, d in _dict_items(ns.get("vendors")):
        if not isinstance(d, dict):
            continue
        base = out.vendors.get(name)
        merged = {"lead_weeks": base.lead_weeks if base else None, "moq_units": base.moq_units if base else 0.0,
                  "default_warehouse": base.default_warehouse if base else "revamp", **{k: v for k, v in d.items() if k in VENDOR_FIELDS}}
        try:
            out.vendors[name] = Vendor(name, store._number(merged, "lead_weeks", required=True, gt=0, hi=104), store._number(merged, "moq_units", lo=0, hi=1e9) or 0.0,
                                       store._enum(merged, "default_warehouse", WAREHOUSES, "revamp"), base.note if base else None)
            out.overridden[("vendor", name)] = tuple(sorted(k for k in d if k in VENDOR_FIELDS))
        except ApiError as e:
            bad(f"vendor {name}: {e.message if hasattr(e, 'message') else e}")
    for key, d in _dict_items(ns.get("prices")):
        vendor, _, prod = key.partition("|")
        prod = normalize_product(prod) or prod
        if not isinstance(d, dict) or vendor not in out.vendors or prod not in PRODUCTS:
            bad(f"price {key}: unknown vendor or product")
            continue
        base = out.vendor_costs.get((vendor, prod))
        merged = {"unit_cost": base.unit_cost if base else None, "deposit_pct": base.deposit_pct if base else None, **{k: v for k, v in d.items() if k in PRICE_FIELDS}}
        try:
            out.vendor_costs[(vendor, prod)] = VendorCost(vendor, prod, store._number(merged, "unit_cost", required=True, lo=0, hi=1e6),
                                                          store._number(merged, "deposit_pct", lo=0, hi=1), base.note if base else None)
            out.overridden[("price", f"{vendor}|{prod}")] = tuple(sorted(k for k in d if k in PRICE_FIELDS))
        except ApiError as e:
            bad(f"price {key}: {e}")
    for key, d in _dict_items(ns.get("reorder")):
        loc, _, prod = key.partition("|")
        prod = normalize_product(prod) or prod
        if not isinstance(d, dict) or loc not in WAREHOUSES or prod not in PRODUCTS:
            bad(f"reorder {key}: unknown location or product")
            continue
        base = out.reorder.get((loc, prod))
        merged = {"lead_days": base.lead_days if base else None, "safety_days": base.safety_days if base else round(settings.default_safety_days), "moq_units": base.moq_units if base else 0.0,
                  **{k: v for k, v in d.items() if k in REORDER_FIELDS}}
        try:
            out.reorder[(loc, prod)] = ReorderParam(loc, prod, int(store._number(merged, "lead_days", required=True, lo=0, hi=730)),
                                                    int(store._number(merged, "safety_days", lo=0, hi=365) or 0), store._number(merged, "moq_units", lo=0, hi=1e9) or 0.0)
            out.overridden[("reorder", f"{loc}|{prod}")] = tuple(sorted(k for k in d if k in REORDER_FIELDS))
        except ApiError as e:
            bad(f"reorder {key}: {e}")
    for prod, d in _dict_items(ns.get("product_costs")):
        prod = normalize_product(prod) or prod
        if not isinstance(d, dict) or prod not in PRODUCTS:
            bad(f"product cost {prod}: unknown product")
            continue
        base = out.product_costs.get(prod)
        try:
            cost = store._number({"china_unit_cost": d.get("china_unit_cost", base.china_unit_cost if base else None)}, "china_unit_cost", required=True, lo=0, hi=1e6)
            out.product_costs[prod] = ProductCost(prod, cost, base.sku if base else None, base.us_unit_cost if base else None)
            out.overridden[("product_cost", prod)] = tuple(sorted(k for k in d if k in PCOST_FIELDS))
        except ApiError as e:
            bad(f"product cost {prod}: {e}")
    for prod, pc in list(out.product_costs.items()):                                 # the P&L's China cost per SKU is the one source: it wins over the table and any overlay
        if pc.sku in shared:
            out.product_costs[prod] = ProductCost(prod, shared[pc.sku], pc.sku, pc.us_unit_cost, "pnl")
    out.pos = _apply_rows(out.pos, ns.get("pos"), "order", store.clean_po, _po_dict, _new_po, bad)
    out.transfers = _apply_rows(out.transfers, ns.get("transfers"), "transfer", store.clean_transfer, _tr_dict, _new_transfer, bad)
    return out


def _new_po(n: int, v: dict, entry: dict) -> PO:
    return PO(id=-n, ref=f"new:{n}", product=v["product"], vendor=v["vendor"], warehouse=v["warehouse"], units=v["units"], land_date=v["land_date"], status=v["status"],
              order_date=v["order_date"], deposit_pct=v["deposit_pct"], unit_cost=v["unit_cost"], po_number=v["po_number"], deposit_paid=v["deposit_paid"],
              shipping_paid=v["shipping_paid"], balance_paid=v["balance_paid"], link=v["link"], note=v["note"], source=str(entry.get("source") or "manual"))


def _new_transfer(n: int, v: dict, entry: dict) -> Transfer:
    return Transfer(id=-n, ref=f"new:{n}", product=v["product"], units=v["units"], ship_date=v["ship_date"], receive_date=v["receive_date"], status=v["status"],
                    transfer_no=v["transfer_no"], note=v["note"], source=str(entry.get("source") or "manual"))


def _apply_rows(rows: list, overlay: Any, noun: str, clean, to_dict, make_new, bad) -> list:
    by_id = {r.id: r for r in rows}
    added: list[tuple[int, Any]] = []
    for ref, d in _dict_items(overlay):
        if not isinstance(d, dict):
            continue
        try:
            kind, n = _ref_parts(str(ref))
        except ValueError as e:
            bad(str(e))
            continue
        try:
            if kind == "new":
                if d.get("deleted"):
                    continue
                v = clean({k: x for k, x in d.items() if k not in ("deleted", "source")})
                added.append((n, make_new(n, v, d)))
            else:
                row = by_id.get(n)
                if row is None:
                    bad(f"{noun} {n}: the stored row no longer exists")
                    continue
                if d.get("deleted"):
                    by_id.pop(n)
                    continue
                fields = {k: x for k, x in d.items() if k not in ("deleted", "source")}
                v = clean(fields, base=to_dict(row))
                new = copy.copy(row)
                for k, x in v.items():
                    setattr(new, k, x)
                new.ref, new.overridden = str(n), tuple(sorted(fields))
                by_id[n] = new
        except ApiError as e:
            bad(f"{noun} {ref}: {e}")
    return [by_id[r.id] for r in rows if r.id in by_id] + [r for _, r in sorted(added, key=lambda t: t[0])]


# ---------------------------------------------------------------------------------------------------------------- writing variables

def _check_lever(item: dict) -> tuple[str, Any]:
    name = item.get("name")
    spec = SPEC_BY_KEY.get(name)
    if spec is None:
        raise ApiError(400, f"unknown variable {name!r}")
    v = item.get("value")
    if v is None:
        return name, None
    if spec.kind == "date":
        try:
            return name, date.fromisoformat(str(v)[:10]).isoformat()
        except ValueError:
            raise ApiError(400, f"{spec.label}: use an ISO date (YYYY-MM-DD)") from None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or v in (float("inf"), float("-inf")):
        raise ApiError(400, f"{spec.label}: enter a number")
    if spec.minimum is not None and v < spec.minimum:
        raise ApiError(400, f"{spec.label}: must be at least {spec.minimum:g}")
    if spec.maximum is not None and v > spec.maximum:
        raise ApiError(400, f"{spec.label}: must be at most {spec.maximum:g}")
    if spec.kind == "int" and v != int(v):
        raise ApiError(400, f"{spec.label}: use a whole number")
    if name == "as_of":
        raise ApiError(400, "as_of is a date")
    return name, int(v) if spec.kind == "int" else float(v)


def _set_path(root: dict, path: list[str], value: Any) -> None:
    """Set root[path...] = value (None removes the key and prunes empty parents)."""
    node, stack = root, []
    for k in path[:-1]:
        if value is None and not isinstance(node.get(k), dict):
            return
        stack.append((node, k))
        node = node.setdefault(k, {})
        if not isinstance(node, dict):
            raise ApiError(400, "scenario parameters are malformed")
    if value is None:
        node.pop(path[-1], None)
        for parent, k in reversed(stack):
            if parent.get(k) == {}:
                parent.pop(k)
            else:
                break
    else:
        node[path[-1]] = value


def _item_path(item: dict) -> tuple[list[str], Any]:
    """(path under params[NS], value) of one change. value None = go back to the default."""
    kind = item.get("kind")
    if kind == "lever":
        name, v = _check_lever(item)
        return [name], v
    if kind == "vendor":
        v, f = item.get("vendor"), item.get("field")
        if not isinstance(v, str) or not v.strip() or len(v) > 60 or f not in VENDOR_FIELDS:
            raise ApiError(400, "vendor change needs vendor and field (lead_weeks, moq_units, default_warehouse)")
        return ["vendors", v.strip(), f], item.get("value")
    if kind == "price":
        v, p, f = item.get("vendor"), normalize_product(item.get("product")), item.get("field")
        if not isinstance(v, str) or not v.strip() or p is None or f not in PRICE_FIELDS:
            raise ApiError(400, "price change needs vendor, product and field (unit_cost, deposit_pct)")
        return ["prices", f"{v.strip()}|{p}", f], item.get("value")
    if kind == "reorder":
        loc, p, f = item.get("location"), normalize_product(item.get("product")), item.get("field")
        if loc not in WAREHOUSES or p is None or f not in REORDER_FIELDS:
            raise ApiError(400, "reorder change needs location (revamp, intl), product and field (lead_days, safety_days, moq_units)")
        return ["reorder", f"{loc}|{p}", f], item.get("value")
    if kind == "product_cost":
        p, f = normalize_product(item.get("product")), item.get("field")
        if p is None or f not in PCOST_FIELDS:
            raise ApiError(400, "product cost change needs product and field china_unit_cost")
        return ["product_costs", p, f], item.get("value")
    if kind in ("po", "transfer"):
        ref, f = str(item.get("id", "")), item.get("field")
        try:
            _ref_parts(ref)
        except ValueError as e:
            raise ApiError(400, str(e)) from None
        allowed = (PO_ENTRY_FIELDS if kind == "po" else TR_ENTRY_FIELDS) + ("deleted",)
        table = "pos" if kind == "po" else "transfers"
        if f is None:
            v = item.get("value")
            if v is not None and not isinstance(v, dict):
                raise ApiError(400, "a whole-row change needs an object (or null to remove the row)")
            if isinstance(v, dict):
                bad = sorted(set(v) - set(allowed))
                if bad:
                    raise ApiError(400, f"unknown field(s): {', '.join(bad)}")
            return [table, ref], v
        if f not in allowed:
            raise ApiError(400, f"unknown field {f!r}")
        return [table, ref, f], item.get("value")
    raise ApiError(400, f"unknown change kind {kind!r}")


def _diff_patch(old: Any, new: Any) -> Any:
    """Merge patch that turns `old` into `new` (None removes a key)."""
    if not isinstance(old, dict) or not isinstance(new, dict):
        return copy.deepcopy(new)
    patch: dict = {}
    for k in old:
        if k not in new:
            patch[k] = None
    for k, v in new.items():
        if k not in old:
            patch[k] = copy.deepcopy(v)
        elif old[k] != v:
            patch[k] = _diff_patch(old[k], v) if isinstance(old[k], dict) and isinstance(v, dict) else copy.deepcopy(v)
    return patch


def build_patch(own_ns: dict, items: list[dict]) -> dict:
    """The merge patch for params[NS] that applies `items` to the scenario's own params. Raises ApiError(400) on a malformed item."""
    if not isinstance(items, list) or not items:
        raise ApiError(400, "items must be a non-empty list")
    if len(items) > 500:
        raise ApiError(400, "at most 500 changes per request")
    new = copy.deepcopy(own_ns)
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise ApiError(400, f"items[{i}] must be an object")
        path, value = _item_path(item)
        if len(path) == 2 and isinstance(value, dict):                      # a whole row: replace, not merge
            new.setdefault(path[0], {})
            if not isinstance(new[path[0]], dict):
                new[path[0]] = {}
            new[path[0]][path[1]] = {k: v for k, v in value.items() if v is not None}
        else:
            _set_path(new, path, value)
    return _diff_patch(own_ns, new)


def set_variables(conn, slug: str, items: list[dict], *, base_inputs: Inputs, expected_version: int | None = None) -> dict:
    """Validate and write a list of variable changes into scenario `slug` (one version, audited, restorable). Returns the scenario row."""
    row = scenarios.get_row(conn, slug)
    own_ns = _ns(row["params"])
    if not isinstance(items, list) or not items:
        raise ApiError(400, "items must be a non-empty list")
    sku_of = {p: pc.sku for p, pc in base_inputs.product_costs.items() if pc.sku}
    mine, shared = [], []
    for item in items:                                                       # a China cost of a SKU belongs to the P&L's param, not to this module's overlay
        if isinstance(item, dict) and item.get("kind") == "product_cost" and item.get("field") == "china_unit_cost" and normalize_product(item.get("product")) in sku_of:
            shared.append((sku_of[normalize_product(item["product"])], item.get("value")))
        else:
            mine.append(item)
    patch = build_patch(own_ns, mine) if mine else {}
    params_patch: dict = {NS: patch} if patch else {}
    if shared:
        own_pnl = copy.deepcopy(row["params"].get(PNL_NS) or {})
        new_pnl = copy.deepcopy(own_pnl)
        for sku, v in shared:
            if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or not 0 <= v <= SHARED_COST_MAX):
                raise ApiError(400, f"China landed cost for {sku}: enter a number from 0 to {SHARED_COST_MAX:g}")
            _set_path(new_pnl, ["cogs", "china_cost", sku], None if v is None else float(v))
        pnl_patch = _diff_patch(own_pnl, new_pnl)
        if pnl_patch:
            params_patch[PNL_NS] = pnl_patch
    if not params_patch:
        return {"scenario": scenarios._public(row), "changed": 0}
    if patch:
        effective = scenarios.get_scenario(conn, slug).params
        prospective = scenarios.merge_patch(effective, {NS: patch})
        apply_overlay(base_inputs, prospective, strict=True)                # every changed order, transfer, vendor, price is validated as a whole
    scn = scenarios.update(conn, slug, params=params_patch, expected_version=expected_version)
    return {"scenario": scn, "changed": len(items)}


def next_new_number(effective_params: dict, table: str) -> int:
    nums = [int(m.group(1)) for k in _dict_items(_ns(effective_params).get(table)) if (m := _NEW.match(str(k[0])))]
    return max(nums, default=0) + 1


# ---------------------------------------------------------------------------------------------------------------- catalog (what the Variables view lists)

def _val(spec, ns: dict) -> Any:
    v = ns.get(spec.key)
    if spec.kind == "date":
        return v if isinstance(v, str) else None
    if v is None or isinstance(v, bool) or not isinstance(v, (int, float)):
        return spec.default
    return v


def catalog(base: Inputs, scenario_row: dict, effective_params: dict) -> dict:
    """Every variable of the module with its effective value, the sheet default, where it came from and whether this scenario changed it."""
    own, eff = _ns(scenario_row["params"]), _ns(effective_params)
    levers = []
    for spec in SETTING_SPECS:
        value = _val(spec, eff)
        origin = "scenario" if spec.key in own else ("inherited" if spec.key in eff else "default")
        levers.append({"key": spec.key, "label": spec.label, "unit": spec.unit, "group": spec.group, "default": spec.default, "value": value, "origin": origin,
                       "source": spec.source, "kind": spec.kind, "min": spec.minimum, "max": spec.maximum})
    applied = apply_overlay(base, effective_params)
    tables: dict[str, list] = {"vendors": [], "prices": [], "reorder": [], "product_costs": []}
    for name, v in sorted(applied.vendors.items()):
        b = base.vendors.get(name)
        tables["vendors"].append({"key": name, "overridden": list(applied.overridden.get(("vendor", name), ())), "new": b is None,
                                  "fields": {"lead_weeks": {"value": v.lead_weeks, "default": b.lead_weeks if b else None},
                                             "moq_units": {"value": v.moq_units, "default": b.moq_units if b else None},
                                             "default_warehouse": {"value": v.default_warehouse, "default": b.default_warehouse if b else None}}})
    for (vendor, prod), c in sorted(applied.vendor_costs.items()):
        b = base.vendor_costs.get((vendor, prod))
        tables["prices"].append({"key": f"{vendor}|{prod}", "overridden": list(applied.overridden.get(("price", f"{vendor}|{prod}"), ())), "new": b is None,
                                 "fields": {"unit_cost": {"value": c.unit_cost, "default": b.unit_cost if b else None},
                                            "deposit_pct": {"value": c.deposit_pct, "default": b.deposit_pct if b else None}}})
    for (loc, prod), r in sorted(applied.reorder.items()):
        b = base.reorder.get((loc, prod))
        tables["reorder"].append({"key": f"{loc}|{prod}", "overridden": list(applied.overridden.get(("reorder", f"{loc}|{prod}"), ())), "new": b is None,
                                  "fields": {"lead_days": {"value": r.lead_days, "default": b.lead_days if b else None},
                                             "safety_days": {"value": r.safety_days, "default": b.safety_days if b else None},
                                             "moq_units": {"value": r.moq_units, "default": b.moq_units if b else None}}})
    for prod, c in sorted(applied.product_costs.items()):
        b = base.product_costs.get(prod)
        tables["product_costs"].append({"key": prod, "overridden": list(applied.overridden.get(("product_cost", prod), ())), "new": b is None, "sku": c.sku,
                                        "source": "P&L cost model" if c.source == "pnl" else "table",
                                        "fields": {"china_unit_cost": {"value": c.china_unit_cost, "default": b.china_unit_cost if b else None}}})
    changes = []
    for table in TABLE_KEYS:
        for k, d in _dict_items(own.get(table)):
            if isinstance(d, dict):
                changes.append({"table": table, "key": k, "fields": sorted(d), "values": d})
    return {"levers": levers, "tables": tables, "changes": changes, "notes": list(applied.notes),
            "inherited_changes": sum(1 for t in TABLE_KEYS for k, _ in _dict_items(eff.get(t)) if k not in (own.get(t) or {}))}


# ---------------------------------------------------------------------------------------------------------------- accepting a plan into a scenario

def accept_plan(conn, slug: str, body: dict) -> dict:
    """Accept a plan into scenario `slug`: the proposed rows become scenario-only rows (source auto-plan); with replace_auto the scenario's current auto-plan
    rows are removed (stored ones are marked deleted for this scenario only). One version; same checks as store.accept_plan."""
    if not isinstance(body, dict):
        raise ApiError(400, "JSON object body required")
    bad = sorted(set(body) - {"pos", "transfers", "replace_auto", "expect_replaced"})
    if bad:
        raise ApiError(400, f"unknown field(s): {', '.join(bad)}")
    pos, trs = body.get("pos", []), body.get("transfers", [])
    if not isinstance(pos, list) or not isinstance(trs, list):
        raise ApiError(400, "pos and transfers must be lists")
    if len(pos) > store.MAX_ROWS_PER_PLAN or len(trs) > store.MAX_ROWS_PER_PLAN:
        raise ApiError(400, f"at most {store.MAX_ROWS_PER_PLAN} rows of each kind per plan")
    if not pos and not trs:
        raise ApiError(400, "the plan has no rows")
    replace = body.get("replace_auto", False)
    if not isinstance(replace, bool):
        raise ApiError(400, "replace_auto must be true or false")
    from . import loaders
    scn = scenarios.get_scenario(conn, slug)
    inputs = loaders.load_inputs(conn, scn)
    old_pos = [p for p in inputs.pos if p.source == "auto-plan"] if replace else []
    old_tr = [t for t in inputs.transfers if t.source == "auto-plan"] if replace else []
    if replace:
        exp = body.get("expect_replaced")
        if not isinstance(exp, dict) or not all(isinstance(exp.get(k), int) and not isinstance(exp.get(k), bool) for k in ("pos", "transfers")):
            raise ApiError(400, "expect_replaced {pos, transfers} (integers) is required with replace_auto")
        if (len(old_pos), len(old_tr)) != (exp["pos"], exp["transfers"]):
            raise ApiError(409, f"plan_stale: {len(old_pos)} auto-plan orders and {len(old_tr)} transfers exist now, the plan was made against "
                                f"{exp['pos']} and {exp['transfers']}. Suggest the plan again.")
    items: list[dict] = []

    def removal(kind: str, row) -> dict:
        ref = row.ref or str(row.id)
        return ({"kind": kind, "id": ref, "field": None, "value": None} if ref.startswith("new:") else {"kind": kind, "id": ref, "field": "deleted", "value": True})

    items += [removal("po", p) for p in old_pos] + [removal("transfer", t) for t in old_tr]
    n_po, n_tr = next_new_number(scn.params, "pos"), next_new_number(scn.params, "transfers")
    for i, p in enumerate(pos):
        v = store.clean_po({k: x for k, x in p.items() if k in store.PO_FIELDS})
        items.append({"kind": "po", "id": f"new:{n_po + i}", "field": None, "value": {**{k: (x.isoformat() if isinstance(x, date) else x) for k, x in v.items() if x is not None}, "source": "auto-plan"}})
    for i, t in enumerate(trs):
        v = store.clean_transfer({k: x for k, x in t.items() if k in store.TRANSFER_FIELDS})
        items.append({"kind": "transfer", "id": f"new:{n_tr + i}", "field": None, "value": {**{k: (x.isoformat() if isinstance(x, date) else x) for k, x in v.items() if x is not None}, "source": "auto-plan"}})
    res = set_variables(conn, slug, items, base_inputs=loaders.load_base(conn))
    return {"added_pos": [f"new:{n_po + i}" for i in range(len(pos))], "added_transfers": [f"new:{n_tr + i}" for i in range(len(trs))],
            "removed_pos": len(old_pos), "removed_transfers": len(old_tr), "scenario": {"slug": slug, "version": res["scenario"]["version"]}}


# ---------------------------------------------------------------------------------------------------------------- validation hook

def validate_params(params: dict, conn=None) -> None:
    """Check params["inventory_supply"] as a whole: scalar variables in range and typed, table overlays well formed (and, given a connection, valid against the
    stored rows). Raises ApiError(400); the core calls it for every change to a scenario's params when it supports module validators."""
    ns = _ns(params)
    for spec in SETTING_SPECS:
        if spec.key in ns and ns[spec.key] is not None:
            _check_lever({"name": spec.key, "value": ns[spec.key]})
    unknown = sorted(set(ns) - set(SPEC_BY_KEY) - set(TABLE_KEYS))
    if unknown:
        raise ApiError(400, f"unknown inventory_supply parameter(s): {', '.join(unknown)}")
    for table in TABLE_KEYS:
        v = ns.get(table)
        if v is not None and not isinstance(v, dict):
            raise ApiError(400, f"inventory_supply.{table} must be an object")
        for key, d in _dict_items(v):
            if not isinstance(d, dict):
                raise ApiError(400, f"inventory_supply.{table}[{key!r}] must be an object")
    if any(ns.get(t) for t in TABLE_KEYS):
        if conn is not None:
            from . import loaders
            apply_overlay(loaders.load_base(conn), params, strict=True)
        else:
            _validate_shapes(ns)


def _validate_shapes(ns: dict) -> None:
    """Without stored rows to check against: unknown row references and fields are still rejected."""
    for table, allowed in (("pos", PO_ENTRY_FIELDS + ("deleted",)), ("transfers", TR_ENTRY_FIELDS + ("deleted",))):
        for ref, d in _dict_items(ns.get(table)):
            try:
                _ref_parts(str(ref))
            except ValueError as e:
                raise ApiError(400, str(e)) from None
            bad = sorted(set(d) - set(allowed))
            if bad:
                raise ApiError(400, f"inventory_supply.{table}[{ref}]: unknown field(s) {', '.join(bad)}")
    for table, allowed in (("vendors", VENDOR_FIELDS), ("prices", PRICE_FIELDS), ("reorder", REORDER_FIELDS), ("product_costs", PCOST_FIELDS)):
        for key, d in _dict_items(ns.get(table)):
            bad = sorted(set(d) - set(allowed))
            if bad:
                raise ApiError(400, f"inventory_supply.{table}[{key}]: unknown field(s) {', '.join(bad)}")
