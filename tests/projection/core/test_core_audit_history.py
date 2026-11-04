from __future__ import annotations

import pytest

from conftest import login
from core_helpers import P, audit_rows, create, get_series, item, put, scenario


def test_every_mutation_writes_an_audit_row_with_old_and_new(client, pg):
    create(client, "Alpha")                                                                       # scenario.create
    create(client, "Beta", "alpha")                                                               # scenario.clone
    put(client, "alpha", item(value=1, note="n1"))                                                # series.set
    put(client, "alpha", item(value=2, note="n1"))                                                # series.update
    put(client, "alpha", item(value=None))                                                        # series.delete
    client.patch(f"{P}/scenarios/alpha", json={"name": "Alpha 2"})                                # scenario.rename
    client.patch(f"{P}/scenarios/alpha", json={"params": {"drivers": {"k": 1}}})                  # scenario.params
    client.post(f"{P}/scenarios/alpha/restore", json={"version": 1})                              # scenario.restore
    client.delete(f"{P}/scenarios/beta", json={"confirm": "beta"})                                # scenario.delete
    actions = [r["action"] for r in audit_rows(pg)]
    assert actions == ["scenario.create", "scenario.clone", "series.set", "series.update", "series.delete", "scenario.rename", "scenario.params",
                       "scenario.restore", "scenario.delete"]
    by = {r["action"]: r for r in audit_rows(pg)}
    assert by["series.set"]["old"] is None and by["series.set"]["new"] == {"value": 1.0, "kind": "input", "note": "n1"}
    assert by["series.update"]["old"]["value"] == 1.0 and by["series.update"]["new"]["value"] == 2.0
    assert by["series.delete"]["old"]["value"] == 2.0 and by["series.delete"]["new"] is None
    assert str(by["series.set"]["month"]) == "2026-11-01" and by["series.set"]["line_key"] == "drivers.spend" and by["series.set"]["scenario_slug"] == "alpha"
    assert by["scenario.rename"]["old"] == {"name": "Alpha"} and by["scenario.rename"]["new"] == {"name": "Alpha 2"}
    assert by["scenario.params"]["old"] == {"params": {}} and by["scenario.params"]["new"] == {"params": {"drivers": {"k": 1}}}
    assert by["scenario.restore"]["new"]["restored_from"] == 1 and by["scenario.restore"]["old"]["params"] == {"drivers": {"k": 1}}
    assert by["scenario.clone"]["new"]["base_slug"] == "alpha"
    assert all(r["actor"] == "JoshHoughton" for r in audit_rows(pg))                # the named session user
    assert all(r["at"] is not None for r in audit_rows(pg))


def test_one_audit_row_per_changed_cell_none_for_rejected_requests(client, pg):
    put(client, "current", item(key="a.a"), item(key="a.b"), item(key="a.c"))
    assert len(audit_rows(pg, "series.set")) == 3
    put(client, "current", item(key="a.a"), item(month="2026-15-01"))                              # rejected: nothing written, nothing audited
    put(client, "current", item(key="a.a"), item(key="a.b"))                                       # unchanged: nothing audited
    client.delete(f"{P}/scenarios/current", json={"confirm": "current"})
    client.patch(f"{P}/scenarios/nope", json={"name": "x"})
    assert len(audit_rows(pg)) == 3


def test_audit_endpoint_filters_and_limits(client):
    create(client, "One")
    create(client, "Two")
    put(client, "one", item(key="x.a"), item(key="x.b"))
    put(client, "two", item(key="x.a"))
    r = client.get(f"{P}/audit?limit=2").get_json()["audit"]
    assert len(r) == 2 and r[0]["id"] > r[1]["id"]                                                   # newest first
    ones = client.get(f"{P}/audit?scenario=one").get_json()["audit"]
    assert {a["scenario_slug"] for a in ones} == {"one"} and len(ones) == 3
    assert [a["line_key"] for a in client.get(f"{P}/audit?line_key=x.b").get_json()["audit"]] == ["x.b"]
    assert client.get(f"{P}/audit?limit=abc").status_code == 400 and client.get(f"{P}/audit?limit=0").status_code == 400
    assert len(client.get(f"{P}/audit?limit=99999").get_json()["audit"]) == 5                       # capped, not an error


def test_actor_is_the_named_session_user_and_anonymous_when_the_app_is_open(app_factory, pg):
    a = app_factory()
    jc, lc = login(a.test_client(), "JoshHoughton"), login(a.test_client(), "Lazer")
    create(jc, "ByJosh")
    create(lc, "ByLazer")
    open_client = app_factory(AUTH_DISABLED=True).test_client()                                     # no login at all; auth reports the user as 'guest'
    create(open_client, "ByNobody")
    assert [r["actor"] for r in audit_rows(pg, "scenario.create")] == ["JoshHoughton", "Lazer", "anonymous"]


def test_actor_outside_a_request_is_system():
    from app.projection import audit
    assert audit.current_actor() == "system"


# ------------------------------------------------------------------------------------------------------------ history and restore

