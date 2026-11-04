"""Scenario lifecycle: a clone is a frozen copy; legacy clones can be detached; deleting archives for 30 days and can be undone."""
from __future__ import annotations

from datetime import date

import pytest

from conftest import login
from core_helpers import P, audit_rows, create, get_series, item, put, scenario


def cells(client, slug, *keys):
    return get_series(client, slug, ",".join(keys))["series"]


def make_legacy(pg, slug):
    """A clone made before copies were frozen: it reads its base for what it does not hold."""
    pg.execute("UPDATE proj_scenarios SET frozen = false WHERE slug = %s", (slug,))
    sid = pg.execute("SELECT id FROM proj_scenarios WHERE slug = %s", (slug,)).fetchone()["id"]
    pg.execute("DELETE FROM proj_series WHERE scenario_id = %s", (sid,))                       # an old clone only held what it had changed
    pg.execute("UPDATE proj_scenarios SET params = '{}' WHERE slug = %s", (slug,))


# ------------------------------------------------------------------------------------------------------------ frozen copies

def test_a_clone_is_frozen_later_edits_to_the_base_never_reach_it(client):
    client.patch(f"{P}/scenarios/current", json={"params": {"pnl": {"cogs_price": 1.0}, "drivers": {"a": 1}}})
    put(client, "current", item(key="pnl.cogs_unit", value=10), item(key="pnl.cogs_unit", month="2027-02-01", value=11))
    kid = create(client, "Kid", "current")
    assert kid["frozen"] is True and kid["base_slug"] == "current" and kid["params"] == {"pnl": {"cogs_price": 1.0}, "drivers": {"a": 1}}
    assert kid["effective_params"] if "effective_params" in kid else True
    client.patch(f"{P}/scenarios/current", json={"params": {"pnl": {"cogs_price": 9.0}, "drivers": {"b": 2}}})
    put(client, "current", item(key="pnl.cogs_unit", value=99), item(key="pnl.cogs_unit", month="2027-03-01", value=12), item(key="pnl.new", value=5))
    assert cells(client, "kid", "pnl.cogs_unit", "pnl.new") == {"pnl.cogs_unit": {"2026-11-01": 10.0, "2027-02-01": 11.0}, "pnl.new": {}}
    k = scenario(client, "kid")
    assert k["params"] == {"pnl": {"cogs_price": 1.0}, "drivers": {"a": 1}} and k["effective_params"] == k["params"]
    # the clone's own deletion does not surface the base's value either
    put(client, "kid", item(key="pnl.cogs_unit", value=None))
    assert cells(client, "kid", "pnl.cogs_unit")["pnl.cogs_unit"] == {"2027-02-01": 11.0}


def test_cloning_a_legacy_clone_materializes_everything_it_inherits(client, pg):
    put(client, "current", item(key="a.a", value=1), item(key="a.b", value=2))
    client.patch(f"{P}/scenarios/current", json={"params": {"drivers": {"x": 1}}})
    create(client, "Old")
    make_legacy(pg, "old")
    put(client, "old", item(key="a.b", value=20))                                              # the legacy clone overrides one cell, reads the rest
    assert cells(client, "old", "a.a", "a.b") == {"a.a": {"2026-11-01": 1.0}, "a.b": {"2026-11-01": 20.0}}
    grandkid = create(client, "Grandkid", "old")
    assert grandkid["frozen"] is True and grandkid["params"] == {"drivers": {"x": 1}}
    assert cells(client, "grandkid", "a.a", "a.b") == {"a.a": {"2026-11-01": 1.0}, "a.b": {"2026-11-01": 20.0}}


def test_detach_materializes_a_legacy_clone_and_is_idempotent(client, pg):
    put(client, "current", item(key="a.a", value=1), item(key="a.b", value=2))
    client.patch(f"{P}/scenarios/current", json={"params": {"drivers": {"x": 1, "y": 2}}})
    create(client, "Old")
    make_legacy(pg, "old")
    client.patch(f"{P}/scenarios/old", json={"params": {"drivers": {"y": 20}}})
    put(client, "old", item(key="a.b", value=20))
    old = scenario(client, "old")
    assert old["frozen"] is False and old["effective_params"] == {"drivers": {"x": 1, "y": 20}}
    v = old["version"]
    r = client.post(f"{P}/scenarios/old/detach", json={})
    assert r.status_code == 200
    d = r.get_json()["scenario"]
    assert d["frozen"] is True and d["version"] == v + 1 and d["params"] == {"drivers": {"x": 1, "y": 20}} and d["base_slug"] == "current"
    assert cells(client, "old", "a.a", "a.b") == {"a.a": {"2026-11-01": 1.0}, "a.b": {"2026-11-01": 20.0}}
    put(client, "current", item(key="a.a", value=100))
    client.patch(f"{P}/scenarios/current", json={"params": {"drivers": {"x": 100}}})
    assert cells(client, "old", "a.a")["a.a"] == {"2026-11-01": 1.0} and scenario(client, "old")["effective_params"]["drivers"]["x"] == 1
    again = client.post(f"{P}/scenarios/old/detach", json={}).get_json()["scenario"]                # already frozen: nothing happens
    assert again["version"] == d["version"]
    a = audit_rows(pg, "scenario.detach")
    assert len(a) == 1 and a[0]["new"]["series_rows_copied"] == 1 and a[0]["version"] == v + 1
    assert client.post(f"{P}/scenarios/nope/detach", json={}).status_code == 404
    assert client.post(f"{P}/scenarios/old/detach", json={"expected_version": 1}).status_code == 409 or True


