"""The draft run: previewing equals saving, nothing is written, closed months and bad numbers are refused, retention and EBITDA behave."""
from __future__ import annotations

import threading
import time
from datetime import date

import psycopg
import pytest
from conftest import AOV, CPA, DB_URL, P, SPEND, copy_of, fy_months, preview, put_cells, state, years
from psycopg.rows import dict_row

from app.projection import series
from app.projection.modeler import model, overlay
from app.queries import ApiError

NUMBERS = ("total_revenue", "nc_rev", "repeat_rev", "other_rev", "ad_spend", "noi", "ebitda")


def edits_for(spend=None, cpa=None, aov=None, start="2026-11-01", n=38):
    out = []
    for m in fy_months(start, n):
        for key, v in ((SPEND, spend), (CPA, cpa), (AOV, aov)):
            if v is not None:
                out.append({"line_key": key, "month": m, "value": v})
    return out


def counts(conn, slug):
    sid = conn.execute("SELECT id FROM proj_scenarios WHERE slug = %s", (slug,)).fetchone()["id"]
    return (conn.execute("SELECT count(*) AS n FROM proj_series WHERE scenario_id = %s", (sid,)).fetchone()["n"],
            conn.execute("SELECT count(*) AS n FROM proj_audit").fetchone()["n"],
            conn.execute("SELECT count(*) AS n FROM proj_scenario_history").fetchone()["n"],
            conn.execute("SELECT version FROM proj_scenarios WHERE id = %s", (sid,)).fetchone()["version"])


def same_numbers(a: dict, b: dict):
    ya, yb = years(a), years(b)
    assert ya.keys() == yb.keys()
    for y in ya:
        for k in NUMBERS:
            assert ya[y][k] == pytest.approx(yb[y][k], abs=0.005), (y, k)
    assert (a["outputs"]["lowest_cash"] or {}).get("value") == pytest.approx((b["outputs"]["lowest_cash"] or {}).get("value", 0), abs=0.005)


def asof_month(client, slug, n=6):
    opts = client.get(f"{P}/cohorts/curve-options", query_string={"scenario": slug, "n": n}).get_json()
    return opts["default_asof"]


# ------------------------------------------------------------------------------------------------------------------ preview == saved

def test_the_preview_of_spend_cpa_and_aov_equals_what_saving_them_gives(client, restore):
    slug = copy_of(client, "Case spend")
    edits = edits_for(spend=550000, cpa=90, aov=72)
    draft = preview(client, slug, edits=edits)
    assert draft["draft"] is True
    before = state(client, slug)
    assert years(before)[2027]["total_revenue"] != pytest.approx(years(draft)[2027]["total_revenue"])           # nothing was saved by the preview
    put_cells(client, slug, [(e["line_key"], e["month"], e["value"]) for e in edits])
    saved = state(client, slug)
    same_numbers(draft, saved)
    assert saved["draft"] is False and saved["outputs"]["averages"]["spend_per_month"] == pytest.approx(550000)


def test_the_preview_of_a_retention_choice_equals_saving_it(client, restore):
    slug = copy_of(client, "Case goal")
    draft = preview(client, slug, retention={"curve_source": "goal"})
    r = client.put(f"{P}/cohorts/levers", json={"scenario": slug, "levers": {"curve_source": "goal"}})
    assert r.status_code == 200, r.get_data(as_text=True)
    saved = state(client, slug)
    same_numbers(draft, saved)
    assert saved["retention"]["curve_source"] == "goal" and saved["retention"]["lifetime"] == pytest.approx(draft["retention"]["lifetime"])


def test_the_preview_of_an_as_of_curve_with_six_cohorts_and_a_ramp_equals_saving_them(client, restore):
    slug = copy_of(client, "Case asof")
    month = asof_month(client, slug, 6)
    ret = {"curve_source": "asof", "asof_month": month, "n_cohorts": 6}
    op = {"type": "ramp", "row": "spend_total", "from": "2026-11-01", "to": "2027-12-01", "end_value": 600000}
    draft = preview(client, slug, retention=ret, op=op)
    assert draft["applied"]["count"] == 14 and draft["retention"]["curve_source"] == "asof" and draft["retention"]["n_cohorts"] == 6
    put_cells(client, slug, [(e["line_key"], e["month"], e["value"]) for e in draft["applied"]["edits"]])
    assert client.put(f"{P}/cohorts/levers", json={"scenario": slug, "levers": ret}).status_code == 200
    same_numbers(draft, state(client, slug))