def test_restore_round_trip_and_undo(client):
    create(client, "Plan")                                                      # v1
    put(client, "plan", item(value=10), item(month="2026-12-01", value=20))     # v2
    client.patch(f"{P}/scenarios/plan", json={"params": {"drivers": {"g": 0.1}}})   # v3
    put(client, "plan", item(value=11), item(key="other.k", value=5))           # v4
    assert scenario(client, "plan")["version"] == 4
    hist = client.get(f"{P}/scenarios/plan/history").get_json()["history"]
    assert [h["version"] for h in hist] == [4, 3, 2, 1] and hist[0]["action"] == "series" and hist[1]["action"] == "params" and hist[3]["action"] == "create"

    r = client.post(f"{P}/scenarios/plan/restore", json={"version": 2})
    assert r.status_code == 200 and r.get_json()["scenario"]["version"] == 5
    assert r.get_json()["scenario"]["params"] == {}                              # v2 predates the params patch
    s = get_series(client, "plan", "drivers.spend,other.k")["series"]
    assert s == {"drivers.spend": {"2026-11-01": 10.0, "2026-12-01": 20.0}, "other.k": {}}
    assert client.get(f"{P}/scenarios/plan/history").get_json()["history"][0]["action"] == "restore"

    r = client.post(f"{P}/scenarios/plan/restore", json={"version": 4})          # undo the restore
    assert r.get_json()["scenario"]["version"] == 6 and r.get_json()["scenario"]["params"] == {"drivers": {"g": 0.1}}
    s = get_series(client, "plan", "drivers.spend,other.k")["series"]
    assert s == {"drivers.spend": {"2026-11-01": 11.0, "2026-12-01": 20.0}, "other.k": {"2026-11-01": 5.0}}


def test_restore_of_a_scenario_seeded_outside_the_api_keeps_the_seed(client, pg):
    """Loaders may write proj_series directly: the state before the first API change is captured as the baseline."""
    sid = pg.execute("SELECT id FROM proj_scenarios WHERE slug = 'current'").fetchone()["id"]
    pg.execute("INSERT INTO proj_series (scenario_id, line_key, month, value, kind, note) VALUES (%s,'seed.k','2026-11-01',42,'input','seed')", (sid,))
    put(client, "current", item(key="seed.k", value=43))                         # first API change: baseline v1 holds the seed
    put(client, "current", item(key="seed.k", value=44))
    assert client.post(f"{P}/scenarios/current/restore", json={"version": 1}).status_code == 200
    assert get_series(client, "current", "seed.k")["series"]["seed.k"] == {"2026-11-01": 42.0}


@pytest.mark.parametrize("body,status", [({}, 400), ({"version": "2"}, 400), ({"version": 0}, 400), ({"version": True}, 400), ({"version": 99}, 404)])
def test_restore_validation(client, body, status):
    assert client.post(f"{P}/scenarios/current/restore", json=body).status_code == status


def test_restore_unknown_scenario_and_conflict(client):
    assert client.post(f"{P}/scenarios/nope/restore", json={"version": 1}).status_code == 404
    put(client, "current", item())
    assert client.post(f"{P}/scenarios/current/restore", json={"version": 1, "expected_version": 1}).status_code == 409


def test_history_keeps_only_the_last_n_versions(client, monkeypatch):
    from app.projection import scenarios
    monkeypatch.setattr(scenarios, "HISTORY_KEEP", 3)
    for i in range(6):
        put(client, "current", item(value=i + 1))
    hist = client.get(f"{P}/scenarios/current/history?limit=50").get_json()["history"]
    assert [h["version"] for h in hist] == [7, 6, 5]
    assert client.post(f"{P}/scenarios/current/restore", json={"version": 2}).status_code == 404         # pruned
    assert client.post(f"{P}/scenarios/current/restore", json={"version": 5}).status_code == 200
    assert client.get(f"{P}/scenarios/nope/history").status_code == 404
    assert client.get(f"{P}/scenarios/current/history?limit=x").status_code == 400


def test_history_is_deleted_with_the_scenario_but_the_audit_trail_stays(client, pg):
    create(client, "Gone")
    put(client, "gone", item())
    client.delete(f"{P}/scenarios/gone", json={"confirm": "gone"})
    assert pg.execute("SELECT count(*) AS n FROM proj_scenario_history h LEFT JOIN proj_scenarios s ON s.id = h.scenario_id WHERE s.id IS NULL").fetchone()["n"] == 0
    assert len(audit_rows(pg, scenario="gone")) == 3


def test_audit_rows_carry_the_version_they_produced_and_it_is_restorable(client):
    create(client, "Hist")                                                     # v1
    put(client, "hist", item(value=1))                                         # v2
    client.patch(f"{P}/scenarios/hist", json={"name": "Hist 2", "params": {"drivers": {"a": 1}}})     # v3 (two audit rows, one version)
    put(client, "hist", item(value=2))                                         # v4
    rows = client.get(f"{P}/audit?scenario=hist").get_json()["audit"]
    assert [(r["action"], r["version"]) for r in reversed(rows)] == [
        ("scenario.create", 1), ("series.set", 2), ("scenario.rename", 3), ("scenario.params", 3), ("series.update", 4)]
    restore_to = next(r["version"] for r in rows if r["action"] == "series.set")
    assert client.post(f"{P}/scenarios/hist/restore", json={"version": restore_to}).status_code == 200
    assert get_series(client, "hist", "drivers.spend")["series"]["drivers.spend"] == {"2026-11-01": 1.0}
    assert client.get(f"{P}/audit?scenario=hist&limit=1").get_json()["audit"][0]["version"] == 5            # the restore itself
