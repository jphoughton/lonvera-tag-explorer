"""HTTP layer: /api/projection/inventory-demand/{table,daily,inputs,countries,upload-intl}.

Auth, rate limits, the Origin check and the body caps are the core's (every non-GET under /api/projection/ is guarded there). Writes are audited:
PUT /inputs through the scenario store (proj_audit + version + restore), uploads through store.audit_load.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

from flask import Blueprint, jsonify, request

from ... import db
from ...queries import ApiError
from .. import audit, rwdb, scenarios
from .. import engine as core_engine
from . import levers as lv
from . import store, table, upload
from .constants import LOCATIONS, MODULE_KEY
from .engine import DemandDetail, resolve_as_of

bp = Blueprint("proj_inventory_demand", __name__)

MAX_DAILY_DAYS = 400
_PROD_RE = re.compile(r"^[a-z0-9_]{1,40}$")


# ------------------------------------------------------------------------------------------------------------------ helpers

def _arg(name: str) -> str | None:
    v = request.args.get(name)
    v = v.strip() if v else None
    return v or None


def _body() -> dict:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ApiError(400, "JSON object body required")
    return data


def _slug(conn, name: str | None) -> str:
    if name is None:
        return scenarios.default_slug(conn)
    scenarios.get_row(conn, name)                                   # 404 for an unknown scenario
    return name


def _run(conn, slug: str):
    res = core_engine.run(slug, conn)
    detail = res.results.get(MODULE_KEY)
    if not isinstance(detail, DemandDetail):
        raise ApiError(500, res.errors.get(MODULE_KEY) or "inventory demand could not be computed")
    return res, detail


def _location(raw: str | None) -> str:
    if raw not in LOCATIONS:
        raise ApiError(400, f"location must be one of {', '.join(LOCATIONS)}")
    return raw


# ------------------------------------------------------------------------------------------------------------------ reads

@bp.get("/table")
def get_table():
    conn = db.get_conn()
    slug = _slug(conn, _arg("scenario"))
    res, d = _run(conn, slug)
    payload = table.units_table(d)
    payload["scenario"] = slug
    payload["version"] = res.scenario.version
    payload["as_of"] = d.as_of.isoformat()
    payload["current_month"] = d.current_month.isoformat()
    return jsonify(payload)


@bp.get("/inputs")
def get_inputs():
    conn = db.get_conn()
    slug = _slug(conn, _arg("scenario"))
    res, d = _run(conn, slug)
    loads = store.read_inputs(conn).loads
    out = table.inputs_payload(d, res.scenario.params, loads, lv.parse(res.scenario.params).as_of is not None)
    out["scenario"], out["version"] = slug, res.scenario.version
    out["stock"] = store.read_stock_snapshot(conn)
    return jsonify(out)


@bp.get("/daily")
def get_daily():
    conn = db.get_conn()
    slug = _slug(conn, _arg("scenario"))
    loc = _location(_arg("location") or "revamp")
    prod = _arg("product") or "all"
    if prod != "all" and not _PROD_RE.match(prod):
        raise ApiError(400, "product must be a product key or 'all'")
    raw_days = _arg("days") or "120"
    if not raw_days.isdigit() or not 7 <= int(raw_days) <= MAX_DAILY_DAYS:
        raise ApiError(400, f"days must be a whole number between 7 and {MAX_DAILY_DAYS}")
    _, d = _run(conn, slug)
    first, last = store.data_range(conn, loc)
    if last is None:
        return jsonify(table.daily_payload([], d.as_of, d.as_of - timedelta(days=1), 0.0, None, loc, None if prod == "all" else prod))
    start = last - timedelta(days=int(raw_days) - 1)
    rows = store.read_daily_window(conn, loc, None if prod == "all" else prod, start, last)
    if prod == "all":
        rate = sum(v for (l, _p), v in d.rates.items() if l == loc)
    else:
        rate = d.rates.get((loc, prod), 0.0)
    return jsonify(table.daily_payload(rows, max(start, first or start), last, rate, d.window.get(loc), loc, None if prod == "all" else prod))


@bp.get("/countries")
def get_countries():
    rows = store.read_countries(db.get_conn())
    months = sorted({r["month"] for r in rows})
    by: dict[str, dict[date, float]] = {}
    days: dict[date, int | None] = {}
    for r in rows:
        by.setdefault(r["country"], {})[r["month"]] = r["units"]
        days[r["month"]] = r["days"]
    total = sum(sum(v.values()) for v in by.values())
    countries = [{"country": c, "units": [v.get(m, 0.0) for m in months], "total": sum(v.values()), "share": (sum(v.values()) / total) if total else 0.0}
                 for c, v in sorted(by.items(), key=lambda kv: -sum(kv[1].values()))]
    return jsonify({"months": [m.isoformat() for m in months], "days": [days.get(m) for m in months], "countries": countries, "total": total})


# ------------------------------------------------------------------------------------------------------------------ writes

@bp.put("/inputs")
def put_inputs():
    data = _body()
    patch = lv.validate_patch(data)
    expected = data.get("expected_version")
    if expected is not None and (isinstance(expected, bool) or not isinstance(expected, int)):
        raise ApiError(400, "expected_version must be an integer")
    scenario = data.get("scenario")
    if scenario is not None and not isinstance(scenario, str):
        raise ApiError(400, "scenario must be a slug")
    with rwdb.get_rw() as conn:
        slug = _slug(conn, scenario)
        row = scenarios.update(conn, slug, params={MODULE_KEY: patch}, expected_version=expected, actor=audit.current_actor())
    return jsonify({"scenario": row, "version": row["version"]})


@bp.post("/upload-intl")
def upload_intl():
    """Preview (default) or commit (?commit=1 with the digest the preview returned) a CSV of international warehouse shipments."""
    f = request.files.get("file")
    if f is None:
        raise ApiError(400, "send the CSV as multipart form field 'file'")
    data = f.read(upload.MAX_LINES * 200 + 1)
    conn = db.get_conn()
    scn = scenarios.get_scenario(conn, None)
    as_of = resolve_as_of(scn.params)
    parsed = upload.parse_csv(data, as_of)
    span = upload.span(parsed)
    existing: dict[tuple[str, date], dict] = {}
    if span:
        for r in store._rows(conn, "SELECT product, day, units::float AS units, is_estimate, source FROM proj_units_daily "
                                   "WHERE location = 'intl' AND day BETWEEN %s AND %s", (span[0], span[1])):
            existing[(r["product"], r["day"])] = r
    diff = upload.diff_against(existing, parsed)
    summary = {"preview": True, "digest": parsed.digest, "errors": parsed.errors, "diff": diff, "as_of": as_of.isoformat(), "filename": f.filename}
    if (request.args.get("commit") or "") not in ("1", "true"):
        return jsonify(summary)
    if parsed.errors:
        raise ApiError(400, f"fix {len(parsed.errors)} problem(s) in the file first: {parsed.errors[0]}")
    if request.form.get("digest") != parsed.digest:
        raise ApiError(400, "preview this exact file first: send the digest the preview returned")
    rows = [store.DailyRow("intl", p, d, u, "upload", False) for (p, d), u in sorted(parsed.cells.items(), key=lambda kv: (kv[0][1], kv[0][0]))]
    with rwdb.get_rw() as wconn:
        seen, changed = store.upsert_daily(wconn, rows)
        store.log_load(wconn, location="intl", source="upload", rows=seen, first_day=span[0], last_day=span[1],
                       note=f"{diff['replaces_estimate']} estimate rows replaced, {diff['changes_loaded']} loaded rows changed")
        store.audit_load(wconn, action="inventory_demand.upload_intl", summary={k: diff[k] for k in diff if k != "moves"}, note=f.filename)
    summary.update({"preview": False, "committed": True, "rows_written": changed})
    return jsonify(summary)
