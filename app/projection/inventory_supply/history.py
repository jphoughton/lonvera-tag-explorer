"""History of plan-of-record supply writes: every change to orders, transfers, stock counts and price lists is one scenario version with its before and
after, listed in History, revertible one change at a time, and the whole supply can be restored to an earlier version or time.

    with history.tracked(conn, "po.create") as ch:        # one change = one bump of the default scenario's version
        ...write the row...
        ch.log("po", row["id"], old=None, new=row, audit_action="inventory_supply.po.create", ...)

`tracked` nests (an inner block joins the outer change), writes nothing when nothing was logged, and on exit bumps the scenario version once, stores one
`proj_supply_history` row per logged record and one `proj_audit` row per record carrying that version (so the History drawer shows them).
Scenario copies keep their changes in params (versioned by the core); this module is only for the stored rows.
"""
from __future__ import annotations

import contextvars
import functools
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Callable

from psycopg.types.json import Jsonb

from ...queries import ApiError
from .. import audit, scenarios

ID_TABLES = {"po": "proj_po", "transfer": "proj_fba_transfer"}
LOOKUPS: dict[str, tuple[str, tuple[str, ...]]] = {
    "stock": ("proj_stock_snapshot", ("location", "product", "as_of")),
    "vendor": ("proj_vendor_lead", ("vendor",)),
    "vendor_cost": ("proj_vendor_cogs", ("vendor", "product")),
    "reorder_param": ("proj_reorder_param", ("location", "product")),
    "product_cost": ("proj_product_cost", ("product",)),
}
KIND_LABEL = {"po": "Purchase order", "transfer": "Transfer", "stock": "Stock count", "vendor": "Vendor", "vendor_cost": "Price", "reorder_param": "Reorder timing",
              "product_cost": "Unit cost"}
_CURRENT: contextvars.ContextVar["Change | None"] = contextvars.ContextVar("supply_change", default=None)


def jsonable(v: Any) -> Any:
    if isinstance(v, dict):
        return {k: jsonable(x) for k, x in v.items()}
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    return v


@dataclass
class Entry:
    kind: str
    record_id: str
    op: str
    old: dict | None
    new: dict | None
    audit_action: str
    line_key: str
    month: date | None = None
    note: str | None = None
    reverts_version: int | None = None


@dataclass
class Change:
    slug: str
    action: str
    actor: str
    entries: list[Entry] = field(default_factory=list)
    audits: list[tuple] = field(default_factory=list)          # extra audit-only rows: (action, line_key, new, note)
    after: list[Callable[[int], None]] = field(default_factory=list)
    reverts: int | None = None

    def log(self, kind: str, record_id: Any, old: dict | None, new: dict | None, *, audit_action: str, line_key: str | None = None, when: date | None = None,
            note: str | None = None, op: str | None = None, reverts_version: int | None = None) -> None:
        op = op or ("create" if old is None and new is not None else "delete" if new is None else "update")
        self.entries.append(Entry(kind, str(record_id), op, jsonable(old) if old is not None else None, jsonable(new) if new is not None else None, audit_action,
                                  line_key or f"inventory_supply.{kind}:{record_id}", when.replace(day=1) if when else None, note, reverts_version if reverts_version is not None else self.reverts))

    def audit_only(self, action: str, line_key: str, new: dict | None, note: str | None = None) -> None:
        self.audits.append((action, line_key, new, note))


def current() -> Change | None:
    return _CURRENT.get()


def log(kind: str, record_id: Any, old: dict | None, new: dict | None, **kw) -> None:
    """Log onto the change in progress; outside a tracked block (loaders, seeds) the audit row is written without a history row."""
    ch = _CURRENT.get()
    if ch is None:
        raise RuntimeError("supply writes must run inside history.tracked()")
    ch.log(kind, record_id, old, new, **kw)


