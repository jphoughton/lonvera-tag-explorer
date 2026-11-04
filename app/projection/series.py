"""Monthly series: typed per-scenario cells (proj_series) and loaded actuals (proj_actuals).

Reads (any connection):
    get_series(conn, scn, keys=[...] | prefix='drivers.', from_, to) -> {line_key: {month: float}}   scenario -> base -> base's base
    get(conn, scn, key, month) -> float | None
    get_actuals(conn, keys=None | prefix=, from_, to) -> {line_key: {month: float}}
Writes (read-write connection, one transaction): apply_items (the PUT /series body), upsert_actuals (loaders).

A scenario cell wins over its base's; deleting a scenario cell (value null) reverts that month to the base's value.
`kind` is 'input' (replaces a typed constant of the sheet) or 'override' (replaces a computed value for that month).
"""
from __future__ import annotations

import contextlib
import contextvars
import math
import re
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Iterable

from ..queries import ApiError
from . import audit, scenarios
from .interfaces import AXIS_END, AXIS_START, Scenario, parse_month

LINE_KEY_RE = re.compile(r"^[a-z0-9_.]{1,120}$")
KINDS = ("input", "override")
MAX_ITEMS = 500
MAX_KEYS = 200
MAX_NOTE = 500
MAX_ABS = Decimal(10) ** 13                      # numeric(18,4) holds < 1e14; stay well inside
_Q = Decimal("0.0001")
_ITEM_KEYS = {"line_key", "month", "value", "kind", "note"}


def check_key(key: Any, what: str = "line_key") -> str:
    if not isinstance(key, str) or not LINE_KEY_RE.match(key):
        raise ApiError(400, f"{what} must match ^[a-z0-9_.]+$ (max 120 characters)")
    return key


def _dec(v: float | int) -> Decimal:
    return Decimal(repr(v) if isinstance(v, float) else v).quantize(_Q, rounding=ROUND_HALF_UP)


# ------------------------------------------------------------------------------------------------------------------ reads

# A read-only draft laid over the scenario's series for what-if runs that must write nothing (the scenario modeler). Active only in the current context
# (a ContextVar: another request's thread never sees it). The function gets the winning cells and returns the cells the run should see.
_OVERLAY: contextvars.ContextVar = contextvars.ContextVar("series_overlay", default=None)


@contextlib.contextmanager
def with_overlay(fn):
    """`with series.with_overlay(fn): engine.run(...)`: every fetch_cells / get_series / get inside sees fn(conn, scn, cells, keys, prefix, from_, to)."""
    token = _OVERLAY.set(fn)
    try:
        yield
    finally:
        _OVERLAY.reset(token)


def fetch_cells(conn, scn: Scenario | str | None, keys: Iterable[str] | None = None, *, prefix: str | None = None,
                from_: date | None = None, to: date | None = None) -> list[dict]:
    """Winning cells for the scenario and its fallback chain: [{line_key, month, value(float), kind, note, origin(slug)}]."""
    ids = scenarios.chain_ids(conn, scn)
    slugs = {r["id"]: r["slug"] for r in conn.execute("SELECT id, slug FROM proj_scenarios WHERE id = ANY(%s)", (ids,)).fetchall()}
    where, args = ["scenario_id = ANY(%s)", "month >= %s", "month <= %s"], [ids, from_ or AXIS_START, to or AXIS_END]
    if keys is not None:
        where.append("line_key = ANY(%s)")
        args.append(list(keys))
    elif prefix is not None:
        where.append("left(line_key, %s) = %s")
        args += [len(prefix), prefix]
    rows = conn.execute("SELECT scenario_id, line_key, month, value, kind, note FROM proj_series WHERE " + " AND ".join(where), args).fetchall()
    rank = {sid: i for i, sid in enumerate(ids)}
    best: dict[tuple[str, date], dict] = {}
    for r in rows:
        k = (r["line_key"], r["month"])
        if k not in best or rank[r["scenario_id"]] < rank[best[k]["scenario_id"]]:
            best[k] = r
    cells = [{"line_key": r["line_key"], "month": r["month"], "value": float(r["value"]), "kind": r["kind"], "note": r["note"],
              "origin": slugs[r["scenario_id"]]} for r in best.values()]
    overlay = _OVERLAY.get()
    return overlay(conn, scn, cells, keys, prefix, from_, to) if overlay is not None else cells