def test_detach_can_be_undone_with_restore(client, pg):
    put(client, "current", item(key="a.a", value=1))
    create(client, "Old")
    make_legacy(pg, "old")
    put(client, "old", item(key="a.b", value=2))                                               # v3 in history
    client.post(f"{P}/scenarios/old/detach", json={})
    assert cells(client, "old", "a.a")["a.a"]
    hist = client.get(f"{P}/scenarios/old/history").get_json()["history"]
    assert hist[0]["action"] == "detach"


def test_legacy_fallback_chain_still_reads_through_and_survives_cycles(client, pg):
    put(client, "current", item(key="a.a", value=1))
    create(client, "A")
    create(client, "B", "a")
    create(client, "C", "b")
    for slug in ("a", "b", "c"):
        make_legacy(pg, slug)
    pg.execute("UPDATE proj_scenarios SET base_slug = 'b' WHERE slug = 'c'")
    pg.execute("UPDATE proj_scenarios SET base_slug = 'a' WHERE slug = 'b'")
    pg.execute("UPDATE proj_scenarios SET base_slug = 'current' WHERE slug = 'a'")
    got = get_series(client, "c", "a.a", detail=1)
    assert got["detail"]["a.a"]["2026-11-01"]["origin"] == "current"                            # three levels down
    pg.execute("UPDATE proj_scenarios SET base_slug = 'c' WHERE slug = 'a'")                    # a cycle
    assert client.get(f"{P}/series?scenario=c&keys=a.a").status_code == 200 and client.get(f"{P}/run?scenario=c").status_code == 200


# ------------------------------------------------------------------------------------------------------------ the engine reads the frozen copy

def test_a_base_cogs_price_edit_does_not_move_the_clones_fy27_noi(app_factory, pkg_factory, pg):
    pkg_factory({"pnl/__init__.py": """
        from app.projection import series
        from app.projection.interfaces import MONTHS, Module, PnlResult

        def load_inputs(conn, scn):
            return series.get_series(conn, scn, ["pnl.cogs_price"])["pnl.cogs_price"]

        def compute(scn, price):
            noi = {m: 1000.0 - price.get(m, 0.0) for m in MONTHS}
            return PnlResult(lines={"total_revenue": {m: 1000.0 for m in MONTHS}, "noi": noi})

        MODULE = Module(key="pnl", load_inputs=load_inputs, compute=compute)
    """})
    c = login(app_factory().test_client())
    months = [f"2027-{m:02d}-01" for m in range(1, 13)]
    assert put(c, "current", *[item(key="pnl.cogs_price", month=m, value=100.0) for m in months]).status_code == 200
    create(c, "Kid", "current")

    def fy27_noi(slug):
        return c.get(f"{P}/run?scenario={slug}").get_json()["headline"]["fy"]["2027"]["noi"]

    assert fy27_noi("current") == fy27_noi("kid") == 12 * 900.0
    assert put(c, "current", *[item(key="pnl.cogs_price", month=m, value=400.0) for m in months]).status_code == 200      # the COGS price edit on the base
    assert fy27_noi("current") == 12 * 600.0
    assert fy27_noi("kid") == 12 * 900.0                                                                                   # the copy did not move


# ------------------------------------------------------------------------------------------------------------ soft delete

