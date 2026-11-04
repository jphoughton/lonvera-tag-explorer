"""HTTP layer: auth, purchase-order and transfer CRUD with validation and audit rows, stock counts, price lists, plan suggest / accept, grid payloads."""
from __future__ import annotations

from datetime import date

import pytest

import inv_supply_support as S
from app.projection import engine as proj_engine

P = "/api/projection"
B = f"{P}/inventory-supply"


def po_body(**kw):
    b = {"product": "bull", "vendor": "China", "warehouse": "intl", "units": 5000, "land_date": "2027-03-01", "status": "planned"}
    b.update(kw)
    return b


def audit_rows(pg, action_prefix="inventory_supply"):
    return pg.execute("SELECT * FROM proj_audit WHERE action LIKE %s ORDER BY id", (action_prefix + "%",)).fetchall()


def vendors(client):
    client.put(f"{B}/vendors/China", json={"lead_weeks": 8, "moq_units": 5000, "default_warehouse": "intl"})
    client.put(f"{B}/vendor-costs", json={"vendor": "China", "product": "bull", "unit_cost": 2.26, "deposit_pct": 0.4})


# ---------------------------------------------------------------------------------------------------------------- auth and wiring

def test_login_on_gives_401_everywhere_and_auth_disabled_is_open(anon, app_factory):
    for method, path in (("get", "summary"), ("get", "po"), ("post", "po"), ("post", "stock"), ("post", "plan/accept"), ("put", "vendors/X")):
        r = getattr(anon, method)(f"{B}/{path}", **({} if method == "get" else {"json": {}}))
        assert r.status_code == 401, (method, path)
    open_app = app_factory(AUTH_DISABLED=True, APP_USERS={})
    c = open_app.test_client()
    assert c.get(f"{B}/summary").status_code == 200
    assert c.post(f"{B}/po", json=po_body()).status_code == 201                      # same guard rails, no cookie needed


def test_module_is_registered_built_and_runs_for_real(client):
    mods = {m["key"]: m for m in client.get(f"{P}/modules").get_json()["modules"]}
    assert mods["inventory_supply"] == {"key": "inventory_supply", "label": "Inventory supply", "group": "P2", "built": True, "ui": "inventory_supply.js"}
    run = client.get(f"{P}/run").get_json()["modules"]["inventory_supply"]
    assert run["built"] is True and run["stub"] is False and "error" not in run


def test_json_payloads_use_iso_dates_and_plain_numbers(client, seeded, with_demand):
    j = client.get(f"{B}/po").get_json()
    p = next(x for x in j["pos"] if x["id"] == 43)
    assert p["land_date"] == "2027-03-01" and isinstance(p["po_value"], float) and isinstance(p["flags"], list)
    assert isinstance(j["today"], str) and j["today"] == "2026-11-03"


# ---------------------------------------------------------------------------------------------------------------- purchase orders

def test_po_create_update_delete_roundtrip_is_audited(client, pg):
    vendors(client)
    r = client.post(f"{B}/po", json=po_body(units=10000))
    assert r.status_code == 201
    po = r.get_json()["po"]
    assert po["id"] >= 1000 and po["source"] == "manual" and po["land_date"] == "2027-03-01"
    row = next(x for x in client.get(f"{B}/po").get_json()["pos"] if x["id"] == po["id"])
    assert row["po_value"] == pytest.approx(22600) and row["order_date"] == "2027-01-04" and row["down_payment"] == pytest.approx(9040)
    r = client.patch(f"{B}/po/{po['id']}", json={"units": 12000, "status": "ordered", "note": "confirmed with vendor"})
    assert r.status_code == 200 and r.get_json()["po"]["units"] == 12000.0
    assert client.patch(f"{B}/po/{po['id']}", json={"units": 12000}).status_code == 200                # unchanged: no audit row
    assert client.delete(f"{B}/po/{po['id']}").status_code == 200
    assert client.delete(f"{B}/po/{po['id']}").status_code == 404
    rows = [a for a in audit_rows(pg) if a["action"].startswith("inventory_supply.po.")]
    assert [a["action"] for a in rows] == ["inventory_supply.po.create", "inventory_supply.po.update", "inventory_supply.po.delete"]
    assert rows[0]["old"] is None and rows[0]["new"]["units"] == 10000 and rows[0]["line_key"] == f"inventory_supply.po:{po['id']}"
    assert rows[1]["old"] == {"units": 10000.0, "status": "planned", "note": None} and rows[1]["new"]["status"] == "ordered"
    assert rows[2]["old"]["units"] == 12000.0 and rows[2]["new"] is None and rows[0]["actor"] == "JoshHoughton"


