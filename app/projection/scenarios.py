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

_COLS = "id, slug, name, base_slug, is_default, params, version, created_at, updated_at"
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
    """[slug, its base, the base's base, ...] (cycle- and depth-safe)."""
    out: list[str] = []
    cur: str | None = slug
    while cur and cur in by_slug and cur not in out and len(out) < MAX_CHAIN:
        out.append(cur)
        cur = by_slug[cur]["base_slug"]
    return out


def _effective(by_slug: dict[str, dict], slug: str) -> dict:
    params: dict = {}
    for s in reversed(_chain_slugs(by_slug, slug)):                 # root first, the scenario last
        params = _deep_merge(params, by_slug[s]["params"])
    return params


def _public(r: dict, effective: dict | None = None) -> dict:
    d = {k: r[k] for k in ("id", "slug", "name", "base_slug", "is_default", "params", "version", "created_at", "updated_at")}
    if effective is not None:
        d["effective_params"] = effective
    return d


def list_scenarios(conn) -> list[dict]:
    by_slug = _all_rows(conn)
    return [_public(r, _effective(by_slug, s)) for s, r in by_slug.items()]


def get_row(conn, slug: str, *, lock: bool = False) -> dict:
    r = conn.execute(f"SELECT {_COLS} FROM proj_scenarios WHERE slug = %s" + (" FOR UPDATE" if lock else ""), (slug,)).fetchone()
    if r is None:
        raise ApiError(404, "scenario not found")
    return r


def default_slug(conn) -> str:
    r = conn.execute("SELECT slug FROM proj_scenarios WHERE is_default").fetchone()
    return r["slug"] if r else CURRENT_SLUG


def get_scenario(conn, slug: str | None = None, *, effective: bool = True) -> Scenario:
    """Scenario by slug (None = the default one). `params` is the effective view (base chain merged under the scenario's own) unless effective=False."""
    by_slug = _all_rows(conn)
    if slug is None:
        slug = next((s for s, r in by_slug.items() if r["is_default"]), CURRENT_SLUG)
    r = by_slug.get(slug)
    if r is None:
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


def _write_snapshot(conn, scenario_id: int, version: int, action: str) -> None:
    conn.execute("INSERT INTO proj_scenario_history (scenario_id, version, snapshot, action) VALUES (%s,%s,%s,%s) "
                 "ON CONFLICT (scenario_id, version) DO UPDATE SET snapshot = EXCLUDED.snapshot, action = EXCLUDED.action, at = now()",
                 (scenario_id, version, Jsonb(snapshot_state(conn, scenario_id)), action))
    conn.execute("DELETE FROM proj_scenario_history WHERE scenario_id = %s AND version NOT IN "
                 "(SELECT version FROM proj_scenario_history WHERE scenario_id = %s ORDER BY version DESC LIMIT %s)",
                 (scenario_id, scenario_id, HISTORY_KEEP))


def lock_for_change(conn, slug: str, expected_version: int | None = None) -> dict:
    """Lock the scenario row, check the caller's version, and make sure the state BEFORE the change is in history (so it can be restored)."""
    r = get_row(conn, slug, lock=True)
    if expected_version is not None and expected_version != r["version"]:
        raise ApiError(409, f"version conflict: the scenario is at version {r['version']}")
    have = conn.execute("SELECT 1 FROM proj_scenario_history WHERE scenario_id = %s AND version = %s", (r["id"], r["version"])).fetchone()
    if not have:
        _write_snapshot(conn, r["id"], r["version"], "baseline")
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
            "INSERT INTO proj_scenarios (slug, name, base_slug, is_default, params) VALUES (%s,%s,%s,false,%s) "
            "ON CONFLICT (slug) DO NOTHING RETURNING " + _COLS,
            (slug, name, scenario_cols["base_slug"], Jsonb(scenario_cols["params"]))).fetchone()
        if row:
            return row
    raise ApiError(409, "could not find a free scenario slug")


def _check_name_free(conn, name: str, except_slug: str | None = None) -> None:
    dup = conn.execute("SELECT 1 FROM proj_scenarios WHERE lower(name) = lower(%s) AND slug IS DISTINCT FROM %s", (name, except_slug)).fetchone()
    if dup:
        raise ApiError(409, "a scenario with that name already exists")


