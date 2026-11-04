"""pnl API: table, summary, ev, compare, drivers/settings edits (audited, versioned), QuickBooks map, upload preview/commit, auth."""
from __future__ import annotations

import io
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.projection.pnl import lines as L, store            # noqa: E402
from pnl_support import TODAY, login, seed_basic                # noqa: E402
from pnl_helpers import make_xlsx, qb_rows, to_csv          # noqa: E402

P = "/api/projection/pnl"
S = "/api/projection"

DRIVERS = [
    ("amazon_fba_fees", [{"from": None, "mode": "rate", "base": "amazon_rev", "rate_kind": "window"}]),
    ("china_cogs", [{"from": None, "mode": "rate", "base": "total_revenue", "rate_kind": "typed", "rate": 0.1}]),
    ("fulfillment_services", [{"from": None, "mode": "rate", "base": "dtc_rev", "rate_kind": "window"}]),
    ("marketing_agency", [{"from": None, "mode": "carry"}]),
    ("accounting", [{"from": None, "mode": "fixed", "value": 2500.0}]),
    ("marketing_software", [{"from": None, "mode": "rate", "base": "total_revenue", "rate_kind": "window"}]),
    ("headcount", [{"from": None, "mode": "headcount"}]),
]


@pytest.fixture()
def data(pg):
    seed_basic(pg)
    store.upsert_driver_defaults(pg, [(k, s, "n", "test") for k, s in DRIVERS])
    store.upsert_qb_map(pg, [("shopify sales", "dtc_rev", None), ("amazon sales", "amazon_rev", None), ("amazon fba fees", "amazon_fba_fees", None),
                             ("china cogs", "china_cogs", None), ("other business expenses", "other_direct", None), ("bank fees", "bank_fees", None),
                             ("accounting", "accounting", None)])
    return pg


def audit_rows(pg, action):
    return pg.execute("SELECT * FROM proj_audit WHERE action = %s ORDER BY id", (action,)).fetchall()


# ------------------------------------------------------------------------------------------------------------------ reads

def test_table_payload_shape(client, data):
    t = client.get(f"{P}/table?scenario=current").get_json()
    assert len(t["months"]) == 60 and len(t["status"]) == 60
    assert t["status"][:19].count("actual") == 19 and t["status"][19] == "projected"          # Jan-25 .. Jul-26 are actual (12 loaded + earlier are blank)
    assert t["actual_through"] == "2026-07-01"
    keys = [r["key"] for r in t["rows"]]
    assert keys.count("noi") == 1 and "hdr_revenue" in keys
    rows = {r["key"]: r for r in t["rows"]}
    assert rows["noi"]["kind"] == "total" and rows["gross_profit_pct"]["kind"] == "ratio" and rows["gross_profit_pct"]["fmt"] == "pct"
    assert all(r["input"] is False for r in t["rows"])                      # editing happens through the driver table, not the grid
    assert rows["hdr_revenue"]["kind"] == "header" if "hdr_revenue" in rows else True
    assert all(len(r["values"]) == 60 for r in t["rows"])
    assert t["fy"]["years"] == [2025, 2026, 2027, 2028, 2029] and len(t["fy"]["rows"]["noi"]) == 5
    assert "QuickBooks through Jul 2026" in t["captions"][0]
    assert "note" in rows["amazon_fba_fees"] and "Projected months" in rows["amazon_fba_fees"]["note"]


def test_every_value_is_json_safe_and_projected_months_are_numbers(client, data):
    t = client.get(f"{P}/table").get_json()
    noi = next(r for r in t["rows"] if r["key"] == "noi")
    assert all(isinstance(v, (int, float)) for v in noi["values"])


def test_summary_headline_and_quickbooks_text(client, data):
    s = client.get(f"{P}/summary").get_json()
    assert s["quickbooks_text"] == "QuickBooks loaded through Jul 2026; Aug-Oct are projected"
    assert s["basis_year"] == 2027 and s["noi"] is not None and s["ev_low"] <= s["ev_high"] and s["loi"] == 7_500_000.0
    assert s["freshness"]["feed"] == "quickbooks_pnl" and s["freshness"]["status"] == "stale"          # Aug (closed 8/31) was due by Oct 15


