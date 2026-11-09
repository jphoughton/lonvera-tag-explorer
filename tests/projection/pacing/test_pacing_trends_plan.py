"""/api/trends/plan: the Trends page's Projected* | Target | % Diff block is the Pacing page's own plan block (views.plan_block), laid out, not recomputed."""
import pytest

BASE = "/api/projection/pacing"


def test_plan_endpoint_equals_the_pacing_rollup_plan_rows(api):
    j = api.get("/api/trends/plan?from=2026-08-01&to=2026-11-01").get_json()
    ref = api.get(f"{BASE}/rollup?grain=month&from=2026-08-01&bands=projection against the plan").get_json()
    assert j["available"] is True and j["months"] == ref["months"] == ["2026-08-01", "2026-09-01", "2026-10-01", "2026-11-01"]
    by = {r["key"]: r["values"] for r in ref["rows"] if r.get("values") is not None}
    for key in ("total_rev", "total_spend", "nc_rev", "ebitda", "noi"):
        for suf in ("proj", "goal", "diff"):
            got = j["values"][f"plan_{key}_{suf}"]
            exp = by[f"plan_{key}_{suf}"]
            assert got == pytest.approx(exp, rel=1e-5, abs=1e-6), (key, suf)
    for key in ("ebitda", "noi"):
        gap = j["values"][f"plan_{key}_gap"]
        for p, g, x in zip(j["values"][f"plan_{key}_proj"], j["values"][f"plan_{key}_goal"], gap):
            assert x == pytest.approx(p - g, abs=1e-3) if p is not None and g is not None else x is None
    assert j["status"][-1] == "projected" and j["status"][0] == "actual"                      # the running month is a projection, closed months are actuals
    assert j["values"]["plan_total_rev_proj"][-1] == pytest.approx(73136.3216 / 2 * 30)       # the same pace-over-the-month figure the Pacing page tests pin


def test_plan_endpoint_validation_and_failure_is_a_200_with_the_reason(api, monkeypatch):
    assert api.get("/api/trends/plan?from=nonsense&to=2026-11-01").status_code == 400
    assert api.get("/api/trends/plan?from=2026-11-01&to=2026-01-01").status_code == 400
    assert api.get("/api/trends/plan?from=2020-01-01&to=2026-11-01").status_code == 400      # more than 36 months
    from app import trends

    def boom():
        raise RuntimeError("engine down")
    monkeypatch.setattr(trends, "_pacing_setup", boom)
    r = api.get("/api/trends/plan?from=2026-10-01&to=2026-11-01")
    assert r.status_code == 200
    j = r.get_json()
    assert j["available"] is False and "engine down" in j["reason"] and j["months"] == [] and j["values"] == {}
