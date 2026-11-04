from __future__ import annotations

import pytest

from core_helpers import P, audit_rows, create, get_series, item, put, scenario


def test_list_has_seeded_current(client):
    r = client.get(f"{P}/scenarios")
    assert r.status_code == 200
    s = r.get_json()["scenarios"]
    assert [x["slug"] for x in s] == ["current"]
    assert s[0]["name"] == "Current plan" and s[0]["is_default"] is True and s[0]["version"] == 1 and s[0]["params"] == {}
    assert s[0]["effective_params"] == {}


def test_create_defaults_to_cloning_current(client):
    c = create(client, "Bull case")
    assert c["slug"] == "bull-case" and c["base_slug"] == "current" and c["is_default"] is False and c["version"] == 1
    assert create(client, "Bull  case!")["slug"] == "bull-case-2"            # slug made unique; the name collision is separate
    # same name (case-insensitive) is refused
    assert client.post(f"{P}/scenarios", json={"name": "BULL CASE"}).status_code == 409


@pytest.mark.parametrize("body,status", [({}, 400), ({"name": ""}, 400), ({"name": "  "}, 400), ({"name": 5}, 400), ({"name": "x" * 81}, 400),
                                         ({"name": "bad\x01name"}, 400), ({"name": "ok", "clone_from": 7}, 400), ({"name": "ok", "clone_from": "nope"}, 404)])
def test_create_validation(client, body, status):
    r = client.post(f"{P}/scenarios", json=body)
    assert r.status_code == status and "error" in r.get_json()


def test_clone_copies_series_and_params_and_stays_independent(client):
    client.patch(f"{P}/scenarios/current", json={"params": {"drivers": {"spend_growth": 0.02}}})
    assert put(client, "current", item(value=100), item(month="2026-12-01", value=200, kind="override", note="why")).status_code == 200
    c = create(client, "Copy", clone_from="current")
    assert c["params"] == {"drivers": {"spend_growth": 0.02}}
    got = get_series(client, "copy", "drivers.spend", detail=1)
    assert got["series"]["drivers.spend"] == {"2026-11-01": 100.0, "2026-12-01": 200.0}
    assert got["detail"]["drivers.spend"]["2026-12-01"] == {"kind": "override", "note": "why", "origin": "copy"}     # copied, so owned by the clone
    assert put(client, "copy", item(value=111)).status_code == 200
    assert get_series(client, "current", "drivers.spend")["series"]["drivers.spend"]["2026-11-01"] == 100.0       # source unchanged
    assert get_series(client, "copy", "drivers.spend")["series"]["drivers.spend"]["2026-11-01"] == 111.0
    # the clone endpoint alias
    r = client.post(f"{P}/scenarios/copy/clone", json={"name": "Copy of copy"})
    assert r.status_code == 201 and r.get_json()["scenario"]["base_slug"] == "copy"


def test_base_fallback_for_cells_the_clone_does_not_hold(client):
    put(client, "current", item(value=100))
    create(client, "Child")
    put(client, "current", item(month="2027-01-01", value=7))                   # added to the base AFTER the clone
    got = get_series(client, "child", "drivers.spend", detail=1)
    assert got["series"]["drivers.spend"]["2027-01-01"] == 7.0
    assert got["detail"]["drivers.spend"]["2027-01-01"]["origin"] == "current"
    assert got["detail"]["drivers.spend"]["2026-11-01"]["origin"] == "child"
    # deleting the clone's own cell reverts the month to the base value
    assert put(client, "child", item(value=None)).status_code == 200
    got = get_series(client, "child", "drivers.spend", detail=1)
    assert got["series"]["drivers.spend"]["2026-11-01"] == 100.0 and got["detail"]["drivers.spend"]["2026-11-01"]["origin"] == "current"


def test_fallback_chain_depth_and_cycle_safety(client, pg):
    put(client, "current", item(value=1))
    create(client, "A", "current")
    create(client, "B", "a")
    create(client, "C", "b")
    pg.execute("DELETE FROM proj_series WHERE scenario_id IN (SELECT id FROM proj_scenarios WHERE slug <> 'current')")
    got = get_series(client, "c", "drivers.spend", detail=1)
    assert got["detail"]["drivers.spend"]["2026-11-01"]["origin"] == "current"        # three levels down
    pg.execute("UPDATE proj_scenarios SET base_slug = 'c' WHERE slug = 'a'")           # a -> c -> b -> a: a cycle
    assert client.get(f"{P}/series?scenario=c&keys=drivers.spend").status_code == 200
    assert client.get(f"{P}/run?scenario=c").status_code == 200


