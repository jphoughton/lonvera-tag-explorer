"""Read, write, reset and compare variables, routed to each module's own mechanism and so to the core's audit / version / restore path.

    ctx = Context(conn, slug)                    one scenario, one engine run (lazily), shared by all reads of a request
    resolve(spec, ctx)    -> Resolved            current value, default, changed, where it comes from
    set_value(rw, slug, spec, value, ...)        validate -> params patch -> scenarios.update (param, pnl_driver) or the inventory overlay (supply)
    reset(rw, slug, spec)                        back to the sheet value (removes the scenario's own value, or writes the sheet value when the base differs)
    scenario_with(scn, spec, value)              an in-memory copy of the scenario with one value moved (sensitivity, tests); nothing is stored

A variable is `changed` when its effective value differs from the sheet value (spec.default). Series and tables have no single value: they report a
summary and, where it can be told, whether they differ from the seeded sheet state.
"""
from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ...queries import ApiError
from .. import audit, engine, scenarios, series as series_mod
from ..interfaces import Scenario
from . import registry
from .spec import VariableSpec

_ABSENT = object()


# ------------------------------------------------------------------------------------------------------------------ small helpers

def dig(d: Any, path: tuple[str, ...]) -> Any:
    cur = d
    for p in path:
        if not isinstance(cur, dict) or p not in cur:
            return _ABSENT
        cur = cur[p]
    return cur


def nest(path: tuple[str, ...], value: Any) -> dict:
    out: Any = value
    for p in reversed(path):
        out = {p: out}
    return out


def same(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b or a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=1e-9)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
    return a == b


def merge_patches(*patches: dict) -> dict:
    """Combine merge patches into one. A None leaf (remove this key) is kept as a None leaf, which RFC 7396 merging would drop."""
    def into(dst: dict, src: dict) -> None:
        for k, v in src.items():
            if isinstance(v, dict) and isinstance(dst.get(k), dict):
                into(dst[k], v)
            else:
                dst[k] = copy.deepcopy(v)
    out: dict = {}
    for p in patches:
        into(out, p)
    return out


# ------------------------------------------------------------------------------------------------------------------ context

class Context:
    """Everything a read needs for one scenario: effective params, own params, the engine result and the inventory overlay, each loaded once."""

    def __init__(self, conn, slug: str | None = None, *, scn: Scenario | None = None):
        self.conn = conn
        self.scn = scn or scenarios.get_scenario(conn, slug)
        self.slug = self.scn.slug
        self._own = None
        self._run = None
        self._applied = None
        self._base_inputs = None

    @property
    def own_params(self) -> dict:
        if self._own is None:
            self._own = scenarios.get_scenario(self.conn, self.slug, effective=False).params or {}
        return self._own

    @property
    def run(self):
        if self._run is None:
            self._run = engine.run(self.scn, self.conn)
        return self._run

    @property
    def supply_base(self):
        if self._base_inputs is None:
            from ..inventory_supply import loaders
            self._base_inputs = loaders.load_base(self.conn)
        return self._base_inputs

    @property
    def supply_applied(self):
        if self._applied is None:
            from ..inventory_supply import overlay
            self._applied = overlay.apply_overlay(self.supply_base, self.scn.params)
        return self._applied


@dataclass
class Resolved:
    value: Any = None
    default: Any = None
    changed: bool | None = False
    origin: str = "default"                     # scenario (own), inherited (from the base scenario) or default (the sheet / stored plan of record)
    summary: dict | None = None
    computed: Any = None                        # a value the model computes when the variable is blank (trailing average, rate)
    note: str | None = None


# ------------------------------------------------------------------------------------------------------------------ resolve

def _origin(ctx: Context, path: tuple[str, ...]) -> str:
    if dig(ctx.own_params, path) is not _ABSENT:
        return "scenario"
    if dig(ctx.scn.params, path) is not _ABSENT:
        return "inherited"
    return "default"


