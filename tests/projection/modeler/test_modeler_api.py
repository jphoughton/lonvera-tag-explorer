"""The modeler endpoints: shape, validation, the working scenario, save as, reset, settings, compare, auth, guards."""
from __future__ import annotations

import pytest
from conftest import AOV, CPA, P, SPEND, copy_of, make_app, preview, put_cells, state, years

SERIES_KEYS = (SPEND, AOV, CPA)


def audit_actions(conn, slug):
    conn.rollback()
    return [r["action"] for r in conn.execute("SELECT action FROM proj_audit WHERE scenario_slug = %s ORDER BY id", (slug,)).fetchall()]


def own_cells(conn, slug):
    conn.rollback()
    return {(r["line_key"], r["month"].isoformat()): float(r["value"]) for r in conn.execute(
        "SELECT line_key, month, value FROM proj_series s JOIN proj_scenarios p ON p.id = s.scenario_id WHERE p.slug = %s AND line_key = ANY(%s)", (slug, list(SERIES_KEYS))).fetchall()}


# ------------------------------------------------------------------------------------------------------------------ registry and shape

def test_the_page_is_registered_first_in_the_plan_group_and_built(client):
    mods = client.get(f"{P}/modules").get_json()["modules"]
    assert mods[0] == {"key": "modeler", "label": "Scenario modeler", "group": "P1", "built": True, "ui": "modeler.js"}
    assert [m["key"] for m in mods].index("cohorts") == 1


def test_state_has_everything_the_page_draws(client):
    s = state(client, "current")
    assert set(s) >= {"scenario", "plan_of_record", "draft", "applied", "months", "status", "rows", "captions", "cellmeta", "fy", "actual_through", "outputs", "retention", "levers", "settings"}
    assert s["scenario"]["slug"] == "current" and s["scenario"]["is_default"] and "por" not in s                  # no difference to show on the plan of record itself
    assert s["draft"] is False and s["applied"] is None and len(s["captions"]) == 1 and s["captions"][0].startswith("QuickBooks through Jul 2026")
    assert s["settings"]["ebitda_addbacks"] == ["interest_expense"]
    assert set(s["outputs"]) == {"years", "headline_year", "lowest_cash", "averages", "ebitda"}


def test_state_of_a_copy_carries_the_plan_of_records_numbers_for_the_difference(client, restore):
    slug = copy_of(client, "Case por")
    s = state(client, slug)
    assert s["por"]["outputs"]["years"] == state(client, "current")["outputs"]["years"] and s["scenario"]["slug"] == slug


def test_unknown_scenario_is_a_404_and_a_missing_body_is_a_400(client):
    assert client.get(f"{P}/modeler/state", query_string={"scenario": "nope"}).status_code == 404
    assert client.post(f"{P}/modeler/preview", json={"scenario": "nope", "preview": True}).status_code == 404
    assert client.post(f"{P}/modeler/preview", data="[]", content_type="application/json").status_code == 400
    assert client.post(f"{P}/modeler/preview", data="x", content_type="text/plain").status_code == 415


@pytest.mark.parametrize("body,fragment", [({"edits": "x"}, "must be a list"), ({"edits": [{"line_key": "drivers.amazon_rev", "month": "2027-01-01", "value": 1}]}, "line_key must be one of"),
                                           ({"op": {"type": "fill", "row": "spend_total", "from": "2027-01-01"}}, "value must be a number"), ({"op": "x"}, "op must be an object"),
                                           ({"op": {"type": "ramp", "row": "nc_cpa", "from": "2027-01-01", "to": "2027-03-01", "end_value": 0}}, "Cost per new customer")])
def test_preview_validation_is_a_400_with_a_plain_message(client, body, fragment):
    r = client.post(f"{P}/modeler/preview", json={"scenario": "current", "preview": True, **body})
    assert r.status_code == 400 and fragment in r.get_json()["error"], r.get_json()


def test_preview_on_the_plan_of_record_is_allowed_because_it_writes_nothing(client, conn):
    conn.rollback()
    before = conn.execute("SELECT version FROM proj_scenarios WHERE slug = 'current'").fetchone()["version"]
    out = preview(client, "current", op={"type": "fill", "row": "spend_total", "from": "2027-01-01", "to": "2027-12-01", "value": 900000})
    assert out["draft"] and out["scenario"]["is_default"]
    conn.rollback()
    assert conn.execute("SELECT version FROM proj_scenarios WHERE slug = 'current'").fetchone()["version"] == before


# ------------------------------------------------------------------------------------------------------------------ the working scenario