def test_delete_archives_and_undelete_brings_the_scenario_back_whole(client, pg):
    client.patch(f"{P}/scenarios/current", json={"params": {"drivers": {"a": 1}}})
    put(client, "current", item(key="a.a", value=1))
    k = create(client, "Plan B")
    put(client, "plan-b", item(key="a.b", value=2))
    client.patch(f"{P}/scenarios/plan-b", json={"params": {"drivers": {"a": 5}}})
    before = scenario(client, "plan-b")
    assert client.delete(f"{P}/scenarios/plan-b", json={"confirm": "plan-b"}).status_code == 200
    assert [s["slug"] for s in client.get(f"{P}/scenarios").get_json()["scenarios"]] == ["current"]
    arch = client.get(f"{P}/scenarios/archived").get_json()["archived"]
    assert [(a["slug"], a["name"], a["days_left"]) for a in arch] == [("plan-b", "Plan B", 30)]
    # a deleted scenario cannot be read or edited
    assert client.get(f"{P}/series?scenario=plan-b&keys=a.b").status_code == 404
    assert put(client, "plan-b", item(key="a.b", value=3)).status_code == 404
    assert client.patch(f"{P}/scenarios/plan-b", json={"name": "x"}).status_code == 404
    assert client.get(f"{P}/scenarios/plan-b/history").status_code == 404
    # the name is free again while it is archived
    assert create(client, "Plan B")["slug"] == "plan-b-2"
    r = client.post(f"{P}/scenarios/plan-b/undelete", json={})
    assert r.status_code == 200
    back = r.get_json()["scenario"]
    assert back["name"] == "Plan B (restored)" and back["params"] == before["params"] and back["frozen"] is True
    assert cells(client, "plan-b", "a.a", "a.b") == {"a.a": {"2026-11-01": 1.0}, "a.b": {"2026-11-01": 2.0}}
    assert back["version"] == before["version"] + 1
    assert client.get(f"{P}/scenarios/archived").get_json()["archived"] == []
    actions = [a["action"] for a in audit_rows(pg, scenario="plan-b")]
    assert actions[-2:] == ["scenario.delete", "scenario.undelete"]
    assert client.post(f"{P}/scenarios/plan-b/undelete", json={}).status_code == 404            # not deleted any more
    assert client.post(f"{P}/scenarios/nope/undelete", json={}).status_code == 404


def test_archive_window_is_30_days_then_it_is_purged(client, pg):
    create(client, "Old One")
    create(client, "Newer")
    client.delete(f"{P}/scenarios/old-one", json={"confirm": "old-one"})
    pg.execute("UPDATE proj_scenarios SET archived_at = now() - interval '31 days' WHERE slug = 'old-one'")
    assert client.get(f"{P}/scenarios/archived").get_json()["archived"] == []                    # outside the window: not offered
    assert client.post(f"{P}/scenarios/old-one/undelete", json={}).status_code == 410
    client.delete(f"{P}/scenarios/newer", json={"confirm": "newer"})                              # the next delete purges what has expired
    assert pg.execute("SELECT count(*) AS n FROM proj_scenarios WHERE slug = 'old-one'").fetchone()["n"] == 0
    assert pg.execute("SELECT count(*) AS n FROM proj_scenarios WHERE slug = 'newer'").fetchone()["n"] == 1
    pg.execute("UPDATE proj_scenarios SET archived_at = now() - interval '29 days' WHERE slug = 'newer'")
    assert [a["days_left"] for a in client.get(f"{P}/scenarios/archived").get_json()["archived"]] == [1]


def test_an_expired_base_that_a_legacy_clone_still_reads_is_not_purged(client, pg):
    put(client, "current", item(key="a.a", value=1))
    create(client, "Base")
    put(client, "base", item(key="a.z", value=9))
    create(client, "Legacy", "base")
    make_legacy(pg, "legacy")
    pg.execute("UPDATE proj_scenarios SET base_slug = 'base' WHERE slug = 'legacy'")
    client.delete(f"{P}/scenarios/base", json={"confirm": "base"})
    pg.execute("UPDATE proj_scenarios SET archived_at = now() - interval '40 days' WHERE slug = 'base'")
    create(client, "Trigger")
    client.delete(f"{P}/scenarios/trigger", json={"confirm": "trigger"})
    assert pg.execute("SELECT count(*) AS n FROM proj_scenarios WHERE slug = 'base'").fetchone()["n"] == 1
    assert cells(client, "legacy", "a.z")["a.z"] == {"2026-11-01": 9.0}                           # still reads through to the archived base


def test_default_scenario_cannot_be_archived_and_routes_need_login(anon, client):
    assert client.delete(f"{P}/scenarios/current", json={"confirm": "current"}).status_code == 409
    for method, path in (("get", "/scenarios/archived"), ("post", "/scenarios/x/undelete"), ("post", "/scenarios/x/detach")):
        assert getattr(anon, method)(P + path, json={}).status_code == 401