def _supply_value(ctx: Context, path: tuple[str, ...]) -> Any:
    ap = ctx.supply_applied
    _, table, key, field_ = path
    if table == "vendors":
        v = ap.vendors.get(key)
        return getattr(v, field_) if v else None
    if table == "prices":
        vendor, prod = key.split("|")
        v = ap.vendor_costs.get((vendor, prod))
        return getattr(v, field_) if v else None
    if table == "reorder":
        loc, prod = key.split("|")
        v = ap.reorder.get((loc, prod))
        return getattr(v, field_) if v else None
    if table == "product_costs":
        v = ap.product_costs.get(key)
        return getattr(v, field_) if v else None
    return None


def _pnl_driver_value(ctx: Context, spec: VariableSpec) -> tuple[Any, Any]:
    res = ctx.run.results.get("pnl")
    info = getattr(res, "driver_info", None) or {}
    e = info.get(spec.path[-1], {})
    mode = e.get("mode")
    if mode == "fixed":
        return e.get("value"), None
    if mode == "rate":
        return e.get("rate"), e.get("rate")
    return None, None


def _demand_rate(ctx: Context, spec: VariableSpec) -> Any:
    ex = spec.extra or {}
    d = ctx.run.results.get("inventory_demand")
    rates = getattr(d, "rates", None) or {}
    return rates.get((ex.get("loc"), ex.get("product")))


def _payback_input(ctx: Context, key: str) -> Any:
    fin = ctx.run.results.get("financing")
    return (getattr(fin, "payback_inputs", None) or {}).get(key)


def resolve(spec: VariableSpec, ctx: Context) -> Resolved:
    if spec.binding == "none":
        return Resolved(value=None, default=spec.default, changed=False, origin="default")
    if spec.binding == "param":
        raw = dig(ctx.scn.params, spec.path)
        pair = (spec.extra or {}).get("pair")
        if pair and dig(ctx.scn.params, pair[0]) is _ABSENT:                         # the module reads a pair of bounds only when both are stored
            raw = _ABSENT
        value = spec.default if raw is _ABSENT else raw
        r = Resolved(value=value, default=spec.default, origin=_origin(ctx, spec.path))
        r.changed = not same(value, spec.default)
        comp = (spec.extra or {}).get("computed")
        if comp == "demand_rate":
            r.computed = _demand_rate(ctx, spec)
        elif comp in ("payback_aov", "payback_cac"):
            r.computed = _payback_input(ctx, comp.split("_")[1])
        return r
    if spec.binding == "supply":
        value = _supply_value(ctx, spec.path)
        if value is None:
            value = spec.default
        r = Resolved(value=value, default=spec.default, origin=_origin(ctx, spec.path))
        r.changed = not same(value, spec.default)
        return r
    if spec.binding == "pnl_driver":
        value, computed = _pnl_driver_value(ctx, spec)
        own = dig(ctx.scn.params, spec.path)
        r = Resolved(value=value, default=spec.default, origin=_origin(ctx, spec.path), computed=computed)
        if own is _ABSENT:
            r.changed = False
        elif spec.default is not None and value is not None and same(value, spec.default):
            r.changed = False
        else:
            r.changed = True
        return r
    if spec.binding == "series":
        return _resolve_series(spec, ctx)
    return _resolve_table(spec, ctx)


# ---- series and tables

def _series_cells(ctx: Context, key: str) -> list[dict]:
    if key.endswith("."):
        return series_mod.fetch_cells(ctx.conn, ctx.scn, prefix=key)
    return series_mod.fetch_cells(ctx.conn, ctx.scn, [key])


def _baseline_cells(conn, key: str) -> dict | None:
    """The seeded sheet state of a series: the oldest history snapshot of the default scenario (taken before the first change)."""
    row = conn.execute("SELECT h.snapshot FROM proj_scenario_history h JOIN proj_scenarios s ON s.id = h.scenario_id WHERE s.is_default "
                       "ORDER BY h.version ASC LIMIT 1").fetchone()
    if not row:
        return None
    out: dict = {}
    for k, month, value, _kind, _note in row["snapshot"].get("series", []):
        if k == key or (key.endswith(".") and k.startswith(key)):
            out[(k, month)] = float(value)
    return out