def test_workspace_creates_modeler_once_as_a_copy_of_the_plan_of_record(restore, client, conn):
    r = client.post(f"{P}/modeler/workspace", json={})
    assert r.status_code == 200 and r.get_json()["created"] is True
    made = r.get_json()["scenario"]
    assert made["name"] == "Modeler" and made["slug"] == "modeler" and made["is_default"] is False
    assert state(client, "modeler")["outputs"]["years"] == state(client, "current")["outputs"]["years"]
    again = client.post(f"{P}/modeler/workspace", json={}).get_json()
    assert again["created"] is False and again["scenario"]["slug"] == "modeler"
    assert audit_actions(conn, "modeler") == ["scenario.clone"]
    put_cells(client, "modeler", [(SPEND, "2027-01-01", 1.0)])
    assert state(client, "current")["outputs"]["years"][1]["ad_spend"] == 4200000.0                              # the plan of record never moved


def test_workspace_finds_it_by_name_whatever_the_slug(restore, client):
    first = client.post(f"{P}/scenarios", json={"name": "modeler", "clone_from": "current"}).get_json()["scenario"]
    got = client.post(f"{P}/modeler/workspace", json={}).get_json()
    assert got["created"] is False and got["scenario"]["slug"] == first["slug"]


# ------------------------------------------------------------------------------------------------------------------ save as, reset, settings

def test_save_as_is_a_frozen_copy_that_later_edits_never_reach(restore, client, conn):
    work = copy_of(client, "Case work")
    put_cells(client, work, [(SPEND, "2027-01-01", 600000.0)])
    r = client.post(f"{P}/modeler/save-as", json={"source": work, "name": "Josh $600K"})
    assert r.status_code == 201
    saved = r.get_json()["scenario"]["slug"]
    assert state(client, saved)["outputs"]["years"] == state(client, work)["outputs"]["years"]
    put_cells(client, work, [(SPEND, "2027-02-01", 1.0), (SPEND, "2027-03-01", 2.0)])
    assert state(client, saved)["outputs"]["years"][1]["ad_spend"] != state(client, work)["outputs"]["years"][1]["ad_spend"]
    assert own_cells(conn, saved)[(SPEND, "2027-02-01")] == 350000.0
    assert client.post(f"{P}/modeler/save-as", json={"source": work, "name": "josh $600k"}).status_code == 409
    assert client.post(f"{P}/modeler/save-as", json={"source": work}).status_code == 400
    assert client.post(f"{P}/modeler/save-as", json={"name": "x"}).status_code == 400


def test_reset_puts_the_inputs_and_the_retention_model_back_and_is_audited_and_restorable(restore, client, conn):
    slug = copy_of(client, "Case reset")
    put_cells(client, slug, [(SPEND, "2027-01-01", 777777.0), (CPA, "2027-02-01", 60.0), (AOV, "2027-03-01", 80.0)])
    assert client.put(f"{P}/cohorts/levers", json={"scenario": slug, "levers": {"curve_source": "goal"}}).status_code == 200
    por = state(client, "current")["outputs"]["years"]
    assert state(client, slug)["outputs"]["years"] != por
    work_version = client.get(f"{P}/scenarios").get_json()["scenarios"]
    version_before = next(s for s in work_version if s["slug"] == slug)["version"]
    r = client.post(f"{P}/modeler/reset", json={"scenario": slug})
    assert r.status_code == 200 and r.get_json()["cells_changed"] == 3 and r.get_json()["retention_reset"] is True
    assert state(client, slug)["outputs"]["years"] == por and state(client, slug)["retention"]["curve_source"] == "live"
    assert "series.update" in audit_actions(conn, slug) and audit_actions(conn, slug).count("scenario.params") >= 2
    assert client.post(f"{P}/modeler/reset", json={"scenario": slug}).get_json()["cells_changed"] == 0               # nothing left to reset
    back = client.post(f"{P}/scenarios/{slug}/restore", json={"version": version_before})
    assert back.status_code == 200
    assert state(client, slug)["outputs"]["years"] != por and state(client, slug)["retention"]["curve_source"] == "goal"


def test_reset_leaves_every_other_setting_alone_and_refuses_the_plan_of_record(restore, client):
    slug = copy_of(client, "Case other settings")
    assert client.put(f"{P}/drivers/levers", json={"scenario": slug, "levers": {"sales_tax_rate": 0.1}}).status_code == 200
    put_cells(client, slug, [(SPEND, "2027-01-01", 1000.0)])
    client.post(f"{P}/modeler/reset", json={"scenario": slug})
    levers = client.get(f"{P}/drivers/levers", query_string={"scenario": slug}).get_json()["levers"]
    assert levers["sales_tax_rate"] == 0.1
    r = client.post(f"{P}/modeler/reset", json={"scenario": "current"})
    assert r.status_code == 400 and "plan of record" in r.get_json()["error"]
    assert client.post(f"{P}/modeler/reset", json={}).status_code == 400


