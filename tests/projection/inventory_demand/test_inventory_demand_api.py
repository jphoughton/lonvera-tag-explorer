"""HTTP layer: auth follows the app, table / inputs / daily / countries payloads, input edits (validated, audited, versioned, restorable)."""
from __future__ import annotations

import math
from datetime import date

import pytest

from etl import projection_inventory_demand as etl
from idemand_helpers import FakeReader, P, daily_tab, days, product_daily_tab, stock_tab

NOV3 = date(2026, 11, 3)


def rows(start: date, n: int, base=(10, 20, 30, 4)) -> dict:
    return {d: tuple(b for b in base) for d in days(start, n)}


@pytest.fixture()
def loaded(conn, monkeypatch):
    """Flat feeds: US direct 10/20/30/4 a day through Nov 2, Amazon through Nov 2, international through Oct 12 (27 estimated days)."""
    monkeypatch.setenv("PROJECTION_TODAY", "2026-11-03")
    t = {"Warehance Daily": daily_tab("revamp", rows(date(2026, 10, 1), 34), "2026-11-03 is a partial day."),
         "Amazon - Product Daily": product_daily_tab(rows(date(2026, 9, 1), 64, (1, 2, 3, 0))),
         "Intl Daily Units": daily_tab("intl", rows(date(2026, 8, 1), 73, (5, 6, 7, 1)), "Rows from 2026-09-16 (grey italics) are estimates."),
         "Warehance Fcst Inputs": stock_tab(NOV3)}
    etl.refresh(conn, days=120, reader=FakeReader(t))
    return t


# ------------------------------------------------------------------------------------------------------------- auth and headers

ENDPOINTS = [("get", "/table"), ("get", "/inputs"), ("get", "/daily"), ("get", "/countries"), ("put", "/inputs"), ("post", "/upload-intl")]


@pytest.mark.parametrize("method,path", ENDPOINTS)
def test_app_login_on_means_401(anon, method, path):
    r = getattr(anon, method)(P + path, json={})
    assert r.status_code == 401 and r.get_json() == {"error": "unauthorized"}


def test_open_when_the_apps_auth_is_off_and_never_indexed(app_factory, loaded):
    c = app_factory(AUTH_DISABLED=True).test_client()
    r = c.get(f"{P}/table")
    assert r.status_code == 200 and "noindex" in r.headers["X-Robots-Tag"]
    assert c.put(f"{P}/inputs", json={"locations": {"revamp": {"baseline_days": 14}}}).status_code == 200


def test_the_module_is_listed_as_built(client):
    mods = {m["key"]: m for m in client.get("/api/projection/modules").get_json()["modules"]}
    assert mods["inventory_demand"]["built"] and mods["inventory_demand"]["ui"] == "inventory_demand.js"


# ------------------------------------------------------------------------------------------------------------- table

def test_table_payload_shape_and_values(client, loaded):
    body = client.get(f"{P}/table").get_json()
    assert len(body["months"]) == 60 == len(body["status"]) and body["months"][0] == "2025-01-01"
    assert body["status"][body["months"].index("2026-10-01")] == "actual" and body["status"][body["months"].index("2026-11-01")] == "projected"
    keys = [r["key"] for r in body["rows"]]
    assert len(keys) == len(set(keys))
    assert {"total", "loc.revamp", "share.amazon", "prod.bull", "cell.revamp.bull", "locsum.intl", "perday.total"} <= set(keys)
    assert all(r["kind"] in ("calc", "subtotal", "total", "header", "ratio") and r["fmt"] in ("num", "pct") for r in body["rows"])
    assert all(len(r["values"]) == 60 and r["input"] is False for r in body["rows"])
    assert 1 <= len(body["captions"]) <= 2 and body["as_of"] == "2026-11-03" and body["scenario"] == "current"
    by = {r["key"]: r for r in body["rows"]}
    nov, dec = body["months"].index("2026-11-01"), body["months"].index("2026-12-01")
    assert by["cell.revamp.bull"]["values"][nov] == pytest.approx(2 * 10 + 28 * 10)           # 2 actual days + 28 forecast days
    assert by["cell.revamp.bull"]["values"][dec] == pytest.approx(310)
    assert by["total"]["values"][dec] == pytest.approx(sum(by[f"loc.{l}"]["values"][dec] for l in ("revamp", "amazon", "intl")))
    assert sum(by[f"share.{l}"]["values"][dec] for l in ("revamp", "amazon", "intl")) == pytest.approx(1.0)
    assert by["perday.revamp"]["values"][dec] == pytest.approx(by["loc.revamp"]["values"][dec] / 31)
    assert not any(isinstance(v, float) and not math.isfinite(v) for r in body["rows"] for v in r["values"] if v is not None)