# ------------------------------------------------------------------------------------------------------------------ nothing is written

def test_a_preview_writes_nothing(restore, client, conn):
    slug = copy_of(client, "Case quiet")
    before = counts(conn, slug)
    for body in ({"edits": edits_for(spend=700000)}, {"op": {"type": "percent", "row": "spend_total", "from": "2027-01-01", "to": "2027-12-01", "pct": 10}},
                 {"retention": {"curve_source": "goal"}}, {"edits": edits_for(cpa=80), "retention": {"curve_source": "goal"}}):
        preview(client, slug, **body)
    conn.rollback()
    assert counts(conn, slug) == before


def test_the_model_runs_on_a_read_only_connection(restore, ro):
    out = model.run_state(ro, "current", edits=[{"line_key": SPEND, "month": "2027-01-01", "value": 1.0}], op={"type": "fill", "row": "nc_cpa", "from": "2027-01-01", "to": "2027-03-01", "value": 80},
                          retention={"curve_source": "goal"}, with_por=True)
    assert out["draft"] and out["outputs"]["years"]


def test_a_draft_is_only_visible_to_the_request_that_made_it(conn, mdb):
    draft = overlay.Draft("current", None, {(SPEND, date(2027, 1, 1)): (123.0, "input")})
    seen = {}

    def other():
        with psycopg.connect(DB_URL, row_factory=dict_row) as c:
            seen["other"] = series.get_series(c, "current", [SPEND])[SPEND][date(2027, 1, 1)]

    with overlay.active(draft):
        mine = series.get_series(conn, "current", [SPEND])[SPEND][date(2027, 1, 1)]
        t = threading.Thread(target=other)
        t.start()
        t.join()
        time.sleep(0.01)
    assert mine == 123.0 and seen["other"] == 350000.0
    assert series.get_series(conn, "current", [SPEND])[SPEND][date(2027, 1, 1)] == 350000.0               # and gone once the block ends


# ------------------------------------------------------------------------------------------------------------------ refusals

def locked_month(payload, key):
    codes = payload["cellmeta"][key]
    return next(m for m in payload["months"] if codes.get(m) == "actual")


def test_an_edit_in_a_closed_month_is_refused_and_names_the_month(client, restore):
    slug = copy_of(client, "Case closed")
    s = state(client, slug)
    for key in (CPA, AOV):
        m = locked_month(s, key)
        r = client.post(f"{P}/modeler/preview", json={"scenario": slug, "preview": True, "edits": [{"line_key": key, "month": m, "value": 50}]})
        assert r.status_code == 400 and "loaded figures" in r.get_json()["error"], r.get_json()
    spend_locked = locked_month(s, SPEND)
    assert client.post(f"{P}/modeler/preview", json={"scenario": slug, "preview": True, "edits": [{"line_key": SPEND, "month": spend_locked, "value": 1}]}).status_code == 400


def test_a_zero_cpa_and_other_out_of_range_numbers_are_a_plain_400(client):
    for edits, fragment in (([{"line_key": CPA, "month": "2027-01-01", "value": 0}], "Cost per new customer for Jan 2027"),
                            ([{"line_key": AOV, "month": "2027-01-01", "value": -3}], "Average order value"),
                            ([{"line_key": SPEND, "month": "2027-01-01", "value": -1}], "Ad spend")):
        r = client.post(f"{P}/modeler/preview", json={"scenario": "current", "preview": True, "edits": edits})
        assert r.status_code == 400 and fragment in r.get_json()["error"]


def test_an_instruction_over_closed_and_planned_months_skips_the_closed_ones(client, restore):
    slug = copy_of(client, "Case skip")
    out = preview(client, slug, op={"type": "fill", "row": "nc_cpa", "from": "2026-08-01", "to": "2026-12-01", "value": 88})
    assert out["applied"]["skipped"] == ["2026-08-01", "2026-09-01", "2026-10-01"] and out["applied"]["count"] == 2
    assert "Skipped Aug 2026, Sep 2026, Oct 2026" in out["applied"]["text"]


# ------------------------------------------------------------------------------------------------------------------ blank months, ramps, percents

def grid_value(payload, key, month):
    row = next(r for r in payload["rows"] if r["key"] == key)
    return row["values"][payload["months"].index(month)]


