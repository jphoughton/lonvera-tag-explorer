"""/api/projection/cohorts: table, curves, cohorts, summary, levers (GET/PUT), upload (preview then commit), goal snapshot.

Auth, Origin check, rate limits, JSON-only bodies and size caps come from the projection write guard (api.install_guards); nothing is repeated here.
Every write is audited in proj_audit; lever edits are scenario edits (versioned, restorable) through scenarios.update.
"""
from __future__ import annotations

from datetime import date

import psycopg
from flask import Blueprint, jsonify, request

from ... import db
from ...queries import ApiError
from .. import audit, engine, rwdb, scenarios
from . import engine as eng
from . import checks
from . import compare as cmp
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
    out = views.summary_payload(c, views.actual_through_for(res.actual_through, c), fresh, today)
    out["last_upload"] = store.last_upload(conn)
    out["goal"] = store.goal_overview(conn)
    return jsonify(out)


# ------------------------------------------------------------------------------------------------------------------ curve choices

def _n_arg(default: int) -> int:
    raw = (request.args.get("n") or "").strip()
    if not raw:
        return default
    try:
        n = int(raw)
    except ValueError:
        raise ApiError(400, "n must be a whole number of cohorts") from None
    if not 1 <= n <= 36:
        raise ApiError(400, "n must be between 1 and 36")
    return n


def _month_arg(name: str) -> date | None:
    raw = (request.args.get(name) or "").strip()
    if not raw:
        return None
    try:
        return eng.parse_month_value(raw)
    except ValueError as e:
        raise ApiError(400, str(e).replace("asof_month", name)) from None


@bp.get("/curve-options")
def curve_options():
    """What the curve picker offers: the saved choice, the sources, the cohort counts, and every month with whether an as-of curve can be built."""
    conn = db.get_conn()
    slug = _scenario_slug(conn)
    scn = scenarios.get_scenario(conn, slug)
    try:
        goal, snap = store.load_goal(conn)
        out = cmp.options(store.load_cohorts(conn), scn, _today(), snap, _n_arg(cmp.effective_levers(scn).n_cohorts))
    except psycopg.errors.UndefinedTable:                       # the cohort tables are not in this database: the picker hides itself
        conn.rollback()
        return jsonify({"scenario": slug, "version": scn.version, "available": False})
    return jsonify({"scenario": slug, "version": scn.version, "available": True, **out})