@pytest.mark.parametrize("patch", [
    {"units": 0}, {"units": -5}, {"units": "10"}, {"units": True}, {"units": 1e12}, {"status": "bogus"}, {"land_date": "2027-13-01"}, {"land_date": "nope"},
    {"land_date": "2031-01-01"}, {"land_date": None}, {"order_date": "2027-04-01"}, {"deposit_pct": 1.5}, {"deposit_pct": -0.1}, {"unit_cost": -1},
    {"warehouse": "mars"}, {"product": "unicorn"}, {"vendor": ""}, {"vendor": "x" * 61}, {"link": "ftp://x"}, {"note": "x" * 501}, {"bogus": 1},
    {"source": "auto-plan"}, {"deposit_paid": "maybe"}])
def test_po_create_rejects_invalid_input_and_writes_nothing(client, pg, patch):
    body = po_body()
    if patch.get("land_date", 1) is None:
        body.pop("land_date")
        body.update({k: v for k, v in patch.items() if k != "land_date"})
    else:
        body.update(patch)
    r = client.post(f"{B}/po", json=body)
    assert r.status_code == 400 and "error" in r.get_json()
    assert pg.execute("SELECT count(*) AS n FROM proj_po").fetchone()["n"] == 0 and audit_rows(pg) == []


def test_po_patch_validates_the_merged_row_and_unknown_id_is_404(client, pg):
    vendors(client)
    po = client.post(f"{B}/po", json=po_body(order_date="2027-02-01")).get_json()["po"]
    assert client.patch(f"{B}/po/{po['id']}", json={"land_date": "2027-01-15"}).status_code == 400       # now before the typed order date
    assert client.patch(f"{B}/po/{po['id']}", json={"status": "bogus"}).status_code == 400
    assert client.patch(f"{B}/po/999999", json={"units": 5}).status_code == 404
    assert client.patch(f"{B}/po/{po['id']}", json={"order_date": None, "land_date": "2027-01-15"}).status_code == 200   # clearing the typed date is allowed
    assert pg.execute("SELECT order_date FROM proj_po WHERE id = %s", (po["id"],)).fetchone()["order_date"] is None


def test_po_accepts_sheet_style_names_for_product_and_warehouse(client):
    r = client.post(f"{B}/po", json=po_body(product="Vital Bull", warehouse="International 3PL"))
    assert r.status_code == 201 and r.get_json()["po"]["product"] == "bull" and r.get_json()["po"]["warehouse"] == "intl"


def test_json_content_type_is_enforced_by_the_core_guard(client):
    assert client.post(f"{B}/po", data="x", content_type="text/plain").status_code == 415


# ---------------------------------------------------------------------------------------------------------------- transfers

def test_transfer_crud_validation_and_audit(client, pg):
    body = {"product": "liver", "units": 3000, "ship_date": "2027-01-10", "receive_date": "2027-01-25"}
    r = client.post(f"{B}/transfers", json=body)
    assert r.status_code == 201
    t = r.get_json()["transfer"]
    assert client.post(f"{B}/transfers", json={**body, "receive_date": "2027-01-05"}).status_code == 400          # arrives before it ships
    assert client.post(f"{B}/transfers", json={**body, "units": 0}).status_code == 400
    assert client.post(f"{B}/transfers", json={**body, "status": "lost"}).status_code == 400
    assert client.post(f"{B}/transfers", json={**body, "bogus": 1}).status_code == 400
    assert client.patch(f"{B}/transfers/{t['id']}", json={"status": "shipped", "units": 3500}).status_code == 200
    assert client.patch(f"{B}/transfers/{t['id']}", json={"ship_date": "2027-02-01"}).status_code == 400
    lst = client.get(f"{B}/transfers").get_json()["transfers"]
    assert [x["units"] for x in lst if x["id"] == t["id"]] == [3500.0] and lst[0]["in_transit_days"] == 15
    assert client.delete(f"{B}/transfers/{t['id']}").status_code == 200
    assert [a["action"] for a in audit_rows(pg, "inventory_supply.transfer")] == ["inventory_supply.transfer.create", "inventory_supply.transfer.update",
                                                                              "inventory_supply.transfer.delete"]
    assert pg.execute("SELECT count(*) AS n FROM proj_fba_transfer").fetchone()["n"] == 0


