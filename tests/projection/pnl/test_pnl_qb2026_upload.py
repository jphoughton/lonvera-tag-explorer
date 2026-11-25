"""New-chart (qb2026) upload: single-month and month-per-column layouts, the Software children, chart detection, preview / commit / undo per chart, and the
scenario setting that selects the chart (table, drivers, typed months, copies)."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from psycopg.types.json import Jsonb                                 # noqa: E402
from app.projection.pnl import qb, store                              # noqa: E402
from pnl_helpers import make_xlsx, qb_rows, qb26_expected, qb26_rows, seed_qb2026, to_csv      # noqa: E402
from pnl_support import TODAY, seed_basic                              # noqa: E402

P = "/api/projection/pnl"
S = "/api/projection"
OCT = date(2026, 10, 1)
REQUIRED = ["revenue", "cost_of_goods", "fulfillment", "marketing_spend", "operating_expenses"]


@pytest.fixture()
def qbdb(pg):
    seed_qb2026(pg)
    pg.execute("INSERT INTO proj_pnl_settings (key, value) VALUES ('upload', %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
               (Jsonb({"low_ratio": 0.001, "high_ratio": 1000.0, "required_families": REQUIRED}),))
    return pg


def post(client, data, name="b.xlsx", commit=False, **form):
    body = {"file": (__import__("io").BytesIO(data), name), **form}
    return client.post(f"{P}/upload" + ("?commit=1" if commit else ""), data=body, content_type="multipart/form-data")


def commit(client, data, name="b.xlsx", **form):
    pv = post(client, data, name, **form).get_json()
    return post(client, data, name, commit=True, diff_sha256=pv["diff_sha256"], **form)


def qb26_actuals(pg):
    return {(r["line_key"], r["month"]): float(r["value"]) for r in pg.execute("SELECT * FROM proj_actuals WHERE left(line_key, 5) = 'qb26.'").fetchall()}


def sheet_actuals_rows(pg):
    return {(r["line_key"], r["month"]): float(r["value"]) for r in pg.execute("SELECT * FROM proj_actuals WHERE left(line_key, 4) = 'pnl.'").fetchall()}


# ---------------------------------------------------------------------------------------------------------------- parser (no database)

def test_single_month_layout_parses_and_ignores_the_percent_column():
    p = qb.parse(make_xlsx(qb26_rows(["Oct 2026"])), "oct.xlsx")
    assert p.months == [OCT] and p.chart_guess == "qb2026"
    names = {a.name for a in p.accounts}
    assert len(p.accounts) == 32 and "4400 Refunds & Discounts to Customers" in names
    assert "4000 Sales" not in names and "6165 Software & Apps" not in names and "Unrealized Gain or Loss" not in names      # headers with no amount
    assert {x["account"] for x in p.ignored} >= {"Total for 4000 Sales", "Total Income", "Gross Profit", "Net Operating Income", "Net Income", "Net Other Income",
                                                "Total for 6165 Software & Apps", "Total Other Income"}
    assert p.accounts[0].values == {OCT: 100_000.0}                          # not the 1.5 in the "% of Income" column


def test_month_per_column_layout_parses_every_month():
    labels = ["Jan 2026", "Feb 2026", "Mar 2026"]
    p = qb.parse(make_xlsx(qb26_rows(labels, [1.0, 1.1, 0.9])), "q1.xlsx")
    assert p.months == [date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1)] and p.chart_guess == "qb2026"
    dtc = next(a for a in p.accounts if a.name == "4020 DTC Sales")
    assert dtc.values == {date(2026, 1, 1): 800_000.0, date(2026, 2, 1): 880_000.0, date(2026, 3, 1): 720_000.0}
    csv_ = qb.parse(to_csv(qb26_rows(labels)), "q1.csv")
    assert [a.name for a in csv_.accounts] == [a.name for a in p.accounts]


def test_the_old_layout_is_still_detected_as_the_sheet_chart():
    p = qb.parse(to_csv(qb_rows()), "old.csv")
    assert p.chart_guess == "sheet" and len(p.months) == 3


def test_software_children_are_told_apart_by_code_by_parent_and_by_renamed_code(pg=None):
    qmap = {"6166 marketing": "software_marketing", "6167 operations": "software_operations", "software & apps > marketing": "software_marketing",
            "software & apps > operations": "software_operations", "6030 marketing media": "x"}
    assert qb.lookup_account(qmap, "6166 Marketing") == "software_marketing" and qb.lookup_account(qmap, "6167 Operations") == "software_operations"
    # an export that drops the codes: the bare names are ambiguous with the 6000 Marketing section, so the parent header decides
    assert qb.lookup_account(qmap, "Marketing", "6165 Software & Apps") == "software_marketing"
    assert qb.lookup_account(qmap, "Operations", "6165 Software & Apps") == "software_operations"
    assert qb.lookup_account(qmap, "Marketing", "6000 Marketing") is None and qb.lookup_account(qmap, "Marketing") is None
    # an account renamed in the books keeps its number
    assert qb.lookup_account(qmap, "6166 Marketing Software") == "software_marketing"
    p = qb.parse(make_xlsx(qb26_rows(["Oct 2026"], drop_codes=True)), "bare.xlsx")
    parents = {a.name: a.parent for a in p.accounts if a.name in ("Marketing", "Operations")}
    assert parents == {"Marketing": "6165 Software & Apps", "Operations": "6165 Software & Apps"}


def test_preview_maps_every_account_and_checks_coverage_without_a_database():
    from pnl_helpers import QB26_ACCOUNTS
    from app.projection.pnl.charts import qb2026 as Q
    code_key = {c: k for k, c in Q.ACCOUNTS.items()}
    qmap = {name.lower(): code_key[name.split(" ", 1)[0]] for name, _ in QB26_ACCOUNTS}
    p = qb.parse(make_xlsx(qb26_rows(["Oct 2026"])), "o.xlsx")
    pv, cells = qb.preview(p, qmap, {}, TODAY, None, {"low_ratio": 0.5, "high_ratio": 2.0, "required_families": REQUIRED}, "qb2026")
    assert pv["chart"] == "qb2026" and len(pv["mapped"]) == 32 and pv["unmapped"] == [] and pv["committable"] and pv["blockers"] == []
    assert len(cells) == 32 and cells[("dtc_sales", OCT)] == 800_000.0 and cells[("refunds_discounts", OCT)] == -150_000.0
    m = pv["months"][0]
    assert m["income"] == pytest.approx(qb26_expected(1.0)["total_income"])
    assert all(m["families"].values())
    # without ad spend accounts the month cannot become books
    thin = {k: v for k, v in qmap.items() if "ads" not in k}
    pv2, _ = qb.preview(p, thin, {}, TODAY, None, {"low_ratio": 0.5, "high_ratio": 2.0, "required_families": REQUIRED}, "qb2026")
    assert not pv2["committable"] and any("ad spend" in b for b in pv2["blockers"])


# ---------------------------------------------------------------------------------------------------------------- upload through the API

def test_preview_detects_the_chart_and_commit_loads_only_new_chart_books(client, qbdb):
    data = make_xlsx(qb26_rows(["Oct 2026"]))
    pv = post(client, data).get_json()
    assert pv["chart"] == "qb2026" and pv["committable"], {k: pv[k] for k in ("blockers", "unmapped", "notes")}
    assert len(pv["mapped"]) == 32 and pv["cells"]["new"] == 32
    assert [m["month"] for m in pv["months"]] == ["2026-10-01"]
    r = commit(client, data)
    assert r.status_code == 200 and r.get_json()["committed"]
    got = qb26_actuals(qbdb)
    assert len(got) == 32 and got[("qb26.dtc_sales", OCT)] == 800_000.0 and got[("qb26.interest_expense", OCT)] == 30_000.0
    assert sheet_actuals_rows(qbdb) == {}                                          # nothing under the sheet chart's prefix
    audit = qbdb.execute("SELECT new FROM proj_audit WHERE action = 'pnl.upload'").fetchone()["new"]
    assert audit["chart"] == "qb2026" and audit["months"] == ["2026-10-01"]
    up = client.get(f"{P}/uploads").get_json()["uploads"][0]
    assert up["chart"] == "qb2026" and up["can_undo"] is True


def test_commit_needs_the_preview_hash_and_an_explicit_chart_overrides_detection(client, qbdb):
    data = make_xlsx(qb26_rows(["Oct 2026"]))
    assert post(client, data, commit=True).status_code == 400
    assert post(client, data, commit=True, diff_sha256="0" * 64).status_code == 409
    forced = post(client, data, chart="sheet").get_json()                          # forced onto the sheet chart: the numbered accounts are not in its map
    assert forced["chart"] == "sheet" and forced["mapped"] == [] and len(forced["unmapped"]) == 32 and not forced["committable"]
    assert post(client, data, chart="nonsense").status_code == 400


def test_month_per_column_upload_loads_every_month_and_a_second_load_is_unchanged(client, qbdb):
    labels = ["Aug 2026", "Sep 2026", "Oct 2026"]
    data = make_xlsx(qb26_rows(labels, [0.9, 1.0, 1.1]))
    assert commit(client, data).status_code == 200
    got = qb26_actuals(qbdb)
    assert len(got) == 96 and got[("qb26.dtc_sales", date(2026, 8, 1))] == 720_000.0 and got[("qb26.dtc_sales", OCT)] == 880_000.0
    again = post(client, data).get_json()
    assert again["cells"] == {"total": 96, "new": 0, "changed": 0, "same": 96}


def test_undo_is_per_chart_and_never_touches_the_other_charts_books(client, qbdb):
    seed_basic(qbdb)                                                               # sheet-chart books through Jul 2026 under 'pnl.'
    store.upsert_qb_map(qbdb, [("shopify sales", "dtc_rev", None), ("amazon sales", "amazon_rev", None), ("amazon fba fees", "amazon_fba_fees", None),
                               ("china cogs", "china_cogs", None), ("other business expenses", "other_direct", None), ("bank fees", "bank_fees", None),
                               ("accounting", "accounting", None), ("fulfillment services", "fulfillment_services", None), ("facebook ads", "mkt_facebook_ads", None)])
    sheet_before = sheet_actuals_rows(qbdb)
    assert commit(client, make_xlsx(qb_rows(["Aug 2026", "Sep 2026", "Oct 2026"])), "sheet.xlsx").status_code == 200       # a sheet-chart upload first
    sheet_mid = sheet_actuals_rows(qbdb)
    assert sheet_mid != sheet_before
    assert commit(client, make_xlsx(qb26_rows(["Oct 2026"])), "new.xlsx").status_code == 200                                # then a new-chart upload (newest overall)
    assert len(qb26_actuals(qbdb)) == 32
    ups = {u["chart"]: u for u in client.get(f"{P}/uploads").get_json()["uploads"]}
    assert ups["sheet"]["can_undo"] and ups["qb2026"]["can_undo"]                  # each chart has its own latest upload
    assert client.post(f"{P}/upload/undo", json={}).get_json()["chart"] == "sheet"                                       # default = the sheet chart, as before
    assert sheet_actuals_rows(qbdb) == sheet_before and len(qb26_actuals(qbdb)) == 32                                    # new-chart books untouched
    undo = client.post(f"{P}/upload/undo", json={"chart": "qb2026"}).get_json()
    assert undo["chart"] == "qb2026" and undo["removed"] == 32 and qb26_actuals(qbdb) == {}
    assert sheet_actuals_rows(qbdb) == sheet_before
    assert client.post(f"{P}/upload/undo", json={"chart": "qb2026"}).status_code == 404                                 # nothing left for that chart


def test_a_new_chart_undo_refuses_when_its_cells_changed_since(client, qbdb):
    assert commit(client, make_xlsx(qb26_rows(["Oct 2026"]))).status_code == 200
    qbdb.execute("UPDATE proj_actuals SET value = 1 WHERE line_key = 'qb26.dtc_sales'")
    assert client.post(f"{P}/upload/undo", json={"chart": "qb2026"}).status_code == 409


def test_qb_map_is_per_chart(client, qbdb):
    m = client.get(f"{P}/qb-map?chart=qb2026").get_json()
    assert m["chart"] == "qb2026" and any(r["account"] == "4020 dtc sales" and r["line_key"] == "dtc_sales" for r in m["map"])
    assert {x["key"] for x in m["lines"]} >= {"dtc_sales", "interest_expense"} and "dtc_rev" not in {x["key"] for x in m["lines"]}
    assert client.get(f"{P}/qb-map").get_json()["map"] == []                       # the sheet chart's map is its own
    assert client.put(f"{P}/qb-map", json={"chart": "qb2026", "account": "6150 Software Extras", "line_key": "software_operations"}).status_code == 200
    assert client.put(f"{P}/qb-map", json={"chart": "qb2026", "account": "x", "line_key": "dtc_rev"}).status_code == 400          # a sheet line is not a new-chart line
    assert client.put(f"{P}/qb-map", json={"account": "x", "line_key": "dtc_sales"}).status_code == 400                           # default chart is the sheet chart
    assert client.delete(f"{P}/qb-map", json={"chart": "qb2026", "account": "6150 Software Extras"}).status_code == 200


# ---------------------------------------------------------------------------------------------------------------- the scenario setting

@pytest.fixture()
def books(client, qbdb):
    seed_basic(qbdb)
    qbdb.execute("INSERT INTO proj_pnl_settings (key, value) VALUES ('engine', '{\"rate_window_months\": 4}'::jsonb) ON CONFLICT (key) DO NOTHING")
    labels = ["Aug 2026", "Sep 2026", "Oct 2026"]
    assert commit(client, make_xlsx(qb26_rows(labels, [0.9, 1.0, 1.1]))).status_code == 200
    return qbdb


def select_chart(client, slug="current", chart="qb2026"):
    return client.put(f"{P}/settings", json={"scenario": slug, "settings": {"chart": chart}})


def test_the_default_chart_stays_the_sheet_chart_until_a_scenario_selects_the_new_one(client, books):
    t = client.get(f"{P}/table").get_json()
    assert "chart" not in t and "noi" in {r["key"] for r in t["rows"]} and "total_income" not in {r["key"] for r in t["rows"]}
    assert select_chart(client).status_code == 200
    q = client.get(f"{P}/table").get_json()
    assert q["chart"] == "qb2026"
    keys = [r["key"] for r in q["rows"]]
    assert {"dtc_sales", "total_income", "total_cogs", "gross_profit", "noi", "net_income", "legacy_cogs_other"} <= set(keys) and "dtc_rev" not in keys
    ms = dict(zip(q["months"], q["month_status"]))
    assert ms["2026-10-01"] == "books" and ms["2026-08-01"] == "books" and ms["2026-03-01"] == "old" and ms["2026-11-01"] == "projected"
    assert q["old_format_through"] == "2026-07-01"
    assert "Old format" in q["status_summary"]["text"] and q["status_summary"]["old_span"].startswith("Jan 2025")
    total = next(r for r in q["rows"] if r["key"] == "total_income")
    assert total["values"][q["months"].index("2026-10-01")] == pytest.approx(qb26_expected(1.1)["total_income"], abs=0.01)
    assert all(len(r["values"]) == 60 for r in q["rows"])
    assert select_chart(client, chart="sheet").status_code == 200 and "chart" not in client.get(f"{P}/table").get_json()


def test_chart_setting_is_validated_and_copies_inherit_it(client, books):
    assert select_chart(client, chart="nonsense").status_code == 400
    assert select_chart(client).status_code == 200
    clone = client.post(f"{S}/scenarios", json={"name": "Lean", "clone_from": "current"}).get_json()["scenario"]["slug"]
    assert client.get(f"{P}/table?scenario={clone}").get_json()["chart"] == "qb2026"                   # a frozen copy carries the setting
    assert select_chart(client, chart="sheet").status_code == 200
    assert client.get(f"{P}/table?scenario={clone}").get_json().get("chart") == "qb2026"               # and keeps it when the original changes


def test_a_scenario_on_a_base_inherits_the_chart(client, books):
    from app.projection import scenarios
    select_chart(client)
    books.execute("INSERT INTO proj_scenarios (slug, name, base_slug, is_default, params, frozen) VALUES ('child', 'Child', 'current', false, '{}', false)")
    assert scenarios.get_scenario(books, "child").params["pnl"]["chart"] == "qb2026"
    assert client.get(f"{P}/table?scenario=child").get_json()["chart"] == "qb2026"


def test_driver_table_for_the_new_chart_shows_mode_base_resolved_value_and_window(client, books):
    select_chart(client)
    d = client.get(f"{P}/drivers").get_json()
    assert d["chart"] == "qb2026" and d["window_text"] == "Aug to Oct 2026" and d["spend_plan_lines"] == [
        {"key": "facebook_ads", "label": "6032 Facebook ads"}, {"key": "google_ads", "label": "6033 Google ads"}]
    items = {i["key"]: i for g in d["groups"] for i in g["items"]}
    assert len(items) == 27 and items["cogs_shopify"]["mode"] == "rate" and items["cogs_shopify"]["base"] == "dtc_sales"
    assert items["cogs_shopify"]["rate"] == pytest.approx(0.1) and items["cogs_shopify"]["window_text"] == "Aug to Oct 2026"
    assert items["consulting"]["mode"] == "books_mean" and items["consulting"]["value"] == pytest.approx(12_000 * 1.0) and items["consulting"]["window"]
    assert items["refunds_discounts"]["rate"] < 0 and "gross sales" in items["refunds_discounts"]["text"]
    assert items["consulting"]["source"] == "Account 6120" and items["consulting"]["overridden"] is False


def test_driver_edits_go_to_the_new_chart_namespace_and_are_validated_per_chart(client, books):
    select_chart(client)
    ok = client.put(f"{P}/drivers", json={"scenario": "current", "key": "consulting", "segments": [{"from": None, "mode": "fixed", "value": 5_000}]})
    assert ok.status_code == 200
    params = books.execute("SELECT params FROM proj_scenarios WHERE slug = 'current'").fetchone()["params"]["pnl"]
    assert params["qb26"]["drivers"]["consulting"]["segments"][0]["value"] == 5_000 and "drivers" not in params and params["chart"] == "qb2026"
    d = client.get(f"{P}/drivers").get_json()
    item = {i["key"]: i for g in d["groups"] for i in g["items"]}["consulting"]
    assert item["overridden"] and item["mode"] == "fixed" and item["value"] == 5_000
    t = client.get(f"{P}/table").get_json()
    assert next(r for r in t["rows"] if r["key"] == "consulting")["values"][t["months"].index("2026-12-01")] == 5_000
    # sheet-chart keys and modes are refused on a new-chart scenario, and the other way round
    assert client.put(f"{P}/drivers", json={"scenario": "current", "key": "china_cogs", "segments": [{"from": None, "mode": "carry"}]}).status_code == 400
    assert client.put(f"{P}/drivers", json={"scenario": "current", "key": "legal", "segments": [{"from": None, "mode": "headcount"}]}).status_code == 400
    assert client.put(f"{P}/drivers", json={"scenario": "current", "key": "legal", "segments": [{"from": None, "mode": "rate", "base": "dtc_rev"}]}).status_code == 400
    assert client.put(f"{P}/drivers", json={"scenario": "current", "key": "facebook_ads", "segments": [{"from": None, "mode": "carry"}]}).status_code == 400   # spend plan line
    assert client.put(f"{P}/drivers", json={"scenario": "current", "key": "legal", "chart": "sheet", "segments": [{"from": None, "mode": "carry"}]}).status_code == 200
    assert books.execute("SELECT params FROM proj_scenarios WHERE slug = 'current'").fetchone()["params"]["pnl"]["drivers"]["legal"]["segments"]      # an explicit chart wins
    back = client.put(f"{P}/drivers", json={"scenario": "current", "key": "consulting", "segments": None})                      # null returns it to the default
    assert back.status_code == 200
    assert "consulting" not in (books.execute("SELECT params FROM proj_scenarios WHERE slug = 'current'").fetchone()["params"]["pnl"].get("qb26", {}).get("drivers") or {})


def test_generic_scenario_patch_cannot_carry_junk_for_either_chart(client, books):
    bad = client.patch(f"{S}/scenarios/current", json={"params": {"pnl": {"qb26": {"drivers": {"china_cogs": {"segments": [{"from": None, "mode": "carry"}]}}}}}})
    assert bad.status_code == 400 and "qb26" in bad.get_json()["error"]
    bad2 = client.patch(f"{S}/scenarios/current", json={"params": {"pnl": {"drivers": {"consulting_x": {"segments": [{"from": None, "mode": "carry"}]}}}}})
    assert bad2.status_code == 400
    bad3 = client.patch(f"{S}/scenarios/current", json={"params": {"pnl": {"qb26": {"headcount": {"base": 1}}}}})
    assert bad3.status_code == 400


def test_typed_months_are_namespaced_per_chart(client, books):
    def put(key, month="2026-12-01", value=1234.0, slug="current"):
        return client.put(f"{S}/series", json={"scenario": slug, "items": [{"line_key": key, "month": month, "value": value, "kind": "override"}]})
    assert put("pnl.qb26.facebook_ads").status_code == 400                           # the scenario shows the sheet chart
    assert put("pnl.accounting").status_code == 200
    select_chart(client)
    assert put("pnl.accounting").status_code == 400                                  # a new-chart scenario takes new-chart months
    assert put("pnl.qb26.facebook_ads").status_code == 200 and put("pnl.qb26.consulting").status_code == 200
    assert put("pnl.qb26.noi").status_code == 400 and put("pnl.qb26.dtc_sales").status_code == 400            # calculated / not projected here
    assert put("pnl.qb26.consulting", month="2026-10-01").status_code == 400                                  # a books month
    assert put("pnl.qb26.legal", value=-5).status_code == 400 and put("pnl.qb26.refunds_discounts", value=-5).status_code == 200
    t = client.get(f"{P}/table").get_json()
    fb = next(r for r in t["rows"] if r["key"] == "facebook_ads")["values"]
    assert fb[t["months"].index("2026-12-01")] == 1234.0 and fb[t["months"].index("2027-01-01")] != 1234.0
    d = client.get(f"{P}/drivers").get_json()
    assert {i["key"]: i for g in d["groups"] for i in g["items"]}["consulting"]["overrides"] == [{"month": "2026-12-01", "value": 1234.0}]


def test_summary_and_metrics_work_for_the_new_chart(client, books):
    select_chart(client)
    s = client.get(f"{P}/summary").get_json()
    assert s["chart"] == "qb2026" and s["actual_through"] == "2026-10-01" and s["revenue"] > 0 and s["quickbooks_text"].startswith("Books through Oct 2026")
    assert client.get(f"{P}/metrics").status_code == 200
    ev = client.get(f"{P}/ev").get_json()
    assert ev["rows"][0]["basis_noi"] is not None
    assert client.get(f"{P}/compare").status_code == 200
