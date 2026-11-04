"""Fix wave: the QuickBooks upload cannot turn a thin month into books and can be undone exactly; one three-state month status; the Pacific clock;
the data checks card; the offer as a multiple of each year's net operating income; the visible sales tax setting."""
from __future__ import annotations

import io
import sys
import types
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.projection.pnl import model as M, store                # noqa: E402
from pnl_helpers import qb_rows, to_csv                          # noqa: E402
from test_pnl_api import P, audit_rows, data, post_file          # noqa: E402,F401  (data is a fixture)
from test_pnl_model import base_actuals, run                      # noqa: E402

AUG_OCT = ["Aug 2026", "Sep 2026", "Oct 2026"]


def csv_of(rows):
    return to_csv(rows)


def partial_file():
    """Only Amazon revenue and ad spend for Aug-Oct 2026: no cost of goods, fulfillment or operating expenses."""
    return csv_of([["", *AUG_OCT], ["Amazon Sales", 200_000.0, 190_000.0, 180_000.0], ["Facebook Ads", 700_000.0, 500_000.0, 450_000.0]])


def full_file(scale=1.0, months=AUG_OCT):
    rows = [["", *months]]
    lines = {"Shopify Sales": 500_000, "Amazon Sales": 100_000, "China COGS": 70_000, "Amazon FBA Fees": 25_000, "Fulfillment Services": 60_000,
             "Facebook Ads": 150_000, "Accounting": 2_500, "Bank Fees": 400}
    for name, base in lines.items():
        rows.append([name, *[base * scale for _ in months]])
    return csv_of(rows)


def actuals_dump(pg):
    return {(r["line_key"], r["month"]): (float(r["value"]), r["source"]) for r in
            pg.execute("SELECT line_key, month, value, source FROM proj_actuals WHERE line_key LIKE 'pnl.%'").fetchall()}


def widen(client, **kw):
    r = client.put(f"{P}/upload-checks", json={"low_ratio": 0.5, "high_ratio": 2.0, **kw})
    assert r.status_code == 200, r.get_data(as_text=True)


# ------------------------------------------------------------------------------------------------------------ the coverage guard

def test_a_partial_file_cannot_become_books(client, data):
    before = actuals_dump(data)
    pv = post_file(client, partial_file()).get_json()
    assert pv["committable"] is False and pv["needs_confirmation"] is False
    assert any("Aug 2026 has no cost of goods and no fulfillment and shipping and no operating expenses accounts" in b for b in pv["blockers"])
    assert all(m["missing"] for m in pv["months"]) and {m["month"] for m in pv["months"]} == {"2026-08-01", "2026-09-01", "2026-10-01"}
    r = post_file(client, partial_file(), commit=True, token=pv["diff_sha256"])
    assert r.status_code == 400 and "cannot become books" in r.get_json()["error"]
    assert actuals_dump(data) == before                                           # nothing written
    assert client.get(f"{P}/table").get_json()["actual_through"] == "2026-07-01"   # Aug-Oct stay out of the books
    assert audit_rows(data, "pnl.upload") == []


def test_revenue_is_always_required_and_costs_alone_do_not_pass(client, data):
    no_revenue = csv_of([["", *AUG_OCT], ["China COGS", 70_000, 70_000, 70_000], ["Fulfillment Services", 60_000, 60_000, 60_000],
                         ["Facebook Ads", 150_000, 150_000, 150_000], ["Accounting", 2_500, 2_500, 2_500]])
    pv = post_file(client, no_revenue).get_json()
    assert pv["committable"] is False and any("no revenue" in b for b in pv["blockers"])
    r = client.put(f"{P}/upload-checks", json={"required_families": ["cost_of_goods"]})
    assert r.status_code == 400 and "revenue must stay required" in r.get_json()["error"]


def test_a_complete_file_passes_and_shows_income_cost_and_noi_next_to_the_plan(client, data):
    pv = post_file(client, full_file()).get_json()
    assert pv["committable"] is True and pv["blockers"] == [] and pv["required_families"][0] == "revenue"
    m = pv["months"][0]
    assert m["ok"] is True and m["missing"] == [] and all(m["families"].values())
    assert m["income"] == 600_000.0 and m["cost"] == pytest.approx(70_000 + 25_000 + 60_000 + 150_000 + 2_500 + 400)
    assert m["noi"] == pytest.approx(m["income"] - m["cost"]) and m["model_noi"] is not None                  # the plan's own figure for the same month


