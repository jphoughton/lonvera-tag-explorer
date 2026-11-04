"""HTTP layer for the pacing module, mounted at /api/projection/pacing.

    GET /month?month=YYYY-MM[&scenario=&as_of=]       month pacing table (Automated Pacing): MTD, last 7 days, goal, projection, pacing, status
    GET /snapshot?month=YYYY-MM                        Month Snapshot: Actual / Projected* / Target / Diff / Diff % and payback
    GET /rollup?grain=day|week|month&from=&to=&view=rollup|meta&bands=a,b&delta=1
                                                       rollup grid: periods as columns, metrics as rows
    GET /catalog?view=rollup|meta                      bands and metrics available to the rollup
    GET /freshness                                     last day held by each feed, with age and status
    GET /intraday?spent=&time=HH:MM                    projected end-of-day Meta spend from the hourly spend curve
    GET /levers?scenario=                              every variable behind the numbers: value, sheet default, source, owner
    PUT /levers   {scenario, levers:{key: value | null}, expected_version?}     edit scenario variables (null restores the sheet's value); audited, versioned
    GET /rules, PUT /rules {rules:{key: value | null}}                          feed classification rules (global; applied when the ETL loads a day)
Goals come from the scenario's P&L (engine); actuals from the proj_feed_* tables. Dates default to the Pacific calendar (PROJECTION_AS_OF pins it).
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any

from flask import Blueprint, jsonify, request
from psycopg.types.json import Jsonb

from ... import db
from ...queries import ApiError
from .. import audit, engine, rwdb, scenarios
from . import catalog, feeds, levers as lv, month as month_mod, periods, views
from .feedspec import freshness as feed_freshness
from .targets import TargetBook

bp = Blueprint("proj_pacing", __name__)

FIRST_MONTH = date(2025, 1, 1)


# ---------------------------------------------------------------------------------------------------------------- helpers

def _arg(name: str) -> str | None:
    v = request.args.get(name)
    v = v.strip() if v else None
    return v or None


def _body() -> dict:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ApiError(400, "JSON object body required")
    return data


def _date_arg(name: str) -> date | None:
    v = _arg(name)
    if v is None:
        return None
    try:
        return date.fromisoformat(v[:10]) if len(v) > 7 else date.fromisoformat(v + "-01")
    except ValueError:
        raise ApiError(400, f"{name} must be a date (YYYY-MM-DD or YYYY-MM)") from None


def _today() -> date:
    try:
        return periods.as_of(_arg("as_of"))
    except ValueError:
        raise ApiError(400, "as_of must be a date (YYYY-MM-DD)") from None


def _setup():
    """(today, engine result, TargetBook with the scenario's levers, connection) for the requested scenario and date."""
    today = _today()
    conn = db.get_conn()
    res = engine.run(_arg("scenario"), conn, today=today)
    L = lv.parse(res.scenario.params)
    book = TargetBook(res.results.get("pnl"), res.results.get("drivers"), res.results.get("cohorts"), levers=L)
    return today, res, book, conn


def _month_arg(today: date, L: lv.Levers) -> date:
    m = _date_arg("month")
    if m is None:
        return periods.default_month(today, L.data_lag_days)
    m = m.replace(day=1)
    if not (FIRST_MONTH <= m <= date(2029, 12, 1)):
        raise ApiError(400, "month must lie between 2025-01 and 2029-12")
    return m


def _month_options(today: date, L: lv.Levers) -> list[dict]:
    last = periods.month_start(periods.data_through(today, L.data_lag_days))
    out, m = [], FIRST_MONTH
    while m <= last:
        out.append({"month": m.isoformat(), "label": m.strftime("%B %Y")})
        m = periods.add_months(m, 1)
    return out[::-1]


def _fresh_chips(conn, today: date, L: lv.Levers) -> list[dict]:
    try:
        return views.freshness_chips(feed_freshness(conn), today, L.data_lag_days, L.stale_bad_days)
    except Exception:                                                        # noqa: BLE001  (feed tables not migrated yet)
        conn.rollback()
        return []


def _load_month_frame(conn, month: date, today: date, L: lv.Levers):
    thr = periods.data_through(today, L.data_lag_days)
    return feeds.load_frame(conn, min(month, thr - timedelta(days=max(L.last_days, 7) - 1)), max(min(periods.month_end(month), thr), min(month, thr)))


# ---------------------------------------------------------------------------------------------------------------- routes

@bp.get("/month")
def get_month():
    today, res, book, conn = _setup()
    L = book.levers
    month = _month_arg(today, L)
    fr = _load_month_frame(conn, month, today, L)
    cohorts = res.results.get("cohorts")
    scn = res.scenario
    out = month_mod.pacing_month(fr, month, today, book, cohorts, scn.params)
    out["scenario"] = {"slug": scn.slug, "name": scn.name, "version": scn.version}
    out["months"] = _month_options(today, L)
    out["freshness"] = _fresh_chips(conn, today, L)
    out["tldr_line"] = month_mod.tldr_sentence(out["tldr"])
    out["captions"] = [f"Actuals through {periods.data_through(today, L.data_lag_days).strftime('%b %-d')}. Goals from the {scn.name} P&L. "
                       "Projections run the month-to-date pace over the whole month."]
    return jsonify(out)


@bp.get("/snapshot")
def get_snapshot():
    today, res, book, conn = _setup()
    L = book.levers
    month = _month_arg(today, L)
    fr = _load_month_frame(conn, month, today, L)
    out = month_mod.snapshot_month(fr, month, today, book, res.results.get("cohorts"), res.scenario.params)
    out["months"] = _month_options(today, L)
    out["missing_goal_rows"] = book.missing_rows()
    return jsonify(out)


@bp.get("/rollup")
def get_rollup():
    grain = _arg("grain") or "month"
    if grain not in ("day", "week", "month"):
        raise ApiError(400, "grain must be day, week or month")
    view = _arg("view") or "rollup"
    if view not in ("rollup", "meta"):
        raise ApiError(400, "view must be rollup or meta")
    bands = [b for b in (_arg("bands") or "").split(",") if b] or None
    today, res, book, conn = _setup()
    try:
        out = views.rollup_payload(conn, grain, _date_arg("from"), _date_arg("to"), today, book, view, bands, _arg("delta") in ("1", "true"))
    except ValueError as e:
        raise ApiError(400, str(e)) from None
    out["freshness"] = _fresh_chips(conn, today, book.levers)
    return jsonify(out)


@bp.get("/catalog")
def get_catalog():
    view = _arg("view") or "rollup"
    return jsonify({"view": view, "bands": catalog.bands_for(view),
                    "metrics": [{"key": m.key, "label": m.short or m.label, "band": m.band, "sub": m.sub, "fmt": m.fmt, "note": m.note} for m in catalog.metrics_for(view)]})


@bp.get("/freshness")
def get_freshness():
    today = _today()
    L = lv.parse(scenarios.get_scenario(db.get_conn(), _arg("scenario")).params)
    return jsonify({"as_of": today.isoformat(), "expected_through": periods.data_through(today, L.data_lag_days).isoformat(),
                    "feeds": _fresh_chips(db.get_conn(), today, L)})


@bp.get("/intraday")
def get_intraday():
    conn = db.get_conn()
    rows = conn.execute("SELECT hour, spend_30d FROM proj_pacing_hourly ORDER BY hour").fetchall()
    hourly = [0.0] * 24
    for r in rows:
        h, s = (r["hour"], r["spend_30d"]) if isinstance(r, dict) else (r[0], r[1])
        hourly[int(h)] = float(s)
    try:
        spent = float(_arg("spent")) if _arg("spent") is not None else None
    except ValueError:
        raise ApiError(400, "spent must be a number") from None
    t = _arg("time")
    if t:
        try:
            hh, mm = t.split(":")[:2]
            hours = int(hh) + int(mm) / 60
        except ValueError:
            raise ApiError(400, "time must be HH:MM") from None
    else:
        now = datetime.now(periods.PACIFIC)
        hours = now.hour + now.minute / 60
    if len(rows) != 24:
        return jsonify({"loaded": False, "message": "The hourly spend curve is not loaded."})
    out = month_mod.intraday_projection(hourly, spent or 0.0, hours) if spent is not None else {"share": None, "projected_eod": None, "to_go": None}
    share_now = month_mod.intraday_projection(hourly, 1.0, hours).get("share")
    out.update({"loaded": True, "hour": round(hours, 2), "share_of_day": share_now, "spent": spent})
    return jsonify(out)


# ---------------------------------------------------------------------------------------------------------------- variables

@bp.get("/levers")
def get_levers():
    conn = db.get_conn()
    scn = scenarios.get_scenario(conn, _arg("scenario"))
    return jsonify({"scenario": scn.slug, "version": scn.version, "levers": lv.describe(scn.params)})


@bp.put("/levers")
def put_levers():
    data = _body()
    slug = data.get("scenario")
    if not isinstance(slug, str) or not slug:
        raise ApiError(400, "scenario is required")
    patch = lv.validate_patch(data.get("levers"))
    ev = data.get("expected_version")
    if ev is not None and (isinstance(ev, bool) or not isinstance(ev, int)):
        raise ApiError(400, "expected_version must be an integer")
    with rwdb.get_rw() as conn:
        current = scenarios.get_scenario(conn, slug)
        lv.cross_check(lv.effective(scenarios.merge_patch(current.params, patch)))
        row = scenarios.update(conn, slug, params=patch, expected_version=ev, actor=audit.current_actor())
        params = scenarios.get_scenario(conn, slug).params
    return jsonify({"scenario": row, "levers": lv.describe(params)})


RULE_KEYS = ("stage_keyword", "product_rules", "product_level_rules")


def _check_rule(key: str, v: Any):
    if key == "stage_keyword":
        if not isinstance(v, str) or not 1 <= len(v.strip()) <= 30:
            raise ApiError(400, "stage_keyword must be a word of 1 to 30 characters")
        return v.strip().lower()
    if not isinstance(v, dict) or not v or any(not isinstance(k, str) or not isinstance(rx, str) for k, rx in v.items()):
        raise ApiError(400, f"{key} maps each product to a pattern")
    for product, rx in v.items():
        if not rx.strip() or len(rx) > 300:
            raise ApiError(400, f"{key}.{product} needs a pattern of up to 300 characters")
        try:
            re.compile(rx)
        except re.error as e:
            raise ApiError(400, f"{key}.{product} is not a valid pattern: {e}") from None
    return dict(v)


@bp.get("/rules")
def get_rules():
    from etl.projection_pacing import DEFAULT_RULES, RULE_NOTES          # the sheet's rules are the defaults of the stored setting
    conn = db.get_conn()
    stored = {}
    for r in conn.execute("SELECT key, value, updated_at FROM proj_pacing_setting").fetchall():
        stored[r["key"]] = r
    out = []
    for k in RULE_KEYS:
        out.append({"key": k, "value": stored[k]["value"] if k in stored else DEFAULT_RULES[k], "default": DEFAULT_RULES[k], "is_default": k not in stored,
                    "source": RULE_NOTES[k], "scope": "feed", "applies": "when the daily refresh loads a day (a backfill reloads every day)"})
    return jsonify({"rules": out})


@bp.put("/rules")
def put_rules():
    data = _body()
    raw = data.get("rules")
    if not isinstance(raw, dict) or not raw:
        raise ApiError(400, "rules must be a non-empty object")
    bad = set(raw) - set(RULE_KEYS)
    if bad:
        raise ApiError(400, f"unknown rule {sorted(bad)[0]!r}")
    checked = {k: (None if v is None else _check_rule(k, v)) for k, v in raw.items()}
    with rwdb.get_rw() as conn:
        for k, v in checked.items():
            old = conn.execute("SELECT value FROM proj_pacing_setting WHERE key = %s", (k,)).fetchone()
            if v is None:
                conn.execute("DELETE FROM proj_pacing_setting WHERE key = %s", (k,))
            else:
                conn.execute("INSERT INTO proj_pacing_setting (key, value, note, updated_at) VALUES (%s, %s, %s, now()) "
                             "ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = now()", (k, Jsonb(v), "edited in the app"))
            audit.record(conn, actor=audit.current_actor(), scenario=None, action="pacing.rule", line_key=f"pacing.{k}", old=None if old is None else old["value"], new=v)
    return get_rules()
