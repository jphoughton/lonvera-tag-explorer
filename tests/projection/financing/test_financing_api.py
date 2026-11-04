"""/api/projection/financing: reads, validation, writes (audit rows, versions, restore), auth."""
from __future__ import annotations

import pytest

from app.projection import registry

P = "/api/projection"
F = f"{P}/financing"


@pytest.fixture(autouse=True)
def only_financing(monkeypatch):
    """Run the engine with this module real and every other module on its fixture stub, so the numbers do not move as other modules land."""
    real = registry.discover

    def discover(package=None, refresh=False):
        return {k: v for k, v in real(package, refresh).items() if k == "financing"}

    monkeypatch.setattr(registry, "discover", discover)


def audit(pg, action=None):
    rows = pg.execute("SELECT * FROM proj_audit ORDER BY id").fetchall()
    return [r for r in rows if action is None or r["action"] == action]


# ------------------------------------------------------------------------------------------------------------------ auth

def test_needs_the_apps_login_and_is_open_when_the_app_is_open(app_factory):
    on, off = app_factory().test_client(), app_factory(AUTH_DISABLED=True).test_client()
    for method, path in (("get", "/cash"), ("get", "/debt"), ("get", "/payback"), ("get", "/inputs"), ("put", "/debt"), ("put", "/inputs")):
        closed = getattr(on, method)(F + path, json={}) if method == "put" else getattr(on, method)(F + path)
        assert closed.status_code == 401, path
        opened = getattr(off, method)(F + path, json={}) if method == "put" else getattr(off, method)(F + path)
        assert opened.status_code != 401, path


# ------------------------------------------------------------------------------------------------------------------ reads

def test_cash_table_payload(client):
    r = client.get(f"{F}/cash?scenario=current")
    assert r.status_code == 200
    j = r.get_json()
    assert len(j["months"]) == 60 and len(j["status"]) == 60
    keys = [row["key"] for row in j["rows"]]
    assert "ending_cash" in keys and "financing.principal_paid" in keys and "inventory_buys" in keys
    row = {x["key"]: x for x in j["rows"]}
    assert row["financing.principal_paid"]["input"] is True and row["ending_cash"]["input"] is False
    i = j["months"].index("2026-09-01")
    assert row["ending_cash"]["values"][i - 1] is None                    # before close: blank, not zero
    assert row["ending_cash"]["values"][i] is not None
    assert all(len(x["values"]) == 60 for x in j["rows"])
    assert j["summary"]["lowest_ending_cash"]["month"]
    assert j["overrides"]["financing.opening_cash"] == ["2026-10-01"]
    assert j["captions"] and len(j["captions"]) == 1
    assert client.get(f"{F}/table?scenario=current").get_json()["months"] == j["months"]


def test_debt_payload(client):
    j = client.get(f"{F}/debt").get_json()
    assert j["terms"] == {"principal": 3_000_000.0, "rate": 0.15, "mode": "typed", "fixed_payment": 125_000.0, "start": "2026-09-01"}
    row = {x["key"]: x for x in j["rows"]}
    i = j["months"].index("2026-10-01")
    assert row["financing.principal_paid"]["values"][i] == 180_000 and row["ending"]["values"][i] == 2_820_000
    assert j["summary"]["total_interest"] == pytest.approx(575_900)
    assert j["seller_note"]["enabled"] is False and j["seller_note"]["payments"] == []
    assert any("$1,000 is still outstanding" in w for w in j["warnings"])


def test_payback_defaults_and_query_overrides(client):
    j = client.get(f"{F}/payback").get_json()
    r = j["result"]
    assert r["months"] == 10 and r["aov"] == pytest.approx(69.156, abs=1e-3) and r["cac"] == pytest.approx(105.4917, abs=1e-3)
    assert len(r["cumulative"]) == 37 and r["retention"][0] is None
    assert j["inputs"]["aov_source"] == "scenario" and j["inputs"]["reference"]["month"]
    fast = client.get(f"{F}/payback?aov=100&cac=60&fulfillment=4&product_cost_pct=0.2&curve=goal").get_json()
    assert fast["curve"] == "goal" and fast["result"]["cm_first"] == pytest.approx(100 - 20 - 4) and fast["result"]["months"] <= 1
    assert fast["inputs"]["aov_source"] == "query"
    assert client.get(f"{F}/payback?max_age=6").get_json()["result"]["ages"] == list(range(7))


@pytest.mark.parametrize("q", ["aov=abc", "aov=0", "cac=-1", "product_cost_pct=2", "curve=other", "max_age=0", "max_age=2.5", "fulfillment=nan"])
def test_payback_rejects_bad_queries(client, q):
    r = client.get(f"{F}/payback?{q}")
    assert r.status_code == 400 and "error" in r.get_json()


