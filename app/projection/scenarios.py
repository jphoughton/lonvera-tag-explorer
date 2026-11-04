"""Scenarios: named lever sets (`params`) plus monthly series, with a base to fall back to, a version counter and per-version history.

All write functions take a read-write connection inside one transaction (rwdb.get_rw) and audit what they do. Read functions work on any
connection. Errors are ApiError (the app's {error} shape).
"""
from __future__ import annotations

import copy
import json
import math
import re
from datetime import date
from decimal import Decimal
from typing import Any

from psycopg.types.json import Jsonb

from ..queries import ApiError
from . import audit
from .interfaces import Scenario

CURRENT_SLUG = "current"
HISTORY_KEEP = 200
MAX_NAME = 80
MAX_PARAMS_BYTES = 256 * 1024
MAX_JSON_DEPTH = 10
MAX_CHAIN = 8

_COLS = "id, slug, name, base_slug, is_default, params, version, created_at, updated_at, frozen, archived_at"
ARCHIVE_DAYS = 30
_CTRL = re.compile(r"[\x00-\x1f\x7f]")
_NS_KEY = re.compile(r"^[a-z0-9_]{1,64}$")


# ------------------------------------------------------------------------------------------------------------------ validation

def validate_json(obj: Any, depth: int = 0) -> None:
    """Plain JSON only: str keys, finite numbers, bounded depth."""
    if depth > MAX_JSON_DEPTH:
        raise ApiError(400, "params nested too deeply")
    if isinstance(obj, dict):
        for k, v in obj.items():
            if not isinstance(k, str) or not k or len(k) > 120:
                raise ApiError(400, "params keys must be non-empty strings")
            validate_json(v, depth + 1)
    elif isinstance(obj, list):
        for v in obj:
            validate_json(v, depth + 1)
    elif isinstance(obj, float):
        if not math.isfinite(obj):
            raise ApiError(400, "params values must be finite numbers")
    elif obj is None or isinstance(obj, (str, int, bool)):
        return
    else:
        raise ApiError(400, "params must be plain JSON")


def validate_name(name: Any) -> str:
    if not isinstance(name, str) or not name.strip():
        raise ApiError(400, "name is required")
    name = name.strip()
    if len(name) > MAX_NAME or _CTRL.search(name):
        raise ApiError(400, f"name must be 1-{MAX_NAME} characters without control characters")
    return name


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40].strip("-")
    return s or "scenario"


def merge_patch(base: dict, patch: dict) -> dict:
    """JSON merge patch (RFC 7396): dicts merge recursively, null removes a key, anything else (lists, scalars) replaces."""
    out = copy.deepcopy(base)
    for k, v in patch.items():
        if v is None:
            out.pop(k, None)
        elif isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = merge_patch(out[k], v)
        elif isinstance(v, dict):
            out[k] = merge_patch({}, v)               # drops nulls inside a new subtree
        else:
            out[k] = copy.deepcopy(v)
    return out


def _deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        out[k] = _deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else copy.deepcopy(v)
    return out


def _check_params_patch(patch: Any) -> dict:
    if not isinstance(patch, dict):
        raise ApiError(400, "params must be an object")
    validate_json(patch)
    for ns, v in patch.items():
        if not _NS_KEY.match(ns):
            raise ApiError(400, f"params namespace {ns!r} must match [a-z0-9_]+ (use one key per module, e.g. 'cohorts')")
        if v is not None and not isinstance(v, dict):
            raise ApiError(400, f"params[{ns!r}] must be an object (or null to remove the namespace)")
    return patch


# ------------------------------------------------------------------------------------------------------------------ reads

def _all_rows(conn) -> dict[str, dict]:
    return {r["slug"]: r for r in conn.execute(f"SELECT {_COLS} FROM proj_scenarios ORDER BY is_default DESC, id").fetchall()}