def test_summary_with_nothing_loaded_says_so(client, pg):
    s = client.get(f"{P}/summary").get_json()
    assert s["quickbooks_text"] == "No QuickBooks history is loaded" and s["freshness"]["status"] == "unknown"


def test_metrics_table(client, data):
    t = client.get(f"{P}/metrics").get_json()
    assert {r["key"] for r in t["rows"]} >= {"ttm_noi", "noi_yoy", "dtc_rev_mom"}


def test_ev_and_compare_across_scenarios(client, data):
    clone = client.post(f"{S}/scenarios", json={"name": "Lean", "clone_from": "current"}).get_json()["scenario"]["slug"]
    client.put(f"{P}/drivers", json={"scenario": clone, "key": "accounting", "segments": [{"from": None, "mode": "fixed", "value": 90_000}]})
    ev = client.get(f"{P}/ev").get_json()
    assert ev["multiples"] == [3, 4, 5, 6, 7, 8] and ev["loi"] == 7_500_000.0 and ev["year"] == 2027
    by = {r["slug"]: r for r in ev["rows"]}
    assert by[clone]["basis_noi"] < by["current"]["basis_noi"]
    assert by["current"]["values"]["3x"] == pytest.approx(by["current"]["basis_noi"] * 3)
    assert by["current"]["vs_loi"]["3x"] == pytest.approx(by["current"]["values"]["3x"] - 7_500_000.0)
    cmp_ = client.get(f"{P}/compare?scenarios=current,{clone}").get_json()
    assert cmp_["years"] == [2025, 2026, 2027, 2028, 2029] and [s["slug"] for s in cmp_["scenarios"]] == ["current", clone]
    assert client.get(f"{P}/compare?scenarios=nope").status_code == 404


def test_unknown_scenario_is_404_for_reads(client, data):
    for path in ("table", "summary", "drivers", "metrics"):
        assert client.get(f"{P}/{path}?scenario=missing").status_code == 404


# ------------------------------------------------------------------------------------------------------------------ driver and setting edits

def test_drivers_payload_lists_every_driven_line_with_its_resolution(client, data):
    d = client.get(f"{P}/drivers").get_json()
    items = {i["key"]: i for g in d["groups"] for i in g["items"]}
    assert set(items) == set(L.DRIVER_KEYS + L.DRIVEN_PARENTS)
    assert items["amazon_fba_fees"]["rate"] == pytest.approx(0.1) and "Apr" in items["amazon_fba_fees"]["window_text"]
    assert items["accounting"]["value"] == 2500.0 and items["accounting"]["text"].startswith("$2,500")
    assert d["window_text"] and d["settings"]["engine"]["rate_window_months"] == 4
    assert d["actual_through"] == "2026-07-01" and d["vendors"] == []


def test_put_driver_edits_the_scenario_audits_and_bumps_the_version(client, data):
    v0 = client.get(f"{S}/scenarios").get_json()["scenarios"][0]["version"]
    r = client.put(f"{P}/drivers", json={"scenario": "current", "key": "accounting", "segments": [{"from": None, "mode": "fixed", "value": 4000}]})
    assert r.status_code == 200 and r.get_json()["scenario"]["version"] == v0 + 1
    rows = audit_rows(data, "scenario.params")
    assert len(rows) == 1 and rows[0]["new"]["params"]["pnl"]["drivers"]["accounting"]["segments"][0]["value"] == 4000.0
    t = client.get(f"{P}/table").get_json()
    acc = next(x for x in t["rows"] if x["key"] == "accounting")
    assert acc["values"][t["months"].index("2026-09-01")] == 4000.0
    d = client.get(f"{P}/drivers").get_json()
    assert next(i for g in d["groups"] for i in g["items"] if i["key"] == "accounting")["overridden"] is True
    # reset to the default
    client.put(f"{P}/drivers", json={"scenario": "current", "key": "accounting", "segments": None})
    t = client.get(f"{P}/table").get_json()
    assert next(x for x in t["rows"] if x["key"] == "accounting")["values"][t["months"].index("2026-09-01")] == 2500.0