def test_matrix_defaults_and_overrides(client):
    j = client.get(f"{F}/matrix").get_json()["result"]
    assert j["target_month"] == 3 and j["basis"] == "cm" and len(j["profit"]) == 19 and len(j["profit"][0]) == 13
    mult = j["cum_retention"] * j["cm_rate"]
    assert j["profit"][0][0] == pytest.approx(200_000 * 0.4 * mult - 200_000)
    assert j["breakeven_roas"] == pytest.approx(1 / mult)
    k = client.get(f"{F}/matrix?target_month=6&basis=loaded&fixed_cost=50000&spends=100000,200000&roas=0.5,1").get_json()["result"]
    assert k["target_month"] == 6 and k["basis"] == "loaded" and k["spends"] == [100000, 200000] and k["roas"] == [0.5, 1.0]
    assert len(k["breakeven_spend"]) == 2
    for q in ("target_month=99", "spends=1,x", "roas=0", "fixed_cost=-1", "basis=weird"):
        assert client.get(f"{F}/matrix?{q}").status_code == 400, q


def test_payout_defaults_and_what_if(client):
    j = client.get(f"{F}/payout").get_json()
    r = j["result"]
    assert j["repeat_source"] == "goal"
    assert r["nc_rev"] == pytest.approx(648_888.89, abs=0.01) and r["cm"] == pytest.approx(-381_855.56, abs=0.01) and r["payback_month"] == 4
    assert r["scales"][0] == 800_000 and r["columns"] == [4, 3, 2]
    assert r["bonus"]["3"][0] == 3_000 and r["bonus"]["4"][0] is None and r["total_comp"]["3"][0] == 8_000
    what = client.get(f"{F}/payout?media_spend=1000000&cpa=70").get_json()["result"]
    assert what["media_spend"] == 1_000_000 and what["selected"]["scale"] == 1_000_000
    assert client.get(f"{F}/payout?cpa=0").status_code == 400
    assert client.get(f"{F}/payout?repeat_source=nope").status_code == 400


def test_inputs_lists_effective_params_defaults_and_series(client):
    j = client.get(f"{F}/inputs").get_json()
    assert j["params"]["debt"]["rate"] == 0.15 and j["scenario_params"]["debt"]["principal"] == 3_000_000
    assert j["defaults"]["payback"]["aov"] is None and j["params"]["payback"]["aov"] == pytest.approx(69.156, abs=1e-3)
    assert any(c["line_key"] == "financing.principal_paid" and c["month"] == "2026-10-01" and c["value"] == 180_000 for c in j["series"])
    assert j["sources"]["debt.principal"].startswith("Term Debt")


def test_unknown_scenario_is_404(client):
    assert client.get(f"{F}/cash?scenario=nope").status_code == 404


# ------------------------------------------------------------------------------------------------------------------ writes

def test_put_debt_payments_are_audited_versioned_and_restorable(client, pg):
    v0 = client.get(f"{P}/scenarios").get_json()["scenarios"][0]["version"]
    r = client.put(f"{F}/debt", json={"scenario": "current", "payments": [{"month": "2026-12-01", "principal_paid": 75000}, {"month": "2027-05-01", "principal_paid": 10000}]})
    assert r.status_code == 200, r.get_json()
    assert r.get_json() == {"version": v0 + 1, "changed": 2}
    rows = audit(pg, "series.update") + audit(pg, "series.set")
    assert {x["line_key"] for x in rows} == {"financing.principal_paid"} and len(rows) == 2
    dec = next(x for x in rows if x["month"].isoformat() == "2026-12-01")
    assert dec["old"]["value"] == 50_000 and dec["new"]["value"] == 75_000 and dec["actor"] == "JoshHoughton" and dec["scenario_slug"] == "current"
    j = client.get(f"{F}/debt").get_json()
    row = {x["key"]: x for x in j["rows"]}
    assert row["financing.principal_paid"]["values"][j["months"].index("2026-12-01")] == 75_000
    assert row["ending"]["values"][j["months"].index("2026-12-01")] == pytest.approx(3_000_000 - 180_000 - 150_000 - 75_000)
    # undo: restore the version before the change
    back = client.post(f"{P}/scenarios/current/restore", json={"version": v0})
    assert back.status_code == 200
    again = client.get(f"{F}/debt").get_json()
    assert {x["key"]: x for x in again["rows"]}["financing.principal_paid"]["values"][again["months"].index("2026-12-01")] == 50_000


def test_put_debt_null_clears_a_typed_payment(client, pg):
    r = client.put(f"{F}/debt", json={"scenario": "current", "payments": [{"month": "2026-10-01", "principal_paid": None}]})
    assert r.status_code == 200 and r.get_json()["changed"] == 1
    assert len(audit(pg, "series.delete")) == 1
    j = client.get(f"{F}/debt").get_json()
    assert {x["key"]: x for x in j["rows"]}["financing.principal_paid"]["values"][j["months"].index("2026-10-01")] == 0