@contextmanager
def tracked(conn, action: str, *, bump: bool = True, version: int | None = None, reverts: int | None = None):
    """One change to the plan of record's supply data. Locks the default scenario, collects what is logged, and on exit bumps its version once."""
    outer = _CURRENT.get()
    if outer is not None:
        yield outer
        return
    slug = scenarios.default_slug(conn)
    row = scenarios.lock_for_change(conn, slug)
    ch = Change(slug=slug, action=action, actor=audit.current_actor(), reverts=reverts)
    token = _CURRENT.set(ch)
    try:
        yield ch
    finally:
        _CURRENT.reset(token)
    if not ch.entries and not ch.audits:
        return
    v = scenarios.finish_change(conn, row["id"], f"supply: {action}") if bump else version
    if v is None:
        raise RuntimeError("a change without a version bump needs the version it belongs to")
    _flush(conn, ch, v)
    for fn in ch.after:
        fn(v)


def _flush(conn, ch: Change, version: int) -> None:
    for e in ch.entries:
        conn.execute("INSERT INTO proj_supply_history (scenario_slug, scenario_version, actor, action, kind, record_id, op, old, new, reverts_version) "
                     "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                     (ch.slug, version, ch.actor, ch.action, e.kind, e.record_id, e.op, Jsonb(e.old) if e.old is not None else None,
                      Jsonb(e.new) if e.new is not None else None, e.reverts_version))
        audit.record(conn, actor=ch.actor, scenario=ch.slug, action=e.audit_action, line_key=e.line_key, month=e.month, old=e.old, new=e.new, note=e.note, version=version)
    for action, line_key, new, note in ch.audits:
        audit.record(conn, actor=ch.actor, scenario=ch.slug, action=action, line_key=line_key, new=new, note=note, version=version)


def tracked_op(action: str):
    """Decorator: run a store function (first argument = the connection) as one tracked change."""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(conn, *a, **kw):
            with tracked(conn, action):
                return fn(conn, *a, **kw)
        return wrapper
    return deco


# ---------------------------------------------------------------------------------------------------------------- reading

def _describe(e: dict) -> str:
    kind, old, new, op = e["kind"], e["old"] or {}, e["new"] or {}, e["op"]
    name = KIND_LABEL.get(kind, kind)
    if kind in ("po", "transfer"):
        row = new if op == "create" else old if op == "delete" else {}
        if op in ("create", "delete"):
            units = row.get("units")
            what = f"{units:,.0f} {row.get('product')}" if units is not None else str(row.get("product"))
            when = row.get("land_date") or row.get("ship_date")
            return f"{'Added' if op == 'create' else 'Deleted'} {name.lower()} {e['record_id']}: {what}" + (f", {when}" if when else "")
        return f"{name} {e['record_id']}: " + ", ".join(f"{k.replace('_', ' ')} {old.get(k)} to {new.get(k)}" for k in new)
    if kind == "stock":
        loc, prod, as_of = (e["record_id"].split("|") + ["", "", ""])[:3]
        return f"{name} {loc} {prod} {as_of}: on hand {old.get('on_hand', 'none')} to {new.get('on_hand', 'none')}"
    return f"{name} {e['record_id'].replace('|', ' ')} saved"


def list_changes(conn, limit: int = 50) -> list[dict]:
    """Newest first, one item per change (scenario version): who, when, what, and whether it can still be reverted."""
    limit = max(1, min(int(limit), 200))
    rows = conn.execute("SELECT * FROM proj_supply_history WHERE scenario_version IN (SELECT DISTINCT scenario_version FROM proj_supply_history "
                        "ORDER BY scenario_version DESC LIMIT %s) ORDER BY id DESC", (limit,)).fetchall()
    out: dict[int, dict] = {}
    for r in rows:
        c = out.setdefault(r["scenario_version"], {"version": r["scenario_version"], "at": r["at"].isoformat(), "actor": r["actor"], "action": r["action"],
                                                    "scenario": r["scenario_slug"], "entries": [], "reverted": True, "is_revert": r["reverts_version"] is not None})
        c["entries"].append({"id": r["id"], "kind": r["kind"], "record_id": r["record_id"], "op": r["op"], "label": _describe(r)})
        if r["reverted_by_version"] is None:
            c["reverted"] = False
    for c in out.values():
        c["entries"].reverse()
        c["revertible"] = not c["reverted"] and not c["is_revert"]
        c["summary"] = c["entries"][0]["label"] if len(c["entries"]) == 1 else f"{c['action']}: {len(c['entries'])} records"
    return sorted(out.values(), key=lambda c: -c["version"])