def test_driver_edit_is_restorable(client, data):
    client.put(f"{P}/drivers", json={"scenario": "current", "key": "accounting", "segments": [{"from": None, "mode": "fixed", "value": 4000}]})
    assert client.post(f"{S}/scenarios/current/restore", json={"version": 1}).status_code == 200
    t = client.get(f"{P}/table").get_json()
    assert next(x for x in t["rows"] if x["key"] == "accounting")["values"][t["months"].index("2026-09-01")] == 2500.0


@pytest.mark.parametrize("body,frag", [
    ({"key": "noi", "segments": [{"from": None, "mode": "carry"}]}, "driven line"),
    ({"key": "accounting", "segments": [{"from": None, "mode": "fixed", "value": "lots"}]}, "numeric"),
    ({"key": "accounting", "segments": []}, "1-12"),
    ({"key": "accounting", "segments": [{"from": "2027-01-10", "mode": "carry"}]}, "first of a month"),
    ({"segments": [{"from": None, "mode": "carry"}]}, "driven line"),
])
def test_put_driver_validation(client, data, body, frag):
    r = client.put(f"{P}/drivers", json={"scenario": "current", **body})
    assert r.status_code == 400 and frag in r.get_json()["error"]
    assert audit_rows(data, "scenario.params") == []


def test_put_driver_unknown_scenario_and_stale_version(client, data):
    seg = [{"from": None, "mode": "carry"}]
    assert client.put(f"{P}/drivers", json={"scenario": "ghost", "key": "accounting", "segments": seg}).status_code == 404
    assert client.put(f"{P}/drivers", json={"scenario": "current", "key": "accounting", "segments": seg, "expected_version": 99}).status_code == 409
    assert client.put(f"{P}/drivers", json={"key": "accounting", "segments": seg}).status_code == 400


def test_put_settings_changes_the_window_and_the_rate(client, data):
    t0 = client.get(f"{P}/table").get_json()
    r = client.put(f"{P}/settings", json={"scenario": "current", "settings": {"engine": {"rate_window_months": 2}}})
    assert r.status_code == 200
    d = client.get(f"{P}/drivers").get_json()
    assert d["settings"]["engine"]["rate_window_months"] == 2 and len(d["window"]) == 2
    assert audit_rows(data, "scenario.params")
    assert t0["months"] == client.get(f"{P}/table").get_json()["months"]


@pytest.mark.parametrize("settings", [
    {"engine": {"rate_window_months": 0}}, {"engine": {"rate_window_months": 2.5}}, {"engine": {"rate_window_months": "4"}},
    {"engine": {"opex_inflation": 5}}, {"headcount": {"start": "2026-09-15"}}, {"cogs": {"us_share": 1.5}}, {"cogs": {"price_per_bottle": 0}},
    {"ev": {"multiples": []}}, {"ev": {"year": 2031}}, {"bogus": {"x": 1}}, {"engine": {"nope": 1}}, {}, {"cogs": {"mix": [1]}},
])
def test_put_settings_validation(client, data, settings):
    r = client.put(f"{P}/settings", json={"scenario": "current", "settings": settings})
    assert r.status_code == 400
    assert audit_rows(data, "scenario.params") == []


def test_ev_year_and_multiples_are_levers(client, data):
    client.put(f"{P}/settings", json={"scenario": "current", "settings": {"ev": {"year": 2028, "multiples": [4, 6]}}})
    ev = client.get(f"{P}/ev?scenarios=current").get_json()
    assert ev["year"] == 2028 and ev["multiples"] == [4, 6] and set(ev["rows"][0]["values"]) == {"4x", "6x"}


