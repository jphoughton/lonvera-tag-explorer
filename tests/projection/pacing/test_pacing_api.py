"""Pacing API: payloads, validation, read-only, auth, and the module wiring (MODULE, load_inputs, compute, reconcile map)."""
import datetime as dt

import pytest

from app.projection import registry
from app.projection.interfaces import Scenario
from app.projection.pacing import MODULE
from app.projection.pacing.model import PacingResult, automated_columns, compute, load_inputs
from app.projection.pacing.reconcile_map import MAP

BASE = "/api/projection/pacing"


def test_module_is_registered_with_its_page():
    info = registry.discover(refresh=True)["pacing"]
    assert info.built, info.error
    assert MODULE.key == "pacing" and MODULE.ui == "pacing.js" and MODULE.blueprint is not None
    listing = {m["key"]: m for m in registry.listing()}
    assert listing["pacing"]["built"] and listing["pacing"]["ui"] == "pacing.js"


def test_compute_through_the_module_contract(conn, pnl, cohorts, book):
    scn = Scenario(id=1, slug="current", name="Current plan", params={})
    inputs = load_inputs(conn, scn)
    res = compute(scn, inputs, pnl=pnl, cohorts=cohorts)                    # row map of the real pnl module is not used by the double: keys are row_<n>
    assert isinstance(res, PacingResult) and res.month == dt.date(2026, 11, 1) and res.data_through == dt.date(2026, 11, 2)
    assert res.summary["days_elapsed"] == 2 and len(res.automated["C"]) == 47
    # the P&L is required: without it the engine marks pacing degraded instead of pacing against goals of zero
    with pytest.raises(TypeError):
        compute(scn, inputs)


def _engine_result(res):
    from app.projection.engine import EngineResult
    return EngineResult(scenario=Scenario(1, "current", "x", {}), actual_through=None, results={"pacing": res})


def test_reconcile_map_points_at_resolvable_paths(conn, pnl, cohorts, monkeypatch):
    from app.projection.pacing import targets
    from app.projection.pacing.model import FULL_ENV
    from snapshot_pnl import ROW_MAP
    monkeypatch.setattr(targets, "default_row_map", lambda: dict(ROW_MAP))
    scn = Scenario(1, "current", "x", {})
    plain = _engine_result(compute(scn, load_inputs(conn), pnl=pnl, cohorts=cohorts))
    for entry in MAP:
        if entry["result"].startswith("pacing.automated"):
            assert len(plain.resolve(entry["result"])) == 47, entry["key"]
            assert entry["kind"] == "column" and entry["r0"] == 4 and entry["r1"] == 50
    monkeypatch.setenv(FULL_ENV, "1")
    full = _engine_result(compute(scn, load_inputs(conn), pnl=pnl, cohorts=cohorts))
    assert len(MAP) > 400
    for entry in MAP:
        got = full.resolve(entry["result"])                                    # every entry resolves in full mode
        assert (len(got) in (47, 22)) if entry["kind"] == "column" else (got is None or isinstance(got, (int, float))), entry["key"]


def test_month_endpoint_payload(api):
    r = api.get(f"{BASE}/month")
    assert r.status_code == 200
    j = r.get_json()
    assert j["month"] == "2026-11-01" and j["days_elapsed"] == 2 and j["early"] is True
    assert j["scenario"]["slug"] == "current" and j["tldr_line"].startswith("Revenue tracking")
    assert j["months"][0]["month"] == "2026-11-01" and j["months"][-1]["month"] == "2025-01-01"
    fresh = {f["feed"]: f["status"] for f in j["freshness"]}
    assert len(fresh) == 12 and fresh.pop("meta_ad_daily") == "bad"                  # ad_daily is empty in the test database: no data is shown as such
    assert fresh.pop("hourly_curve") == "good"
    assert set(fresh.values()) == {"good"}
    by = {x["key"]: x for x in j["rows"]}
    assert by["total_rev"]["status"] == "good" and by["advertising"]["direction"] == "down"
    assert j["payback"]["available"] and j["payback"]["months"]["mtd"] == 10