# ---------------------------------------------------------------------------------------------------------------- inverse operations

def _same(current: dict, expected: dict) -> bool:
    cur = jsonable(current)
    return all(cur.get(k) == v for k, v in expected.items())


def _key_where(kind: str, record_id: str) -> tuple[str, tuple]:
    table, cols = LOOKUPS[kind]
    parts = record_id.split("|")
    if len(parts) != len(cols):
        raise ApiError(409, f"history record {record_id!r} does not match its key")
    return " AND ".join(f"{c} = %s" for c in cols), tuple(parts)


def _fetch(conn, kind: str, record_id: str) -> dict | None:
    if kind in ID_TABLES:
        return conn.execute(f"SELECT * FROM {ID_TABLES[kind]} WHERE id = %s", (int(record_id),)).fetchone()          # noqa: S608 (constant table names)
    table, _ = LOOKUPS[kind]
    where, args = _key_where(kind, record_id)
    return conn.execute(f"SELECT * FROM {table} WHERE {where}", args).fetchone()                                      # noqa: S608


def _insert(conn, kind: str, row: dict) -> dict:
    table = ID_TABLES.get(kind) or LOOKUPS[kind][0]
    cols = list(row)
    sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})"                         # noqa: S608
    if kind in LOOKUPS:
        keys = LOOKUPS[kind][1]
        rest = [c for c in cols if c not in keys]
        sql += f" ON CONFLICT ({', '.join(keys)}) DO " + (f"UPDATE SET {', '.join(f'{c} = EXCLUDED.{c}' for c in rest)}" if rest else "NOTHING")
    return conn.execute(sql + " RETURNING *", [row[c] for c in cols]).fetchone()


def _delete(conn, kind: str, record_id: str) -> None:
    if kind in ID_TABLES:
        conn.execute(f"DELETE FROM {ID_TABLES[kind]} WHERE id = %s", (int(record_id),))                               # noqa: S608
    else:
        table, _ = LOOKUPS[kind]
        where, args = _key_where(kind, record_id)
        conn.execute(f"DELETE FROM {table} WHERE {where}", args)                                                       # noqa: S608


def _apply_inverse(conn, ch: Change, e: dict, *, check: bool) -> None:
    """Undo one history row: delete what it created, put back what it deleted, restore the columns it changed. `check` refuses when the record was changed
    again since (the later change has to be reverted first)."""
    kind, rid, old, new = e["kind"], e["record_id"], e["old"], e["new"]
    cur = _fetch(conn, kind, rid)
    when = None
    if e["op"] == "create" or (e["op"] == "put" and old is None):               # undo a create: delete the row
        if cur is None:
            if check:
                raise ApiError(409, f"{KIND_LABEL[kind]} {rid} is already gone")
            return
        if check and new and not _same(cur, new):
            raise ApiError(409, f"{KIND_LABEL[kind]} {rid} was changed after this; revert the later change first")
        _delete(conn, kind, rid)
        ch.log(kind, rid, cur, None, audit_action=f"inventory_supply.{kind}.delete", op="delete", note=f"revert of version {e['scenario_version']}", reverts_version=e["scenario_version"])
    elif e["op"] == "delete":                                                   # undo a delete: put the row back
        if cur is not None:
            if check:
                raise ApiError(409, f"{KIND_LABEL[kind]} {rid} exists again; nothing to restore")
            return
        row = _insert(conn, kind, old)
        ch.log(kind, rid, None, row, audit_action=f"inventory_supply.{kind}.create", op="create", note=f"revert of version {e['scenario_version']}", reverts_version=e["scenario_version"])
    else:                                                                       # undo an update / put: restore the old columns
        if cur is None:
            if check:
                raise ApiError(409, f"{KIND_LABEL[kind]} {rid} is gone")
            return
        if check and new and not _same(cur, new):
            raise ApiError(409, f"{KIND_LABEL[kind]} {rid} was changed after this; revert the later change first")
        if kind in ID_TABLES:
            sets = ", ".join(f"{c} = %s" for c in old)
            row = conn.execute(f"UPDATE {ID_TABLES[kind]} SET {sets}, updated_at = now() WHERE id = %s RETURNING *", [*old.values(), int(rid)]).fetchone()   # noqa: S608
            ch.log(kind, rid, {c: cur[c] for c in old}, {c: row[c] for c in old}, audit_action=f"inventory_supply.{kind}.update", op="update",
                   note=f"revert of version {e['scenario_version']}", reverts_version=e["scenario_version"])
        else:
            row = _insert(conn, kind, old)
            ch.log(kind, rid, cur, row, audit_action=f"inventory_supply.{kind}.put", op="put", note=f"revert of version {e['scenario_version']}", reverts_version=e["scenario_version"])