def test_table_for_an_unknown_scenario_is_404_and_with_no_data_is_empty_not_an_error(client):
    assert client.get(f"{P}/table?scenario=nope").status_code == 404
    r = client.get(f"{P}/table")
    assert r.status_code == 200 and all(v in (None, 0) for row in r.get_json()["rows"] for v in row["values"] if row["kind"] != "header")


# ------------------------------------------------------------------------------------------------------------- inputs

def test_inputs_payload_has_levers_baselines_and_feed_status(client, loaded):
    body = client.get(f"{P}/inputs").get_json()
    locs = {l["key"]: l for l in body["locations"]}
    assert set(locs) == {"revamp", "amazon", "intl"} and body["as_of"] == "2026-11-03" and body["as_of_pinned"] is False
    r = locs["revamp"]
    assert r["levers"]["baseline_days"] == 28 and r["levers"]["seasonality"] == [1.0] * 12
    assert r["window"] == {"start": "2026-10-06", "end": "2026-11-02"}
    bull = next(p for p in r["products"] if p["key"] == "bull")
    assert bull["effective_rate"] == pytest.approx(10) and bull["window_units"] == pytest.approx(280) and bull["rate_override"] is None
    assert sum(p["mix"] for p in r["products"]) == pytest.approx(1)
    assert r["feed"]["status"] == "ok" and r["feed"]["data_through"] == "2026-11-02" and r["feed"]["age_days"] == 1
    assert locs["amazon"]["feed"]["status"] == "ok" and locs["amazon"]["feed"]["age_days"] == 1 and locs["amazon"]["feed"]["data_through"] == "2026-11-02"
    assert locs["intl"]["feed"]["estimate_days"] == 27 and locs["intl"]["feed"]["status"] == "stale"
    assert {s["product"] for s in body["stock"]} == {"bull", "liver", "boneflex", "belle"}


def test_putting_inputs_changes_the_forecast_audits_the_edit_and_can_be_undone(client, loaded, pg):
    v0 = client.get(f"{P}/inputs").get_json()["version"]
    r = client.put(f"{P}/inputs", json={"expected_version": v0, "locations": {"revamp": {"monthly_growth": 0.1, "baseline_days": 14}}})
    assert r.status_code == 200 and r.get_json()["version"] == v0 + 1
    body = client.get(f"{P}/table").get_json()
    by = {x["key"]: x for x in body["rows"]}
    dec = body["months"].index("2026-12-01")
    assert by["cell.revamp.bull"]["values"][dec] == pytest.approx(310 * 1.1)
    audit = pg.execute("SELECT actor, action, old, new, version FROM proj_audit WHERE action = 'scenario.params'").fetchall()
    assert len(audit) == 1 and audit[0]["new"]["params"]["inventory_demand"]["locations"]["revamp"]["monthly_growth"] == 0.1 and audit[0]["version"] == v0 + 1
    assert client.post("/api/projection/scenarios/current/restore", json={"version": v0}).status_code == 200
    back = {x["key"]: x for x in client.get(f"{P}/table").get_json()["rows"]}
    assert back["cell.revamp.bull"]["values"][dec] == pytest.approx(310)


def test_seasonality_and_a_typed_rate_flow_through(client, loaded):
    season = [1.0] * 12
    season[11] = 2.0
    client.put(f"{P}/inputs", json={"locations": {"revamp": {"seasonality": season, "products": {"bull": {"rate": 4.0}}}}})
    body = client.get(f"{P}/table").get_json()
    by = {x["key"]: x for x in body["rows"]}
    dec = body["months"].index("2026-12-01")
    assert by["cell.revamp.bull"]["values"][dec] == pytest.approx(31 * 4.0 * 2)
    inputs = client.get(f"{P}/inputs").get_json()
    bull = next(p for p in next(l for l in inputs["locations"] if l["key"] == "revamp")["products"] if p["key"] == "bull")
    assert bull["rate_override"] == 4.0 and bull["baseline_rate"] == pytest.approx(10) and bull["effective_rate"] == 4.0
    client.put(f"{P}/inputs", json={"locations": {"revamp": {"products": {"bull": {"rate": None}}}}})                 # null removes the override
    bull = next(p for p in next(l for l in client.get(f"{P}/inputs").get_json()["locations"] if l["key"] == "revamp")["products"] if p["key"] == "bull")
    assert bull["rate_override"] is None