def test_an_unusual_month_needs_a_confirmation_but_a_thin_one_is_blocked_outright(client, data):
    widen(client)                                                                    # the default rule: below 50% or above 200% of the neighbours
    small = full_file(scale=0.01)
    pv = post_file(client, small).get_json()
    assert pv["committable"] is True and pv["needs_confirmation"] is True and pv["review"]
    assert any("income" in r and "neighbouring months" in r for r in pv["review"])
    r = post_file(client, small, commit=True, token=pv["diff_sha256"])
    assert r.status_code == 400 and "review them and confirm" in r.get_json()["error"]
    ok = client.post(f"{P}/upload?commit=1", data={"file": (io.BytesIO(small), "e.csv"), "diff_sha256": pv["diff_sha256"], "confirm_review": "1"},
                     content_type="multipart/form-data")
    assert ok.status_code == 200 and ok.get_json()["committed"] is True


def test_upload_checks_are_settings_with_validation_and_an_audit_row(client, data):
    assert client.put(f"{P}/upload-checks", json={"low_ratio": 0.3, "high_ratio": 3.0, "required_families": ["revenue", "cost_of_goods"]}).status_code == 200
    a = audit_rows(data, "pnl.setting")
    assert a[-1]["line_key"] == "pnl.upload" and a[-1]["new"]["low_ratio"] == 0.3
    pv = post_file(client, partial_file()).get_json()                                  # only revenue + cost of goods now required: still no cost of goods
    assert pv["committable"] is False and "operating expenses" not in " ".join(pv["blockers"])
    for bad in ({"low_ratio": 0}, {"low_ratio": 2}, {"high_ratio": 0.5}, {"required_families": []}, {"required_families": ["nope", "revenue"]},
                {"required_families": ["revenue", "revenue"]}):
        assert client.put(f"{P}/upload-checks", json=bad).status_code == 400, bad


# ------------------------------------------------------------------------------------------------------------ undo

def commit(client, raw, name="f.csv", confirm=False):
    pv = post_file(client, raw, name).get_json()
    assert "diff_sha256" in pv, pv
    form = {"file": (io.BytesIO(raw), name), "diff_sha256": pv["diff_sha256"]}
    if confirm:
        form["confirm_review"] = "1"
    r = client.post(f"{P}/upload?commit=1", data=form, content_type="multipart/form-data")
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()


def test_undo_puts_back_exactly_what_the_upload_replaced_and_removes_what_it_added(client, data):
    widen(client, low_ratio=0.01, high_ratio=100.0)
    before = actuals_dump(data)
    first = commit(client, full_file(months=["Jan 2026", "Feb 2026", "Mar 2026"]), "a.csv")
    after_a = actuals_dump(data)
    assert len(after_a) > len(before)
    second_raw = full_file(scale=1.1, months=["Mar 2026", "Apr 2026", "May 2026"])      # changes Mar, adds Apr and May
    second = commit(client, second_raw, "b.csv")
    assert second["upload_id"] > first["upload_id"] and second["cells"]["changed"] > 0 and second["cells"]["new"] > 0
    ups = client.get(f"{P}/uploads").get_json()["uploads"]
    assert [u["can_undo"] for u in ups[:2]] == [True, False] and ups[0]["filename"] == "b.csv" and ups[0]["status"] == "applied"
    # only the latest can be undone
    assert client.post(f"{P}/upload/undo", json={"upload_id": first["upload_id"]}).status_code == 409
    r = client.post(f"{P}/upload/undo", json={"upload_id": second["upload_id"]})
    assert r.status_code == 200 and r.get_json()["restored"] == second["cells"]["changed"] and r.get_json()["removed"] == second["cells"]["new"]
    assert actuals_dump(data) == after_a                                              # exactly as after the first upload: values, sources, no extras
    a = audit_rows(data, "pnl.upload.undo")
    assert len(a) == 1 and a[0]["old"]["upload_id"] == second["upload_id"] and a[0]["actor"] == "JoshHoughton"
    assert client.get(f"{P}/uploads").get_json()["uploads"][0]["status"] == "undone"
    # the next undo is the first upload; then there is nothing left
    assert client.post(f"{P}/upload/undo", json={}).status_code == 200
    assert {k: v for k, v in actuals_dump(data).items() if k not in before} == {}
    assert actuals_dump(data) == before
    assert client.post(f"{P}/upload/undo", json={}).status_code == 404