def _chain_slugs(by_slug: dict[str, dict], slug: str) -> list[str]:
    """[slug, its base, the base's base, ...] (cycle- and depth-safe). A frozen scenario (every clone made since the lifecycle change, and any detached
    one) holds its own copy of everything and reads nothing from its base: its chain is just itself."""
    out: list[str] = []
    cur: str | None = slug
    while cur and cur in by_slug and cur not in out and len(out) < MAX_CHAIN:
        out.append(cur)
        if by_slug[cur]["frozen"]:
            break
        cur = by_slug[cur]["base_slug"]
    return out


def _effective(by_slug: dict[str, dict], slug: str) -> dict:
    params: dict = {}
    for s in reversed(_chain_slugs(by_slug, slug)):                 # root first, the scenario last
        params = _deep_merge(params, by_slug[s]["params"])
    return params


def _public(r: dict, effective: dict | None = None) -> dict:
    d = {k: r[k] for k in ("id", "slug", "name", "base_slug", "is_default", "params", "version", "created_at", "updated_at", "frozen")}
    if effective is not None:
        d["effective_params"] = effective
    return d


def list_scenarios(conn) -> list[dict]:
    """Active (not deleted) scenarios, default first."""
    by_slug = _all_rows(conn)
    return [_public(r, _effective(by_slug, s)) for s, r in by_slug.items() if r["archived_at"] is None]


def list_archived(conn) -> list[dict]:
    """Deleted scenarios still inside the 30-day archive, newest first, with days_left."""
    rows = conn.execute(f"SELECT {_COLS}, GREATEST(0, %s - EXTRACT(day FROM now() - archived_at)::int) AS days_left FROM proj_scenarios "
                        "WHERE archived_at IS NOT NULL AND archived_at > now() - make_interval(days => %s) ORDER BY archived_at DESC",
                        (ARCHIVE_DAYS, ARCHIVE_DAYS)).fetchall()
    return [{**_public(r), "archived_at": r["archived_at"], "days_left": r["days_left"]} for r in rows]


def get_row(conn, slug: str, *, lock: bool = False, include_archived: bool = False) -> dict:
    r = conn.execute(f"SELECT {_COLS} FROM proj_scenarios WHERE slug = %s" + ("" if include_archived else " AND archived_at IS NULL")
                     + (" FOR UPDATE" if lock else ""), (slug,)).fetchone()
    if r is None:
        raise ApiError(404, "scenario not found")
    return r


def default_slug(conn) -> str:
    r = conn.execute("SELECT slug FROM proj_scenarios WHERE is_default AND archived_at IS NULL").fetchone()
    return r["slug"] if r else CURRENT_SLUG


def get_scenario(conn, slug: str | None = None, *, effective: bool = True) -> Scenario:
    """Scenario by slug (None = the default one). `params` is the effective view (base chain merged under the scenario's own) unless effective=False."""
    by_slug = _all_rows(conn)
    if slug is None:
        slug = next((s for s, r in by_slug.items() if r["is_default"]), CURRENT_SLUG)
    r = by_slug.get(slug)
    if r is None or r["archived_at"] is not None:
        raise ApiError(404, "scenario not found")
    return Scenario(id=r["id"], slug=r["slug"], name=r["name"], params=_effective(by_slug, slug) if effective else copy.deepcopy(r["params"]),
                    base_slug=r["base_slug"], version=r["version"], is_default=r["is_default"])


def chain_ids(conn, scn: Scenario | str | int | None) -> list[int]:
    """Scenario ids in fallback order: the scenario, its base, the base's base, ..."""
    by_slug = _all_rows(conn)
    if isinstance(scn, Scenario):
        slug = scn.slug
    elif isinstance(scn, int):
        slug = next((s for s, r in by_slug.items() if r["id"] == scn), None)
    else:
        slug = scn or next((s for s, r in by_slug.items() if r["is_default"]), CURRENT_SLUG)
    if slug not in by_slug:
        raise ApiError(404, "scenario not found")
    return [by_slug[s]["id"] for s in _chain_slugs(by_slug, slug)]


# ------------------------------------------------------------------------------------------------------------------ history