def test_put_debt_terms_and_payments_together(client, pg):
    r = client.put(f"{F}/debt", json={"scenario": "current", "terms": {"rate": 0.12}, "payments": [{"month": "2026-10-01", "principal_paid": 1}]})
    assert r.status_code == 200
    assert len(audit(pg, "scenario.params")) == 1 and len(audit(pg, "series.update")) == 1
    assert client.get(f"{F}/debt").get_json()["terms"]["rate"] == 0.12


def test_put_debt_nothing_changed_writes_nothing(client, pg):
    r = client.put(f"{F}/debt", json={"scenario": "current", "payments": [{"month": "2026-10-01", "principal_paid": 180000}]})
    assert r.status_code == 200 and r.get_json()["changed"] == 0
    assert audit(pg) == []


@pytest.mark.parametrize("body", [
    {},                                                                                                       # no scenario
    {"scenario": "current"},                                                                                  # nothing to write
    {"scenario": "current", "payments": []},
    {"scenario": "current", "payments": "x"},
    {"scenario": "current", "payments": [{"month": "2026-10-15", "principal_paid": 1}]},                      # not the first of the month
    {"scenario": "current", "payments": [{"month": "2031-01-01", "principal_paid": 1}]},                      # off the axis
    {"scenario": "current", "payments": [{"month": "2026-10-01", "principal_paid": -5}]},
    {"scenario": "current", "payments": [{"month": "2026-10-01", "principal_paid": "9"}]},
    {"scenario": "current", "payments": [{"month": "2026-10-01"}]},
    {"scenario": "current", "payments": [{"month": "2026-10-01", "principal_paid": 1, "extra": 1}]},
    {"scenario": "current", "payments": [{"month": "2026-10-01", "principal_paid": 1}] * 2},                  # duplicate cell
    {"scenario": "current", "terms": {"rate": 5}},
    {"scenario": "current", "terms": {"mode": "weekly"}},
    {"scenario": "current", "terms": {"colour": 1}},
    {"scenario": "current", "terms": "x"},
    {"scenario": "current", "payments": [{"month": "2026-10-01", "principal_paid": 1}], "expected_version": "1"},
    {"scenario": "current", "payments": [{"month": "2026-10-01", "principal_paid": 1}], "surprise": 1},
])
def test_put_debt_rejects_bad_bodies_and_writes_nothing(client, pg, body):
    r = client.put(f"{F}/debt", json=body)
    assert r.status_code == 400 and "error" in r.get_json(), body
    assert audit(pg) == []


def test_put_debt_stale_version_conflicts(client, pg):
    r = client.put(f"{F}/debt", json={"scenario": "current", "payments": [{"month": "2026-12-01", "principal_paid": 1}], "expected_version": 99})
    assert r.status_code == 409
    assert audit(pg) == []


def test_put_debt_unknown_scenario_is_404(client):
    assert client.put(f"{F}/debt", json={"scenario": "nope", "payments": [{"month": "2026-12-01", "principal_paid": 1}]}).status_code == 404


def test_put_inputs_merges_validates_and_audits(client, pg):
    r = client.put(f"{F}/inputs", json={"scenario": "current", "params": {"payback": {"aov": 80, "cac": 90}, "matrix": {"target_month": 4}}})
    assert r.status_code == 200
    assert len(audit(pg, "scenario.params")) == 1
    j = client.get(f"{F}/payback").get_json()
    assert j["result"]["aov"] == 80 and j["result"]["cac"] == 90
    assert client.get(f"{F}/matrix").get_json()["result"]["target_month"] == 4
    # null removes the key: back to the default, which is the drivers' last closed month
    r2 = client.put(f"{F}/inputs", json={"scenario": "current", "params": {"payback": {"aov": None, "cac": None}}})
    assert r2.status_code == 200
    again = client.get(f"{F}/payback").get_json()
    assert again["inputs"]["aov_source"] in ("last closed month", None)


@pytest.mark.parametrize("params", [
    None, {}, {"unknown": 1}, {"debt": {"principal": -1}}, {"debt": {"rate": "high"}}, {"debt": {"mode": "x"}}, {"debt": {"nope": 1}},
    {"close_month": "2026-09-15"}, {"close_month": "2040-01-01"}, {"tax": {"payments": [{"month": "2027-09-01"}]}},
    {"tax": {"payments": [{"month": "2027-09-01", "from": "2027-02-01", "to": "2027-01-01"}]}}, {"tax": {"rate": 1.5}},
    {"seller_note": {"installments": 0}}, {"seller_note": {"enabled": "yes"}}, {"matrix": {"spends": []}}, {"matrix": {"roas": [0]}},
    {"payout": {"columns": [{"payback": 4}]}}, {"payout": {"scales": [5, 5]}}, {"payback": {"max_age": 99}}, {"payback": {"curve": "median"}},
    {"cash": {"floor": float("inf")}}, {"debt": []},
])
def test_put_inputs_rejects_bad_params(client, pg, params):
    r = client.put(f"{F}/inputs", json={"scenario": "current", "params": params} if params is not None else {"scenario": "current"})
    assert r.status_code == 400, params
    assert audit(pg) == []