# ------------------------------------------------------------------------------------------------------------------ QuickBooks map

def test_qb_map_crud_is_audited(client, data):
    r = client.put(f"{P}/qb-map", json={"account": "  Sales:  Shopify ", "line_key": "dtc_rev"})
    assert r.status_code == 200 and r.get_json()["account"] == "sales: shopify"
    m = client.get(f"{P}/qb-map").get_json()
    assert any(x["account"] == "sales: shopify" and x["line_key"] == "dtc_rev" for x in m["map"]) and any(l["key"] == "noi" for l in m["lines"]) is False
    assert len(audit_rows(data, "pnl.qb_map")) == 1
    assert client.delete(f"{P}/qb-map", json={"account": "Sales: Shopify"}).status_code == 200
    assert client.delete(f"{P}/qb-map", json={"account": "Sales: Shopify"}).status_code == 404
    assert len(audit_rows(data, "pnl.qb_map.delete")) == 1


def test_qb_map_rejects_computed_lines_and_blank_accounts(client, data):
    assert client.put(f"{P}/qb-map", json={"account": "Profit", "line_key": "noi"}).status_code == 400
    assert client.put(f"{P}/qb-map", json={"account": "  ", "line_key": "dtc_rev"}).status_code == 400


# ------------------------------------------------------------------------------------------------------------------ upload

def post_file(client, data_bytes, name="export.csv", commit=False, token=None):
    form = {"file": (io.BytesIO(data_bytes), name)}
    if token is not None:
        form["diff_sha256"] = token
    return client.post(f"{P}/upload" + ("?commit=1" if commit else ""), data=form, content_type="multipart/form-data")


def fresh_months(pg):
    pg.execute("DELETE FROM proj_actuals")                           # an empty books table so the file's months are new


def test_upload_preview_then_commit_loads_actuals_and_audits_the_file(client, data):
    fresh_months(data)
    raw = to_csv(qb_rows())
    pv = post_file(client, raw)
    assert pv.status_code == 200
    j = pv.get_json()
    assert j["committed"] is False and j["cells"]["new"] == 3 * 7 and j["cells"]["changed"] == 0
    assert [u["account"] for u in j["unmapped"]] == [] and {m["month"] for m in j["months"]} == {"2026-01-01", "2026-02-01", "2026-03-01"}
    assert data.execute("SELECT count(*) AS n FROM proj_actuals").fetchone()["n"] == 0                       # a preview writes nothing
    # no token / wrong token
    assert post_file(client, raw, commit=True).status_code == 400
    assert post_file(client, raw, commit=True, token="0" * 64).status_code == 409
    ok = post_file(client, raw, commit=True, token=j["diff_sha256"])
    assert ok.status_code == 200 and ok.get_json()["committed"] is True and ok.get_json()["cells_written"] == 21
    v = data.execute("SELECT value FROM proj_actuals WHERE line_key = 'pnl.dtc_rev' AND month = '2026-02-01'").fetchone()["value"]
    assert float(v) == 2000.0
    a = audit_rows(data, "pnl.upload")
    assert len(a) == 1 and len(a[0]["new"]["sha256"]) == 64 and a[0]["new"]["cells"] == 21 and a[0]["note"] == "export.csv"
    assert a[0]["actor"] == "JoshHoughton"
    t = client.get(f"{P}/table").get_json()
    assert t["actual_through"] == "2026-03-01"