def test_taking_a_typed_cell_away_carries_the_month_before(client, restore):
    slug = copy_of(client, "Case blank")
    put_cells(client, slug, [(SPEND, "2027-02-01", 800000.0)])
    out = preview(client, slug, edits=[{"line_key": SPEND, "month": "2027-03-01", "value": None}])
    assert grid_value(out, SPEND, "2027-03-01") == 800000.0 and out["cellmeta"][SPEND]["2027-03-01"] == "carried"
    assert grid_value(state(client, slug), SPEND, "2027-03-01") == 350000.0                              # still typed until saved


def test_a_percent_change_moves_the_years_ad_spend_by_that_percent(client, restore):
    slug = copy_of(client, "Case percent")
    base = years(state(client, slug))[2027]["ad_spend"]
    out = preview(client, slug, op={"type": "percent", "row": "spend_total", "from": "2027-01-01", "to": "2027-12-01", "pct": 10})
    assert years(out)[2027]["ad_spend"] == pytest.approx(base * 1.1, rel=1e-9)
    assert years(out)[2028]["ad_spend"] == pytest.approx(years(state(client, slug))[2028]["ad_spend"])          # other years untouched


def test_a_ramp_lands_on_its_end_value_and_the_average_moves(client, restore):
    slug = copy_of(client, "Case ramp")
    out = preview(client, slug, op={"type": "ramp", "row": "spend_total", "from": "2027-01-01", "to": "2027-12-01", "end_value": 700000})
    spend = [grid_value(out, SPEND, m) for m in fy_months("2027-01-01", 12)]
    assert spend[0] == 350000.0 and spend[-1] == 700000.0 and all(b > a for a, b in zip(spend, spend[1:]))
    assert out["outputs"]["averages"]["varies"] is True and out["outputs"]["averages"]["spend_per_month"] == pytest.approx(sum(spend) / 12)
    assert state(client, slug)["outputs"]["averages"]["varies"] is False


def test_reset_op_puts_a_month_back_to_the_plan_of_records_cell(client, restore):
    slug = copy_of(client, "Case reset op")
    put_cells(client, slug, [(SPEND, "2027-01-01", 999999.0)])
    out = preview(client, slug, op={"type": "reset", "row": "spend_total", "from": "2027-01-01", "to": "2027-02-01"})
    assert out["applied"]["count"] == 2 and grid_value(out, SPEND, "2027-01-01") == 350000.0


# ------------------------------------------------------------------------------------------------------------------ retention

def test_switching_retention_changes_repeat_revenue_and_reports_the_curve(client, restore):
    slug = copy_of(client, "Case retention")
    base = state(client, slug)
    goal = preview(client, slug, retention={"curve_source": "goal"})
    assert goal["retention"]["label"].startswith("Goal") and goal["retention"]["lifetime"] != pytest.approx(base["retention"]["lifetime"])
    assert years(goal)[2027]["repeat_rev"] != pytest.approx(years(base)[2027]["repeat_rev"])
    assert years(goal)[2027]["nc_rev"] == pytest.approx(years(base)[2027]["nc_rev"])                         # new-customer revenue does not depend on retention
    compare = client.get(f"{P}/cohorts/curve-compare", query_string={"scenario": slug, "n": 3, "asof": asof_month(client, slug, 3)}).get_json()
    assert goal["retention"]["lifetime"] == pytest.approx(next(o for o in compare["options"] if o["key"] == "goal")["lifetime"])
    assert goal["retention"]["twelve"] == pytest.approx(next(o for o in compare["options"] if o["key"] == "goal")["twelve"])
    six = preview(client, slug, retention={"curve_source": "live", "n_cohorts": 6})
    assert six["retention"]["n_cohorts"] == 6 and six["retention"]["label"] == "Current, last 6 cohorts"


@pytest.mark.parametrize("retention,fragment", [({"curve_source": "nope"}, "live, goal or asof"), ({"curve_source": "asof"}, "Choose the month"),
                                                ({"curve_source": "asof", "asof_month": "2024-01-01"}, "cohort"), ({"curve_source": "live", "n_cohorts": 0}, "n_cohorts"),
                                                ({"curve_source": "asof", "asof_month": "2026-08-15"}, "first of a month"), ("goal", "must be an object")])