def snapshot_state(conn, scenario_id: int) -> dict:
    """params + every own series cell, compact: series rows are [line_key, month, value-as-text, kind, note]."""
    params = conn.execute("SELECT params FROM proj_scenarios WHERE id = %s", (scenario_id,)).fetchone()["params"]
    rows = conn.execute("SELECT line_key, month, value, kind, note FROM proj_series WHERE scenario_id = %s ORDER BY line_key, month",
                        (scenario_id,)).fetchall()
    return {"params": params, "series": [[r["line_key"], r["month"].isoformat(), str(r["value"]), r["kind"], r["note"]] for r in rows]}


def _write_snapshot(conn, scenario_id: int, version: int, action: str, state: dict | None = None) -> None:
    conn.execute("INSERT INTO proj_scenario_history (scenario_id, version, snapshot, action) VALUES (%s,%s,%s,%s) "
                 "ON CONFLICT (scenario_id, version) DO UPDATE SET snapshot = EXCLUDED.snapshot, action = EXCLUDED.action, at = now()",
                 (scenario_id, version, Jsonb(state if state is not None else snapshot_state(conn, scenario_id)), action))
    conn.execute("DELETE FROM proj_scenario_history WHERE scenario_id = %s AND version NOT IN "
                 "(SELECT version FROM proj_scenario_history WHERE scenario_id = %s ORDER BY version DESC LIMIT %s)",
                 (scenario_id, scenario_id, HISTORY_KEEP))


def lock_for_change(conn, slug: str, expected_version: int | None = None) -> dict:
    """Lock the scenario row, check the caller's version, and make sure the state BEFORE the change is in history (so it can be restored).

    The snapshot of the current version is taken lazily from the live state at the first audited write, and refreshed when the live state has
    drifted from it (a loader or seeder wrote outside the audited path since): the state the user starts editing from is what version N means."""
    r = get_row(conn, slug, lock=True)
    if expected_version is not None and expected_version != r["version"]:
        raise ApiError(409, f"version conflict: the scenario is at version {r['version']}")
    state = snapshot_state(conn, r["id"])
    have = conn.execute("SELECT snapshot FROM proj_scenario_history WHERE scenario_id = %s AND version = %s", (r["id"], r["version"])).fetchone()
    if have is None:
        _write_snapshot(conn, r["id"], r["version"], "baseline", state)
    elif have["snapshot"] != state:
        conn.execute("UPDATE proj_scenario_history SET snapshot = %s WHERE scenario_id = %s AND version = %s", (Jsonb(state), r["id"], r["version"]))
    return r


def finish_change(conn, scenario_id: int, action: str) -> int:
    """Bump the version once for a change and snapshot the new state. Returns the new version."""
    v = conn.execute("UPDATE proj_scenarios SET version = version + 1, updated_at = now() WHERE id = %s RETURNING version",
                     (scenario_id,)).fetchone()["version"]
    _write_snapshot(conn, scenario_id, v, action)
    return v


def list_history(conn, slug: str, limit: int = 50) -> list[dict]:
    r = get_row(conn, slug)
    limit = max(1, min(int(limit), HISTORY_KEEP))
    return conn.execute("SELECT version, at, action FROM proj_scenario_history WHERE scenario_id = %s ORDER BY version DESC LIMIT %s",
                        (r["id"], limit)).fetchall()


# ------------------------------------------------------------------------------------------------------------------ writes

def _unique_slug(conn, name: str, scenario_cols: dict) -> dict:
    base = slugify(name)
    for i in range(1, 200):
        slug = base if i == 1 else f"{base}-{i}"
        row = conn.execute(
            "INSERT INTO proj_scenarios (slug, name, base_slug, is_default, params, frozen) VALUES (%s,%s,%s,false,%s,true) "
            "ON CONFLICT (slug) DO NOTHING RETURNING " + _COLS,
            (slug, name, scenario_cols["base_slug"], Jsonb(scenario_cols["params"]))).fetchone()
        if row:
            return row
    raise ApiError(409, "could not find a free scenario slug")


