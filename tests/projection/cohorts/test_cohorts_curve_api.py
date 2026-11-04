"""Curve picker API: /curve-options, /curve-compare (read only), and saving the choice through the audited lever path."""
from __future__ import annotations

from datetime import date

import pytest
from conftest import build_history, seed_cohorts

from app.projection import rwdb
from app.projection.cohorts import model as cohorts_model

P = "/api/projection"
C = f"{P}/cohorts"
TODAY = date(2026, 11, 3)


@pytest.fixture(autouse=True)
def _frozen(monkeypatch, tmp_path):
    monkeypatch.setattr(cohorts_model, "today", lambda: TODAY)
    monkeypatch.setenv("PROJECTION_SNAPSHOT_DIR", str(tmp_path))


@pytest.fixture()
def loaded(cohorts_db):
    """Cohorts Aug-24 .. Oct-26 with 14 observed months for the oldest: as-of months Oct-25 .. Oct-26 can be built with 3 cohorts, Jan-26 .. with 6."""
    with rwdb.get_rw(cohorts_db) as conn:
        seed_cohorts(conn, build_history(first=date(2024, 8, 1), last=date(2026, 10, 1), ratios=(0.2, 0.3, 0.2, 0.1, 0.05, 0.04, 0.03, 0.03, 0.02, 0.02, 0.02, 0.02, 0.01, 0.01)),
                     goal=[0.2, 0.1] + [0.01] * 58)


def counts(pg):
    q = lambda t: pg.execute(f"SELECT count(*) AS n FROM {t}").fetchone()["n"]            # noqa: E731
    return (q("proj_audit"), q("proj_series"), q("proj_scenario_history"), q("proj_cohorts"), q("proj_cohort_cum"), q("proj_curve_goal"),
            pg.execute("SELECT version FROM proj_scenarios WHERE slug = 'current'").fetchone()["version"])


# ---------------------------------------------------------------------------------------------------------------- options

def test_options_list_the_sources_n_choices_and_every_month(client, loaded):
    o = client.get(f"{C}/curve-options").get_json()
    assert [s["key"] for s in o["sources"]] == ["live", "goal", "asof"] and [s["label"] for s in o["sources"]] == ["Current", "Goal", "As of month"]
    assert o["n_choices"] == [3, 6] and o["n"] == 3 and o["saved"]["label"] == "Current, last 3 cohorts" and o["saved"]["curve_source"] == "live"
    months = {m["month"]: m for m in o["asof_months"]}
    assert min(months) == "2024-08-01" and max(months) == "2026-10-01" and o["latest_month"] == "2026-10-01"
    ok = [m for m in o["asof_months"] if m["computable"]]
    assert ok[0]["month"] == "2025-10-01" and ok[0]["label"] == "Oct 2025" and ok[-1]["is_latest"] and ok[-1]["month"] == "2026-10-01"
    assert all(m["lifetime"] > 0 and m["twelve"] > 0 for m in ok) and not months["2024-08-01"]["computable"] and months["2024-08-01"]["lifetime"] is None
    assert o["default_asof"] == "2026-08-01" and o["goal_snapshot"] == "2026-09-29" and "12 months" in o["needs"]


def test_options_for_six_cohorts_start_later_and_n_is_validated(client, loaded):
    first = lambda n: next(m["month"] for m in client.get(f"{C}/curve-options?n={n}").get_json()["asof_months"] if m["computable"])      # noqa: E731
    assert first(6) > first(3)
    for bad in ("0", "37", "abc"):
        assert client.get(f"{C}/curve-options?n={bad}").status_code == 400


def test_options_with_no_data_have_no_months_and_no_default(client):
    o = client.get(f"{C}/curve-options").get_json()
    assert o["asof_months"] == [] and o["default_asof"] is None and o["latest_month"] is None


def test_options_follow_the_scenario(client, loaded):
    client.post(f"{P}/scenarios", json={"name": "Six", "clone_from": "current"})
    client.put(f"{C}/levers", json={"scenario": "six", "levers": {"n_cohorts": 6}})
    assert client.get(f"{C}/curve-options?scenario=six").get_json()["n"] == 6 and client.get(f"{C}/curve-options").get_json()["n"] == 3
    assert client.get(f"{C}/curve-options?scenario=nope").status_code == 404


# ---------------------------------------------------------------------------------------------------------------- compare

