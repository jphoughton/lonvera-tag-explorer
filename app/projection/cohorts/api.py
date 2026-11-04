"""/api/projection/cohorts: table, curves, cohorts, summary, levers (GET/PUT), upload (preview then commit), goal snapshot.

Auth, Origin check, rate limits, JSON-only bodies and size caps come from the projection write guard (api.install_guards); nothing is repeated here.
Every write is audited in proj_audit; lever edits are scenario edits (versioned, restorable) through scenarios.update.
"""
from __future__ import annotations

from datetime import date

from flask import Blueprint, jsonify, request

from ... import db
from ...queries import ApiError
from .. import audit, engine, rwdb, scenarios
from . import engine as eng
from . import store, upload, views
from . import model
from .model import CohortResultX

bp = Blueprint("proj_cohorts", __name__)


def _today() -> date:
    return model.today()


def _scenario_slug(conn) -> str:
    name = (request.args.get("scenario") or "").strip() or None
    if name is None:
        return scenarios.default_slug(conn)
    scenarios.get_row(conn, name)                             # 404 for an unknown scenario
    return name


def _run(conn=None, slug: str | None = None):
    conn = conn or db.get_conn()
    slug = slug or _scenario_slug(conn)
    res = engine.run(slug, conn, today=_today())              # not strict: another module's failure falls back to its stub and must not stop this page
    c = res.cohorts
    if not isinstance(c, CohortResultX):
        raise ApiError(500, res.errors.get("cohorts") or "cohorts module did not run")
    return res, c, slug


def _body() -> dict:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ApiError(400, "JSON object body required")
    return data


# ------------------------------------------------------------------------------------------------------------------ reads

@bp.get("/table")
def table():
    res, c, _ = _run()
    return jsonify(views.table_payload(c, views.actual_through_for(res.actual_through, c)))


@bp.get("/curves")
def curves():
    _, c, _ = _run()
    return jsonify(views.curves_payload(c))


@bp.get("/cohorts")
def cohorts():
    _, c, _ = _run()
    return jsonify(views.cohorts_payload(c, c.cohort_table))


@bp.get("/summary")
def summary():
    conn = db.get_conn()
    res, c, _ = _run(conn)
    today = _today()
    fresh = store.freshness(conn, today)[0]
    return jsonify(views.summary_payload(c, views.actual_through_for(res.actual_through, c), fresh, today))


# ------------------------------------------------------------------------------------------------------------------ levers

def _levers_payload(conn, slug: str) -> dict:
    row = scenarios.get_row(conn, slug)
    scn = scenarios.get_scenario(conn, slug)
    return {"scenario": slug, "version": row["version"], "levers": eng.parse_levers((scn.params or {}).get("cohorts"), strict=False).to_dict(),
            "own": (row["params"] or {}).get("cohorts", {}), "defaults": eng.DEFAULT_LEVERS.to_dict(), "bounds": eng.LEVER_BOUNDS}


@bp.get("/levers")
def levers_get():
    conn = db.get_conn()
    return jsonify(_levers_payload(conn, _scenario_slug(conn)))


@bp.put("/levers")
def levers_put():
    data = _body()
    patch = data.get("levers")
    if not isinstance(patch, dict) or not patch:
        raise ApiError(400, "levers must be a non-empty object")
    slug = data.get("scenario")
    if slug is not None and not isinstance(slug, str):
        raise ApiError(400, "scenario must be a slug")
    ev = data.get("expected_version")
    if ev is not None and (isinstance(ev, bool) or not isinstance(ev, int)):
        raise ApiError(400, "expected_version must be an integer")
    with rwdb.get_rw() as conn:
        slug = slug or scenarios.default_slug(conn)
        scn = scenarios.get_scenario(conn, slug)                 # 404 for an unknown scenario
        current = dict((scn.params or {}).get("cohorts") or {})
        check = {**current, **{k: v for k, v in patch.items() if v is not None}}
        for k in patch:
            if patch[k] is None:
                check.pop(k, None)
        try:
            eng.parse_levers(check, strict=True)                  # range checks, unknown keys, min <= n
        except ValueError as e:
            raise ApiError(400, str(e)) from None
        norm = {k: (eng.parse_curve_choice(v) if k == "curve" and v is not None else v) for k, v in patch.items()}
        scenarios.update(conn, slug, params={"cohorts": norm}, expected_version=ev, actor=audit.current_actor())
        return jsonify(_levers_payload(conn, slug))