def _check_name_free(conn, name: str, except_slug: str | None = None) -> None:
    dup = conn.execute("SELECT 1 FROM proj_scenarios WHERE lower(name) = lower(%s) AND slug IS DISTINCT FROM %s AND archived_at IS NULL", (name, except_slug)).fetchone()
    if dup:
        raise ApiError(409, "a scenario with that name already exists")


def _effective_cells(conn, scenario_ids: list[int]) -> dict[tuple, tuple]:
    """(line_key, month) -> (value, kind, note) of the scenario at the head of `scenario_ids`, filled from the ones after it (nearest wins)."""
    rank = {sid: i for i, sid in enumerate(scenario_ids)}
    best: dict[tuple, tuple[int, tuple]] = {}
    for r in conn.execute("SELECT scenario_id, line_key, month, value, kind, note FROM proj_series WHERE scenario_id = ANY(%s)", (scenario_ids,)).fetchall():
        k = (r["line_key"], r["month"])
        if k not in best or rank[r["scenario_id"]] < best[k][0]:
            best[k] = (rank[r["scenario_id"]], (r["value"], r["kind"], r["note"]))
    return {k: v for k, (_, v) in best.items()}


def create(conn, name: Any, clone_from: str | None = None, *, actor: str | None = None) -> dict:
    """New scenario = a FROZEN COPY of `clone_from` (default: the default scenario): the effective params and series of the source are materialized into the
    new scenario now, and `base_slug` only records where it came from. Later edits to the source never change the copy."""
    name = validate_name(name)
    _check_name_free(conn, name)
    src = get_row(conn, clone_from or default_slug(conn))
    by_slug = _all_rows(conn)
    params = _effective(by_slug, src["slug"])
    row = _unique_slug(conn, name, {"base_slug": src["slug"], "params": params})
    cells = _effective_cells(conn, chain_ids(conn, src["slug"]))
    with conn.cursor() as cur:
        cur.executemany("INSERT INTO proj_series (scenario_id, line_key, month, value, kind, note) VALUES (%s,%s,%s,%s,%s,%s)",
                        [(row["id"], k, m, v, kind, note) for (k, m), (v, kind, note) in cells.items()])
    _write_snapshot(conn, row["id"], 1, "clone" if clone_from else "create")
    audit.record(conn, actor=actor, scenario=row["slug"], action="scenario.clone" if clone_from else "scenario.create", version=1,
                 new={"name": name, "base_slug": src["slug"], "params": params, "series_rows": len(cells), "frozen": True})
    return _public(row)


def detach(conn, slug: str, *, expected_version: int | None = None, actor: str | None = None) -> dict:
    """Freeze a scenario that still reads its base (a clone made before copies were frozen): copy the base's params and cells it does not hold into it
    now, then stop reading the base. Nothing it holds changes; what it used to inherit becomes its own."""
    r = lock_for_change(conn, slug, expected_version)
    if r["frozen"]:
        return _public(r)
    by_slug = _all_rows(conn)
    params = _effective(by_slug, slug)
    cells = _effective_cells(conn, chain_ids(conn, slug))
    own = {(x["line_key"], x["month"]) for x in conn.execute("SELECT line_key, month FROM proj_series WHERE scenario_id = %s", (r["id"],)).fetchall()}
    missing = [(k, m, *v) for (k, m), v in cells.items() if (k, m) not in own]
    with conn.cursor() as cur:
        cur.executemany("INSERT INTO proj_series (scenario_id, line_key, month, value, kind, note) VALUES (%s,%s,%s,%s,%s,%s)",
                        [(r["id"], k, m, v, kind, note) for k, m, v, kind, note in missing])
    conn.execute("UPDATE proj_scenarios SET frozen = true, params = %s WHERE id = %s", (Jsonb(params), r["id"]))
    version = finish_change(conn, r["id"], "detach")
    audit.record(conn, actor=actor, scenario=slug, action="scenario.detach", version=version,
                 old={"params": r["params"], "base_slug": r["base_slug"]}, new={"params": params, "series_rows_copied": len(missing), "frozen": True})
    return _public(get_row(conn, slug))