def _mark(conn, ids: list[int]) -> Callable[[int], None]:
    def run(version: int) -> None:
        conn.execute("UPDATE proj_supply_history SET reverted_by_version = %s WHERE id = ANY(%s)", (version, ids))
    return run


def revert_change(conn, version: int) -> dict:
    """Undo the change made at scenario version `version` (all its records, newest first). 409 when a record changed again since."""
    rows = conn.execute("SELECT * FROM proj_supply_history WHERE scenario_version = %s ORDER BY id DESC", (version,)).fetchall()
    if not rows:
        raise ApiError(404, "no supply change at that version")
    if any(r["reverts_version"] is not None for r in rows):
        raise ApiError(400, "a revert cannot be reverted; make the change again instead")
    if all(r["reverted_by_version"] is not None for r in rows):
        raise ApiError(409, "that change was already reverted")
    with tracked(conn, f"revert v{version}") as ch:
        for r in rows:
            if r["reverted_by_version"] is None:
                _apply_inverse(conn, ch, r, check=True)
        ch.after.append(_mark(conn, [r["id"] for r in rows if r["reverted_by_version"] is None]))
    return {"reverted_version": version, "scenario_version": scenarios.get_row(conn, ch.slug)["version"], "records": len(rows)}


def _pending(conn, *, to_version: int | None = None, at: datetime | None = None, below: int | None = None) -> list[dict]:
    """Changes still in force after a version or time: not reverted, not themselves reverts, newest first."""
    where, args = ["reverted_by_version IS NULL", "reverts_version IS NULL"], []
    if to_version is not None:
        where.append("scenario_version > %s")
        args.append(to_version)
    if at is not None:
        where.append("at > %s")
        args.append(at)
    if below is not None:
        where.append("scenario_version < %s")
        args.append(below)
    return conn.execute(f"SELECT * FROM proj_supply_history WHERE {' AND '.join(where)} ORDER BY id DESC", args).fetchall()                # noqa: S608


def restore_to(conn, *, to_version: int | None = None, at: datetime | None = None) -> dict:
    """Restore the supply data to how it was at a scenario version or time: every change made after it that is still in force is undone, newest first,
    as one new version. Nothing is deleted from history."""
    if (to_version is None) == (at is None):
        raise ApiError(400, "send exactly one of to_version or at")
    rows = _pending(conn, to_version=to_version, at=at)
    if not rows:
        slug = scenarios.default_slug(conn)
        return {"reverted": 0, "scenario_version": scenarios.get_row(conn, slug)["version"]}
    with tracked(conn, "restore") as ch:
        for r in rows:
            _apply_inverse(conn, ch, r, check=False)
        ch.after.append(_mark(conn, [r["id"] for r in rows]))
    return {"reverted": len(rows), "scenario_version": scenarios.get_row(conn, ch.slug)["version"]}


def on_restore(conn, slug: str, to_version: int, new_version: int, actor: str | None = None) -> dict:
    """Hook for the core's scenario restore (default scenario): after params and series went back to `to_version` and the restore became `new_version`,
    undo the supply changes made in between so orders, transfers and counts match. Writes history under `new_version` without a second bump."""
    if slug != scenarios.default_slug(conn):
        return {"reverted": 0}
    rows = _pending(conn, to_version=to_version, below=new_version)
    if not rows:
        return {"reverted": 0}
    with tracked(conn, f"restore to v{to_version}", bump=False, version=new_version) as ch:
        for r in rows:
            _apply_inverse(conn, ch, r, check=False)
        ch.after.append(_mark(conn, [r["id"] for r in rows]))
    return {"reverted": len(rows)}