# ---------------------------------------------------------------------------------------------------------------- stock, vendors

def test_stock_counts_append_by_day_and_a_second_count_for_the_day_replaces_it(client, pg):
    b = {"location": "amazon", "product": "bull", "as_of": "2026-11-02", "on_hand": 3100}
    assert client.post(f"{B}/stock", json=b).status_code == 201
    assert client.post(f"{B}/stock", json={**b, "as_of": "2026-11-03", "on_hand": 2900, "note": "Seller Central"}).status_code == 201
    assert client.post(f"{B}/stock", json={**b, "as_of": "2026-11-03", "on_hand": 2800}).status_code == 201
    rows = pg.execute("SELECT as_of, on_hand FROM proj_stock_snapshot ORDER BY as_of").fetchall()
    assert [(r["as_of"], float(r["on_hand"])) for r in rows] == [(date(2026, 11, 2), 3100.0), (date(2026, 11, 3), 2800.0)]
    latest = client.get(f"{B}/stock").get_json()["latest"]
    assert latest == [{"location": "amazon", "product": "bull", "as_of": "2026-11-03", "on_hand": 2800.0, "allocated": 0.0, "inbound_per_3pl": 0.0,
                       "estimate": False, "note": None, "opening": 2800.0}]
    assert len(audit_rows(pg, "inventory_supply.stock")) == 3                       # create, create, replace: an identical re-post would add none
    client.post(f"{B}/stock", json={**b, "as_of": "2026-11-03", "on_hand": 2800})
    assert len(audit_rows(pg, "inventory_supply.stock")) == 3


@pytest.mark.parametrize("patch", [{"location": "mars"}, {"product": "x"}, {"as_of": "2026-13-40"}, {"as_of": "2030-01-01"}, {"on_hand": "5"}, {"on_hand": None},
                                   {"allocated": -1}, {"estimate": "yes"}, {"bogus": 1}])
def test_stock_count_validation(client, pg, patch):
    body = {"location": "amazon", "product": "bull", "as_of": "2026-11-03", "on_hand": 100}
    body.update(patch)
    assert client.post(f"{B}/stock", json=body).status_code == 400
    assert pg.execute("SELECT count(*) AS n FROM proj_stock_snapshot").fetchone()["n"] == 0


def test_vendor_price_list_and_reorder_parameters_are_validated_and_audited(client, pg):
    assert client.put(f"{B}/vendor-costs", json={"vendor": "Nobody", "product": "bull", "unit_cost": 1}).status_code == 400          # needs a lead time first
    assert client.put(f"{B}/vendors/China", json={"lead_weeks": 0}).status_code == 400
    assert client.put(f"{B}/vendors/China", json={"lead_weeks": 8, "default_warehouse": "mars"}).status_code == 400
    assert client.put(f"{B}/vendors/China", json={"lead_weeks": 8, "moq_units": 5000, "default_warehouse": "intl"}).status_code == 200
    assert client.put(f"{B}/vendor-costs", json={"vendor": "China", "product": "bull", "unit_cost": -2}).status_code == 400
    assert client.put(f"{B}/vendor-costs", json={"vendor": "China", "product": "bull", "unit_cost": 2.26, "deposit_pct": 0.4}).status_code == 200
    assert client.put(f"{B}/reorder-params", json={"location": "intl", "product": "bull", "lead_days": 60, "safety_days": 14}).status_code == 200
    assert client.put(f"{B}/reorder-params", json={"location": "amazon", "product": "bull", "lead_days": 60}).status_code == 400
    assert client.put(f"{B}/product-costs", json={"product": "bull", "china_unit_cost": 2.26, "sku": "LV-VBULL"}).status_code == 200
    v = client.get(f"{B}/vendors").get_json()
    assert v["vendors"][0]["lead_weeks"] == 8.0 and v["vendor_costs"][0]["unit_cost"] == 2.26 and v["reorder_params"][0]["lead_days"] == 60
    assert len(audit_rows(pg)) == 4