def update(conn, slug: str, *, name: Any = None, params: Any = None, expected_version: int | None = None, actor: str | None = None) -> dict:
    """Rename and/or patch params (merge patch per namespace: nested objects merge, null removes a key, lists replace)."""
    if name is None and params is None:
        raise ApiError(400, "nothing to change: send name and/or params")
    new_name = validate_name(name) if name is not None else None
    patch = _check_params_patch(params) if params is not None else None
    r = lock_for_change(conn, slug, expected_version)
    sets, args, actions = [], [], []
    audits = []
    if new_name is not None and new_name != r["name"]:
        _check_name_free(conn, new_name, slug)
        sets.append("name = %s")
        args.append(new_name)
        actions.append("rename")
        audits.append(("scenario.rename", {"name": r["name"]}, {"name": new_name}))
    if patch is not None:
        merged = merge_patch(r["params"], patch)
        validate_json(merged)
        if len(json.dumps(merged)) > MAX_PARAMS_BYTES:
            raise ApiError(400, "params too large")
        if merged != r["params"]:
            from . import validators                   # late: validators reads the module registry
            validators.check_params(conn, get_scenario(conn, slug), patch, merged)
            sets.append("params = %s")
            args.append(Jsonb(merged))
            actions.append("params")
            audits.append(("scenario.params", {"params": r["params"]}, {"params": merged}))
    if not sets:
        return _public(r)                                              # nothing changed: no version bump, no audit
    conn.execute(f"UPDATE proj_scenarios SET {', '.join(sets)} WHERE id = %s", (*args, r["id"]))
    version = finish_change(conn, r["id"], "+".join(actions))
    for action, old, new in audits:
        audit.record(conn, actor=actor, scenario=slug, action=action, old=old, new=new, version=version)
    return _public(get_row(conn, slug))


def purge_archived(conn) -> int:
    """Permanently remove scenarios archived more than ARCHIVE_DAYS ago. Returns how many."""
    return conn.execute("DELETE FROM proj_scenarios p WHERE archived_at IS NOT NULL AND archived_at <= now() - make_interval(days => %s) "
                        "AND NOT EXISTS (SELECT 1 FROM proj_scenarios d WHERE d.base_slug = p.slug AND NOT d.frozen AND d.archived_at IS NULL)",   # a legacy clone still reads it
                        (ARCHIVE_DAYS,)).rowcount


def delete(conn, slug: str, confirm: Any, *, actor: str | None = None) -> None:
    """Archive the scenario for ARCHIVE_DAYS (it disappears from the list; `undelete` brings it back whole), then it is purged."""
    r = get_row(conn, slug, lock=True)
    if r["slug"] == CURRENT_SLUG or r["is_default"]:
        raise ApiError(409, "the current scenario cannot be deleted")
    if confirm != slug:
        raise ApiError(400, f'send {{"confirm": "{slug}"}} to delete this scenario')
    n = conn.execute("SELECT count(*) AS n FROM proj_series WHERE scenario_id = %s", (r["id"],)).fetchone()["n"]
    conn.execute("UPDATE proj_scenarios SET archived_at = now() WHERE id = %s", (r["id"],))
    purge_archived(conn)
    audit.record(conn, actor=actor, scenario=slug, action="scenario.delete", version=r["version"],
                 old={"name": r["name"], "base_slug": r["base_slug"], "version": r["version"], "params": r["params"], "series_rows": n},
                 new={"archived_for_days": ARCHIVE_DAYS})