def test_compare_returns_current_goal_and_as_of_with_the_agreed_fields(client, loaded):
    r = client.get(f"{C}/curve-compare?asof=2026-08&n=3")
    assert r.status_code == 200
    d = r.get_json()
    assert [o["key"] for o in d["options"]] == ["current", "goal", "asof"] and d["n"] == 3 and d["year"] == 2027 and d["ages"][0] == 1 and len(d["ages"]) == 60
    cur, goal, asof = d["options"]
    assert cur["label"] == "Current, last 3 cohorts" and asof["label"] == "As of Aug 2026, last 3 cohorts" and goal["label"] == "Goal, saved Sep 2026"
    for o in d["options"]:
        assert len(o["curve"]) == 60 and o["lifetime"] == pytest.approx(sum(o["curve"])) and o["twelve"] == pytest.approx(sum(o["curve"][:12]))
        assert set(o["fy"]) >= {"year", "repeat_rev", "total_revenue", "noi", "ev", "loi"} and set(o["vs_current"]) >= {"lifetime", "twelve", "repeat_rev", "total_revenue", "noi", "ev"}
    assert cur["vs_current"]["lifetime"] == 0 and cur["vs_current"]["noi"] in (0, 0.0, None)         # None: no P&L tables in this database
    assert goal["curve"][:2] == [0.2, 0.1] and goal["vs_current"]["lifetime"] == pytest.approx(goal["lifetime"] - cur["lifetime"])
    assert asof["curve"] != cur["curve"] and asof["overrides"] == {"n_cohorts": 3, "min_cohorts_per_age": 1, "curve_source": "asof", "asof_month": "2026-08-01"}
    assert [o["active"] for o in d["options"]] == [True, False, False] and d["saved_label"] == "Current, last 3 cohorts"


def test_compare_defaults_to_the_saved_or_default_month_and_adds_a_second_as_of(client, loaded):
    d = client.get(f"{C}/curve-compare").get_json()
    assert d["options"][2]["label"] == "As of Aug 2026, last 3 cohorts"
    d2 = client.get(f"{C}/curve-compare?asof=2026-08&asof2=2026-05").get_json()
    assert [o["key"] for o in d2["options"]] == ["current", "goal", "asof", "asof2"] and d2["options"][3]["label"] == "As of May 2026, last 3 cohorts"
    assert d2["options"][3]["curve"] != d2["options"][2]["curve"]


def test_compare_with_six_cohorts_changes_current_and_as_of_but_not_goal(client, loaded):
    d3 = client.get(f"{C}/curve-compare?asof=2026-08&n=3").get_json()["options"]
    d6 = client.get(f"{C}/curve-compare?asof=2026-08&n=6").get_json()["options"]
    assert d6[0]["label"] == "Current, last 6 cohorts" and d6[0]["curve"] != d3[0]["curve"] and d6[2]["curve"] != d3[2]["curve"] and d6[1]["curve"] == d3[1]["curve"]


def test_compare_writes_nothing(client, loaded, pg):
    before = counts(pg)
    for q in ("asof=2026-08&n=3", "asof=2026-05&n=6&asof2=2026-08", ""):
        assert client.get(f"{C}/curve-compare?{q}").status_code == 200
    assert counts(pg) == before


@pytest.mark.parametrize("q,fragment", [("asof=2026-08-15", "first of a month"), ("asof=soon", "month like"), ("asof=2024-12", "choose a month from Oct 2025 to Oct 2026"),
                                        ("asof=2026-08&n=0", "between 1 and 36"), ("asof=2026-08&n=x", "whole number"), ("asof=2026-08&asof2=2020-01", "choose a month from"),
                                        ("asof=2025-10&n=6", "choose a month from"), ("asof=2027-06", "choose a month from")])
def test_compare_rejects_months_that_cannot_be_built(client, loaded, q, fragment):
    r = client.get(f"{C}/curve-compare?{q}")
    assert r.status_code == 400 and fragment in r.get_json()["error"]


def test_compare_with_no_data_is_a_plain_400(client):
    r = client.get(f"{C}/curve-compare")
    assert r.status_code == 400 and "enough cohort history" in r.get_json()["error"]


# ---------------------------------------------------------------------------------------------------------------- saving the choice