def test_month_endpoint_other_month_and_validation(api):
    j = api.get(f"{BASE}/month?month=2026-10").get_json()
    assert j["month"] == "2026-10-01" and j["days_elapsed"] == 31 and j["early"] is False
    assert api.get(f"{BASE}/month?month=2030-01").status_code == 400
    assert api.get(f"{BASE}/month?month=garbage").status_code == 400
    assert api.get(f"{BASE}/month?as_of=nope").status_code == 400
    future = api.get(f"{BASE}/month?month=2026-12").get_json()
    assert future["rows"] == [] and future["days_elapsed"] == 0


def test_as_of_parameter_moves_the_pacing_date(api):
    j = api.get(f"{BASE}/month?as_of=2026-11-02").get_json()
    assert j["days_elapsed"] == 1 and j["data_through"] == "2026-11-01"
    first = api.get(f"{BASE}/month?as_of=2026-11-01").get_json()
    assert first["month"] == "2026-10-01" and first["days_elapsed"] == 31


def test_snapshot_endpoint(api):
    j = api.get(f"{BASE}/snapshot?month=2026-11").get_json()
    assert j["partial"] and j["scale"] == 15 and j["status_text"] == "Month to date: 2 of 30 days"
    keys = [r["key"] for r in j["rows"]]
    assert keys[0] == "total_rev" and "noi" in keys
    closed = api.get(f"{BASE}/snapshot?month=2026-10").get_json()
    assert closed["status_text"] == "Closed month" and closed["payback"]["months"]["actual"] is not None


def test_rollup_month_grain_has_plan_block_and_partial_flag(api):
    j = api.get(f"{BASE}/rollup?grain=month&from=2026-09-01").get_json()
    assert j["months"] == ["2026-09-01", "2026-10-01", "2026-11-01"] and j["status"] == ["actual", "actual", "projected"]
    assert j["days"] == [30, 31, 2] and j["days_in_period"] == [30, 31, 30]
    keys = [r["key"] for r in j["rows"]]
    assert keys[0] == "band:plan" and "plan_noi_proj" in keys and "biz_total_rev" in keys
    by = {r["key"]: r for r in j["rows"]}
    nov = by["plan_total_rev_proj"]["values"][-1]
    assert nov == pytest.approx(73136.3216 / 2 * 30)                          # projected over the month's 30 days
    assert by["plan_total_rev_diff"]["values"][-1] == pytest.approx(nov / by["plan_total_rev_goal"]["values"][-1] - 1)
    assert by["meta_unique_outbound_clicks"]["values"][1] > 0 and j["captions"]


def test_rollup_week_and_day_grains_and_meta_view(api):
    w = api.get(f"{BASE}/rollup?grain=week&from=2026-10-12").get_json()
    assert w["labels"][0].startswith("W42") or w["keys"][0] == "2026-W42"
    assert w["keys"][-1] == "2026-W45" and w["status"][-1] == "projected" and w["days"][-1] == 1
    assert "band:plan" not in [r["key"] for r in w["rows"]]                    # the plan block is monthly
    d = api.get(f"{BASE}/rollup?grain=day&from=2026-10-30&to=2026-11-09").get_json()
    assert d["months"] == ["2026-10-30", "2026-10-31", "2026-11-01", "2026-11-02"]       # clipped to yesterday
    m = api.get(f"{BASE}/rollup?grain=day&view=meta&from=2026-11-01").get_json()
    assert m["view"] == "meta" and [r["key"] for r in m["rows"]][:2] == ["band:Meta campaigns: All Meta", "all_spend"]
    assert len(m["bands"]) == 6


def test_rollup_delta_and_band_filter(api):
    j = api.get(f"{BASE}/rollup?grain=month&from=2026-09-01&delta=1&bands=Google").get_json()
    row = {r["key"]: r for r in j["rows"]}["google_spend"]
    assert row["delta"][0] is None and row["delta"][1] == pytest.approx((row["values"][1] - row["values"][0]) / abs(row["values"][0]))
    assert [r["key"] for r in j["rows"] if r["kind"] == "header"] == ["band:Google"]


@pytest.mark.parametrize("qs", ["grain=year", "view=nope", "grain=day&from=2025-01-01&to=2026-11-02", "from=nonsense"])
def test_rollup_validation(api, qs):
    assert api.get(f"{BASE}/rollup?{qs}").status_code == 400