def get_series(conn, scn: Scenario | str | None, keys: Iterable[str] | None = None, *, prefix: str | None = None,
               from_: date | None = None, to: date | None = None) -> dict[str, dict[date, float]]:
    """{line_key: {month: value}}; every requested key is present (empty when it holds no cells)."""
    keys = list(keys) if keys is not None else None
    out: dict[str, dict[date, float]] = {k: {} for k in keys or []}
    for c in fetch_cells(conn, scn, keys, prefix=prefix, from_=from_, to=to):
        out.setdefault(c["line_key"], {})[c["month"]] = c["value"]
    return out


def get(conn, scn: Scenario | str | None, key: str, month: date) -> float | None:
    return get_series(conn, scn, [key], from_=month, to=month)[key].get(month)


def get_actuals(conn, keys: Iterable[str] | None = None, *, prefix: str | None = None,
                from_: date | None = None, to: date | None = None) -> dict[str, dict[date, float]]:
    where, args = ["TRUE"], []
    keys = list(keys) if keys is not None else None
    if keys is not None:
        where.append("line_key = ANY(%s)")
        args.append(keys)
    elif prefix is not None:
        where.append("left(line_key, %s) = %s")
        args += [len(prefix), prefix]
    if from_:
        where.append("month >= %s")
        args.append(from_)
    if to:
        where.append("month <= %s")
        args.append(to)
    out: dict[str, dict[date, float]] = {k: {} for k in keys or []}
    for r in conn.execute("SELECT line_key, month, value FROM proj_actuals WHERE " + " AND ".join(where), args).fetchall():
        out.setdefault(r["line_key"], {})[r["month"]] = float(r["value"])
    return out


def actual_through(conn, today: date) -> date | None:
    """Last closed month (before the first of `today`'s month) that holds any loaded actual."""
    r = conn.execute("SELECT max(month) AS m FROM proj_actuals WHERE month < %s", (today.replace(day=1),)).fetchone()
    return r["m"]


# ------------------------------------------------------------------------------------------------------------------ writes

@dataclass(frozen=True)
class Item:
    line_key: str
    month: date
    value: Decimal | None                        # None = delete the cell
    kind: str
    note: str | None


def parse_items(raw: Any) -> list[Item]:
    """Validate the PUT /series `items` list. Every problem is a 400 naming the item."""
    if not isinstance(raw, list) or not raw:
        raise ApiError(400, "items must be a non-empty list")
    if len(raw) > MAX_ITEMS:
        raise ApiError(400, f"at most {MAX_ITEMS} items per request")
    out: list[Item] = []
    seen: set[tuple[str, date]] = set()
    for i, it in enumerate(raw):
        where = f"items[{i}]"
        if not isinstance(it, dict):
            raise ApiError(400, f"{where} must be an object")
        extra = set(it) - _ITEM_KEYS
        if extra:
            raise ApiError(400, f"{where}: unknown field {sorted(extra)[0]!r}")
        try:
            key = check_key(it.get("line_key"))
            month = parse_month(it.get("month"))
        except ValueError as e:
            raise ApiError(400, f"{where}: {e} (month must be a first-of-month ISO date inside 2025-01 .. 2029-12)") from None
        except ApiError as e:
            raise ApiError(400, f"{where}: {e.message}") from None
        if "value" not in it:
            raise ApiError(400, f"{where}: value is required (null deletes the cell)")
        v = it["value"]
        value: Decimal | None = None
        if v is not None:
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                raise ApiError(400, f"{where}: value must be a number or null")
            if isinstance(v, float) and not math.isfinite(v):
                raise ApiError(400, f"{where}: value must be finite")
            value = _dec(v)
            if abs(value) >= MAX_ABS:
                raise ApiError(400, f"{where}: value out of range")
        kind = it.get("kind", "input")
        if value is not None and kind not in KINDS:
            raise ApiError(400, f"{where}: kind must be 'input' or 'override'")
        note = it.get("note")
        if note is not None and (not isinstance(note, str) or len(note) > MAX_NOTE):
            raise ApiError(400, f"{where}: note must be a string of at most {MAX_NOTE} characters")
        if (key, month) in seen:
            raise ApiError(400, f"{where}: duplicate cell {key} {month.isoformat()}")
        seen.add((key, month))
        out.append(Item(key, month, value, kind if value is not None else "input", note or None))
    return out


