"""Cohort tables in Postgres: read the cohort table and Goal curve, upsert rows. Parameterized SQL only.

Reads work on any connection (dict rows). Writes take a read-write connection inside one transaction (rwdb.get_rw) and are audited by the caller.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Iterable, Sequence

from ..freshness import today_pacific
from ..interfaces import Series
from .engine import COHORT_AGES, CURVE_AGES, Cohort


def _f(v) -> float | None:
    return None if v is None else float(v)


# ---------------------------------------------------------------------------------------------------------------- reads

def load_cohorts(conn) -> list[Cohort]:
    heads = conn.execute(
        "SELECT cohort_month, new_customers, first_order_per_customer, first_order_realized, repeat_90d, cac, payback_months, nc_rev_reference "
        "FROM proj_cohorts ORDER BY cohort_month").fetchall()
    cum: dict[date, list[float | None]] = {}
    for r in conn.execute("SELECT cohort_month, age, cum_per_customer FROM proj_cohort_cum").fetchall():
        cum.setdefault(r["cohort_month"], [None] * COHORT_AGES)[r["age"] - 1] = float(r["cum_per_customer"])
    return [Cohort(month=h["cohort_month"], new_customers=float(h["new_customers"]), first_order=_f(h["first_order_per_customer"]),
                   first_order_realized=_f(h["first_order_realized"]), repeat_90d=_f(h["repeat_90d"]), cac=_f(h["cac"]),
                   payback_months=_f(h["payback_months"]), cum=tuple(cum.get(h["cohort_month"], [None] * COHORT_AGES)),
                   nc_rev_reference=_f(h["nc_rev_reference"]))
            for h in heads]


def _goal_values(conn, snapshot_id: int) -> list[float]:
    goal = [0.0] * CURVE_AGES
    for r in conn.execute("SELECT age, value FROM proj_curve_goal WHERE snapshot_id = %s", (snapshot_id,)).fetchall():
        goal[r["age"] - 1] = float(r["value"])
    return goal


def load_goal(conn) -> tuple[list[float], date | None]:
    """(Goal curve for ages 1..60 - zeros when none is loaded, the date it was saved or taken). The Goal is the newest snapshot."""
    head = conn.execute("SELECT snapshot_id, snapshot_date FROM proj_curve_goal ORDER BY snapshot_id DESC LIMIT 1").fetchone()
    if head is None:
        return [0.0] * CURVE_AGES, None
    return _goal_values(conn, head["snapshot_id"]), head["snapshot_date"]


def goal_snapshots(conn, limit: int = 20) -> list[dict]:
    """Goal snapshots newest first: {id, snapshot_date, saved_at, saved_by, note, restored_from, lifetime, twelve, active}. The first one is the Goal in use."""
    rows = conn.execute(
        "SELECT snapshot_id, max(snapshot_date) AS snapshot_date, max(saved_at) AS saved_at, max(saved_by) AS saved_by, max(note) AS note, "
        "max(restored_from) AS restored_from, sum(value) AS lifetime, sum(value) FILTER (WHERE age <= 12) AS twelve "
        "FROM proj_curve_goal GROUP BY snapshot_id ORDER BY snapshot_id DESC LIMIT %s", (max(1, min(int(limit), 100)),)).fetchall()
    return [{"id": r["snapshot_id"], "snapshot_date": r["snapshot_date"], "saved_at": r["saved_at"], "saved_by": r["saved_by"], "note": r["note"],
             "restored_from": r["restored_from"], "lifetime": float(r["lifetime"]), "twelve": float(r["twelve"] or 0), "active": i == 0} for i, r in enumerate(rows)]


def goal_overview(conn) -> dict:
    """The Goal in use and the one it replaced (what 'Restore previous Goal' would put back)."""
    snaps = goal_snapshots(conn, 2)
    return {"active": snaps[0] if snaps else None, "previous": snaps[1] if len(snaps) > 1 else None}


def first_order_revenue(conn) -> Series:
    """New-customer revenue by cohort month = new customers x first order (for the drivers module's actual months)."""
    return {c.month: c.first_order_rev for c in load_cohorts(conn) if c.first_order is not None}


def new_customers(conn) -> Series:
    return {c.month: c.new_customers for c in load_cohorts(conn)}


# ---------------------------------------------------------------------------------------------------------------- writes

@dataclass
class CohortRow:
    """One cohort as it is written: head fields (None = leave as is on update) and observed cumulative cells {age: cum $/customer}."""
    month: date
    new_customers: float
    first_order: float | None = None
    first_order_realized: float | None = None
    repeat_90d: float | None = None
    cac: float | None = None
    payback_months: float | None = None
    nc_rev_reference: float | None = None
    cum: dict[int, float] = field(default_factory=dict)


def upsert_cohorts(conn, rows: Iterable[CohortRow], source: str) -> dict:
    """Insert or update cohort rows and their cumulative cells. Head fields given as None keep the stored value; cumulative cells absent from
    the row keep the stored value (an export can add and revise cells, never silently erase them). Returns counts."""
    n_heads = n_cells = 0
    with conn.cursor() as cur:
        for r in rows:
            cur.execute(
                "INSERT INTO proj_cohorts (cohort_month, new_customers, first_order_per_customer, first_order_realized, repeat_90d, cac, "
                "payback_months, nc_rev_reference, source) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (cohort_month) DO UPDATE SET new_customers = EXCLUDED.new_customers, "
                "first_order_per_customer = COALESCE(EXCLUDED.first_order_per_customer, proj_cohorts.first_order_per_customer), "
                "first_order_realized = COALESCE(EXCLUDED.first_order_realized, proj_cohorts.first_order_realized), "
                "repeat_90d = COALESCE(EXCLUDED.repeat_90d, proj_cohorts.repeat_90d), cac = COALESCE(EXCLUDED.cac, proj_cohorts.cac), "
                "payback_months = COALESCE(EXCLUDED.payback_months, proj_cohorts.payback_months), "
                "nc_rev_reference = COALESCE(EXCLUDED.nc_rev_reference, proj_cohorts.nc_rev_reference), "
                "source = EXCLUDED.source, loaded_at = now()",
                (r.month, r.new_customers, r.first_order, r.first_order_realized, r.repeat_90d, r.cac, r.payback_months,
                 r.nc_rev_reference, source))
            n_heads += 1
            cells = [(r.month, a, v) for a, v in sorted(r.cum.items()) if 1 <= a <= COHORT_AGES and v is not None]
            if cells:
                cur.executemany(
                    "INSERT INTO proj_cohort_cum (cohort_month, age, cum_per_customer) VALUES (%s,%s,%s) "
                    "ON CONFLICT (cohort_month, age) DO UPDATE SET cum_per_customer = EXCLUDED.cum_per_customer", cells)
                n_cells += len(cells)
    return {"cohorts": n_heads, "cells": n_cells}


def _new_snapshot_id(conn) -> int:
    return conn.execute("SELECT nextval('proj_curve_goal_snapshot_seq') AS i").fetchone()["i"]


def save_goal(conn, snapshot_date: date, values: Sequence[float], note: str | None = None, *, saved_by: str | None = None, restored_from: int | None = None) -> int:
    """Add a Goal snapshot (never overwrites one; it becomes the Goal in use). Returns its id."""
    sid = _new_snapshot_id(conn)
    with conn.cursor() as cur:
        cur.executemany("INSERT INTO proj_curve_goal (snapshot_id, snapshot_date, age, value, note, saved_by, restored_from) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                        [(sid, snapshot_date, i + 1, float(v), note, saved_by, restored_from) for i, v in enumerate(values[:CURVE_AGES])])
    return sid


def replace_goal(conn, snapshot_date: date, values: Sequence[float], note: str | None = None) -> int:
    """Loader helper (the sheet seeder): write the snapshot with this date and note, replacing it in place when it exists so a re-run changes nothing.
    Returns the number of ages written. The page never uses this: saves go through save_goal."""
    row = conn.execute("SELECT snapshot_id FROM proj_curve_goal WHERE snapshot_date = %s AND note IS NOT DISTINCT FROM %s LIMIT 1", (snapshot_date, note)).fetchone()
    if row is None:
        save_goal(conn, snapshot_date, values, note, saved_by="seed")
    else:
        conn.execute("DELETE FROM proj_curve_goal WHERE snapshot_id = %s", (row["snapshot_id"],))
        with conn.cursor() as cur:
            cur.executemany("INSERT INTO proj_curve_goal (snapshot_id, snapshot_date, age, value, note, saved_by) VALUES (%s,%s,%s,%s,%s,%s)",
                            [(row["snapshot_id"], snapshot_date, i + 1, float(v), note, "seed") for i, v in enumerate(values[:CURVE_AGES])])
    return min(len(values), CURVE_AGES)


class GoalError(ValueError):
    """Why a Goal save or restore cannot run (message goes to the user); .status is the HTTP status."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def restore_goal(conn, snapshot_id: int | None, saved_by: str | None) -> dict:
    """Make an earlier Goal snapshot the Goal in use again by saving a copy of it (the one it replaces stays in history, so this can be undone the same way).
    Default target: the snapshot just before the one in use."""
    snaps = goal_snapshots(conn, 100)
    if not snaps:
        raise GoalError("There is no Goal curve to restore.")
    active = snaps[0]
    if snapshot_id is None:
        if len(snaps) < 2:
            raise GoalError("There is no earlier Goal curve to restore.")
        target = snaps[1]
    else:
        target = next((x for x in snaps if x["id"] == snapshot_id), None)
        if target is None:
            raise GoalError("That Goal snapshot does not exist.", 404)
        if target["active"]:
            raise GoalError("That Goal curve is already the one in use.", 409)
    note = f"Restored the Goal saved {target['snapshot_date'].isoformat()} (snapshot {target['id']})"
    sid = save_goal(conn, target["snapshot_date"], _goal_values(conn, target["id"]), note, saved_by=saved_by, restored_from=target["id"])
    return {"snapshot_id": sid, "snapshot_date": target["snapshot_date"], "lifetime": target["lifetime"], "restored_from": target["id"],
            "replaced": {"snapshot_id": active["id"], "snapshot_date": active["snapshot_date"], "lifetime": active["lifetime"]}}


# ---------------------------------------------------------------------------------------------------------------- freshness

STALE_AFTER_DAYS = 45


def _month_end(d: date) -> date:
    return date(d.year + (d.month == 12), d.month % 12 + 1, 1) - timedelta(days=1)


def freshness(conn, today: date | None = None) -> list[dict]:
    """How current the cohort export is. The feed loads by monthly upload (no daily pull).

    `data_through` = last day of the last fully closed cohort month with data, so the shared freshness check (age > stale_after_days) gives a
    month about two weeks to be uploaded after it closes: loaded through Oct, fresh until Dec 15, stale from Dec 16.
    `behind` is the literal rule: the last closed calendar month is not loaded (either the cohort frontier or the last month a loaded cumulative
    cell lands in is older than it). `data_through_month` / `observed_through` are first-of-month dates for the page."""
    from .engine import data_through, last_closed_month, observed_through
    today = today or today_pacific()
    cohorts = load_cohorts(conn)
    closed = last_closed_month(today)
    through = data_through(cohorts, today)
    observed = observed_through(cohorts, today)
    frontier = min(through, observed) if through and observed else None
    end = _month_end(through) if through else None
    return [{
        "feed": "cohort_export", "label": "Cohort export", "cadence": "monthly-upload",
        "data_through": end, "data_through_month": through, "observed_through": observed, "last_closed_month": closed,
        "behind": frontier is None or frontier < closed,
        "stale": end is None or (today - end).days > STALE_AFTER_DAYS,
        "stale_after_days": STALE_AFTER_DAYS,
        "age_days": (today - end).days if end else None,
        "next_close": _month_end(today) + timedelta(days=1),     # the open month's cohort can be loaded from this date
        "cohorts": len(cohorts),
    }]


# ---------------------------------------------------------------------------------------------------------------- upload history and undo

_HEAD_COLS = ("new_customers", "first_order_per_customer", "first_order_realized", "repeat_90d", "cac", "payback_months", "nc_rev_reference", "source")


def snapshot_state(conn, months: Iterable[date]) -> list[dict]:
    """The stored state of the given cohorts, exactly: [{month, existed, head:{column: text|None}, cum:{age: text}}]. Values are kept as text so a restore is exact."""
    months = sorted(set(months))
    heads = {r["cohort_month"]: r for r in conn.execute(
        "SELECT cohort_month, new_customers::text AS new_customers, first_order_per_customer::text AS first_order_per_customer, "
        "first_order_realized::text AS first_order_realized, repeat_90d::text AS repeat_90d, cac::text AS cac, payback_months::text AS payback_months, "
        "nc_rev_reference::text AS nc_rev_reference, source FROM proj_cohorts WHERE cohort_month = ANY(%s)", (months,)).fetchall()}
    cells: dict[date, dict[str, str]] = {}
    for r in conn.execute("SELECT cohort_month, age, cum_per_customer::text AS v FROM proj_cohort_cum WHERE cohort_month = ANY(%s)", (months,)).fetchall():
        cells.setdefault(r["cohort_month"], {})[str(r["age"])] = r["v"]
    return [{"month": m.isoformat(), "existed": m in heads, "head": {c: heads[m][c] for c in _HEAD_COLS} if m in heads else None, "cum": cells.get(m, {})} for m in months]


def record_upload(conn, *, actor: str, filename: str, sha256: str, through: date, summary: dict, before: list[dict]) -> int:
    from psycopg.types.json import Jsonb
    return conn.execute("INSERT INTO proj_cohort_uploads (actor, filename, sha256, through, summary, before) VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
                        (actor, filename, sha256, through, Jsonb(summary), Jsonb(before))).fetchone()["id"]


def _public(r: dict, latest_id: int | None) -> dict:
    s = r["summary"] or {}
    return {"id": r["id"], "at": r["at"], "actor": r["actor"], "filename": r["filename"], "sha256": r["sha256"], "through": r["through"],
            "cohorts_added": s.get("cohorts_added", 0), "cohorts_changed": s.get("cohorts_changed", 0), "cells_added": s.get("cells_added", 0),
            "cells_changed": s.get("cells_changed", 0), "lifetime_before": s.get("lifetime_before"), "lifetime_after": s.get("lifetime_after"),
            "undone": r["undone_at"] is not None, "undone_at": r["undone_at"], "undone_by": r["undone_by"],
            "undoable": r["undone_at"] is None and r["id"] == latest_id}


def upload_history(conn, limit: int = 10) -> list[dict]:
    rows = conn.execute("SELECT id, at, actor, filename, sha256, through, summary, undone_at, undone_by FROM proj_cohort_uploads ORDER BY id DESC LIMIT %s",
                        (max(1, min(int(limit), 100)),)).fetchall()
    latest = conn.execute("SELECT max(id) AS i FROM proj_cohort_uploads WHERE undone_at IS NULL").fetchone()["i"]
    return [_public(r, latest) for r in rows]


def last_upload(conn) -> dict | None:
    """The newest upload that has not been undone (the one 'Undo last upload' would reverse), or None."""
    rows = [h for h in upload_history(conn, 1) if h["undoable"]]
    return rows[0] if rows else None


class UndoError(ValueError):
    """Why an undo cannot run (message goes to the user); .status is the HTTP status."""

    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


def undo_upload(conn, upload_id: int, actor: str) -> dict:
    """Put every cohort the upload touched back exactly as it was: heads and cumulative cells restored from the saved text, cohorts the upload created removed.
    Only the newest upload that has not been undone can be undone (so an earlier state is never restored over a later upload)."""
    row = conn.execute("SELECT id, at, filename, summary, before, undone_at FROM proj_cohort_uploads WHERE id = %s FOR UPDATE", (upload_id,)).fetchone()
    if row is None:
        raise UndoError("That upload does not exist.", 404)
    if row["undone_at"] is not None:
        raise UndoError("That upload was already undone.")
    newest = conn.execute("SELECT max(id) AS i FROM proj_cohort_uploads WHERE undone_at IS NULL").fetchone()["i"]
    if newest != upload_id:
        raise UndoError("A newer upload has to be undone first.")
    restored_cohorts = restored_cells = removed = 0
    with conn.cursor() as cur:
        for st in row["before"]:
            m = date.fromisoformat(st["month"])
            if not st["existed"]:
                cur.execute("DELETE FROM proj_cohorts WHERE cohort_month = %s", (m,))
                removed += 1
                continue
            h = st["head"]
            cur.execute("UPDATE proj_cohorts SET new_customers = %s, first_order_per_customer = %s, first_order_realized = %s, repeat_90d = %s, cac = %s, "
                        "payback_months = %s, nc_rev_reference = %s, source = %s, loaded_at = now() WHERE cohort_month = %s",
                        (h["new_customers"], h["first_order_per_customer"], h["first_order_realized"], h["repeat_90d"], h["cac"], h["payback_months"],
                         h["nc_rev_reference"], h["source"], m))
            cur.execute("DELETE FROM proj_cohort_cum WHERE cohort_month = %s", (m,))
            if st["cum"]:
                cur.executemany("INSERT INTO proj_cohort_cum (cohort_month, age, cum_per_customer) VALUES (%s,%s,%s)",
                                [(m, int(a), v) for a, v in sorted(st["cum"].items(), key=lambda kv: int(kv[0]))])
            restored_cohorts += 1
            restored_cells += len(st["cum"])
        cur.execute("UPDATE proj_cohort_uploads SET undone_at = now(), undone_by = %s WHERE id = %s", (actor, upload_id))
    return {"upload_id": upload_id, "filename": row["filename"], "cohorts_restored": restored_cohorts, "cells_restored": restored_cells, "cohorts_removed": removed}