def test_catalog_and_freshness_endpoints(api):
    c = api.get(f"{BASE}/catalog").get_json()
    assert "Business-wide" in c["bands"] and any(m["key"] == "dtc_nc_roas" for m in c["metrics"])
    f = api.get(f"{BASE}/freshness").get_json()
    assert f["expected_through"] == "2026-11-02" and {x["feed"] for x in f["feeds"]} >= {"orders", "amazon", "platform", "meta_ad_daily", "anyclick", "traffic", "meta_reach"}
    stale = api.get(f"{BASE}/freshness?as_of=2026-11-12").get_json()["feeds"]
    ages = {x["feed"]: (x["age_days"], x["status"]) for x in stale}
    assert ages["orders"] == (9, "bad") and ages["amazon"][0] == 8


def test_intraday_endpoint(api):
    j = api.get(f"{BASE}/intraday?spent=7528&time=19:52").get_json()
    assert j["loaded"] and j["projected_eod"] == pytest.approx(7528 / j["share_of_day"], rel=1e-6) and j["to_go"] == pytest.approx(j["projected_eod"] - 7528)
    assert api.get(f"{BASE}/intraday?spent=abc").status_code == 400
    assert api.get(f"{BASE}/intraday?spent=10&time=noon").status_code == 400
    blank = api.get(f"{BASE}/intraday?time=10:00").get_json()
    assert blank["projected_eod"] is None


def test_the_module_is_read_only(api):
    for path in ("month", "snapshot", "rollup", "freshness"):
        r = api.post(f"{BASE}/{path}", json={})
        assert r.status_code in (404, 405), path
        assert api.put(f"{BASE}/{path}", json={}).status_code in (404, 405)
        assert api.delete(f"{BASE}/{path}", json={}).status_code in (404, 405)


def test_login_on_means_401_without_a_session_and_open_when_auth_is_disabled(api_app, pacing_db):
    anon = api_app.test_client()
    for path in ("month", "snapshot", "rollup", "catalog", "freshness", "intraday"):
        assert anon.get(f"{BASE}/{path}").status_code == 401, path
    from pacing_support import make_app
    open_app = make_app(pacing_db, AUTH_DISABLED=True)
    try:
        assert open_app.test_client().get(f"{BASE}/catalog").status_code == 200
    finally:
        open_app.extensions["pool"].close()


def test_noindex_header_on_the_api(api):
    assert "noindex" in api.get(f"{BASE}/catalog").headers.get("X-Robots-Tag", "")


def test_full_mode_adds_the_rollups_the_reconcile_compares(conn, pnl, cohorts, monkeypatch):
    import snapshot_read as sr
    from app.projection.pacing.model import FULL_ENV
    from app.projection.pacing import targets
    from snapshot_pnl import ROW_MAP
    monkeypatch.setattr(targets, "default_row_map", lambda: dict(ROW_MAP))
    monkeypatch.setenv(FULL_ENV, "1")
    scn = Scenario(id=1, slug="current", name="Current plan", params={})
    res = compute(scn, load_inputs(conn, scn), pnl=pnl, cohorts=cohorts)
    sheet = lambda tab, ref: sr.cell(tab, ref)                                 # noqa: E731
    assert res.rollup_month["biz_total_rev"] == pytest.approx(sheet("Rollup - Monthly (MoM)", "W26"), rel=0.005)          # Oct 2026
    assert res.rollup_week["dtc_total_rev"] == pytest.approx(sheet("Rollup - Weekly (WoW)", "Q100"), rel=0.005)           # 2026-W44
    assert res.rollup_day["dtc_total_rev"] == pytest.approx(sheet("Pacing - Daily (180d)", "P675"), rel=0.005)            # Nov 2
    assert res.snapshot["B"][8 - 7] == pytest.approx(sheet("Month Snapshot", "B8"))
    assert res.snapshot["B"][28 - 7] == 10 and res.snapshot["D"][28 - 7] == 5                                       # payback months
    monkeypatch.delenv(FULL_ENV)
    assert compute(scn, load_inputs(conn, scn), pnl=pnl, cohorts=cohorts).rollup_month == {}