# ---------------------------------------------------------------------------------------------------------------- plan

def test_plan_suggest_writes_nothing_and_accept_replaces_only_auto_rows(client, pg, seeded, with_demand):
    before = pg.execute("SELECT (SELECT count(*) FROM proj_po) AS p, (SELECT count(*) FROM proj_fba_transfer) AS t, (SELECT count(*) FROM proj_audit) AS a").fetchone()
    plan = client.get(f"{B}/plan/suggest").get_json()
    assert pg.execute("SELECT (SELECT count(*) FROM proj_po) AS p, (SELECT count(*) FROM proj_fba_transfer) AS t, (SELECT count(*) FROM proj_audit) AS a"
                      ).fetchone() == before
    assert plan["pos"] and plan["summary"]["removed_pos"] == 36 and plan["summary"]["removed_transfers"] == 34 and plan["removed"]["pos"][0]["id"] == 43
    assert all(p["source"] == "auto-plan" and p["units"] % 1000 == 0 for p in plan["pos"])
    manual_before = pg.execute("SELECT count(*) AS n FROM proj_po WHERE source = 'manual'").fetchone()["n"]
    body = {"pos": plan["pos"], "transfers": plan["transfers"], "replace_auto": True,
            "expect_replaced": {"pos": plan["summary"]["removed_pos"], "transfers": plan["summary"]["removed_transfers"]}}
    r = client.post(f"{B}/plan/accept", json=body)
    assert r.status_code == 201, r.get_json()
    out = r.get_json()
    assert out["removed_pos"] == 36 and len(out["added_pos"]) == len(plan["pos"]) and len(out["added_transfers"]) == len(plan["transfers"])
    assert pg.execute("SELECT count(*) AS n FROM proj_po WHERE source = 'manual'").fetchone()["n"] == manual_before             # Josh's rows are untouched
    assert pg.execute("SELECT count(*) AS n FROM proj_po WHERE source = 'auto-plan'").fetchone()["n"] == len(plan["pos"])
    acts = [a["action"] for a in audit_rows(pg)]
    assert acts.count("inventory_supply.po.delete") == 36 and acts.count("inventory_supply.po.create") == len(plan["pos"]) and "inventory_supply.plan.accept" in acts
    again = client.get(f"{B}/plan/suggest").get_json()
    assert again["summary"]["removed_pos"] == len(plan["pos"])


def test_plan_accept_refuses_a_stale_plan_missing_confirmation_and_bad_rows_without_changing_anything(client, pg, seeded):
    n_auto = pg.execute("SELECT count(*) AS n FROM proj_po WHERE source = 'auto-plan'").fetchone()["n"]
    good = po_body()
    assert client.post(f"{B}/plan/accept", json={"pos": [good], "replace_auto": True}).status_code == 400            # expect_replaced is required
    r = client.post(f"{B}/plan/accept", json={"pos": [good], "replace_auto": True, "expect_replaced": {"pos": 1, "transfers": 0}})
    assert r.status_code == 409 and "plan_stale" in r.get_json()["error"]
    r = client.post(f"{B}/plan/accept", json={"pos": [good, po_body(units=0)], "replace_auto": True, "expect_replaced": {"pos": n_auto, "transfers": 34}})
    assert r.status_code == 400
    assert client.post(f"{B}/plan/accept", json={}).status_code == 400
    assert client.post(f"{B}/plan/accept", json={"pos": [good] * 201}).status_code == 400
    assert client.post(f"{B}/plan/accept", json={"pos": [good], "bogus": 1}).status_code == 400
    assert pg.execute("SELECT count(*) AS n FROM proj_po WHERE source = 'auto-plan'").fetchone()["n"] == n_auto           # the failed accept rolled back
    assert audit_rows(pg, "inventory_supply.po.delete") == []