def _resolve_series(spec: VariableSpec, ctx: Context) -> Resolved:
    cells = _series_cells(ctx, spec.series_key)
    vals = [c["value"] for c in cells]
    months = sorted(c["month"] for c in cells)
    summary = {"cells": len(cells), "first": months[0].isoformat() if months else None, "last": months[-1].isoformat() if months else None,
               "min": min(vals) if vals else None, "max": max(vals) if vals else None, "sheet_cells": spec.series_cells,
               "text": (f"{len(cells)} typed months, {months[0]:%b %Y} to {months[-1]:%b %Y}" if cells else "No typed months")}
    base = _baseline_cells(ctx.conn, spec.series_key)
    changed: bool | None
    if base is None:
        changed = False
    else:
        now = {(c["line_key"], c["month"].isoformat()): c["value"] for c in cells}
        changed = (set(now) != set(base)) or any(not same(now[k], base[k]) for k in now)
    return Resolved(value=None, default=None, changed=changed, origin="scenario" if any(c["origin"] == ctx.slug for c in cells) else "default", summary=summary)


def _count(conn, sql: str, args: tuple = ()) -> int | None:
    try:
        r = conn.execute(sql, args).fetchone()
        return int(next(iter(r.values())))
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return None


def _resolve_table(spec: VariableSpec, ctx: Context) -> Resolved:
    conn, name = ctx.conn, spec.id
    text, changed = "Edit on the module page", None
    if name == "cohorts.cohort_table":
        n = _count(conn, "SELECT count(*) FROM proj_cohorts")
        text = f"{n} cohorts" if n is not None else text
    elif name == "cohorts.goal_curve":
        n = _count(conn, "SELECT count(DISTINCT snapshot_date) FROM proj_curve_goal")
        text = f"{n} saved snapshot{'s' if n != 1 else ''}, newest in use" if n is not None else text
        changed = bool(n and n > 1)
    elif name == "pnl.monthly_overrides":
        cells = [c for c in _series_cells(ctx, "pnl.") if c["kind"] == "override"]
        text, changed = (f"{len(cells)} typed month{'s' if len(cells) != 1 else ''}" if cells else "No typed months"), bool(cells)
    elif name == "pnl.cogs_sku":
        n = _count(conn, "SELECT count(*) FROM proj_pnl_cogs_sku")
        text = f"{n} SKUs" if n is not None else text
    elif name == "pnl.qb_actuals":
        r = conn.execute("SELECT count(DISTINCT month) AS n, max(month) AS m FROM proj_actuals WHERE left(line_key, 4) = 'pnl.'").fetchone()
        text = f"{r['n']} months loaded, through {r['m']:%b %Y}" if r and r["m"] else "No months loaded"
    elif name.startswith("pnl.upload."):
        r = conn.execute("SELECT value FROM proj_pnl_settings WHERE key = 'upload'").fetchone()
        v = (r["value"] or {}) if r else {}
        key = name.split(".")[-1]
        shown = v.get(key)
        text = ("from the module default" if shown is None else (", ".join(shown) if isinstance(shown, list) else f"{shown:g}"))
    elif name == "pnl.vendor_retainers":
        n = _count(conn, "SELECT count(*) FROM proj_pnl_vendors")
        text = f"{n} vendors, reference only" if n is not None else text
    elif name in ("inventory_demand.stock_counts", "inventory_supply.stock_counts"):
        n = _count(conn, "SELECT count(DISTINCT (location, product)) FROM proj_stock_snapshot")
        text = f"{n} location and product counts" if n is not None else text
    elif name == "inventory_supply.purchase_orders":
        n = len(ctx.supply_applied.pos)
        text = f"{n} orders"
        changed = bool(dig(ctx.own_params, ("inventory_supply", "pos")) not in (_ABSENT, None, {}))
    elif name == "inventory_supply.fba_transfers":
        n = len(ctx.supply_applied.transfers)
        text = f"{n} transfers"
        changed = bool(dig(ctx.own_params, ("inventory_supply", "transfers")) not in (_ABSENT, None, {}))
    elif name == "financing.tax_payments":
        pays = dig(ctx.scn.params, ("financing", "tax", "payments"))
        n = len(pays) if isinstance(pays, list) else 0
        text = f"{n} payments"
    elif name == "financing.payout_columns":
        cols = dig(ctx.scn.params, ("financing", "payout", "columns"))
        n = len(cols) if isinstance(cols, list) else 0
        text = f"{n} payback columns"
    elif name == "pacing.cost_drivers":
        v = dig(ctx.scn.params, ("pacing", "cost_drivers"))
        n = len(v) if isinstance(v, dict) else 0
        text, changed = (f"{n} line{'s' if n != 1 else ''} changed" if n else "Sheet drivers"), bool(n)
    elif name.startswith("pacing.feed."):
        n = _count(conn, "SELECT count(*) FROM proj_pacing_setting WHERE key = %s", (name.split(".")[-1],))
        text, changed = ("Edited rule" if n else "Sheet rule"), bool(n)
    elif name == "pacing.hourly_spend_curve":
        text = "24 hourly values"
    return Resolved(value=None, default=None, changed=changed, origin="default", summary={"text": text})