def test_upload_shows_the_diff_before_overwriting_a_loaded_month(client, data):
    fresh_months(data)
    raw = to_csv(qb_rows())
    j = post_file(client, raw).get_json()
    post_file(client, raw, commit=True, token=j["diff_sha256"])
    edited = to_csv(qb_rows()).replace(b"2000.0", b"2500.0")
    pv = post_file(client, edited, "edited.csv").get_json()
    assert pv["cells"]["changed"] == 1 and pv["cells"]["same"] == 20
    assert pv["changes"] == [{"line_key": "dtc_rev", "month": "2026-02-01", "old": 2000.0, "new": 2500.0}]
    assert any("would be replaced" in n for n in pv["notes"])
    # a token from the OTHER file does not commit this one
    assert post_file(client, edited, "edited.csv", commit=True, token=j["diff_sha256"]).status_code == 409
    assert post_file(client, edited, "edited.csv", commit=True, token=pv["diff_sha256"]).status_code == 200
    v = data.execute("SELECT value FROM proj_actuals WHERE line_key = 'pnl.dtc_rev' AND month = '2026-02-01'").fetchone()["value"]
    assert float(v) == 2500.0


def test_upload_unmapped_accounts_are_listed_and_mapping_fixes_the_next_preview(client, data):
    fresh_months(data)
    data.execute("DELETE FROM proj_pnl_qb_map WHERE account = 'shopify sales'")
    j = post_file(client, to_csv(qb_rows())).get_json()
    assert [u["account"] for u in j["unmapped"]] == ["Shopify Sales"] and j["unmapped"][0]["total"] == 6000.0
    client.put(f"{P}/qb-map", json={"account": "Shopify Sales", "line_key": "dtc_rev"})
    assert post_file(client, to_csv(qb_rows())).get_json()["unmapped"] == []


def test_upload_xlsx_equals_csv(client, data):
    fresh_months(data)
    a = post_file(client, to_csv(qb_rows())).get_json()
    b = post_file(client, make_xlsx(qb_rows()), "e.xlsx").get_json()
    assert a["diff_sha256"] == b["diff_sha256"]


def test_upload_skips_open_months_and_refuses_a_file_with_nothing_to_load(client, data):
    fresh_months(data)
    rows = [["", "Oct 2026", "Nov 2026"], ["Accounting", 1, 2]]
    pv = post_file(client, to_csv([["", "Oct 2026", "Nov 2026", "Dec 2026"], ["Accounting", 1, 2, 3]])).get_json()
    assert pv["cells"]["total"] == 1 and {s["month"] for s in pv["skipped_months"]} == {"2026-11-01", "2026-12-01"}
    nothing = to_csv([["", "Nov 2026", "Dec 2026", "Jan 2027"], ["Accounting", 1, 2, 3]])
    pv = post_file(client, nothing).get_json()
    assert pv["committable"] is False
    assert post_file(client, nothing, commit=True, token=pv["diff_sha256"]).status_code == 400


@pytest.mark.parametrize("raw,name", [(b"", "a.csv"), (b"not a statement", "a.csv"), (b"PK\x03\x04junk", "a.xlsx")])
def test_upload_bad_files_are_400_with_a_message(client, data, raw, name):
    r = post_file(client, raw, name)
    assert r.status_code == 400 and r.get_json()["error"]


def test_upload_without_a_file_is_400(client, data):
    r = client.post(f"{P}/upload", data={}, content_type="multipart/form-data")
    assert r.status_code == 400