def _cell_json(value: Decimal, kind: str, note: str | None) -> dict:
    return {"value": float(value), "kind": kind, "note": note}


def apply_items(conn, slug: str, items: list[Item], *, expected_version: int | None = None, actor: str | None = None) -> dict:
    """Upsert / delete cells of one scenario. One version bump, one history snapshot and one audit row per changed cell.
    Returns {version, changed}; a request that changes nothing leaves the version alone."""
    r = scenarios.lock_for_change(conn, slug, expected_version)
    sid = r["id"]
    from . import validators                           # late: validators reads the module registry
    validators.check_series(conn, scenarios.get_scenario(conn, slug), items)
    existing = {(e["line_key"], e["month"]): e for e in conn.execute(
        "SELECT line_key, month, value, kind, note FROM proj_series WHERE scenario_id = %s "
        "AND (line_key, month) IN (SELECT * FROM unnest(%s::text[], %s::date[]))",
        (sid, [i.line_key for i in items], [i.month for i in items])).fetchall()}
    upserts: list[tuple] = []
    deletes: list[tuple] = []
    trail: list[tuple] = []
    for it in items:
        old = existing.get((it.line_key, it.month))
        old_j = _cell_json(old["value"], old["kind"], old["note"]) if old else None
        if it.value is None:
            if old:
                deletes.append((sid, it.line_key, it.month))
                trail.append(("series.delete", it.line_key, it.month, old_j, None))
            continue
        if old and old["value"] == it.value and old["kind"] == it.kind and (old["note"] or None) == it.note:
            continue
        upserts.append((sid, it.line_key, it.month, it.value, it.kind, it.note))
        trail.append(("series.update" if old else "series.set", it.line_key, it.month, old_j, _cell_json(it.value, it.kind, it.note)))
    if not trail:
        return {"version": r["version"], "changed": 0}
    with conn.cursor() as cur:
        if upserts:
            cur.executemany(
                "INSERT INTO proj_series (scenario_id, line_key, month, value, kind, note) VALUES (%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (scenario_id, line_key, month) DO UPDATE SET value = EXCLUDED.value, kind = EXCLUDED.kind, "
                "note = EXCLUDED.note, updated_at = now()", upserts)
        if deletes:
            cur.executemany("DELETE FROM proj_series WHERE scenario_id = %s AND line_key = %s AND month = %s", deletes)
    version = scenarios.finish_change(conn, sid, "series")
    who = actor or audit.current_actor()
    audit.record_many(conn, [(who, slug, action, key, month, old_j, new_j, None, version) for action, key, month, old_j, new_j in trail])
    return {"version": version, "changed": len(trail)}


def upsert_actuals(conn, rows: Iterable[tuple[str, date, float]], source: str, *, actor: str | None = None) -> int:
    """Loader helper: upsert (line_key, first-of-month, value) rows into proj_actuals and write one audit row for the load.
    Months may lie before the 60-month axis (cohort history). Aggregates only."""
    prepared: list[tuple] = []
    for key, month, value in rows:
        check_key(key)
        if not isinstance(month, date) or month.day != 1:
            raise ApiError(400, f"actuals month must be a first-of-month date, got {month!r}")
        if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)) or not math.isfinite(float(value)):
            raise ApiError(400, "actuals values must be finite numbers")
        prepared.append((key, month, _dec(value) if not isinstance(value, Decimal) else value.quantize(_Q), source))
    if prepared:
        with conn.cursor() as cur:
            cur.executemany("INSERT INTO proj_actuals (line_key, month, value, source) VALUES (%s,%s,%s,%s) "
                            "ON CONFLICT (line_key, month) DO UPDATE SET value = EXCLUDED.value, source = EXCLUDED.source, loaded_at = now()",
                            prepared)
    months = [p[1] for p in prepared]
    audit.record(conn, actor=actor, scenario=None, action="actuals.load", note=source,
                 new={"rows": len(prepared), "keys": len({p[0] for p in prepared}),
                      "from": min(months).isoformat() if months else None, "to": max(months).isoformat() if months else None})
    return len(prepared)
