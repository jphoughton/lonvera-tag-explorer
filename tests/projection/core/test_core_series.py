from __future__ import annotations

import pytest

from core_helpers import P, audit_rows, create, get_series, item, put, scenario


def test_put_get_round_trip(client):
    r = put(client, "current", item(value=1234.5, note="from sheet"), item(month="2026-12-01", value=-5), item(key="pnl.rate", value=0.0425, kind="override"))
    assert r.status_code == 200 and r.get_json() == {"version": 2, "changed": 3}
    got = get_series(client, "current", "drivers.spend,pnl.rate,missing.key")
    assert got["version"] == 2 and got["scenario"] == "current"
    assert got["series"] == {"drivers.spend": {"2026-11-01": 1234.5, "2026-12-01": -5.0}, "pnl.rate": {"2026-11-01": 0.0425}, "missing.key": {}}
    g = get_series(client, "current", "drivers.spend", **{"from": "2026-12-01", "to": "2027-01-01"})
    assert g["series"]["drivers.spend"] == {"2026-12-01": -5.0}


def test_prefix_query_and_exactly_one_selector(client):
    put(client, "current", item(key="drivers.a"), item(key="drivers.b"), item(key="drivers_x.c"), item(key="pnl.a"))
    r = client.get(f"{P}/series?scenario=current&prefix=drivers.")
    assert sorted(r.get_json()["series"]) == ["drivers.a", "drivers.b"]           # literal prefix: '_' is not a wildcard
    assert client.get(f"{P}/series?scenario=current").status_code == 400
    assert client.get(f"{P}/series?scenario=current&keys=a&prefix=b").status_code == 400
    assert client.get(f"{P}/series?scenario=nope&keys=a").status_code == 404
    assert client.get(f"{P}/series?keys=drivers.a").get_json()["scenario"] == "current"       # default scenario


def test_update_delete_and_noop(client, pg):
    put(client, "current", item(value=10, note="a"))
    v = scenario(client, "current")["version"]
    assert put(client, "current", item(value=10, note="a")).get_json() == {"version": v, "changed": 0}            # identical: no bump, no audit
    assert put(client, "current", item(key="never.set", value=None)).get_json() == {"version": v, "changed": 0}   # deleting nothing
    assert put(client, "current", item(value=11, note="a")).get_json()["version"] == v + 1                         # value change
    assert put(client, "current", item(value=11, note="b")).get_json()["version"] == v + 2                         # note change
    assert put(client, "current", item(value=11, note="b", kind="override")).get_json()["version"] == v + 3        # kind change
    assert put(client, "current", item(value=None)).get_json()["version"] == v + 4                                  # null deletes
    assert get_series(client, "current", "drivers.spend")["series"]["drivers.spend"] == {}
    assert [r["action"] for r in audit_rows(pg, scenario="current") if r["action"].startswith("series")] == \
        ["series.set", "series.update", "series.update", "series.update", "series.delete"]


def test_value_precision_and_range(client):
    assert put(client, "current", item(value=0.123456789), item(month="2026-12-01", value=99999999999.9999)).status_code == 200
    s = get_series(client, "current", "drivers.spend")["series"]["drivers.spend"]
    assert s["2026-11-01"] == 0.1235 and s["2026-12-01"] == 99999999999.9999
    assert put(client, "current", item(value=1e13)).status_code == 400


BAD_ITEMS = [
    ("line_key uppercase", item(key="Drivers.Spend")), ("line_key dash", item(key="drivers-spend")), ("line_key empty", item(key="")),
    ("line_key long", item(key="a" * 121)), ("line_key missing", {"month": "2026-11-01", "value": 1}), ("line_key type", item(key=5)),
    ("month not first", item(month="2026-11-15")), ("month before axis", item(month="2024-12-01")), ("month after axis", item(month="2030-01-01")),
    ("month format", item(month="11/01/2026")), ("month type", item(month=20261101)), ("month missing", {"line_key": "a", "value": 1}),
    ("value string", item(value="12")), ("value bool", item(value=True)), ("value list", item(value=[1])), ("value missing", {"line_key": "a", "month": "2026-11-01"}),
    ("kind", item(kind="guess")), ("note type", item(note=5)), ("note long", item(note="x" * 501)), ("extra field", {**item(), "foo": 1}), ("not an object", "abc"),
]


@pytest.mark.parametrize("name,bad", BAD_ITEMS, ids=[b[0] for b in BAD_ITEMS])
def test_item_validation(client, name, bad):
    r = client.put(f"{P}/series", json={"scenario": "current", "items": [item(month="2026-10-01"), bad]})
    assert r.status_code == 400, name
    assert "items[1]" in r.get_json()["error"] or "error" in r.get_json()
    assert get_series(client, "current", "drivers.spend")["series"]["drivers.spend"] == {}        # nothing from the request was applied