@bp.get("/curve-compare")
def curve_compare():
    """Current, Goal and an as-of curve through the whole engine in memory: curve, multiples, the P&L year's repeat revenue, revenue, net operating
    income and implied value, and the change against Current. Reads only; saving a choice is PUT /levers."""
    conn = db.get_conn()
    slug = _scenario_slug(conn)
    scn = scenarios.get_scenario(conn, slug)
    saved = cmp.effective_levers(scn)
    cohorts = store.load_cohorts(conn)
    n = _n_arg(saved.n_cohorts)
    asof, asof2 = _month_arg("asof"), _month_arg("asof2")
    if asof is None:
        asof = saved.asof_month or cmp.default_asof(cmp.snapshot_list(cohorts, scn, _today(), n))
    if asof is None:
        raise ApiError(400, "No month has enough cohort history to build an as-of curve yet.")
    try:
        return jsonify(cmp.compare(conn, scn, _today(), asof, n, asof2))
    except cmp.CompareError as e:
        raise ApiError(400, str(e)) from None


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
            parsed = eng.parse_levers(check, strict=True)             # range checks, unknown keys, min <= n, a month with an as-of curve
        except ValueError as e:
            raise ApiError(400, str(e)) from None
        problem = cmp.asof_problem(store.load_cohorts(conn), parsed, _today())
        if problem:
            raise ApiError(400, problem)
        norm = {k: v for k, v in patch.items()}
        if norm.get("curve_source") is not None:
            norm["curve_source"] = eng.parse_curve_choice(norm["curve_source"])
        if norm.get("asof_month") is not None:
            norm["asof_month"] = eng.parse_month_value(norm["asof_month"]).isoformat()
        if "curve" in norm:                                            # the earlier name: store it under the new one
            legacy = norm.pop("curve")
            norm.setdefault("curve_source", None if legacy is None else eng.parse_curve_choice(legacy))
        if "curve_source" in norm:
            norm["curve"] = None                                       # never leave the earlier name behind to shadow a reset
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
    existing = store.load_cohorts(conn)
    summary = upload.diff(applied, existing)
    report = _check(conn, applied, existing)
    errors = report.errors
    committable = bool(applied.rows) and not errors
    if applied.rows and report.impact:
        summary["lifetime_before"] = report.impact["lifetime"]["before"]
        summary["lifetime_after"] = report.impact["lifetime"]["after"]
    out = {"committed": False, "committable": committable, "layout": parsed.layout, "rows_read": parsed.rows_read, "sha256": parsed.sha256,
           "filename": f.filename[:120], "through": last_month.isoformat(), "min_customers": min_customers, "summary": summary,
           "errors": errors, "warnings": applied.warnings + report.warnings, "issues": [i.to_dict() for i in report.issues], "impact": report.impact}
    if not commit:
        return jsonify(out)
    if not expect:
        raise ApiError(400, "preview first: send the preview's sha256 as expect_sha256 with ?commit=1")
    if expect != parsed.sha256:
        raise ApiError(409, "the file differs from the one previewed; preview it again")
    if not applied.rows:
        raise ApiError(400, "nothing to load: no cohort passed the rules")
    if errors:
        more = f" (and {len(errors) - 1} more)" if len(errors) > 1 else ""
        raise ApiError(400, f"This file cannot be loaded: {errors[0]}{more}")
    with rwdb.get_rw() as rw:
        who = audit.current_actor()
        before = store.snapshot_state(rw, [r.month for r in applied.rows])
        counts = store.upsert_cohorts(rw, applied.rows, source=f"upload {f.filename[:80]} {parsed.sha256[:12]}")
        upload_id = store.record_upload(rw, actor=who, filename=f.filename[:120], sha256=parsed.sha256, through=last_month,
                                        summary={k: v for k, v in summary.items() if k not in ("changes", "skipped", "held_back")}, before=before)
        audit.record(rw, actor=who, scenario=None, action="cohorts.upload", note=f.filename[:120],
                     new={"upload_id": upload_id, "sha256": parsed.sha256, "layout": parsed.layout, "rows_read": parsed.rows_read, "through": last_month.isoformat(),
                          "cohorts": counts["cohorts"], "cells": counts["cells"],
                          **{k: summary[k] for k in ("cohorts_added", "cohorts_changed", "cells_added", "cells_changed",
                                                     "cells_blanked_open_month", "cells_zero_dropped")}})
    out["committed"] = True
    out["loaded"] = counts
    out["upload_id"] = upload_id
    return jsonify(out)


def _check(conn, applied: upload.Applied, existing) -> checks.Report:
    """Plausibility checks with the bands and the cohort settings of the plan of record (an upload belongs to no scenario)."""
    params = (scenarios.get_scenario(conn, None).params or {}).get("cohorts")
    levers = eng.parse_levers(params, strict=False)
    return checks.check(applied.rows, existing, eng.parse_bands(params), levers)


@bp.get("/upload/history")
def upload_history():
    raw = (request.args.get("limit") or "10").strip()
    if not raw.isdigit() or int(raw) < 1:
        raise ApiError(400, "limit must be a positive integer")
    return jsonify({"uploads": store.upload_history(db.get_conn(), int(raw))})