# ---------------------------------------------------------------------------------------------------------------- grids and headline

def test_burn_grids_match_the_sheet_and_carry_status_chips(client, seeded, with_demand):
    t = client.get(f"{B}/burn?location=revamp").get_json()
    assert len(t["months"]) == 38 and t["months"][0] == "2026-11-01" and set(t["status"]) == {"projected"}
    rows = {r["key"]: r for r in t["rows"]}
    assert rows["revamp.bull.closing"]["values"][:3] == [31581.0, 89614.85714285714, 82648.71428571429] and rows["revamp.bull.closing"]["kind"] == "subtotal"
    assert rows["revamp.bull.status"]["fmt"] == "text" and "OUT" in rows["revamp.bull.status"]["values"] or "LOW" in rows["revamp.bull.status"]["values"]
    assert rows["revamp.bull.h"]["kind"] == "header" and rows["revamp.bull.out"]["values"][0] == 1500.0
    assert client.get(f"{B}/burn?location=mars").status_code == 400
    a = {r["key"]: r for r in client.get(f"{B}/burn?location=amazon").get_json()["rows"]}
    assert "amazon.bull.to_send" in a and a["amazon.bull.to_send"]["values"][0] == pytest.approx(254.15, abs=0.01)
    assert "total.flow.closing" in {r["key"] for r in client.get(f"{B}/burn?location=total").get_json()["rows"]}


def test_outlay_and_value_grids(client, seeded, with_demand):
    o = client.get(f"{B}/outlay").get_json()
    assert len(o["months"]) == 60 and o["status"][0] == "actual" and o["status"][-1] == "projected"
    rows = {r["key"]: r for r in o["rows"]}
    assert sum(rows["total"]["values"]) == pytest.approx(4533370) and rows["cumulative"]["values"][-1] == pytest.approx(4533370)
    v = client.get(f"{B}/value").get_json()
    assert {r["key"] for r in v["rows"]} >= {"revamp.total", "amazon.total", "intl.total", "all.total"}
    assert next(r for r in v["rows"] if r["key"] == "all.total")["values"][0] == pytest.approx(413658.23)


def test_reorder_warnings_summary_and_freshness_endpoints(client, seeded, with_demand):
    ro = client.get(f"{B}/reorder").get_json()["reorder"]
    assert ro[0]["status"] == "OVERDUE" and ro[0]["product_name"] and ro[0]["location_name"] and "serial" not in ro[0]
    codes = {w["code"] for w in client.get(f"{B}/warnings").get_json()["warnings"]}
    assert {"landed_still_inbound", "past_land_not_landed", "transfer_receive_before_ship", "stock_stale", "zero_product_cost"} <= codes
    s = client.get(f"{B}/summary").get_json()
    assert s["tldr"]["first_stockout"]["location"] == "intl" and s["tldr"]["oldest_stock"]["location"] == "amazon" and s["tldr"]["cash_next_90_days"] > 0
    assert s["demand_stub"] is False and s["freshness"][0]["feed"] == "purchase_orders"
    fr = {f["feed"]: f for f in client.get(f"{B}/freshness").get_json()["freshness"]}
    assert fr["stock_fba"]["status"] == "STALE" and fr["stock_revamp"]["status"] == "fresh"


def test_engine_run_gives_the_financing_module_the_cash_outlay(client, pg, seeded):
    eng = proj_engine.run("current", pg)
    res = eng.results["inventory_supply"]
    assert not eng.stubbed.get("inventory_supply") and sum(res.outlay.values()) == pytest.approx(4533370)
    assert res.outlay[date(2026, 12, 1)] > 0 and set(res.outlay) == set(proj_engine.MONTHS)


GRID_FMTS = {"usd", "usd0", "pct", "num", "x", "text", "date"}
GRID_KINDS = {"input", "calc", "subtotal", "total", "header", "ratio"}