# ------------------------------------------------------------------------------------------------------------------ coerce and validate

def _num(raw: Any, spec: VariableSpec) -> float:
    if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
        raise ApiError(400, f"{spec.label}: enter a number")
    try:
        v = float(raw)
    except (TypeError, ValueError):
        raise ApiError(400, f"{spec.label}: enter a number") from None
    if not math.isfinite(v):
        raise ApiError(400, f"{spec.label}: enter a finite number")
    return v


def _month_iso(raw: Any, spec: VariableSpec) -> str:
    s = str(raw).strip()
    if len(s) == 7:
        s += "-01"
    try:
        d = date.fromisoformat(s[:10])
    except ValueError:
        raise ApiError(400, f"{spec.label}: use a month like 2026-11") from None
    if spec.value_type == "month" and d.day != 1:
        raise ApiError(400, f"{spec.label}: use the first of a month")
    return d.isoformat()


def coerce(spec: VariableSpec, raw: Any) -> Any:
    """The typed value a spec stores, or ApiError 400 naming the variable. None passes through (reset)."""
    if raw is None:
        return None
    t = spec.value_type
    if t == "bool":
        if not isinstance(raw, bool):
            raise ApiError(400, f"{spec.label}: choose on or off")
        return raw
    if t in ("number", "money", "fraction", "integer"):
        v = _num(raw, spec)
        if t == "integer":
            if v != int(v):
                raise ApiError(400, f"{spec.label}: use a whole number")
            v = int(v)
        if spec.lo is not None and v < spec.lo:
            raise ApiError(400, f"{spec.label}: must be at least {spec.lo:g}")
        if spec.hi is not None and v > spec.hi:
            raise ApiError(400, f"{spec.label}: must be at most {spec.hi:g}")
        return v
    if t == "choice":
        if raw not in (spec.choices or ()):
            raise ApiError(400, f"{spec.label}: choose one of {', '.join(spec.choices or ())}")
        return raw
    if t in ("month", "date"):
        return _month_iso(raw, spec)
    if t == "text":
        s = str(raw).strip()
        if not s or len(s) > 120:
            raise ApiError(400, f"{spec.label}: enter text of 1 to 120 characters")
        return s
    if t == "list":
        if isinstance(raw, str):
            raw = [x.strip() for x in raw.replace("\n", ",").split(",") if x.strip()]
        if not isinstance(raw, list):
            raise ApiError(400, f"{spec.label}: enter a list separated by commas")
        if spec.choices:
            bad = [x for x in raw if x not in spec.choices]
            if bad:
                raise ApiError(400, f"{spec.label}: {bad[0]!r} is not one of {', '.join(spec.choices)}")
            return list(raw)
        sample = spec.default[0] if isinstance(spec.default, list) and spec.default else 0
        if isinstance(sample, (int, float)) and not isinstance(sample, bool):
            vals = [_num(x, spec) for x in raw]
            return [int(v) if isinstance(sample, int) and v == int(v) else v for v in vals]
        return list(raw)
    return raw