@bp.post("/upload/undo")
def upload_undo():
    """Undo the newest upload that has not been undone: every cohort it touched goes back exactly as it was. Body: {upload_id} (the one the page shows)."""
    data = _body()
    uid = data.get("upload_id")
    if isinstance(uid, bool) or not isinstance(uid, int):
        raise ApiError(400, "upload_id must be the number of the upload to undo")
    with rwdb.get_rw() as rw:
        who = audit.current_actor()
        try:
            res = store.undo_upload(rw, uid, who)
        except store.UndoError as e:
            raise ApiError(e.status, str(e)) from None
        audit.record(rw, actor=who, scenario=None, action="cohorts.upload.undo", note=res["filename"], new=res)
    return jsonify(res)


# ------------------------------------------------------------------------------------------------------------------ goal curve

@bp.get("/goal")
def goal_overview():
    """The Goal in use, the one it replaced (what 'Restore previous Goal' would put back) and the recent snapshots."""
    conn = db.get_conn()
    return jsonify({**store.goal_overview(conn), "history": store.goal_snapshots(conn, 20)})


@bp.post("/goal")
def goal_snapshot():
    """Take the scenario's current curve as the new Goal curve. The Goal it replaces stays as a dated snapshot (GET /goal, POST /goal/restore)."""
    data = _body()
    if data.get("from") != "live":
        raise ApiError(400, 'send {"from": "live"} to save the current curve as the Goal curve')
    slug = data.get("scenario")
    conn = db.get_conn()
    if slug is not None and not isinstance(slug, str):
        raise ApiError(400, "scenario must be a slug")
    slug = slug or scenarios.default_slug(conn)
    scenarios.get_row(conn, slug)
    _, c, _ = _run(conn, slug)
    today = _today()
    old = store.goal_overview(conn)["active"]
    if old and max(abs(a - b) for a, b in zip(c.curve_goal, c.curve_live)) < 1e-9:      # stored to ten decimals
        raise ApiError(409, "The Goal curve already equals the current curve.")
    with rwdb.get_rw() as rw:
        who = audit.current_actor()
        sid = store.save_goal(rw, today, c.curve_live, note=f"Current curve of scenario {slug} on {today.isoformat()}", saved_by=who)
        out = {"snapshot_id": sid, "snapshot_date": today.isoformat(), "ages": len(c.curve_live), "lifetime": c.lifetime_live,
               "replaced": {"snapshot_id": old["id"], "snapshot_date": old["snapshot_date"].isoformat(), "lifetime": old["lifetime"]} if old else None}
        audit.record(rw, actor=who, scenario=slug, action="cohorts.goal.snapshot", note="Current curve saved as the Goal curve", version=sid,
                     old=out["replaced"], new={"snapshot_id": sid, "snapshot_date": today.isoformat(), "lifetime": c.lifetime_live})
    return jsonify(out)


@bp.post("/goal/restore")
def goal_restore():
    """Put an earlier Goal snapshot back in use (default: the one just before the Goal in use). Saves a copy, so the Goal replaced stays in history."""
    data = _body()
    sid = data.get("snapshot_id")
    if sid is not None and (isinstance(sid, bool) or not isinstance(sid, int)):
        raise ApiError(400, "snapshot_id must be a whole number")
    with rwdb.get_rw() as rw:
        who = audit.current_actor()
        try:
            res = store.restore_goal(rw, sid, who)
        except store.GoalError as e:
            raise ApiError(e.status, str(e)) from None
        audit.record(rw, actor=who, scenario=None, action="cohorts.goal.restore", note="Earlier Goal curve put back in use", version=res["snapshot_id"],
                     old={"snapshot_id": res["replaced"]["snapshot_id"], "lifetime": res["replaced"]["lifetime"]},
                     new={"snapshot_id": res["snapshot_id"], "restored_from": res["restored_from"], "snapshot_date": res["snapshot_date"].isoformat(), "lifetime": res["lifetime"]})
    return jsonify({**res, "snapshot_date": res["snapshot_date"].isoformat(), "replaced": {**res["replaced"], "snapshot_date": res["replaced"]["snapshot_date"].isoformat()}})
