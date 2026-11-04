"""Acceptance test: for the same scenario, yearly revenue and EBITDA on the modeler equal the P&L page's fiscal-year figures to the cent, for several
scenarios (changed spend, cost per customer and order value; a changed retention model; both together). And the plan rows are the Spend plan page's own rows."""
from __future__ import annotations

import pytest
from conftest import AOV, CPA, P, SPEND, copy_of, fy_months, preview, put_cells, state, years


def pnl_fy(client, slug):
    t = client.get(f"{P}/pnl/table", query_string={"scenario": slug}).get_json()
    ys = t["fy"]["years"]
    return {k: dict(zip(ys, v)) for k, v in t["fy"]["rows"].items()}


def assert_ties_to_the_pnl_page(client, slug):
    s = state(client, slug)
    fy = pnl_fy(client, slug)
    got = years(s)
    assert set(got) == {2026, 2027, 2028, 2029}
    for y, e in got.items():
        assert e["total_revenue"] == pytest.approx(fy["total_revenue"][y], abs=0.005), (slug, y, "revenue")
        assert e["noi"] == pytest.approx(fy["noi"][y], abs=0.005), (slug, y, "noi")
        assert e["ebitda"] == pytest.approx(fy["noi"][y] + fy["interest_expense"][y], abs=0.005), (slug, y, "ebitda")
        assert e["nc_rev"] == pytest.approx(fy["nc_rev"][y], abs=0.005) and e["repeat_rev"] == pytest.approx(fy["repeat_rev"][y], abs=0.005)
        assert e["ad_spend"] == pytest.approx(fy["marketing_spend"][y], abs=0.005)
        assert e["total_revenue"] == pytest.approx(e["nc_rev"] + e["repeat_rev"] + e["other_rev"], abs=0.005)
    run = client.get(f"{P}/run", query_string={"scenario": slug}).get_json()["headline"]
    for y in ("2026", "2027", "2028", "2029"):
        assert got[int(y)]["total_revenue"] == pytest.approx(run["fy"][y]["revenue"], abs=0.005) and got[int(y)]["noi"] == pytest.approx(run["fy"][y]["noi"], abs=0.005)
    assert s["outputs"]["lowest_cash"]["value"] == pytest.approx(run["lowest_ending_cash"]["value"], abs=0.005)
    assert s["outputs"]["lowest_cash"]["month"] == run["lowest_ending_cash"]["month"]
    return s


def test_plan_of_record_ties_to_the_pnl_page(client):
    assert_ties_to_the_pnl_page(client, "current")


def test_changed_spend_cpa_and_aov_tie_to_the_pnl_page(restore, client):
    slug = copy_of(client, "Tie spend")
    base = state(client, slug)
    put_cells(client, slug, [(k, m, v) for m in fy_months("2026-11-01", 38) for k, v in ((SPEND, 550000.0), (CPA, 90.0), (AOV, 72.0))])
    s = assert_ties_to_the_pnl_page(client, slug)
    assert years(s)[2027]["total_revenue"] != pytest.approx(years(base)[2027]["total_revenue"]) and s["outputs"]["averages"]["cpa"] == pytest.approx(90.0)


def test_a_changed_retention_model_ties_to_the_pnl_page(restore, client):
    slug = copy_of(client, "Tie goal")
    assert client.put(f"{P}/cohorts/levers", json={"scenario": slug, "levers": {"curve_source": "goal"}}).status_code == 200
    s = assert_ties_to_the_pnl_page(client, slug)
    assert s["retention"]["curve_source"] == "goal"


def test_an_as_of_curve_with_changed_inputs_ties_to_the_pnl_page(restore, client):
    slug = copy_of(client, "Tie asof")
    month = client.get(f"{P}/cohorts/curve-options", query_string={"scenario": slug, "n": 6}).get_json()["default_asof"]
    assert client.put(f"{P}/cohorts/levers", json={"scenario": slug, "levers": {"curve_source": "asof", "asof_month": month, "n_cohorts": 6}}).status_code == 200
    put_cells(client, slug, [(SPEND, m, 450000.0) for m in fy_months("2027-01-01", 24)] + [(CPA, m, 105.0) for m in fy_months("2027-01-01", 24)])
    s = assert_ties_to_the_pnl_page(client, slug)
    assert s["retention"]["curve_source"] == "asof" and s["retention"]["n_cohorts"] == 6