def test_put_inputs_requires_json_and_a_scenario(client):
    assert client.put(f"{F}/inputs", json={"params": {"debt": {"rate": 0.1}}}).status_code == 400
    assert client.put(f"{F}/inputs", data="x", content_type="text/plain").status_code == 415            # the core write guard


def test_origin_must_match_for_writes(client):
    r = client.put(f"{F}/debt", json={"scenario": "current", "payments": [{"month": "2026-12-01", "principal_paid": 1}]}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


def test_generic_series_put_works_for_the_other_input_rows(client, pg):
    items = [{"line_key": "financing.tax", "month": "2027-09-01", "value": 100000, "kind": "override", "note": "typed"},
             {"line_key": "financing.equity_payback", "month": "2027-03-01", "value": 50000, "kind": "input"}]
    assert client.put(f"{P}/series", json={"scenario": "current", "items": items}).status_code == 200
    j = client.get(f"{F}/cash").get_json()
    row = {x["key"]: x for x in j["rows"]}
    assert row["financing.tax"]["values"][j["months"].index("2027-09-01")] == 100_000
    assert row["financing.equity_payback"]["values"][j["months"].index("2027-03-01")] == 50_000
    assert j["overrides"]["financing.tax"] == ["2027-09-01"]
    assert len(audit(pg, "series.set")) == 2


def test_clone_edits_do_not_touch_the_current_plan(client):
    c = client.post(f"{P}/scenarios", json={"name": "Faster paydown", "clone_from": "current"})
    assert c.status_code == 201
    slug = c.get_json()["scenario"]["slug"]
    client.put(f"{F}/debt", json={"scenario": slug, "payments": [{"month": "2026-12-01", "principal_paid": 400000}]})
    a = client.get(f"{F}/debt?scenario={slug}").get_json()
    b = client.get(f"{F}/debt?scenario=current").get_json()
    i = a["months"].index("2026-12-01")
    assert {x["key"]: x for x in a["rows"]}["financing.principal_paid"]["values"][i] == 400_000
    assert {x["key"]: x for x in b["rows"]}["financing.principal_paid"]["values"][i] == 50_000


def test_run_headline_carries_lowest_ending_cash(client):
    j = client.get(f"{P}/run?scenario=current").get_json()
    assert j["modules"]["financing"]["built"] is True and j["modules"]["financing"]["stub"] is False
    assert j["headline"]["lowest_ending_cash"]["month"]


def test_seller_note_appears_in_the_cash_grid_only_when_enabled(client):
    keys = lambda: [r["key"] for r in client.get(f"{F}/cash").get_json()["rows"]]       # noqa: E731
    assert "seller_principal" not in keys()
    assert client.put(f"{F}/inputs", json={"scenario": "current", "params": {"seller_note": {"enabled": True, "ebitda_floor": 1500000}}}).status_code == 200
    cash = client.get(f"{F}/cash").get_json()
    row = {x["key"]: x for x in cash["rows"]}
    i = cash["months"].index("2027-09-01")
    assert row["seller_principal"]["values"][i] == 500_000 and row["seller_interest"]["values"][i] == 75_000
    pays = client.get(f"{F}/debt").get_json()["seller_note"]["payments"]
    assert [p["month"] for p in pays] == ["2027-09-01", "2028-09-01", "2029-09-01"]
    assert all("ttm_noi" in p and p["below_floor"] in (True, False) for p in pays)


def test_noi_before_inventory_row_is_editable_as_an_override(client, pg):
    j = client.get(f"{F}/cash").get_json()
    row = {x["key"]: x for x in j["rows"]}["financing.noi_before_inventory"]
    assert row["input"] is True and row["values"][j["months"].index("2026-09-01")] == 150_000
    put = client.put(f"{P}/series", json={"scenario": "current", "items": [{"line_key": "financing.noi_before_inventory", "month": "2026-09-01", "value": None}]})
    assert put.status_code == 200
    again = {x["key"]: x for x in client.get(f"{F}/cash").get_json()["rows"]}["financing.noi_before_inventory"]
    assert again["values"][j["months"].index("2026-09-01")] != 150_000                 # cleared: back to net operating income + China COGS from the P&L stub