def test_settings_are_audited_validated_and_scenario_specific(restore, client, conn):
    slug = copy_of(client, "Case settings")
    r = client.put(f"{P}/modeler/settings", json={"scenario": slug, "ebitda_addbacks": ["interest_expense", "uncategorized", "uncategorized"]})
    assert r.status_code == 200 and r.get_json()["ebitda_addbacks"] == ["interest_expense", "uncategorized"]
    assert "scenario.params" in audit_actions(conn, slug)
    assert state(client, slug)["settings"]["ebitda_addbacks"] == ["interest_expense", "uncategorized"]
    assert state(client, "current")["settings"]["ebitda_addbacks"] == ["interest_expense"]
    for bad in ("x", ["rent"], [1]):
        assert client.put(f"{P}/modeler/settings", json={"scenario": slug, "ebitda_addbacks": bad}).status_code == 400
    assert client.put(f"{P}/modeler/settings", json={"ebitda_addbacks": []}).status_code == 400
    assert client.put(f"{P}/modeler/settings", json={"scenario": slug, "ebitda_addbacks": []}).status_code == 200
    s = state(client, slug)
    assert s["settings"]["ebitda_addbacks"] == [] and s["outputs"]["ebitda"]["text"] == "EBITDA is net operating income."
    assert all(y["ebitda"] == pytest.approx(y["noi"]) for y in s["outputs"]["years"])


# ------------------------------------------------------------------------------------------------------------------ compare

def test_compare_gives_each_scenarios_saved_yearly_table(restore, client):
    a, b = copy_of(client, "Case A"), copy_of(client, "Case B")
    put_cells(client, a, [(SPEND, m, 600000.0) for m in ("2027-01-01", "2027-02-01")])
    r = client.get(f"{P}/modeler/compare", query_string={"scenarios": f"current,{a},{b}"})
    assert r.status_code == 200
    got = {c["scenario"]["slug"]: c for c in r.get_json()["scenarios"]}
    assert got["current"]["is_plan_of_record"] and not got[a]["is_plan_of_record"]
    for slug in (a, b, "current"):
        assert got[slug]["outputs"]["years"] == state(client, slug)["outputs"]["years"]
    assert got[a]["outputs"]["years"][1]["total_revenue"] != got[b]["outputs"]["years"][1]["total_revenue"]


def test_compare_validation(client):
    assert client.get(f"{P}/modeler/compare").status_code == 400
    assert client.get(f"{P}/modeler/compare", query_string={"scenarios": "a,b,c,d,e,f"}).status_code == 400
    assert client.get(f"{P}/modeler/compare", query_string={"scenarios": "current,nope"}).status_code == 404


# ------------------------------------------------------------------------------------------------------------------ auth and guards

def test_the_endpoints_follow_the_apps_login_and_stay_closed_without_it(mdb):
    app = make_app(mdb)
    c = app.test_client()
    try:
        for method, path, body in (("get", f"{P}/modeler/state", None), ("post", f"{P}/modeler/preview", {"scenario": "current", "preview": True}),
                                   ("get", f"{P}/modeler/compare?scenarios=current", None), ("post", f"{P}/modeler/workspace", {}),
                                   ("post", f"{P}/modeler/save-as", {"source": "current", "name": "x"}), ("post", f"{P}/modeler/reset", {"scenario": "x"}),
                                   ("put", f"{P}/modeler/settings", {"scenario": "x", "ebitda_addbacks": []})):
            r = getattr(c, method)(path, json=body) if body is not None else getattr(c, method)(path)
            assert r.status_code == 401, (method, path)
    finally:
        app.extensions["pool"].close()


def test_noindex_and_the_origin_rule_come_from_the_core_guards(client):
    r = client.get(f"{P}/modeler/state")
    assert "noindex" in r.headers.get("X-Robots-Tag", "")
    bad = client.post(f"{P}/modeler/preview", json={"scenario": "current", "preview": True}, headers={"Origin": "https://evil.example"})
    assert bad.status_code == 403


def test_a_preview_has_its_own_budget_and_does_not_use_up_the_write_limit(mdb):
    """Core rule: a POST whose path ends /preview computes and writes nothing, so it is counted separately (PROJ_RATE_PREVIEWS_PER_MIN, default 600)."""
    app = make_app(mdb, AUTH_DISABLED=True, PROJ_RATE_MUTATIONS_PER_MIN=3, PROJ_RATE_PREVIEWS_PER_MIN=6)
    c = app.test_client()
    try:
        codes = [c.post(f"{P}/modeler/preview", json={"scenario": "current", "preview": True}).status_code for _ in range(8)]
        assert codes[:6] == [200] * 6 and codes[6] == 429                                              # its own limit...
        assert c.post(f"{P}/scenarios", json={"name": "Still allowed"}).status_code == 201             # ...and the write budget is untouched
    finally:
        app.extensions["pool"].close()