def undelete(conn, slug: str, *, actor: str | None = None) -> dict:
    """Bring a deleted scenario back exactly as it was (inside the archive window). A name now taken by another scenario gets ' (restored)'."""
    r = conn.execute(f"SELECT {_COLS} FROM proj_scenarios WHERE slug = %s AND archived_at IS NOT NULL FOR UPDATE", (slug,)).fetchone()
    if r is None or r["archived_at"] is None:
        raise ApiError(404, "no deleted scenario with that slug")
    age = conn.execute("SELECT now() - %s > make_interval(days => %s) AS old", (r["archived_at"], ARCHIVE_DAYS)).fetchone()["old"]
    if age:
        raise ApiError(410, f"deleted more than {ARCHIVE_DAYS} days ago; it can no longer be restored")
    name = r["name"]
    while conn.execute("SELECT 1 FROM proj_scenarios WHERE lower(name) = lower(%s) AND archived_at IS NULL", (name,)).fetchone():
        name += " (restored)"
    conn.execute("UPDATE proj_scenarios SET archived_at = NULL, name = %s WHERE id = %s", (name, r["id"]))
    version = finish_change(conn, r["id"], "undelete")
    audit.record(conn, actor=actor, scenario=slug, action="scenario.undelete", version=version, old={"archived_at": r["archived_at"].isoformat()}, new={"name": name})
    return _public(get_row(conn, slug))


_MISSING = object()


def _leaves(d: dict, prefix: tuple = ()):
    for k, v in d.items():
        if isinstance(v, dict) and v:
            yield from _leaves(v, prefix + (k,))
        else:
            yield prefix + (k,), v


def _get_path(d: dict, path: tuple):
    for k in path:
        if not isinstance(d, dict) or k not in d:
            return _MISSING
        d = d[k]
    return d


def _set_path(d: dict, path: tuple, value: Any) -> None:
    for k in path[:-1]:
        if not isinstance(d.get(k), dict):
            d[k] = {}
        d = d[k]
    d[path[-1]] = value


def _del_path(d: dict, path: tuple) -> None:
    """Remove the key at `path` and any parent dict the removal leaves empty."""
    chain = [d]
    for k in path[:-1]:
        if not isinstance(chain[-1].get(k), dict):
            return
        chain.append(chain[-1][k])
    chain[-1].pop(path[-1], None)
    for i in range(len(path) - 1, 0, -1):
        if chain[i]:
            break
        chain[i - 1].pop(path[i - 1], None)


def _diff_paths(a: dict, b: dict, prefix: tuple = ()) -> set[tuple]:
    out: set[tuple] = set()
    for k in a.keys() | b.keys():
        va, vb = a.get(k, _MISSING), b.get(k, _MISSING)
        if isinstance(va, dict) and isinstance(vb, dict):
            out |= _diff_paths(va, vb, prefix + (k,))
        elif va != vb:
            out.add(prefix + (k,))
    return out


def restore_params(current: dict, snap: dict, changed: set[tuple]) -> dict:
    """Params after a restore: every value the snapshot holds goes back; keys changed through the audited path since then and absent from the
    snapshot are removed; anything else (seeded outside the audited path) is left as it is."""
    out = copy.deepcopy(current)
    for path, val in _leaves(snap):
        _set_path(out, path, copy.deepcopy(val))
    for path in changed:
        if _get_path(snap, path) is _MISSING:
            _del_path(out, path)
    return out


def _audited_params_changed(conn, slug: str, created_at, after_version: int) -> set[tuple]:
    rows = conn.execute("SELECT old, new FROM proj_audit WHERE scenario_slug = %s AND version > %s AND at >= %s AND action IN ('scenario.params', 'scenario.restore')",
                        (slug, after_version, created_at)).fetchall()
    paths: set[tuple] = set()
    for r in rows:
        paths |= _diff_paths((r["old"] or {}).get("params") or {}, (r["new"] or {}).get("params") or {})
    return paths


def _first_audited_series_action(conn, slug: str, created_at, after_version: int) -> dict[tuple[str, str], str]:
    """(line_key, month iso) -> the first audited action on that cell after `after_version` (series.set means the audited path created it)."""
    rows = conn.execute("SELECT DISTINCT ON (line_key, month) line_key, month, action FROM proj_audit WHERE scenario_slug = %s AND version > %s AND at >= %s "
                        "AND action IN ('series.set', 'series.update', 'series.delete') AND line_key IS NOT NULL ORDER BY line_key, month, id",
                        (slug, after_version, created_at)).fetchall()
    return {(r["line_key"], r["month"].isoformat()): r["action"] for r in rows}