def test_upload_rejects_json_bodies_and_cross_origin(client, data):
    assert client.post(f"{P}/upload", json={"x": 1}).status_code == 400
    r = client.post(f"{P}/upload", data={"file": (io.BytesIO(b"x"), "a.csv")}, content_type="multipart/form-data", headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


# ------------------------------------------------------------------------------------------------------------------ auth and freshness

def test_endpoints_need_the_apps_login_and_open_when_it_is_off(app_factory, upstream_pkg, monkeypatch, data):
    from app.projection import registry
    monkeypatch.setattr(registry, "PACKAGE", upstream_pkg)
    on, off = app_factory().test_client(), app_factory(AUTH_DISABLED=True).test_client()
    calls = [("get", "/table"), ("get", "/summary"), ("get", "/drivers"), ("get", "/ev"), ("put", "/drivers"), ("put", "/settings"),
             ("put", "/qb-map"), ("post", "/upload")]
    for method, path in calls:
        closed = getattr(on, method)(P + path, json={})
        opened = getattr(off, method)(P + path, json={})
        assert closed.status_code == 401, (method, path)
        assert opened.status_code != 401, (method, path)


def test_freshness_entry_rule(pg):
    from app.projection.pnl.store import freshness_entries
    assert freshness_entries(pg, date(2026, 11, 3))[0]["data_through"] is None
    seed_basic(pg, through=date(2026, 7, 1))
    e = freshness_entries(pg, date(2026, 11, 3))[0]
    assert e["feed"] == "quickbooks_pnl" and e["cadence"] == "monthly-upload" and e["data_through"] == date(2026, 7, 31)
    assert e["stale_after_days"] == 45 + 31                                      # 45 days plus the length of August, the first month not loaded
    from app.projection import freshness as F
    assert F.evaluate("pnl", e, date(2026, 10, 14))["status"] == "fresh"            # Aug closed 8/31: 44 days
    assert F.evaluate("pnl", e, date(2026, 10, 16))["status"] == "stale"            # 46 days
    pg.execute("DELETE FROM proj_actuals")
    seed_basic(pg, through=date(2026, 9, 1))
    e = freshness_entries(pg, date(2026, 11, 3))[0]
    assert F.evaluate("pnl", e, date(2026, 11, 3))["status"] == "fresh"             # Oct closed 10/31: 3 days


def test_freshness_hook_through_etl_and_endpoint(client, data):
    from etl import projection_pnl
    assert projection_pnl.freshness(data, date(2026, 11, 3))[0]["feed"] == "quickbooks_pnl"
    f = client.get(f"{P}/freshness").get_json()
    assert f["status"] == "stale" and f["data_through"] == "2026-07-31"


def test_freshness_threshold_is_a_setting(client, data):
    base = client.get(f"{P}/freshness").get_json()
    assert base["late_after_days"] == 45 and base["status"] == "stale" and base["stale_after_days"] == 45 + 31
    r = client.put(f"{P}/freshness", json={"stale_after_days": 100})
    assert r.status_code == 200 and r.get_json() == {"stale_after_days": 100}
    a = audit_rows(data, "pnl.setting")
    assert len(a) == 1 and a[0]["old"] == {"value": 45} and a[0]["new"] == {"value": 100} and a[0]["line_key"] == "pnl.freshness.stale_after_days"
    moved = client.get(f"{P}/freshness").get_json()
    assert moved["stale_after_days"] == 131 and moved["status"] == "fresh"                    # 95 days old, allowed 131
    s = client.get(f"{P}/summary").get_json()
    assert s["next_due"] == {"month": "2026-08-01", "due_by": "2026-12-09"} and s["freshness"]["status"] == "fresh"
    assert client.put(f"{P}/freshness", json={"stale_after_days": 100}).status_code == 200      # unchanged: no second audit row
    assert len(audit_rows(data, "pnl.setting")) == 1
    for bad in (0, 400, "45", None, 2.5):
        assert client.put(f"{P}/freshness", json={"stale_after_days": bad}).status_code == 400


def test_cogs_unit_costs_and_trend_window_validate_and_save(client, data):
    ok = client.put(f"{P}/settings", json={"scenario": "current", "settings": {"cogs": {"china_cost": {"LV-VBULL": 3.1}, "us_cost": {"LV-VBULL": 4.2}, "mix": {"LV-VBULL": 0.6}},
                                                                                      "trend": {"avg_window": 3}}})
    assert ok.status_code == 200
    d = client.get(f"{P}/drivers").get_json()
    assert d["settings"]["trend"]["avg_window"] == 3 and d["settings"]["cogs"]["china_cost"] == {"LV-VBULL": 3.1}
    for bad in ({"trend": {"avg_window": 0}}, {"cogs": {"china_cost": {"X": -1}}}, {"cogs": {"us_cost": {"X": 5000}}}, {"cogs": {"china_cost": [1]}}):
        assert client.put(f"{P}/settings", json={"scenario": "current", "settings": bad}).status_code == 400
