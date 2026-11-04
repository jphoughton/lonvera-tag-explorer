"""Undo one Spend plan change: put back exactly the cells (and the NC-ROAS slide lever) a change wrote, as one audited, versioned change.

    undo_change(conn, slug, version, expected_version=None, actor=None) -> {"version", "restored", "skipped"}

`version` is the version the change produced (a pattern fill returns it). The change's audit rows say what each cell held before and after:
a cell the change created is deleted, a cell it updated goes back to its old value, kind and note, a cell it deleted is written back. A cell
that was changed again since (its current value is no longer what the change wrote) is left alone and counted in `skipped`. The undo itself
writes audit rows (series.set / update / delete, scenario.params), so it shows in History and can be restored over like any other change.
It restores a state the scenario already held, so it does not run the new-input validators (a closed month's old typed input can come back).
"""
from __future__ import annotations

from decimal import Decimal

from psycopg.types.json import Jsonb

from ...queries import ApiError
from .. import audit, scenarios
from .model import SERIES_PREFIX

_ACTIONS = ("series.set", "series.update", "series.delete", "scenario.params")
_TOL = 5e-5


def _same(cur: dict, wrote: dict) -> bool:
    return cur["kind"] == wrote["kind"] and abs(float(cur["value"]) - float(wrote["value"])) < _TOL


def undo_change(conn, slug: str, version, *, expected_version: int | None = None, actor: str | None = None) -> dict:
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ApiError(400, "version must be a positive integer")
    r = scenarios.lock_for_change(conn, slug, expected_version)
    sid = r["id"]
    rows = conn.execute("SELECT action, line_key, month, old, new FROM proj_audit WHERE scenario_slug = %s AND version = %s AND action = ANY(%s) ORDER BY id",
                        (slug, version, list(_ACTIONS))).fetchall()
    cells = [x for x in rows if x["action"].startswith("series.") and (x["line_key"] or "").startswith(SERIES_PREFIX)]
    params = [x for x in rows if x["action"] == "scenario.params"]
    if not cells and not params:
        raise ApiError(404, "there is no Spend plan change at that version to undo")
    existing = {(e["line_key"], e["month"]): e for e in conn.execute(
        "SELECT line_key, month, value, kind, note FROM proj_series WHERE scenario_id = %s AND (line_key, month) IN (SELECT * FROM unnest(%s::text[], %s::date[]))",
        (sid, [x["line_key"] for x in cells], [x["month"] for x in cells])).fetchall()} if cells else {}
    trail: list[tuple] = []
    skipped = 0
    with conn.cursor() as cur:
        for x in cells:
            key, month, old, new = x["line_key"], x["month"], x["old"], x["new"]
            now = existing.get((key, month))
            if x["action"] == "series.delete":
                if now is not None:
                    skipped += 1                                       # something was typed there since
                    continue
                cur.execute("INSERT INTO proj_series (scenario_id, line_key, month, value, kind, note) VALUES (%s,%s,%s,%s,%s,%s)",
                            (sid, key, month, Decimal(repr(old["value"])), old["kind"], old.get("note")))
                trail.append(("series.set", key, month, None, old))
                continue
            if now is None or not _same(now, new):
                skipped += 1                                           # changed again since: not this change's to undo
                continue
            now_j = {"value": float(now["value"]), "kind": now["kind"], "note": now["note"]}
            if old is None:
                cur.execute("DELETE FROM proj_series WHERE scenario_id = %s AND line_key = %s AND month = %s", (sid, key, month))
                trail.append(("series.delete", key, month, now_j, None))
            else:
                cur.execute("UPDATE proj_series SET value = %s, kind = %s, note = %s, updated_at = now() WHERE scenario_id = %s AND line_key = %s AND month = %s",
                            (Decimal(repr(old["value"])), old["kind"], old.get("note"), sid, key, month))
                trail.append(("series.update", key, month, now_j, old))
        new_params = r["params"]
        for x in params:                                               # the NC-ROAS slide (and any other drivers lever) the change set
            old_d = ((x["old"] or {}).get("params") or {}).get("drivers") or {}
            new_d = ((x["new"] or {}).get("params") or {}).get("drivers") or {}
            cur_d = (new_params or {}).get("drivers") or {}
            patch = {k: old_d.get(k) for k in set(old_d) | set(new_d) if old_d.get(k) != new_d.get(k) and cur_d.get(k) == new_d.get(k)}
            skipped += sum(1 for k in set(old_d) | set(new_d) if old_d.get(k) != new_d.get(k) and cur_d.get(k) != new_d.get(k))
            if patch:
                new_params = scenarios.merge_patch(new_params, {"drivers": patch})
        params_changed = new_params != r["params"]
        if params_changed:
            cur.execute("UPDATE proj_scenarios SET params = %s WHERE id = %s", (Jsonb(new_params), sid))
    if not trail and not params_changed:
        return {"version": r["version"], "restored": 0, "skipped": skipped}
    v = scenarios.finish_change(conn, sid, "undo")
    who = actor or audit.current_actor()
    audit.record_many(conn, [(who, slug, action, key, month, old_j, new_j, f"undo of version {version}", v) for action, key, month, old_j, new_j in trail])
    if params_changed:
        audit.record(conn, actor=who, scenario=slug, action="scenario.params", old={"params": r["params"]}, new={"params": new_params}, note=f"undo of version {version}", version=v)
    return {"version": v, "restored": len(trail) + int(params_changed), "skipped": skipped}