def restore(conn, slug: str, version: Any, *, expected_version: int | None = None, actor: str | None = None) -> tuple[dict, dict]:
    """Put params and series cells back to what the scenario held at `version` -> (scenario, {cells_changed, kept}). Itself a new version, audited cell by cell, undoable.

    Safe by rule, not by replacement: every cell and param value in the snapshot is written back; a cell that is not in the snapshot is deleted only
    when the audited path created it after that version (its first audit row is series.set); every other cell, and every param key, that the snapshot does
    not hold is left alone (seeded or loaded outside the audited path, so it was never the user's to undo). `kept` counts those cells."""
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ApiError(400, "version must be a positive integer")
    r = lock_for_change(conn, slug, expected_version)
    snap = conn.execute("SELECT snapshot FROM proj_scenario_history WHERE scenario_id = %s AND version = %s", (r["id"], version)).fetchone()
    if snap is None:
        raise ApiError(404, "no snapshot for that version")
    state = snap["snapshot"]
    now = {(k, d): (v, kind, note) for k, d, v, kind, note in snapshot_state(conn, r["id"])["series"]}
    target = {(k, d): (v, kind, note) for k, d, v, kind, note in state["series"]}
    first = _first_audited_series_action(conn, slug, r["created_at"], version)
    doomed = [key for key in now if key not in target and first.get(key) == "series.set"]
    kept = len(now.keys() - target.keys()) - len(doomed)
    writes = [key for key in target if now.get(key) != target[key]]
    with conn.cursor() as cur:
        if doomed:
            cur.executemany("DELETE FROM proj_series WHERE scenario_id = %s AND line_key = %s AND month = %s", [(r["id"], k, date.fromisoformat(d)) for k, d in doomed])
        if writes:
            cur.executemany("INSERT INTO proj_series (scenario_id, line_key, month, value, kind, note) VALUES (%s,%s,%s,%s,%s,%s) "
                            "ON CONFLICT (scenario_id, line_key, month) DO UPDATE SET value = EXCLUDED.value, kind = EXCLUDED.kind, note = EXCLUDED.note, updated_at = now()",
                            [(r["id"], k, date.fromisoformat(d), Decimal(target[(k, d)][0]), target[(k, d)][1], target[(k, d)][2]) for k, d in writes])
    params = restore_params(r["params"], state["params"], _audited_params_changed(conn, slug, r["created_at"], version))
    if params != r["params"]:
        conn.execute("UPDATE proj_scenarios SET params = %s WHERE id = %s", (Jsonb(params), r["id"]))
    new_version = finish_change(conn, r["id"], "restore")
    stats = {"cells_changed": len(doomed) + len(writes), "kept": kept}
    who = actor or audit.current_actor()
    if r["is_default"]:                                   # the plan of record: modules that keep rows outside params and series put them back too
        from . import registry
        modules = {key: registry.run_hook(fn, conn=conn, slug=slug, to_version=version, new_version=new_version, actor=who)
                   for key, fn in registry.hooks("on_restore")}
        if modules:
            stats["modules"] = modules

    def cell(v):
        return None if v is None else {"value": float(v[0]), "kind": v[1], "note": v[2]}

    trail = [(who, slug, "series.delete", k, date.fromisoformat(d), cell(now[(k, d)]), None, f"restore to version {version}", new_version) for k, d in doomed]
    trail += [(who, slug, "series.update" if (k, d) in now else "series.set", k, date.fromisoformat(d), cell(now.get((k, d))), cell(target[(k, d)]),
               f"restore to version {version}", new_version) for k, d in writes]
    audit.record_many(conn, trail)                      # cell by cell, so a later restore knows these cells went through the audited path
    audit.record(conn, actor=actor, scenario=slug, action="scenario.restore", version=new_version,
                 old={"version": r["version"], "params": r["params"], "series_rows": len(now)},
                 new={"version": new_version, "restored_from": version, "params": params, "series_rows": len((now.keys() - set(doomed)) | set(writes)), **stats})
    return _public(get_row(conn, slug)), stats