def test_pinning_the_forecast_date_shows_in_the_payload(client, loaded):
    client.put(f"{P}/inputs", json={"as_of": "2026-12-15"})
    body = client.get(f"{P}/inputs").get_json()
    assert body["as_of"] == "2026-12-15" and body["as_of_pinned"] is True
    client.put(f"{P}/inputs", json={"as_of": None})
    assert client.get(f"{P}/inputs").get_json()["as_of_pinned"] is False


def test_edits_on_a_clone_do_not_touch_the_base(client, loaded):
    s = client.post("/api/projection/scenarios", json={"name": "Faster", "clone_from": "current"}).get_json()["scenario"]
    client.put(f"{P}/inputs", json={"scenario": s["slug"], "locations": {"amazon": {"monthly_growth": 0.2}}})
    def dec_amazon(slug):
        b = client.get(f"{P}/table?scenario={slug}").get_json()
        return next(r for r in b["rows"] if r["key"] == "cell.amazon.bull")["values"][b["months"].index("2026-12-01")]
    assert dec_amazon(s["slug"]) == pytest.approx(dec_amazon("current") * 1.2)


@pytest.mark.parametrize("body,status", [
    ({}, 400), ({"locations": {"revamp": {"baseline_days": 0}}}, 400), ({"locations": {"mars": {}}}, 400), ({"as_of": "soon"}, 400),
    ({"locations": {"revamp": {"seasonality": [1] * 3}}}, 400), ({"bogus": 1}, 400), ({"scenario": 5, "as_of": None}, 400),
    ({"scenario": "nope", "as_of": None}, 404), ({"expected_version": "x", "as_of": None}, 400), ({"expected_version": 999, "as_of": None}, 409),
])
def test_bad_edits_are_rejected_and_nothing_is_written(client, loaded, pg, body, status):
    before = pg.execute("SELECT version, params FROM proj_scenarios WHERE slug = 'current'").fetchone()
    r = client.put(f"{P}/inputs", json=body)
    assert r.status_code == status and "error" in r.get_json()
    assert pg.execute("SELECT version, params FROM proj_scenarios WHERE slug = 'current'").fetchone() == before
    assert pg.execute("SELECT count(*) AS n FROM proj_audit WHERE action = 'scenario.params'").fetchone()["n"] == 0


def test_a_non_json_body_is_refused_by_the_core_guard(client):
    r = client.put(f"{P}/inputs", data="x=1", content_type="application/x-www-form-urlencoded")
    assert r.status_code == 415


# ------------------------------------------------------------------------------------------------------------- daily and countries

def test_daily_series_and_baseline_overlay(client, loaded):
    b = client.get(f"{P}/daily?location=revamp&product=bull&days=30").get_json()
    n = len(b["days"])
    assert n == 30 and b["days"][-1] == "2026-11-02" and len(b["units"]) == len(b["estimate"]) == len(b["ma7"]) == len(b["baseline"]) == n
    assert b["units"][-1] == 10 and b["rate"] == pytest.approx(10) and b["window"] == {"start": "2026-10-06", "end": "2026-11-02"}
    assert b["baseline"][0] is None and b["baseline"][-1] == pytest.approx(10) and b["ma7"][5] is None and b["ma7"][6] == pytest.approx(10)
    allp = client.get(f"{P}/daily?location=revamp&days=7").get_json()
    assert allp["product"] == "all" and allp["units"][-1] == 64 and allp["rate"] == pytest.approx(64)
    intl = client.get(f"{P}/daily?location=intl&days=40").get_json()
    assert intl["estimate"].count(True) == 27 and intl["days"][-1] == "2026-10-12"


@pytest.mark.parametrize("q", ["location=mars", "location=revamp&days=3", "location=revamp&days=9999", "location=revamp&days=x", "location=revamp&product=Bad Key"])
def test_daily_validates_its_arguments(client, loaded, q):
    assert client.get(f"{P}/daily?{q}").status_code == 400


def test_daily_for_a_location_without_data_is_empty(client):
    b = client.get(f"{P}/daily?location=amazon").get_json()
    assert b["days"] == [] and b["units"] == []


def test_countries_endpoint_ranks_by_units(client, conn):
    from app.projection.inventory_demand import store
    store.replace_countries(conn, [(date(2026, 7, 1), "United Kingdom", 80, None), (date(2026, 7, 1), "Canada", 20, None),
                                   (date(2026, 9, 1), "United Kingdom", 20, 15)], "test")
    conn.commit()
    b = client.get(f"{P}/countries").get_json()
    assert b["months"] == ["2026-07-01", "2026-09-01"] and b["days"] == [None, 15]
    assert b["countries"][0]["country"] == "United Kingdom" and b["countries"][0]["total"] == 100 and b["countries"][0]["share"] == pytest.approx(100 / 120)
