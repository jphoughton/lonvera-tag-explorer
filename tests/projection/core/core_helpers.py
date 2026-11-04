"""Small helpers shared by the projection core tests."""
from __future__ import annotations

P = "/api/projection"


def item(key="drivers.spend", month="2026-11-01", value=100.0, kind="input", note=None):
    d = {"line_key": key, "month": month, "value": value, "kind": kind}
    if note is not None:
        d["note"] = note
    return d


def put(client, scenario, *items, **extra):
    return client.put(f"{P}/series", json={"scenario": scenario, "items": list(items), **extra})


def get_series(client, scenario, keys, **q):
    qs = "&".join([f"scenario={scenario}", f"keys={keys}"] + [f"{k}={v}" for k, v in q.items()])
    r = client.get(f"{P}/series?{qs}")
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()


def create(client, name, clone_from=None):
    body = {"name": name}
    if clone_from:
        body["clone_from"] = clone_from
    r = client.post(f"{P}/scenarios", json=body)
    assert r.status_code == 201, r.get_data(as_text=True)
    return r.get_json()["scenario"]


def scenario(client, slug):
    return next(s for s in client.get(f"{P}/scenarios").get_json()["scenarios"] if s["slug"] == slug)


def audit_rows(pg, action=None, scenario=None):
    where, args = [], []
    if action:
        where.append("action = %s")
        args.append(action)
    if scenario:
        where.append("scenario_slug = %s")
        args.append(scenario)
    return pg.execute("SELECT * FROM proj_audit" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id", args).fetchall()