def test_undo_refuses_when_a_loaded_value_changed_after_the_upload(client, data):
    widen(client, low_ratio=0.01, high_ratio=100.0)
    commit(client, full_file(months=["Jan 2026", "Feb 2026", "Mar 2026"]), "a.csv")
    data.execute("UPDATE proj_actuals SET value = value + 1 WHERE line_key = 'pnl.dtc_rev' AND month = '2026-01-01'")
    r = client.post(f"{P}/upload/undo", json={})
    assert r.status_code == 409 and "cannot be undone safely" in r.get_json()["error"]
    assert client.get(f"{P}/uploads").get_json()["uploads"][0]["status"] == "applied"


def test_undo_validation_and_history_limits(client, data):
    assert client.post(f"{P}/upload/undo", json={"upload_id": "x"}).status_code == 400
    assert client.post(f"{P}/upload/undo", json={}).status_code == 404
    assert client.get(f"{P}/uploads").get_json()["uploads"] == []


# ------------------------------------------------------------------------------------------------------------ one month status, one clock

def test_month_status_is_three_words_with_one_rule():
    ms = M.month_status(date(2026, 7, 1), date(2026, 10, 1))
    assert ms[date(2026, 7, 1)] == "books" and ms[date(2026, 8, 1)] == "feeds" and ms[date(2026, 10, 1)] == "feeds" and ms[date(2026, 11, 1)] == "projected"
    assert set(ms.values()) == {"books", "feeds", "projected"}
    none = M.month_status(None, None)
    assert set(none.values()) == {"projected"}
    only_feeds = M.month_status(None, date(2026, 10, 1))
    assert only_feeds[date(2025, 1, 1)] == "feeds" and only_feeds[date(2026, 11, 1)] == "projected"


def test_engine_reports_books_feeds_and_projected_and_prefers_feeds_through():
    from types import SimpleNamespace
    out = run()
    assert out.books_through == date(2026, 7, 1) and out.actual_through == out.books_through
    drivers = SimpleNamespace(feeds_through=date(2026, 10, 1), actual_through=date(2026, 7, 1))
    inputs = M.PnlInputs(actuals=base_actuals(), drivers={}, today=date(2026, 11, 3))
    o = M.compute_pnl(inputs, drivers=drivers)
    assert o.feeds_through == date(2026, 10, 1) and o.month_status[date(2026, 9, 1)] == "feeds" and o.month_status[date(2026, 11, 1)] == "projected"


def test_the_clock_is_the_core_pacific_helper_when_it_exists(monkeypatch):
    fake = types.ModuleType("app.projection.clock")
    fake.today_pt = lambda: date(2031, 5, 17)
    monkeypatch.setitem(sys.modules, "app.projection.clock", fake)
    assert store.today() == date(2031, 5, 17)
    monkeypatch.delitem(sys.modules, "app.projection.clock")
    monkeypatch.setattr(__import__("app.projection", fromlist=["x"]), "clock", None, raising=False)
    assert isinstance(store.today(), date)                                             # falls back to the Pacific day from freshness.today_pacific


# ------------------------------------------------------------------------------------------------------------ data checks, offer multiples, sales tax

def test_data_checks_show_the_typed_figures_next_to_the_cohort_figures(client, data):
    d = client.get(f"{P}/checks").get_json()
    assert d["months"] == ["2026-08-01"] and "nothing here changes a number" in d["note"]
    rows = {r["key"]: r["values"][0] for r in d["rows"]}
    assert rows["nc_used"] == 300_000.0 and rows["nc_cohort"] == 534_185.0 and rows["nc_gap"] == pytest.approx(300_000 / 534_185 - 1)
    assert rows["repeat_used"] == pytest.approx(984_286.67) and rows["repeat_typed"] == pytest.approx(984_286.67)
    assert rows["repeat_model"] == 500_000.0 and rows["repeat_export"] == 839_643.0 and rows["repeat_gap"] == pytest.approx(984_286.67 / 839_643 - 1)