def module_validate(spec: VariableSpec, ns_patch: dict, ctx_params: dict) -> dict:
    """Run the owning module's own validation on the namespace patch; returns the (possibly normalised) patch. ApiError 400 on a bad value."""
    m = spec.module
    try:
        if m == "cohorts":
            from ..cohorts import engine as eng
            cur = dict((ctx_params or {}).get("cohorts") or {})
            merged = scenarios.merge_patch(cur, ns_patch.get("cohorts", {}))
            eng.parse_levers(merged, strict=True)
        elif m == "drivers":
            from ..drivers import api as dapi
            dapi._check_levers(ns_patch.get("drivers", {}))
        elif m == "pnl" and spec.binding == "param":
            from ..pnl import api as papi
            papi.check_settings(ns_patch.get("pnl", {}))
        elif m == "inventory_demand":
            from ..inventory_demand import levers as dl
            dl.validate_patch(ns_patch.get("inventory_demand", {}))
        elif m == "inventory_supply" and spec.binding == "param":
            from ..inventory_supply import overlay
            name = spec.path[-1]
            val = dig(ns_patch, spec.path)
            overlay._check_lever({"name": name, "value": None if val is _ABSENT else val})
        elif m == "financing":
            from ..financing import params as fp
            fp.validate_patch(ns_patch.get("financing", {}))
        elif m == "modeler":
            from ..modeler import model as mm
            val = dig(ns_patch, spec.path)
            mm.check_addbacks([] if val is _ABSENT or val is None else val)
        elif m == "pacing":
            from ..pacing import levers as pl
            key = spec.id.split(".", 1)[1]
            val = dig(ns_patch, spec.path)
            pl.validate_patch({key: None if val is _ABSENT else val})
    except ApiError:
        raise
    except ValueError as e:
        raise ApiError(400, f"{spec.label}: {e}") from None
    return ns_patch


# ------------------------------------------------------------------------------------------------------------------ patches

SHARE_KEYS = ("amazon", "meta", "google", "bing", "tiktok")


def _rebalance_shares(ctx: Context, key: str, value: float) -> dict:
    """Setting one channel share moves the other four proportionally so the five still add up to 100%."""
    cur = dict(dig(ctx.scn.params, ("drivers", "channel_share")) or {}) if dig(ctx.scn.params, ("drivers", "channel_share")) is not _ABSENT else {}
    from ..drivers.model import DEFAULT_SHARES
    shares = {k: float(cur.get(k, DEFAULT_SHARES[k])) for k in SHARE_KEYS}
    others = [k for k in SHARE_KEYS if k != key]
    rest = sum(shares[k] for k in others)
    target = 1.0 - value
    if target < -1e-12:
        raise ApiError(400, "Channel shares cannot add up to more than 100%")
    if rest <= 0:
        if target > 1e-9:
            raise ApiError(400, "The other channels have no spend to rebalance; edit them first")
        scaled = {k: 0.0 for k in others}
    else:
        scaled = {k: shares[k] * target / rest for k in others}
    out = {**scaled, key: value}
    return {k: round(out[k], 10) for k in SHARE_KEYS}