def test_request_level_validation(client):
    assert client.put(f"{P}/series", json={"items": [item()]}).status_code == 400                      # scenario required
    assert client.put(f"{P}/series", json={"scenario": "current"}).status_code == 400
    assert client.put(f"{P}/series", json={"scenario": "current", "items": []}).status_code == 400
    assert client.put(f"{P}/series", json={"scenario": "current", "items": "x"}).status_code == 400
    assert client.put(f"{P}/series", json={"scenario": "nope", "items": [item()]}).status_code == 404
    assert client.put(f"{P}/series", json=[1, 2]).status_code == 400
    assert client.put(f"{P}/series", data="not json", content_type="application/json").status_code == 400
    dup = client.put(f"{P}/series", json={"scenario": "current", "items": [item(value=1), item(value=2)]})
    assert dup.status_code == 400 and "duplicate" in dup.get_json()["error"]
    assert client.put(f"{P}/series", json={"scenario": "current", "items": [item(value=1)], "expected_version": 99}).status_code == 409
    nan = client.put(f"{P}/series", data='{"scenario": "current", "items": [{"line_key": "a", "month": "2026-11-01", "value": NaN}]}', content_type="application/json")
    assert nan.status_code == 400


def test_item_count_limit(client):
    months = [f"{y}-{m:02d}-01" for y in range(2025, 2030) for m in range(1, 13)]
    five_hundred = [item(key=f"k{i}", month=months[i % 60]) for i in range(500)]
    assert put(client, "current", *five_hundred).get_json()["changed"] == 500
    r = put(client, "current", *five_hundred, item(key="one.more"))
    assert r.status_code == 400 and "500" in r.get_json()["error"]


def test_a_500_item_body_larger_than_the_app_wide_64kb_cap_is_accepted(client):
    months = [f"{y}-{m:02d}-01" for y in range(2025, 2030) for m in range(1, 13)]
    items = [item(key=f"k{i}", month=months[i % 60], note="n" * 300) for i in range(500)]
    r = client.put(f"{P}/series", json={"scenario": "current", "items": items})
    assert r.status_code == 200 and r.get_json()["changed"] == 500


def test_unknown_get_params_and_key_limits(client):
    keys = ",".join(f"k{i}" for i in range(201))
    assert client.get(f"{P}/series?keys={keys}").status_code == 400
    assert client.get(f"{P}/series?keys=Bad Key").status_code == 400
    assert client.get(f"{P}/series?keys=a&from=2026-11-15").status_code == 400
    assert client.get(f"{P}/series?keys=a&prefix=BAD").status_code == 400


def test_series_module_functions(core_db):
    from datetime import date
    from app.projection import rwdb, scenarios, series
    with rwdb.get_rw(core_db) as conn:
        series.apply_items(conn, "current", series.parse_items([item(value=5), item(month="2026-12-01", value=6)]), actor="t")
    with rwdb.get_rw(core_db) as conn:
        scn = scenarios.get_scenario(conn, "current")
        assert series.get(conn, scn, "drivers.spend", date(2026, 11, 1)) == 5.0
        assert series.get(conn, "current", "drivers.spend", date(2027, 5, 1)) is None
        assert series.get_series(conn, scn, ["drivers.spend", "x.y"], from_=date(2026, 12, 1)) == {"drivers.spend": {date(2026, 12, 1): 6.0}, "x.y": {}}
        assert series.get_series(conn, None, prefix="drivers.") == {"drivers.spend": {date(2026, 11, 1): 5.0, date(2026, 12, 1): 6.0}}


def test_actuals_helpers_and_actual_through(core_db, pg):
    from datetime import date
    from app.projection import rwdb, series
    from app.projection.interfaces import Scenario
    from app.projection import engine
    rows = [("qb.revenue", date(2026, 5, 1), 100.0), ("qb.revenue", date(2026, 6, 1), 110.5), ("qb.revenue", date(2024, 2, 1), 1.0),
            ("qb.cogs", date(2026, 6, 1), 40.0), ("qb.revenue", date(2026, 11, 1), 999.0)]
    with rwdb.get_rw(core_db) as conn:
        assert series.upsert_actuals(conn, rows, "test load", actor="t") == 5
        assert series.upsert_actuals(conn, [("qb.revenue", date(2026, 6, 1), 120.0)], "test reload", actor="t") == 1
    with rwdb.get_rw(core_db) as conn:
        a = series.get_actuals(conn, ["qb.revenue"], from_=date(2026, 1, 1))
        assert a == {"qb.revenue": {date(2026, 5, 1): 100.0, date(2026, 6, 1): 120.0, date(2026, 11, 1): 999.0}}
        assert set(series.get_actuals(conn, prefix="qb.")) == {"qb.revenue", "qb.cogs"}
        assert series.actual_through(conn, date(2026, 11, 3)) == date(2026, 6, 1)           # Nov-26 is not closed on 2026-11-03
        assert series.actual_through(conn, date(2026, 12, 3)) == date(2026, 11, 1)
    loads = audit_rows(pg, "actuals.load")
    assert len(loads) == 2 and loads[0]["new"]["rows"] == 5 and loads[0]["new"]["from"] == "2024-02-01" and loads[0]["note"] == "test load"
    with pytest.raises(Exception):
        with rwdb.get_rw(core_db) as conn:
            series.upsert_actuals(conn, [("qb.x", date(2026, 6, 2), 1.0)], "bad")
    with pytest.raises(Exception):
        with rwdb.get_rw(core_db) as conn:
            series.upsert_actuals(conn, [("Bad Key", date(2026, 6, 1), 1.0)], "bad")
    with pytest.raises(Exception):
        with rwdb.get_rw(core_db) as conn:
            series.upsert_actuals(conn, [("qb.x", date(2026, 6, 1), float("nan"))], "bad")