def test_the_offer_is_shown_as_a_multiple_of_each_year_of_net_operating_income(client, data):
    ev = client.get(f"{P}/ev?scenarios=current").get_json()
    row = ev["rows"][0]
    assert ev["offer_years"] == [2027, 2028, 2029] and set(row["offer_multiple"]) == {"2027", "2028", "2029"}
    for y in ("2027", "2028", "2029"):
        noi = row["year_noi"][y]
        assert row["offer_multiple"][y] == pytest.approx(ev["loi"] / noi if noi > 0 else None)
    client.put(f"{P}/settings", json={"scenario": "current", "settings": {"ev": {"loi": 3_000_000}}})
    assert client.get(f"{P}/ev?scenarios=current").get_json()["rows"][0]["offer_multiple"]["2027"] < row["offer_multiple"]["2027"]


def test_the_sales_tax_rate_and_its_plain_line_are_visible_on_the_settings(client, data):
    d = client.get(f"{P}/drivers").get_json()
    assert d["sales_tax"]["rate"] == 0.07 and d["sales_tax"]["text"] == "History months carry no deduction; projected months deduct this."
    assert d["upload_checks"]["required_families"][0] == "revenue" and d["late_after_days"] == 45


# ------------------------------------------------------------------------------------------------------------ core write validation (hooks)

def _put(client, key, month, value, kind="override"):
    return client.put("/api/projection/series", json={"scenario": "current", "items": [{"line_key": key, "month": month, "value": value, "kind": kind}]})


def test_series_writes_are_held_to_the_pnl_rules(client, data):
    ok = _put(client, "pnl.accounting", "2026-12-01", 9_999.0)
    assert ok.status_code == 200
    for key, month, value, frag in (("pnl.noi", "2026-12-01", 1.0, "calculated"), ("pnl.gross_profit", "2026-12-01", 1.0, "calculated"),
                                    ("pnl.zzz_unknown", "2026-12-01", 1.0, "not a P&L line"), ("pnl.accounting", "2026-03-01", 5.0, "in the books"),
                                    ("pnl.accounting", "2026-12-01", -5.0, "cannot be negative")):
        r = _put(client, key, month, value)
        assert r.status_code == 400 and frag in r.get_json()["error"], (key, month, r.get_json())
    assert _put(client, "pnl.uncategorized", "2026-12-01", -250.0).status_code == 200                    # the one line that can be negative
    assert _put(client, "pnl.accounting", "2026-03-01", None).status_code == 200                          # clearing is always allowed
    assert _put(client, "pnl.marketing_spend", "2026-12-01", 123_000.0).status_code == 200


def test_a_scenario_patch_is_held_to_the_settings_limits(client, data):
    bad = client.patch("/api/projection/scenarios/current", json={"params": {"pnl": {"engine": {"rate_window_months": 99}}}})
    assert bad.status_code == 400
    bad = client.patch("/api/projection/scenarios/current", json={"params": {"pnl": {"drivers": {"accounting": {"segments": [{"mode": "nope"}]}}}}})
    assert bad.status_code == 400 and "pnl.drivers.accounting" in bad.get_json()["error"]
    good = client.patch("/api/projection/scenarios/current", json={"params": {"pnl": {"engine": {"rate_window_months": 6}}}})
    assert good.status_code == 200


def test_the_other_business_expenses_driver_is_editable_and_a_typed_rate_stays_a_share(client, data):
    seg = [{"from": None, "mode": "rate", "base": "total_revenue", "rate_kind": "typed", "rate": 0.002}]
    assert client.put(f"{P}/drivers", json={"scenario": "current", "key": "other_business_expenses", "segments": seg}).status_code == 200
    t = client.get(f"{P}/table").get_json()
    row = {r["key"]: r["values"] for r in t["rows"]}
    i = t["months"].index("2026-12-01")
    assert row["other_business_expenses"][i] == pytest.approx(0.002 * row["total_revenue"][i])
    for rate in (5.0, -0.2):
        bad = [{"from": None, "mode": "rate", "base": "total_revenue", "rate_kind": "typed", "rate": rate}]
        assert client.put(f"{P}/drivers", json={"scenario": "current", "key": "amazon_fba_fees", "segments": bad}).status_code == 400