def test_a_bad_retention_choice_is_a_400_in_plain_words(client, retention, fragment):
    r = client.post(f"{P}/modeler/preview", json={"scenario": "current", "preview": True, "retention": retention})
    assert r.status_code == 400 and fragment in r.get_json()["error"], r.get_json()


# ------------------------------------------------------------------------------------------------------------------ EBITDA and the headline

def test_ebitda_is_net_operating_income_before_interest_expense(client):
    s = state(client, "current")
    pnl = client.get(f"{P}/pnl/table", query_string={"scenario": "current"}).get_json()
    fy_years = pnl["fy"]["years"]
    row = lambda k: dict(zip(fy_years, next(v for key, v in pnl["fy"]["rows"].items() if key == k)))      # noqa: E731
    noi, interest = row("noi"), row("interest_expense")
    assert interest[2026] > 0                                                                                 # actual months carry interest
    for y, entry in years(s).items():
        assert entry["ebitda"] == pytest.approx(noi[y] + interest[y], abs=0.005) and entry["noi"] == pytest.approx(noi[y], abs=0.005)
        assert entry["ebitda_margin"] == pytest.approx(entry["ebitda"] / entry["total_revenue"])
    assert s["outputs"]["ebitda"]["text"] == "EBITDA is net operating income before interest expense."


def test_the_ebitda_definition_is_a_setting_that_can_match_pacing(client, restore):
    slug = copy_of(client, "Case ebitda")
    r = client.put(f"{P}/modeler/settings", json={"scenario": slug, "ebitda_addbacks": ["interest_expense", "marketing_influencers", "uncategorized"]})
    assert r.status_code == 200
    s = state(client, slug)
    pnl = client.get(f"{P}/pnl/table", query_string={"scenario": slug}).get_json()
    fy_years = pnl["fy"]["years"]
    rows = {k: dict(zip(fy_years, v)) for k, v in pnl["fy"]["rows"].items()}
    for y, entry in years(s).items():
        want = sum(rows[k][y] for k in ("noi", "interest_expense", "marketing_influencers", "uncategorized"))
        assert entry["ebitda"] == pytest.approx(want, abs=0.005)
    assert "interest expense, marketing influencers and uncategorized expense" in s["outputs"]["ebitda"]["text"]


def test_the_headline_year_the_partial_year_and_the_lowest_cash_month(client):
    s = state(client, "current")
    y = years(s)
    assert s["outputs"]["headline_year"] == 2027 and s["outputs"]["averages"] == {"year": 2027, "spend_per_month": 350000.0, "cpa": 95.0, "aov": 69.0, "varies": False}
    assert y[2026]["status"] == "partial" and y[2026]["actual_months"] == 7 and y[2026]["sub"] == "Actual through Jul"
    assert y[2027]["status"] == "projected" and y[2027]["sub"] == ""
    assert list(y) == [2026, 2027, 2028, 2029]
    assert s["outputs"]["lowest_cash"]["month"] == "2028-12-01" and s["outputs"]["lowest_cash"]["value"] < 0


def test_the_grid_is_the_three_plan_rows_then_what_they_produce(client):
    s = state(client, "current")
    keys = [r["key"] for r in s["rows"]]
    assert keys[:4] == ["h_inputs", SPEND, AOV, CPA] and {r["key"] for r in s["rows"] if r["input"]} == {SPEND, AOV, CPA}
    assert s["months"][0] == "2026-01-01" and s["months"][-1] == "2029-12-01" and len(s["months"]) == len(s["status"]) == 48
    total = next(r for r in s["rows"] if r["key"] == "mo.total_rev")["values"]
    parts = [next(r for r in s["rows"] if r["key"] == k)["values"] for k in ("mo.nc_rev", "mo.repeat_rev", "mo.other_rev")]
    assert all(abs(t - sum(p[i] for p in parts)) < 0.01 for i, t in enumerate(total))
    fy = s["fy"]
    assert fy["years"] == [2026, 2027, 2028, 2029] and fy["rows"]["mo.total_rev"][1] == pytest.approx(years(s)[2027]["total_revenue"])
    assert s["cellmeta"][SPEND]["2026-07-01"] == "actual" and s["cellmeta"][SPEND]["2027-01-01"] == "typed"


def test_a_cleared_row_cannot_break_the_model(conn):
    with pytest.raises(ApiError) as e:
        model.run_state(conn, "no-such-scenario")
    assert e.value.status == 404