# ------------------------------------------------------------------------------------------------------------------ upload

@bp.post("/upload")
def upload_cohorts():
    """multipart: file (CSV). Without ?commit=1 it only previews. To commit send ?commit=1 and the preview's sha256 as expect_sha256."""
    conn = db.get_conn()
    f = request.files.get("file")
    if f is None or not f.filename:
        raise ApiError(400, "send the CSV as multipart field 'file'")
    raw = f.read(upload.MAX_BYTES + 1)
    commit = (request.args.get("commit") or request.form.get("commit")) == "1"
    expect = (request.form.get("expect_sha256") or request.args.get("expect_sha256") or "").strip().lower()
    through_arg = (request.form.get("through") or request.args.get("through") or "").strip()
    min_arg = (request.form.get("min_customers") or request.args.get("min_customers") or "").strip()
    try:
        through = upload.parse_month_cell(through_arg) if through_arg else None
        min_customers = float(min_arg) if min_arg else upload.MIN_CUSTOMERS
        if not 0 <= min_customers <= 100000:
            raise ValueError("min_customers must be between 0 and 100000")
        parsed = upload.parse_csv(raw)
    except (upload.UploadError, ValueError) as e:
        raise ApiError(400, str(e)) from None
    today = _today()
    last_month = upload.default_through(today, f.filename, through)
    applied = upload.apply_rules(parsed, last_month, min_customers)
    summary = upload.diff(applied, store.load_cohorts(conn))
    out = {"committed": False, "layout": parsed.layout, "rows_read": parsed.rows_read, "sha256": parsed.sha256, "filename": f.filename[:120],
           "through": last_month.isoformat(), "min_customers": min_customers, "summary": summary, "warnings": applied.warnings}
    if not commit:
        return jsonify(out)
    if not expect:
        raise ApiError(400, "preview first: send the preview's sha256 as expect_sha256 with ?commit=1")
    if expect != parsed.sha256:
        raise ApiError(409, "the file differs from the one previewed; preview it again")
    if not applied.rows:
        raise ApiError(400, "nothing to load: no cohort passed the rules")
    with rwdb.get_rw() as rw:
        counts = store.upsert_cohorts(rw, applied.rows, source=f"upload {f.filename[:80]} {parsed.sha256[:12]}")
        audit.record(rw, actor=audit.current_actor(), scenario=None, action="cohorts.upload", note=f.filename[:120],
                     new={"sha256": parsed.sha256, "layout": parsed.layout, "rows_read": parsed.rows_read, "through": last_month.isoformat(),
                          "cohorts": counts["cohorts"], "cells": counts["cells"],
                          **{k: summary[k] for k in ("cohorts_added", "cohorts_changed", "cells_added", "cells_changed",
                                                     "cells_blanked_open_month", "cells_zero_dropped")}})
    out["committed"] = True
    out["loaded"] = counts
    return jsonify(out)


# ------------------------------------------------------------------------------------------------------------------ goal curve

@bp.post("/goal")
def goal_snapshot():
    """Take the scenario's Live curve as the new Goal curve (a dated snapshot; earlier snapshots are kept)."""
    data = _body()
    if data.get("from") != "live":
        raise ApiError(400, 'send {"from": "live"} to snapshot the Live curve as the Goal curve')
    slug = data.get("scenario")
    conn = db.get_conn()
    if slug is not None and not isinstance(slug, str):
        raise ApiError(400, "scenario must be a slug")
    slug = slug or scenarios.default_slug(conn)
    scenarios.get_row(conn, slug)
    _, c, _ = _run(conn, slug)
    today = _today()
    old_life, old_snap = c.lifetime_goal, c.goal_snapshot
    with rwdb.get_rw() as rw:
        n = store.replace_goal(rw, today, c.curve_live, note=f"Live curve of scenario {slug} on {today.isoformat()}")
        audit.record(rw, actor=audit.current_actor(), scenario=slug, action="cohorts.goal.snapshot", note="Live curve taken as the Goal curve",
                     old={"snapshot_date": old_snap.isoformat() if old_snap else None, "lifetime": old_life},
                     new={"snapshot_date": today.isoformat(), "lifetime": c.lifetime_live, "ages": n})
    return jsonify({"snapshot_date": today.isoformat(), "ages": n, "lifetime": c.lifetime_live})