@pytest.mark.parametrize("path", ["burn?location=revamp", "burn?location=amazon", "burn?location=intl", "burn?location=total", "value", "outlay", "table"])
def test_every_grid_payload_has_the_shape_the_shared_grid_reads(client, seeded, with_demand, path):
    t = client.get(f"{B}/{path}").get_json()
    n = len(t["months"])
    assert n in (38, 60) and len(t["status"]) == n and t["captions"] and len(t["captions"]) == 1
    keys = [r["key"] for r in t["rows"]]
    assert len(keys) == len(set(keys))
    for r in t["rows"]:
        assert len(r["values"]) == n and r["fmt"] in GRID_FMTS and r["kind"] in GRID_KINDS and r["input"] is False and r["label"]
        assert all(v is None or isinstance(v, (int, float, str)) for v in r["values"])
        if r["kind"] != "header" and r["fmt"] != "text":
            assert all(v is None or isinstance(v, (int, float)) for v in r["values"])


def test_the_ui_file_the_module_declares_exists_and_is_served(client):
    from app.projection import registry
    ui = registry.listing()
    page = next(m["ui"] for m in ui if m["key"] == "inventory_supply")
    assert page == "inventory_supply.js"
    r = client.get(f"/static/js/projection/{page}")
    assert r.status_code == 200 and b"export async function render(el, ctx)" in r.data


def test_the_burn_demand_row_is_labelled_for_what_it_counts(client, seeded, with_demand):
    for loc in ("revamp", "amazon", "intl", "total"):
        t = client.get(f"{B}/burn?location={loc}").get_json()
        demand_rows = [r for r in t["rows"] if r["key"].endswith(".demand")]
        assert demand_rows and {r["label"] for r in demand_rows} == {"Demand still to ship after today's count"}
        assert t["captions"] == ["Demand is what is still to ship after today's count: this month's forecast minus units already shipped."]
    row = {r["key"]: r for r in client.get(f"{B}/burn?location=revamp").get_json()["rows"]}["revamp.bull.demand"]
    assert row["values"][0] == 6292.0 and row["values"][1] == pytest.approx(6966.142857142858)               # 6,996 forecast minus 704 already shipped


def test_a_degraded_demand_is_a_503_not_zeros_but_edits_still_work(client, pg, seeded, monkeypatch):
    from app.projection.inventory_demand import store as demand_store

    def boom(conn):
        raise RuntimeError("warehouse feed unreadable")

    monkeypatch.setattr(demand_store, "read_inputs", boom)
    for path in ("summary", "burn?location=revamp", "reorder", "po", "outlay", "value", "warnings", "table", "plan/suggest", "transfers"):
        r = client.get(f"{B}/{path}")
        assert r.status_code == 503, (path, r.status_code)
        body = r.get_json()
        assert body["error"] == "upstream_degraded" and {m["module"] for m in body["modules"]} >= {"inventory_demand"}
    assert client.get(f"{B}/history").status_code == 200 and client.get(f"{B}/variables").status_code == 200       # no engine needed
    assert client.post(f"{B}/po", json=po_body()).status_code == 201                                             # the data can still be corrected


def test_defaults_for_a_new_reorder_row_come_from_the_default_variables(client, pg):
    from app.projection.inventory_supply.model import ReorderParam
    with pytest.raises(TypeError):
        ReorderParam("revamp", "bull")                                                                           # no literal default left in the model
    client.put(f"{B}/variables", json={"scenario": "current", "items": [{"kind": "lever", "name": "default_safety_days", "value": 21}]})
    r = client.put(f"{B}/reorder-params", json={"location": "revamp", "product": "belle", "lead_days": 40})
    assert r.status_code == 200 and r.get_json()["reorder_param"]["safety_days"] == 21
    bear = client.post(f"{P}/scenarios", json={"name": "Bear", "clone_from": "current"}).get_json()["scenario"]["slug"]
    client.put(f"{B}/variables", json={"scenario": bear, "items": [{"kind": "lever", "name": "default_safety_days", "value": 9}]})
    assert client.put(f"{B}/reorder-params?scenario={bear}", json={"location": "intl", "product": "flow", "lead_days": 30}).status_code == 200
    rp = next(x for x in client.get(f"{B}/vendors?scenario={bear}").get_json()["reorder_params"] if x["product"] == "flow")
    assert rp["safety_days"] == 9 and rp["lead_days"] == 30