def _driver_segments(ctx: Context, spec: VariableSpec, value: float) -> list[dict]:
    """The scenario's steps for a cost line with the long-run step set to `value` (fixed dollars, or a typed rate of the line's revenue base)."""
    key = spec.path[-1]
    res = ctx.run.results.get("pnl")
    info = (getattr(res, "driver_info", None) or {}).get(key, {})
    segs = copy.deepcopy(info.get("segments") or [])
    ex = spec.extra or {}
    last = segs[-1] if segs else {"from": None}
    frm = last.get("from")
    if ex.get("mode") == "rate":
        new = {"from": frm, "mode": "rate", "base": ex["base"], "rate_kind": "typed", "rate": float(value)}
    else:
        new = {"from": frm, "mode": "fixed", "value": float(value)}
    if segs:
        segs[-1] = new
    else:
        segs = [new]
    return segs


def build_patch(ctx: Context, spec: VariableSpec, value: Any) -> dict:
    """The params merge patch that sets `spec` to `value` (None removes the scenario's own value). For supply bindings see supply_item."""
    if spec.binding == "pnl_driver":
        from ..pnl import model as pm
        if value is None:
            return nest(spec.path[:-1], {spec.path[-1]: None})
        try:
            segs = pm.validate_segments(spec.path[-1], _driver_segments(ctx, spec, value))
        except ValueError as e:
            raise ApiError(400, f"{spec.label}: {e}") from None
        return nest(spec.path, {"segments": segs})
    pair = (spec.extra or {}).get("pair")
    if pair:                                                                          # two bounds the module saves as a pair: write both, or reset both
        sib_path, sib_default = pair
        if value is None:
            return merge_patches(nest(spec.path, None), nest(sib_path, None))
        sib = dig(ctx.scn.params, sib_path)
        return merge_patches(nest(spec.path, value), nest(sib_path, sib_default if sib is _ABSENT else sib))
    if spec.id.startswith("drivers.channel_share.") and value is not None:
        shares = _rebalance_shares(ctx, spec.path[-1], float(value))
        return {"drivers": {"channel_share": shares}}
    patches = [nest(spec.path, value)]
    for m in spec.mirrors:
        patches.append(nest(m, value))
    return merge_patches(*patches)


def supply_item(spec: VariableSpec, value: Any) -> dict:
    _, table, key, field_ = spec.path
    if table == "vendors":
        return {"kind": "vendor", "vendor": key, "field": field_, "value": value}
    if table == "prices":
        vendor, prod = key.split("|")
        return {"kind": "price", "vendor": vendor, "product": prod, "field": field_, "value": value}
    if table == "reorder":
        loc, prod = key.split("|")
        return {"kind": "reorder", "location": loc, "product": prod, "field": field_, "value": value}
    return {"kind": "product_cost", "product": key, "field": field_, "value": value}


# ------------------------------------------------------------------------------------------------------------------ write

def _check_writable(spec: VariableSpec) -> None:
    if not spec.editable_inline:
        raise ApiError(400, f"{spec.label} is edited on its own page ({spec.page or 'module page'}), not here")


def _apply(conn, slug: str, ctx: Context, plan: list[tuple[VariableSpec, Any]], *, expected_version: int | None, actor: str | None) -> dict:
    """plan = [(spec, coerced value or None)]. Param-bound and driver items are merged into one scenario update; supply items go through the overlay."""
    param_patches, supply_items = [], []
    for spec, value in plan:
        if spec.binding == "supply":
            supply_items.append(supply_item(spec, value))
            continue
        patch = build_patch(ctx, spec, value)
        if value is not None:
            for ns in patch:
                module_validate(spec, {ns: patch[ns]}, ctx.scn.params)
        param_patches.append(patch)
    row = None
    if param_patches:
        row = scenarios.update(conn, slug, params=merge_patches(*param_patches), expected_version=expected_version, actor=actor)
        expected_version = None
    if supply_items:
        from ..inventory_supply import loaders, overlay
        res = overlay.set_variables(conn, slug, supply_items, base_inputs=loaders.load_base(conn), expected_version=expected_version)
        row = res.get("scenario") or row
    return row or scenarios._public(scenarios.get_row(conn, slug))