def test_choosing_an_as_of_curve_is_audited_versioned_and_restorable(client, loaded, pg):
    v0 = client.get(f"{C}/levers").get_json()["version"]
    r = client.put(f"{C}/levers", json={"levers": {"curve_source": "asof", "asof_month": "2026-08"}})
    assert r.status_code == 200
    out = r.get_json()
    assert out["version"] == v0 + 1 and out["levers"]["curve_source"] == "asof" and out["levers"]["asof_month"] == "2026-08-01"
    row = pg.execute("SELECT * FROM proj_audit WHERE action = 'scenario.params' ORDER BY id DESC LIMIT 1").fetchone()
    assert row["actor"] == "JoshHoughton" and row["new"]["params"]["cohorts"]["asof_month"] == "2026-08-01" and row["new"]["params"]["cohorts"]["curve_source"] == "asof"
    # everything that reads the scenario follows
    assert client.get(f"{C}/curve-options").get_json()["saved"]["label"] == "As of Aug 2026, last 3 cohorts"
    assert client.get(f"{C}/summary").get_json()["curve_label"] == "As of Aug 2026, last 3 cohorts"
    assert "As of Aug 2026" in client.get(f"{C}/table").get_json()["captions"][0]
    cmpd = client.get(f"{C}/curve-compare").get_json()
    assert [o["active"] for o in cmpd["options"]] == [False, False, True] and cmpd["options"][2]["label"] == "As of Aug 2026, last 3 cohorts"
    live_total = client.get(f"{C}/curves").get_json()
    assert live_total["summary"]["curve"] == "asof" and live_total["used"] == cmpd["options"][2]["curve"] and live_total["live"] != live_total["used"]
    # undo through the core restore
    assert client.post(f"{P}/scenarios/current/restore", json={"version": v0}).status_code == 200
    assert client.get(f"{C}/curve-options").get_json()["saved"]["label"] == "Current, last 3 cohorts"


def test_choosing_current_or_goal_clears_nothing_else_and_goal_is_saved_by_name(client, loaded):
    client.put(f"{C}/levers", json={"levers": {"curve_source": "asof", "asof_month": "2026-08-01"}})
    out = client.put(f"{C}/levers", json={"levers": {"curve_source": "goal"}}).get_json()
    assert out["levers"]["curve_source"] == "goal" and out["levers"]["asof_month"] == "2026-08-01"        # the month is remembered
    assert client.get(f"{C}/curve-options").get_json()["saved"]["label"] == "Goal, saved Sep 2026"
    out = client.put(f"{C}/levers", json={"levers": {"curve_source": None}}).get_json()
    assert out["levers"]["curve_source"] == "live"


def test_invalid_choices_are_400_and_change_nothing(client, loaded, pg):
    before = counts(pg)
    for levers, fragment in [({"curve_source": "asof"}, "choose a month"), ({"curve_source": "asof", "asof_month": "2024-12"}, "choose a month from Oct 2025 to Oct 2026"),
                             ({"curve_source": "asof", "asof_month": "2026-08-15"}, "first of a month"), ({"curve_source": "asof", "asof_month": "later"}, "month like"),
                             ({"curve_source": "asof", "asof_month": "2027-01"}, "choose a month from")]:
        r = client.put(f"{C}/levers", json={"levers": levers})
        assert r.status_code == 400 and fragment in r.get_json()["error"], levers
    assert counts(pg) == before


def test_switching_to_six_cohorts_needs_a_month_that_six_can_build(client, loaded):
    client.put(f"{C}/levers", json={"levers": {"curve_source": "asof", "asof_month": "2025-10"}})        # fine with 3
    r = client.put(f"{C}/levers", json={"levers": {"n_cohorts": 6}})
    assert r.status_code == 400 and "needs at least 6 cohorts" in r.get_json()["error"]
    ok = client.put(f"{C}/levers", json={"levers": {"n_cohorts": 6, "asof_month": "2026-08"}})
    assert ok.status_code == 200 and ok.get_json()["levers"]["n_cohorts"] == 6


def test_each_scenario_keeps_its_own_choice(client, loaded):
    client.post(f"{P}/scenarios", json={"name": "Optimistic", "clone_from": "current"})
    client.put(f"{C}/levers", json={"scenario": "optimistic", "levers": {"curve_source": "goal"}})
    assert client.get(f"{C}/curve-options?scenario=optimistic").get_json()["saved"]["curve_source"] == "goal"
    assert client.get(f"{C}/curve-options?scenario=current").get_json()["saved"]["curve_source"] == "live"
    assert client.get(f"{C}/summary?scenario=optimistic").get_json()["curve_label"].startswith("Goal")


def test_the_earlier_curve_name_still_works_when_writing(client, loaded):
    out = client.put(f"{C}/levers", json={"levers": {"curve": "goal"}}).get_json()
    assert out["levers"]["curve_source"] == "goal" and "curve" not in out["own"] and out["own"]["curve_source"] == "goal"


def test_the_picker_endpoints_need_the_apps_login(anon, loaded):
    assert anon.get(f"{C}/curve-options").status_code == 401 and anon.get(f"{C}/curve-compare").status_code == 401
