"""Undo of a Spend plan change (pattern fill, NC-ROAS slide) and the compare differences from the viewing scenario's side."""
from __future__ import annotations

from datetime import date

import pytest
from conftest import put_cells, small_plan

BASE = "/api/projection/drivers"
M = lambda y, m: date(y, m, 1)                # noqa: E731


@pytest.fixture()
def world(rw, stub_world):
    return small_plan(rw)                      # books Jan-Jun 2026; plan cells Jul-26 .. Dec-27 (spend 300K, CPA 95, AOV 70)


def table(client, scenario="current"):
    r = client.get(f"{BASE}/table", query_string={"scenario": scenario})
    assert r.status_code == 200
    return r.get_json()


def vals(t, key):
    return next(r for r in t["rows"] if r["key"] == key)["values"]


def fill(client, **kw):
    body = {"scenario": "current", "target": "spend_total", "from": "2027-01-01", "to": "2027-06-01", "pattern": {"type": "flat", "value": 500000}}
    body.update(kw)
    r = client.post(f"{BASE}/patterns", json=body)
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def undo(client, version, **kw):
    return client.post(f"{BASE}/undo", json={"scenario": "current", "version": version, **kw})


def test_undo_puts_back_exactly_the_cells_the_fill_changed(client, world, pg):
    before = vals(table(client), "drivers.spend_total")
    applied = fill(client)
    assert vals(table(client), "drivers.spend_total") != before
    r = undo(client, applied["version"])
    assert r.status_code == 200
    j = r.get_json()
    assert j["restored"] == 6 and j["skipped"] == 0 and j["version"] == applied["version"] + 1
    assert vals(table(client), "drivers.spend_total") == before
    rows = pg.execute("SELECT action, note, version FROM proj_audit WHERE note LIKE 'undo of version%%' ORDER BY id").fetchall()
    assert len(rows) == 6 and {x["action"] for x in rows} == {"series.update"} and {x["version"] for x in rows} == {j["version"]}


def test_cells_the_fill_created_are_deleted_by_the_undo(client, world, pg):
    applied = fill(client, **{"from": "2028-01-01", "to": "2028-03-01"})           # beyond the typed plan: these cells did not exist
    n = pg.execute("SELECT count(*) AS n FROM proj_series WHERE line_key = 'drivers.spend_total' AND month >= '2028-01-01'").fetchone()["n"]
    assert n == 3
    assert undo(client, applied["version"]).get_json()["restored"] == 3
    assert pg.execute("SELECT count(*) AS n FROM proj_series WHERE line_key = 'drivers.spend_total' AND month >= '2028-01-01'").fetchone()["n"] == 0
    assert {a["action"] for a in pg.execute("SELECT action FROM proj_audit WHERE note LIKE 'undo of version%%'").fetchall()} == {"series.delete"}


def test_a_cell_edited_again_since_is_left_alone(client, world, rw):
    applied = fill(client)
    put_cells(rw, "drivers.spend_total", {M(2027, 3): 777000.0})
    j = undo(client, applied["version"]).get_json()
    assert j["restored"] == 5 and j["skipped"] == 1
    v = vals(table(client), "drivers.spend_total")
    t = table(client)
    ix = t["months"].index("2027-03-01")
    assert v[ix] == 777000.0 and v[ix - 1] == 300000.0 and v[ix + 1] == 300000.0


def test_undo_is_itself_a_version_and_can_be_restored_over(client, world):
    before = vals(table(client), "drivers.spend_total")
    applied = fill(client)
    j = undo(client, applied["version"]).get_json()
    r = client.post("/api/projection/scenarios/current/restore", json={"version": applied["version"]})
    assert r.status_code == 200 and vals(table(client), "drivers.spend_total") != before          # restore over the undo brings the fill back
    assert j["version"] > applied["version"]