def set_values(conn, slug: str, items: list[tuple[VariableSpec, Any]], *, expected_version: int | None = None, actor: str | None = None) -> dict:
    """Set several variables of one scenario (value None = reset to the sheet value). One request, at most two versions."""
    if not items:
        raise ApiError(400, "items must be a non-empty list")
    ctx = Context(conn, slug)
    plan: list[tuple[VariableSpec, Any]] = []
    for spec, raw in items:
        _check_writable(spec)
        plan.append((spec, coerce(spec, raw)))
    plan = [(s, _reset_value(ctx, s) if v is None else v) for s, v in plan]
    return _apply(conn, slug, ctx, plan, expected_version=expected_version, actor=actor)


def set_value(conn, slug: str, spec: VariableSpec, value: Any, *, expected_version: int | None = None, actor: str | None = None) -> dict:
    return set_values(conn, slug, [(spec, value)], expected_version=expected_version, actor=actor)


def _reset_value(ctx: Context, spec: VariableSpec) -> Any:
    """What to write to go back to the sheet value: None (drop the scenario's own value) when the base scenario already gives the sheet value,
    else the sheet value itself."""
    if spec.binding == "pnl_driver":
        return None
    if spec.binding == "supply":
        return None
    base_slug = ctx.scn.base_slug
    if not base_slug:
        return None
    base = scenarios.get_scenario(ctx.conn, base_slug)
    inherited = dig(base.params, spec.path)
    inherited = spec.default if inherited is _ABSENT else inherited
    return None if same(inherited, spec.default) else spec.default


def reset(conn, slug: str, specs: list[VariableSpec], *, expected_version: int | None = None, actor: str | None = None) -> dict:
    ctx = Context(conn, slug)
    plan = []
    for spec in specs:
        _check_writable(spec)
        v = _reset_value(ctx, spec)
        plan.append((spec, v))
    if not plan:
        raise ApiError(400, "nothing to reset")
    return _apply(conn, slug, ctx, plan, expected_version=expected_version, actor=actor)


# ------------------------------------------------------------------------------------------------------------------ in-memory copies

def scenario_with(ctx: Context, spec: VariableSpec, value: Any) -> Scenario:
    """A copy of the scenario whose params carry `value` for `spec`; nothing is stored. Used by the sensitivity run and the proof tests."""
    scn = copy.deepcopy(ctx.scn)
    if spec.binding == "supply":
        patch = nest(spec.path, value)
    else:
        patch = build_patch(ctx, spec, value)
    scn.params = scenarios.merge_patch(scn.params or {}, patch)
    return scn


def moved_value(spec: VariableSpec, current: Any) -> Any:
    """A different, valid value for `spec`: +25% of the current for numbers (clamped to the bounds), the other choice for choices, the flipped flag."""
    t = spec.value_type
    if (spec.extra or {}).get("alt") is not None:
        return spec.extra["alt"]
    if t == "bool":
        return not bool(current)
    if t == "choice":
        return next((c for c in spec.choices or () if c != current), current)
    if t in ("number", "money", "fraction", "integer"):
        cur = float(current) if isinstance(current, (int, float)) and not isinstance(current, bool) else 0.0
        step = spec.step or (1.0 if t == "integer" else 0.01)
        lo = spec.lo if spec.lo is not None else -1e18
        hi = spec.hi if spec.hi is not None else 1e18
        cands = [cur * 1.25, cur * 0.75, cur + step, cur - step, lo + (min(hi, lo + 100) - lo) * 0.25 if lo > -1e17 else 1.0, hi - step if hi < 1e17 else 2.0]
        for v in cands:
            if t == "integer":
                v = int(round(v))
            if lo <= v <= hi and not math.isclose(v, cur, rel_tol=1e-9, abs_tol=1e-12):
                return v
        return cur
    if t == "month":
        d = date.fromisoformat(str(current)[:10]) if current else date(2026, 9, 1)
        return (date(d.year + (d.month // 12), d.month % 12 + 1, 1)).isoformat()
    return current
