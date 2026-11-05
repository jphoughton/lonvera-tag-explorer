"""HTTP layer for the pacing module, mounted at /api/projection/pacing.

    GET /month?month=YYYY-MM[&scenario=&as_of=]       month pacing table (Automated Pacing): MTD, last 7 days, goal, projection, pacing, status
    GET /snapshot?month=YYYY-MM                        Month Snapshot: Actual / Projected* / Target / Diff / Diff % and payback
    GET /rollup?grain=day|week|month&from=&to=&view=rollup|meta&bands=a,b&delta=1
                                                       rollup grid: periods as columns, metrics as rows
    GET /catalog?view=rollup|meta                      bands and metrics available to the rollup
    GET /freshness                                     last day held by each feed, with age and status
    GET /intraday?spent=&time=HH:MM                    projected end-of-day Meta spend from the hourly spend curve
    GET /intraday-nc?spent=&time=HH:MM&dow=all|same    projected end-of-day new-customer revenue from the 30-closed-day hourly share curve (feed nc_hourly)
    GET /live-today                                    today so far (Meta spend, NC revenue) from the latest hourly Upstack snapshot (etl.live_today), with age and stale flag
    GET /levers?scenario=                              every variable behind the numbers: value, sheet default, source, owner
    PUT /levers   {scenario, levers:{key: value | null}, expected_version?}     edit scenario variables (null restores the sheet's value); audited, versioned
    GET /rules, PUT /rules {rules:{key: value | null}}                          feed classification rules (global; applied when the ETL loads a day)
Goals come from the scenario's P&L (engine); actuals from the proj_feed_* tables. Dates default to the Pacific calendar (PROJECTION_AS_OF pins it).
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

from flask import Blueprint, jsonify, request
from psycopg.types.json import Jsonb

from ... import db
from ...queries import ApiError
from .. import audit, degraded, engine, rwdb, scenarios
from . import catalog, feeds, levers as lv, month as month_mod, nc_curve, periods, views
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
        d = periods.as_of(_arg("as_of"))
    except ValueError:
        raise ApiError(400, "as_of must be a date (YYYY-MM-DD)") from None
    if not (FIRST_MONTH <= d <= date(2029, 12, 31)):
        raise ApiError(400, "as_of must lie between 2025-01-01 and 2029-12-31")
    return d


def _setup():
    """(today, engine result, TargetBook with the scenario's levers, connection) for the requested scenario and date."""
    today = _today()
    conn = db.get_conn()
    res = engine.run(_arg("scenario"), conn, today=today)
    degraded.ensure_ok(res, "pacing")                      # 503 upstream_degraded when the P&L, drivers or cohorts failed: never goals of zero
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
        fresh = feed_freshness(conn)
    except Exception:                                                        # noqa: BLE001  (feed tables not migrated yet)
        conn.rollback()
        return []
    fresh = fresh + _nc_hourly_entry(conn)
    return views.freshness_chips(fresh, today, L.data_lag_days, L.stale_bad_days)


def _nc_hourly_entry(conn) -> list[dict]:
    """The chip of feed nc_hourly (no sheet tab, so it is not a FeedSpec and the ETL's freshness never sees it): shown once the feed has loaded a day."""
    try:
        r = conn.execute("SELECT max(day) AS d FROM proj_feed_nc_hourly").fetchone()
        last = r["d"] if isinstance(r, dict) else (r[0] if r else None)
    except Exception:                                                        # noqa: BLE001  (migration 020 not applied yet)
        conn.rollback()
        return []
    if last is None:
        return []
    return [{"feed": "nc_hourly", "label": "New-customer revenue by hour (ExpandFi)", "cadence": "daily", "data_through": last, "stale_after_days": nc_curve.STALE_AFTER_DAYS}]


def _load_month_frame(conn, month: date, today: date, L: lv.Levers):
    thr = periods.data_through(today, L.data_lag_days)
    back = max(L.last_days, L.cpa_window_days, 7) - 1
    return feeds.load_frame(conn, min(month, thr - timedelta(days=back)), max(min(periods.month_end(month), thr), min(month, thr)))


# ---------------------------------------------------------------------------------------------------------------- routes

@bp.get("/month")
def get_month():
    today, res, book, conn = _setup()
    L = book.levers
    month = _month_arg(today, L)
    fr = _load_month_frame(conn, month, today, L)
    cohorts = res.results.get("cohorts")
    scn = res.scenario
    out = month_mod.pacing_month(fr, month, today, book, cohorts, scn.params, res.actual_through, feeds.cpa_reference(conn, scn.params, L.cpa_window_days, today))
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
    out = month_mod.snapshot_month(fr, month, today, book, res.results.get("cohorts"), res.scenario.params, res.actual_through)
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
    valid = {b.lower() for b in catalog.bands_for(view)} | {"projection against the plan"}
    unknown = [b for b in bands or [] if b.lower() not in valid and b.lower().split(":")[0] not in valid]
    if unknown:
        raise ApiError(400, f"unknown band {unknown[0]!r}: choose from {', '.join(catalog.bands_for(view))}")
    frm, to = _date_arg("from"), _date_arg("to")
    if frm and to and frm > to:
        raise ApiError(400, "from must not be after to")
    today, res, book, conn = _setup()
    try:
        out = views.rollup_payload(conn, grain, frm, to, today, book, view, bands, _arg("delta") in ("1", "true"))
    except ValueError as e:
        raise ApiError(400, str(e)) from None
    out["freshness"] = _fresh_chips(conn, today, book.levers)
    return jsonify(out)


@bp.get("/catalog")
def get_catalog():
    view = _arg("view") or "rollup"
    if view not in ("rollup", "meta"):
        raise ApiError(400, "view must be rollup or meta")
    return jsonify({"view": view, "bands": catalog.bands_for(view),
                    "metrics": [{"key": m.key, "label": m.short or m.label, "band": m.band, "sub": m.sub, "fmt": m.fmt, "note": m.note} for m in catalog.metrics_for(view)]})


@bp.get("/freshness")
def get_freshness():
    today = _today()
    L = lv.parse(scenarios.get_scenario(db.get_conn(), _arg("scenario")).params)
    return jsonify({"as_of": today.isoformat(), "expected_through": periods.data_through(today, L.data_lag_days).isoformat(),
                    "feeds": _fresh_chips(db.get_conn(), today, L)})


def _hours_arg() -> float:
    t = _arg("time")
    if t:
        try:
            hh, mm = t.split(":")[:2]
            hours = int(hh) + int(mm) / 60
        except ValueError:
            raise ApiError(400, "time must be HH:MM") from None
        if not 0 <= hours <= 24:
            raise ApiError(400, "time must be between 00:00 and 24:00")
        return hours
    now = datetime.now(periods.PACIFIC)
    return now.hour + now.minute / 60


@bp.get("/intraday-nc")
def get_intraday_nc():
    """The 'NC revenue today' card: end-of-day new-customer revenue = revenue so far / share of a typical day's NC revenue already in by that time.
    The curve is the ratio-of-sums share by Pacific hour over the last 30 closed days of feed nc_hourly (today never enters). No curve = no projection
    (revenue is never assumed even through the day)."""
    conn = db.get_conn()
    today = _today()
    try:
        spent = float(_arg("spent")) if _arg("spent") is not None else None
    except ValueError:
        raise ApiError(400, "spent must be a number") from None
    if spent is not None and not (0 <= spent < 1e9):
        raise ApiError(400, "spent must be between 0 and 1,000,000,000")
    dow = _arg("dow") or "all"
    if dow not in ("all", "same"):
        raise ApiError(400, "dow must be all or same")
    hours = _hours_arg()
    first, _ = nc_curve.window_dates(today)
    try:
        rows = [tuple(r.values()) if isinstance(r, dict) else tuple(r)
                for r in conn.execute("SELECT day, hour, nc_rev, nc_orders FROM proj_feed_nc_hourly WHERE day >= %s AND day < %s", (first, today)).fetchall()]
    except Exception:                                                        # noqa: BLE001  (migration 020 not applied yet)
        conn.rollback()
        rows = []
    out = nc_curve.build(rows, today, hours, spent, dow)
    if not out["loaded"] and not out.get("reason"):
        out["reason"] = "No closed day of new-customer revenue by hour is loaded yet."
    out["message"] = out.get("reason") if not out["loaded"] or out.get("projected_eod") is None else None
    out["as_of"] = today.isoformat()
    out["basis"] = (f"{out['window']['days_used']} closed days, {out['window']['from']} to {out['window']['to']}" if out["loaded"] else None)
    return jsonify(out)


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
    even = len(rows) != 24 or not sum(hourly)
    if even:                                                                 # no curve: assume spend is even through the day, and say so
        hourly = [1.0] * 24
    out = month_mod.intraday_projection(hourly, spent or 0.0, hours) if spent is not None else {"share": None, "projected_eod": None, "to_go": None}
    share_now = month_mod.intraday_projection(hourly, 1.0, hours).get("share")
    out.update({"loaded": not even, "estimate": even, "hour": round(hours, 2), "share_of_day": share_now, "spent": spent})
    if even:
        out["message"] = ("The hourly spend curve is not loaded, so this assumes spend is even through the day. "
                          "Load the curve with `python -m etl.projection_pacing seed-hourly` (it reads the sheet's Automated Pacing L23:M46); "
                          "the daily refresh also loads it the next time it runs.")
    return jsonify(out)


# ---------------------------------------------------------------------------------------------------------------- live today

LIVE_STALE_MINUTES = 150            # the cron runs hourly; two missed pulls plus slack


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


@bp.get("/live-today")
def get_live_today():
    """The latest hourly snapshot of today so far (table proj_live_today_snapshots, written by `python -m etl.live_today`). `stale` is true when the
    snapshot is older than 150 minutes or belongs to a previous Pacific day (then the numbers are NOT today's and the page must not use them)."""
    conn = db.get_conn()
    today = _today()
    try:
        r = conn.execute("SELECT pulled_at, day, pacific_time, meta_spend, google_spend, total_spend, nc_rev, nc_orders FROM proj_live_today_snapshots "
                         "ORDER BY pulled_at DESC LIMIT 1").fetchone()
    except Exception:                                                        # noqa: BLE001  (migration 192 not applied yet)
        conn.rollback()
        r = None
    base = {"loaded": False, "as_of": today.isoformat(), "stale": False, "stale_reason": None, "stale_after_minutes": LIVE_STALE_MINUTES, "snapshot": None, "age_minutes": None}
    if r is None:
        return jsonify({**base, "message": "No live pull yet. Type today's numbers, or wait for the next hourly pull."})
    pulled_at, day, ptime, meta, google, total, nc_rev, nc_orders = (tuple(r.values()) if isinstance(r, dict) else tuple(r))
    age = max(0, int((_now_utc() - pulled_at).total_seconds() // 60))
    reason = "previous_day" if day != today else ("age" if age > LIVE_STALE_MINUTES else None)
    msg = {"previous_day": f"The newest live pull is from {day.strftime('%b %-d')}, not today. Type today's numbers.",
           "age": f"The newest live pull is {age} minutes old (the pull runs hourly)."}.get(reason)
    snap = {"pulled_at": pulled_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"), "day": day.isoformat(), "pacific_time": ptime.strftime("%H:%M"),
            "meta_spend": float(meta), "google_spend": float(google), "total_spend": float(total), "nc_rev": float(nc_rev), "nc_orders": int(nc_orders)}
    return jsonify({**base, "loaded": True, "snapshot": snap, "age_minutes": age, "stale": reason is not None, "stale_reason": reason, "message": msg})


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