def test_patch_params_deep_merge_per_namespace(client):
    r = client.patch(f"{P}/scenarios/current", json={"params": {"drivers": {"a": 1, "nested": {"x": 1, "y": 2}}, "pnl": {"rate": 0.5}}})
    assert r.status_code == 200 and r.get_json()["scenario"]["version"] == 2
    r = client.patch(f"{P}/scenarios/current", json={"params": {"drivers": {"nested": {"y": 3, "z": 4}, "a": None}, "cohorts": {"tail": 0.95}}})
    p = r.get_json()["scenario"]["params"]
    assert p == {"drivers": {"nested": {"x": 1, "y": 3, "z": 4}}, "pnl": {"rate": 0.5}, "cohorts": {"tail": 0.95}}
    assert scenario(client, "current")["version"] == 3
    # lists replace; null removes a whole namespace
    r = client.patch(f"{P}/scenarios/current", json={"params": {"pnl": None, "drivers": {"list": [1, 2, 3]}}})
    assert r.get_json()["scenario"]["params"] == {"drivers": {"nested": {"x": 1, "y": 3, "z": 4}, "list": [1, 2, 3]}, "cohorts": {"tail": 0.95}}


@pytest.mark.parametrize("body", [{}, {"params": []}, {"params": {"Bad Key": {}}}, {"params": {"drivers": 5}}, {"name": ""}, {"params": {"drivers": {"v": 1e999}}},
                                  {"expected_version": "x", "name": "ok"}])
def test_patch_validation(client, body):
    r = client.patch(f"{P}/scenarios/current", json=body)
    assert r.status_code == 400 and "error" in r.get_json()


def test_patch_nan_in_raw_json_is_rejected(client):
    r = client.patch(f"{P}/scenarios/current", data='{"params": {"drivers": {"v": NaN}}}', content_type="application/json")
    assert r.status_code == 400


def test_patch_unknown_scenario_and_noop_and_version_conflict(client):
    assert client.patch(f"{P}/scenarios/nope", json={"name": "x"}).status_code == 404
    v0 = scenario(client, "current")["version"]
    assert client.patch(f"{P}/scenarios/current", json={"name": "Current plan", "params": {}}).status_code == 200
    assert scenario(client, "current")["version"] == v0                          # nothing changed: no bump
    client.patch(f"{P}/scenarios/current", json={"name": "Renamed"})
    r = client.patch(f"{P}/scenarios/current", json={"name": "Again", "expected_version": v0})
    assert r.status_code == 409
    r = client.patch(f"{P}/scenarios/current", json={"name": "Again", "expected_version": v0 + 1})
    assert r.status_code == 200 and r.get_json()["scenario"]["name"] == "Again"


def test_rename_keeps_slug_and_refuses_duplicate_names(client):
    create(client, "One")
    create(client, "Two")
    r = client.patch(f"{P}/scenarios/one", json={"name": "Uno"})
    assert r.get_json()["scenario"]["slug"] == "one" and r.get_json()["scenario"]["name"] == "Uno"
    assert client.patch(f"{P}/scenarios/one", json={"name": "two"}).status_code == 409


def test_delete_rules(client, pg):
    create(client, "Temp")
    create(client, "Kid", "temp")
    assert client.delete(f"{P}/scenarios/current", json={"confirm": "current"}).status_code == 409        # current is undeletable
    assert client.delete(f"{P}/scenarios/temp").status_code == 400                                           # no body
    assert client.delete(f"{P}/scenarios/temp", json={}).status_code == 400
    assert client.delete(f"{P}/scenarios/temp", json={"confirm": "Temp"}).status_code == 400                 # wrong confirm
    assert client.delete(f"{P}/scenarios/nope", json={"confirm": "nope"}).status_code == 404
    assert scenario(client, "temp")
    r = client.delete(f"{P}/scenarios/temp", json={"confirm": "temp"})
    assert r.status_code == 200 and r.get_json() == {"deleted": "temp"}
    assert scenario(client, "kid")["base_slug"] == "current"                                                  # dependents re-pointed
    assert [s["slug"] for s in client.get(f"{P}/scenarios").get_json()["scenarios"]] == ["current", "kid"]
    rows = audit_rows(pg, "scenario.delete")
    assert len(rows) == 1 and rows[0]["scenario_slug"] == "temp" and rows[0]["old"]["name"] == "Temp"


def test_effective_params_inherit_from_base(client):
    client.patch(f"{P}/scenarios/current", json={"params": {"drivers": {"a": 1, "b": 2}}})
    create(client, "Kid")
    client.patch(f"{P}/scenarios/kid", json={"params": {"drivers": {"b": 20}}})
    client.patch(f"{P}/scenarios/current", json={"params": {"drivers": {"c": 3}}})
    kid = scenario(client, "kid")
    assert kid["params"] == {"drivers": {"a": 1, "b": 20}}
    assert kid["effective_params"] == {"drivers": {"a": 1, "b": 20, "c": 3}}
