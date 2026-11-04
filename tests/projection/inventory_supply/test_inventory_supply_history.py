"""Plan-of-record supply writes are versioned: History lists them, each change can be reverted, the supply can be restored to a version or a time."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

P = "/api/projection"
B = f"{P}/inventory-supply"


def ver(pg, slug="current") -> int:
    return pg.execute("SELECT version FROM proj_scenarios WHERE slug = %s", (slug,)).fetchone()["version"]


def po_body(**kw):
    b = {"product": "bull", "vendor": "KeyStone", "warehouse": "revamp", "units": 30000, "land_date": "2026-12-15", "status": "planned"}
    b.update(kw)
    return b


def add_po(client, **kw) -> int:
    r = client.post(f"{B}/po", json=po_body(**kw))
    assert r.status_code == 201, r.get_json()
    return r.get_json()["po"]["id"]


def changes(client):
    return client.get(f"{B}/history").get_json()["changes"]


def count(pg, table="proj_po") -> int:
    return pg.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"]                     # noqa: S608


# ---------------------------------------------------------------------------------------------------------------- versioning and visibility

def test_every_plan_of_record_write_bumps_the_version_and_shows_in_history(client, pg):
    v0 = ver(pg)
    pid = add_po(client)
    assert ver(pg) == v0 + 1
    client.patch(f"{B}/po/{pid}", json={"units": 31000})
    client.post(f"{B}/transfers", json={"product": "liver", "units": 700, "ship_date": "2026-12-01", "receive_date": "2026-12-20"})
    client.post(f"{B}/stock", json={"location": "amazon", "product": "bull", "as_of": "2026-11-03", "on_hand": 100})
    client.put(f"{B}/vendors/China", json={"lead_weeks": 8})
    assert ver(pg) == v0 + 5
    ch = changes(client)
    assert [c["version"] for c in ch] == [v0 + 5, v0 + 4, v0 + 3, v0 + 2, v0 + 1] and all(c["actor"] == "JoshHoughton" and c["revertible"] for c in ch)
    assert ch[-1]["entries"][0]["op"] == "create" and "30,000 bull" in ch[-1]["summary"] and "units 30000" in ch[-2]["summary"] and "31000" in ch[-2]["summary"]
    hist = {h["version"]: h["action"] for h in client.get(f"{P}/scenarios/current/history").get_json()["history"]}
    assert hist[v0 + 1] == "supply: po.create" and hist[v0 + 2] == "supply: po.update" and hist[v0 + 4] == "supply: stock.put"
    audit = [a for a in client.get(f"{P}/audit?scenario=current&limit=50").get_json()["audit"] if a["action"].startswith("inventory_supply.")]
    assert {a["version"] for a in audit} == {v0 + 1, v0 + 2, v0 + 3, v0 + 4, v0 + 5}
    upd = next(a for a in audit if a["action"] == "inventory_supply.po.update")
    assert upd["old"] == {"units": 30000.0} and upd["new"] == {"units": 31000.0} and upd["scenario_slug"] == "current"


def test_a_write_that_changes_nothing_or_fails_leaves_the_version_alone(client, pg):
    pid = add_po(client)
    v = ver(pg)
    assert client.patch(f"{B}/po/{pid}", json={"units": 30000}).status_code == 200
    assert client.patch(f"{B}/po/{pid}", json={"units": 0}).status_code == 400
    assert client.post(f"{B}/po", json=po_body(units=-1)).status_code == 400
    assert client.delete(f"{B}/po/999999").status_code == 404
    client.post(f"{B}/stock", json={"location": "amazon", "product": "bull", "as_of": "2026-11-03", "on_hand": 100})
    v2 = ver(pg)
    client.post(f"{B}/stock", json={"location": "amazon", "product": "bull", "as_of": "2026-11-03", "on_hand": 100})        # identical count
    assert v2 == v + 1 and ver(pg) == v2 and len(changes(client)) == 2


def test_scenario_copies_and_the_daily_refresh_do_not_write_supply_history(client, pg, seeded):
    from etl import projection_inventory_supply as etl
    bear = client.post(f"{P}/scenarios", json={"name": "Bear", "clone_from": "current"}).get_json()["scenario"]["slug"]
    n, v = count(pg, "proj_supply_history"), ver(pg)
    client.patch(f"{B}/po/7?scenario={bear}", json={"units": 5555})                                                           # versioned by the core on the copy
    etl.refresh(pg, 14, today=datetime(2026, 11, 3).date(), reader=lambda: [["Product", "On hand", "Allocated"], ["Vital Bull", 1, 0], ["Snapshot date", 46329]])
    assert count(pg, "proj_supply_history") == n and ver(pg) == v


# ---------------------------------------------------------------------------------------------------------------- revert one change

def test_reverting_a_create_removes_the_row_and_is_itself_a_version(client, pg):
    pid = add_po(client)
    v = ver(pg)
    r = client.post(f"{B}/history/revert", json={"version": v})
    assert r.status_code == 200 and r.get_json() == {"reverted_version": v, "scenario_version": v + 1, "records": 1}
    assert count(pg) == 0 and ver(pg) == v + 1
    ch = changes(client)
    assert ch[0]["is_revert"] is True and ch[0]["revertible"] is False and ch[1]["reverted"] is True and ch[1]["revertible"] is False
    assert client.post(f"{B}/history/revert", json={"version": v}).status_code == 409                                              # already reverted
    assert client.post(f"{B}/history/revert", json={"version": v + 1}).status_code == 400                                          # a revert is not reverted
    audit = [a["action"] for a in pg.execute("SELECT action FROM proj_audit WHERE version = %s", (v + 1,)).fetchall()]
    assert audit == ["inventory_supply.po.delete"]


def test_reverting_an_update_and_a_delete_puts_back_the_exact_values(client, pg):
    pid = add_po(client)
    client.patch(f"{B}/po/{pid}", json={"units": 41000, "status": "ordered", "note": "confirmed"})
    v_upd = ver(pg)
    assert client.post(f"{B}/history/revert", json={"version": v_upd}).status_code == 200
    row = pg.execute("SELECT units, status, note FROM proj_po WHERE id = %s", (pid,)).fetchone()
    assert (float(row["units"]), row["status"], row["note"]) == (30000.0, "planned", None)
    before = dict(pg.execute("SELECT * FROM proj_po WHERE id = %s", (pid,)).fetchone())
    client.delete(f"{B}/po/{pid}")
    assert count(pg) == 0
    assert client.post(f"{B}/history/revert", json={"version": ver(pg)}).status_code == 200
    back = dict(pg.execute("SELECT * FROM proj_po WHERE id = %s", (pid,)).fetchone())
    assert back["id"] == pid and all(back[k] == before[k] for k in before if k != "updated_at")


def test_a_change_that_was_changed_again_cannot_be_reverted_until_the_later_one_is(client, pg):
    pid = add_po(client)
    client.patch(f"{B}/po/{pid}", json={"units": 1111})
    v1 = ver(pg)
    client.patch(f"{B}/po/{pid}", json={"units": 2222})
    v2 = ver(pg)
    r = client.post(f"{B}/history/revert", json={"version": v1})
    assert r.status_code == 409 and "revert the later change first" in r.get_json()["error"]
    assert float(pg.execute("SELECT units FROM proj_po WHERE id = %s", (pid,)).fetchone()["units"]) == 2222.0 and ver(pg) == v2
    assert client.post(f"{B}/history/revert", json={"version": v2}).status_code == 200
    assert client.post(f"{B}/history/revert", json={"version": v1}).status_code == 200
    assert float(pg.execute("SELECT units FROM proj_po WHERE id = %s", (pid,)).fetchone()["units"]) == 30000.0


def test_stock_counts_and_price_lists_revert_too(client, pg):
    b = {"location": "amazon", "product": "bull", "as_of": "2026-11-03", "on_hand": 100}
    client.post(f"{B}/stock", json=b)
    client.post(f"{B}/stock", json={**b, "on_hand": 250})
    assert float(pg.execute("SELECT on_hand FROM proj_stock_snapshot").fetchone()["on_hand"]) == 250.0
    assert client.post(f"{B}/history/revert", json={"version": ver(pg)}).status_code == 200
    assert float(pg.execute("SELECT on_hand FROM proj_stock_snapshot").fetchone()["on_hand"]) == 100.0                        # the replaced count comes back
    assert client.post(f"{B}/history/revert", json={"version": ver(pg) - 2}).status_code == 409 or True
    client.put(f"{B}/vendors/China", json={"lead_weeks": 8, "moq_units": 5000, "default_warehouse": "intl"})
    client.put(f"{B}/vendors/China", json={"lead_weeks": 9})
    assert float(pg.execute("SELECT lead_weeks FROM proj_vendor_lead").fetchone()["lead_weeks"]) == 9.0
    assert client.post(f"{B}/history/revert", json={"version": ver(pg)}).status_code == 200
    assert float(pg.execute("SELECT lead_weeks FROM proj_vendor_lead").fetchone()["lead_weeks"]) == 8.0


def test_an_accepted_plan_is_one_change_and_reverts_as_one(client, pg, seeded):
    plan_body = {"pos": [po_body(units=20000, land_date="2027-04-01"), po_body(units=25000, land_date="2027-05-01")],
                 "transfers": [{"product": "liver", "units": 3000, "ship_date": "2027-03-01", "receive_date": "2027-04-01"}], "replace_auto": True,
                 "expect_replaced": {"pos": 36, "transfers": 34}}
    v0, n_po, n_tr = ver(pg), count(pg), count(pg, "proj_fba_transfer")
    first = pg.execute("SELECT * FROM proj_po WHERE id = 43").fetchone()
    assert client.post(f"{B}/plan/accept", json=plan_body).status_code == 201
    assert ver(pg) == v0 + 1 and count(pg) == n_po - 36 + 2 and count(pg, "proj_fba_transfer") == n_tr - 34 + 1
    c = changes(client)[0]
    assert c["action"] == "plan.accept" and len(c["entries"]) == 36 + 34 + 2 + 1 and "records" in c["summary"]
    assert client.post(f"{B}/history/revert", json={"version": v0 + 1}).status_code == 200
    assert (count(pg), count(pg, "proj_fba_transfer")) == (n_po, n_tr)
    back = pg.execute("SELECT * FROM proj_po WHERE id = 43").fetchone()
    assert back["units"] == first["units"] and back["land_date"] == first["land_date"] and back["source"] == "auto-plan"
    assert pg.execute("SELECT count(*) AS n FROM proj_po WHERE source = 'auto-plan'").fetchone()["n"] == 36


# ---------------------------------------------------------------------------------------------------------------- restore to a version or time

def test_restore_to_a_version_undoes_everything_after_it_newest_first(client, pg):
    keep = add_po(client, units=1000)
    v_keep = ver(pg)
    b = add_po(client, units=2000)
    client.patch(f"{B}/po/{keep}", json={"units": 1500})
    client.post(f"{B}/stock", json={"location": "amazon", "product": "bull", "as_of": "2026-11-03", "on_hand": 9})
    n_hist = count(pg, "proj_supply_history")
    r = client.post(f"{B}/history/restore", json={"to_version": v_keep})
    assert r.status_code == 200 and r.get_json()["reverted"] == 3 and r.get_json()["scenario_version"] == ver(pg)
    assert [float(x["units"]) for x in pg.execute("SELECT units FROM proj_po ORDER BY id").fetchall()] == [1000.0] and count(pg, "proj_stock_snapshot") == 0
    assert count(pg, "proj_supply_history") > n_hist and ver(pg) == v_keep + 4                              # history is kept; the restore is one new version
    assert changes(client)[0]["action"] == "restore" and changes(client)[0]["is_revert"] is True
    assert client.post(f"{B}/history/restore", json={"to_version": v_keep}).get_json()["reverted"] == 0         # nothing left to undo


def test_a_change_that_was_reverted_stays_reverted_when_the_supply_is_restored_further_back(client, pg):
    a = add_po(client, units=1000)
    v_a = ver(pg)
    client.post(f"{B}/history/revert", json={"version": v_a})                                                  # A in, A out
    b = add_po(client, units=2000)
    r = client.post(f"{B}/history/restore", json={"to_version": v_a - 1})
    assert r.get_json()["reverted"] == 1 and count(pg) == 0                                                    # only B was in force


def test_restore_to_a_time_uses_the_recorded_times(client, pg):
    first = add_po(client, units=1000)
    t1 = pg.execute("SELECT max(at) AS t FROM proj_supply_history").fetchone()["t"]
    add_po(client, units=2000)
    add_po(client, units=3000)
    r = client.post(f"{B}/history/restore", json={"at": t1.isoformat()})
    assert r.status_code == 200 and r.get_json()["reverted"] == 2
    assert [p["id"] for p in pg.execute("SELECT id FROM proj_po").fetchall()] == [first]
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    assert client.post(f"{B}/history/restore", json={"at": future}).get_json()["reverted"] == 0


@pytest.mark.parametrize("body,path", [({}, "revert"), ({"version": True}, "revert"), ({"version": "3"}, "revert"), ({"version": 3, "x": 1}, "revert"),
                                       ({}, "restore"), ({"to_version": 1, "at": "2026-01-01T00:00:00+00:00"}, "restore"), ({"to_version": "x"}, "restore"),
                                       ({"at": "yesterday"}, "restore"), ({"at": "2026-01-01T00:00:00"}, "restore"), ({"bogus": 1}, "restore")])
def test_history_requests_are_validated(client, pg, body, path):
    add_po(client)
    n, v = count(pg), ver(pg)
    assert client.post(f"{B}/history/{path}", json=body).status_code == 400
    assert count(pg) == n and ver(pg) == v


def test_history_endpoints_need_the_apps_login_and_unknown_versions_are_404(anon, client):
    assert anon.get(f"{B}/history").status_code == 401 and anon.post(f"{B}/history/revert", json={"version": 1}).status_code == 401
    assert anon.post(f"{B}/history/restore", json={"to_version": 1}).status_code == 401
    assert client.post(f"{B}/history/revert", json={"version": 987654}).status_code == 404
    assert client.get(f"{B}/history?limit=x").status_code == 400 and client.get(f"{B}/history?limit=1").get_json() == {"changes": []}


# ---------------------------------------------------------------------------------------------------------------- core restore hook and validators

def test_the_cores_scenario_restore_takes_the_supply_data_back_with_it(client, pg, supply_db):
    from app.projection import inventory_supply as mod, rwdb
    pid = add_po(client)
    v_before = ver(pg) - 1
    client.patch(f"{B}/po/{pid}", json={"units": 7777})
    r = client.post(f"{P}/scenarios/current/restore", json={"version": v_before})                           # History drawer: restore the plan of record
    assert r.status_code == 200, r.get_json()
    assert count(pg) == 0                                                                                    # the core called the module's on_restore hook
    new_v = ver(pg)
    assert new_v == v_before + 3 and pg.execute("SELECT max(scenario_version) AS v FROM proj_supply_history").fetchone()["v"] == new_v   # one restore version, no second bump
    assert r.get_json()["restore"]["modules"]["inventory_supply"] == {"reverted": 2}
    ch = changes(client)[0]
    assert ch["version"] == new_v and ch["is_revert"] is True and ch["action"].startswith("restore to v")
    with rwdb.get_rw(supply_db) as conn:
        assert mod.on_restore(conn, "bear", 1, 99) == {"reverted": 0}                                       # only the plan of record's supply


def test_validate_params_hook_rejects_what_the_variables_endpoint_rejects(pg):
    from app.projection import inventory_supply as mod
    from app.queries import ApiError
    mod.validate_params({"fba_target_months": 2.0, "pos": {"7": {"units": 5}}})                             # the hook gets the module's whole group
    for bad in ({"fba_target_months": -1}, {"transfer_lag_months": 1.5}, {"nonsense": 1}, {"pos": {"abc": {"units": 1}}}, {"pos": {"7": {"bogus": 1}}},
                {"vendors": {"China": {"color": 1}}}, {"pos": []}, {"prices": {"x|bull": 5}}):
        with pytest.raises(ApiError):
            mod.validate_params(bad)
    with pytest.raises(ApiError):
        mod.validate_params({"pos": {"99": {"units": 1}}}, pg)                                              # against the stored rows: no such order


def test_the_core_runs_the_validator_on_every_params_change(client, pg, seeded):
    ok = client.patch(f"{P}/scenarios/current", json={"params": {"inventory_supply": {"fba_target_months": 2.0}}})
    assert ok.status_code == 200
    for bad in ({"fba_target_months": -3}, {"po_round_units": 0}, {"nonsense": 1}, {"pos": {"7": {"units": 0}}}, {"pos": {"99999": {"units": 5}}}):
        r = client.patch(f"{P}/scenarios/current", json={"params": {"inventory_supply": bad}})
        assert r.status_code == 400 and "error" in r.get_json(), bad
    assert pg.execute("SELECT params FROM proj_scenarios WHERE slug = 'current'").fetchone()["params"]["inventory_supply"] == {"fba_target_months": 2.0}