def create(conn, name: Any, clone_from: str | None = None, *, actor: str | None = None) -> dict:
    """New scenario = copy of `clone_from` (default: the default scenario): params and own series copied, base_slug = the source."""
    name = validate_name(name)
    _check_name_free(conn, name)
    src = get_row(conn, clone_from or default_slug(conn))
    row = _unique_slug(conn, name, {"base_slug": src["slug"], "params": src["params"]})
    n = conn.execute("INSERT INTO proj_series (scenario_id, line_key, month, value, kind, note) "
                     "SELECT %s, line_key, month, value, kind, note FROM proj_series WHERE scenario_id = %s", (row["id"], src["id"])).rowcount
    _write_snapshot(conn, row["id"], 1, "clone" if clone_from else "create")
    audit.record(conn, actor=actor, scenario=row["slug"], action="scenario.clone" if clone_from else "scenario.create", version=1,
                 new={"name": name, "base_slug": src["slug"], "params": src["params"], "series_rows": n})
    return _public(row)


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


def delete(conn, slug: str, confirm: Any, *, actor: str | None = None) -> None:
    r = get_row(conn, slug, lock=True)
    if r["slug"] == CURRENT_SLUG or r["is_default"]:
        raise ApiError(409, "the current scenario cannot be deleted")
    if confirm != slug:
        raise ApiError(400, f'send {{"confirm": "{slug}"}} to delete this scenario')
    n = conn.execute("SELECT count(*) AS n FROM proj_series WHERE scenario_id = %s", (r["id"],)).fetchone()["n"]
    conn.execute("UPDATE proj_scenarios SET base_slug = %s, version = version + 1, updated_at = now() WHERE base_slug = %s",
                 (r["base_slug"], slug))                               # dependents keep a valid fallback chain
    conn.execute("DELETE FROM proj_scenarios WHERE id = %s", (r["id"],))
    audit.record(conn, actor=actor, scenario=slug, action="scenario.delete", version=r["version"],
                 old={"name": r["name"], "base_slug": r["base_slug"], "version": r["version"], "params": r["params"], "series_rows": n})


def restore(conn, slug: str, version: Any, *, expected_version: int | None = None, actor: str | None = None) -> dict:
    """Put params and every series cell back to what the scenario held at `version`. Itself a new version, audited, and undoable."""
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ApiError(400, "version must be a positive integer")
    r = lock_for_change(conn, slug, expected_version)
    snap = conn.execute("SELECT snapshot FROM proj_scenario_history WHERE scenario_id = %s AND version = %s", (r["id"], version)).fetchone()
    if snap is None:
        raise ApiError(404, "no snapshot for that version")
    state = snap["snapshot"]
    before = {(k, d): (v, kind, note) for k, d, v, kind, note in snapshot_state(conn, r["id"])["series"]}
    after = {(k, d): (v, kind, note) for k, d, v, kind, note in state["series"]}
    changed = sum(1 for key in before.keys() | after.keys() if before.get(key) != after.get(key))
    conn.execute("DELETE FROM proj_series WHERE scenario_id = %s", (r["id"],))
    if state["series"]:
        with conn.cursor() as cur:
            cur.executemany("INSERT INTO proj_series (scenario_id, line_key, month, value, kind, note) VALUES (%s,%s,%s,%s,%s,%s)",
                            [(r["id"], k, date.fromisoformat(d), Decimal(v), kind, note) for k, d, v, kind, note in state["series"]])
    conn.execute("UPDATE proj_scenarios SET params = %s WHERE id = %s", (Jsonb(state["params"]), r["id"]))
    new_version = finish_change(conn, r["id"], "restore")
    audit.record(conn, actor=actor, scenario=slug, action="scenario.restore", version=new_version,
                 old={"version": r["version"], "params": r["params"], "series_rows": len(before)},
                 new={"version": new_version, "restored_from": version, "params": state["params"], "series_rows": len(after), "cells_changed": changed})
    return _public(get_row(conn, slug))