def test_a_closed_month_typed_input_comes_back_after_an_undo(client, world, rw):
    # Aug-26 holds a typed input from before; a fill over it writes an override (closed month); the undo restores the original input
    rw.execute("UPDATE proj_series SET kind = 'input' WHERE line_key = 'drivers.spend_total' AND month = '2026-08-01'")
    rw.commit()
    applied = fill(client, **{"from": "2026-08-01", "to": "2026-09-01"})
    assert undo(client, applied["version"]).get_json()["restored"] == 2
    row = rw.execute("SELECT kind, value FROM proj_series s JOIN proj_scenarios p ON p.id = s.scenario_id WHERE p.slug = 'current' "
                     "AND line_key = 'drivers.spend_total' AND month = '2026-08-01'").fetchone()
    assert row["kind"] == "input" and float(row["value"]) == 300000.0


def test_undo_of_a_roas_slide_restores_the_lever(client, world):
    applied = client.post(f"{BASE}/patterns", json={"scenario": "current", "target": "nc_roas", "from": "2027-01-01", "to": "2027-06-01",
                                                    "pattern": {"type": "roas_slide", "start": 0.8, "end": 0.6}}).get_json()
    assert table(client)["levers"]["roas_slide"]["enabled"] is True
    j = undo(client, applied["version"]).get_json()
    assert j["restored"] == 1 and table(client)["levers"]["roas_slide"]["enabled"] is False


def test_undo_errors(client, world):
    applied = fill(client)
    assert undo(client, 9999).status_code == 404
    assert undo(client, "x").status_code == 400
    assert undo(client, 0).status_code == 400
    assert client.post(f"{BASE}/undo", json={"version": applied["version"]}).status_code == 400
    assert client.post(f"{BASE}/undo", json={"scenario": "nope", "version": 2}).status_code == 404
    assert undo(client, applied["version"], expected_version=applied["version"] + 5).status_code == 409
    assert undo(client, applied["version"]).status_code == 200
    again = undo(client, applied["version"])                                    # the cells are already back: nothing left to restore
    assert again.status_code == 200 and again.get_json()["restored"] == 0


def test_undo_needs_login_and_json(anon, client, world):
    assert anon.post(f"{BASE}/undo", json={"scenario": "current", "version": 2}).status_code == 401
    assert client.post(f"{BASE}/undo", data="x", content_type="text/plain").status_code == 415


def test_compare_differences_read_from_the_viewing_scenarios_side(client, world):
    assert client.post("/api/projection/scenarios", json={"name": "Growth", "clone_from": "current"}).status_code == 201
    r = client.post(f"{BASE}/patterns", json={"scenario": "growth", "target": "spend_total", "from": "2027-01-01", "to": "2027-12-01",
                                             "pattern": {"type": "flat", "value": 600000}})
    assert r.status_code == 200
    # known pair, FY27 marketing spend: Current plan 12 x 300,000 = 3,600,000; Growth 12 x 600,000 = 7,200,000
    from_growth = client.get(f"{BASE}/compare?scenarios=growth,current").get_json()
    assert from_growth["view"] == "growth"
    cur = next(s for s in from_growth["scenarios"] if s["slug"] == "current")
    assert cur["fy"]["2027"]["spend_total"] == 3_600_000 and next(s for s in from_growth["scenarios"] if s["slug"] == "growth")["fy"]["2027"]["spend_total"] == 7_200_000
    d = cur["vs_view"]["fy"]["2027"]["spend_total"]
    assert cur["vs_view"]["view"] == "growth" and d["diff"] == 3_600_000 and d["pct"] == pytest.approx(1.0)        # Growth has $3.6M more, +100% on Current plan
    from_current = client.get(f"{BASE}/compare?scenarios=current,growth").get_json()
    gro = next(s for s in from_current["scenarios"] if s["slug"] == "growth")
    d2 = gro["vs_view"]["fy"]["2027"]["spend_total"]
    assert from_current["view"] == "current" and d2["diff"] == -3_600_000 and d2["pct"] == pytest.approx(-0.5)      # Current plan has $3.6M less, -50% on Growth
    assert "vs_view" not in next(s for s in from_current["scenarios"] if s["slug"] == "current")
    assert cur["vs_view"]["fy"]["2027"]["nc_rev"]["diff"] > 0                                                      # more spend, more first-order revenue