def test_the_preview_of_each_of_those_ties_to_the_pnl_page_once_saved(restore, client):
    slug = copy_of(client, "Tie preview")
    edits = [{"line_key": k, "month": m, "value": v} for m in fy_months("2027-01-01", 12) for k, v in ((SPEND, 500000.0), (AOV, 75.0))]
    draft = preview(client, slug, edits=edits, retention={"curve_source": "goal"})
    put_cells(client, slug, [(e["line_key"], e["month"], e["value"]) for e in edits])
    client.put(f"{P}/cohorts/levers", json={"scenario": slug, "levers": {"curve_source": "goal"}})
    fy = pnl_fy(client, slug)
    for y, e in years(draft).items():
        assert e["total_revenue"] == pytest.approx(fy["total_revenue"][y], abs=0.005) and e["ebitda"] == pytest.approx(fy["noi"][y] + fy["interest_expense"][y], abs=0.005)


# ------------------------------------------------------------------------------------------------------------------ one source of truth with the Spend plan page

def drivers_row(client, slug, key):
    t = client.get(f"{P}/drivers/table", query_string={"scenario": slug}).get_json()
    return dict(zip(t["months"], next(r for r in t["rows"] if r["key"] == key)["values"]))


def test_an_edit_made_here_shows_on_the_spend_plan_page_and_the_other_way_round(restore, client):
    slug = copy_of(client, "Same series")
    here = state(client, slug)
    put_cells(client, slug, [(SPEND, "2027-04-01", 431000.0), (CPA, "2027-04-01", 88.0), (AOV, "2027-04-01", 73.5)])
    after = state(client, slug)
    for key in (SPEND, CPA, AOV):
        assert drivers_row(client, slug, key)["2027-04-01"] == next(r for r in after["rows"] if r["key"] == key)["values"][after["months"].index("2027-04-01")]
    assert drivers_row(client, slug, SPEND)["2027-04-01"] == 431000.0 and years(after) != years(here)
    r = client.post(f"{P}/drivers/patterns", json={"scenario": slug, "target": "spend_total", "from": "2027-06-01", "to": "2027-08-01",
                                                  "pattern": {"type": "flat", "value": 515000}})
    assert r.status_code == 200, r.get_json()
    s3 = state(client, slug)
    spend = next(r for r in s3["rows"] if r["key"] == SPEND)["values"]
    assert [spend[s3["months"].index(m)] for m in ("2027-06-01", "2027-07-01", "2027-08-01")] == [515000.0] * 3


def test_every_input_cell_equals_the_spend_plan_pages_cell_for_the_same_scenario(restore, client):
    slug = copy_of(client, "Same grid")
    put_cells(client, slug, [(SPEND, "2027-02-01", 612345.0)])
    s = state(client, slug)
    for key in (SPEND, CPA, AOV):
        mine = dict(zip(s["months"], next(r for r in s["rows"] if r["key"] == key)["values"]))
        theirs = drivers_row(client, slug, key)
        assert all(mine[m] == pytest.approx(theirs[m]) for m in s["months"]), key


def test_an_instruction_here_writes_the_same_cells_the_spend_plan_pages_pattern_does(restore, client):
    a, b = copy_of(client, "Same op A"), copy_of(client, "Same op B")
    out = preview(client, a, op={"type": "fill", "row": "nc_cpa", "from": "2027-01-01", "to": "2027-06-01", "value": 92.5})
    put_cells(client, a, [(e["line_key"], e["month"], e["value"]) for e in out["applied"]["edits"]])
    assert client.post(f"{P}/drivers/patterns", json={"scenario": b, "target": "nc_cpa", "from": "2027-01-01", "to": "2027-06-01", "pattern": {"type": "flat", "value": 92.5}}).status_code == 200
    assert years(state(client, a)) == years(state(client, b)) and drivers_row(client, a, CPA) == drivers_row(client, b, CPA)
