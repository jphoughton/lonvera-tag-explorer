"""/api/projection/cohorts: payloads, levers (versioned, audited, restorable), upload (preview then commit), goal snapshot, auth."""
from __future__ import annotations

import io
from datetime import date

import pytest
from conftest import build_history, login, make_cohort, seed_cohorts

from app.projection import rwdb
from app.projection.cohorts import model as cohorts_model

P = "/api/projection"
C = f"{P}/cohorts"
TODAY = date(2026, 11, 3)
HEADER = "Cohort Month,New Customers,90-Day Repeat Rate,CAC,Payback Period (Months),First Order (Realized),First Order," + ",".join(f"M{i}" for i in range(1, 37))


@pytest.fixture(autouse=True)
def _frozen_today(monkeypatch, tmp_path):
    monkeypatch.setattr(cohorts_model, "today", lambda: TODAY)
    monkeypatch.setenv("PROJECTION_SNAPSHOT_DIR", str(tmp_path))          # other modules' stubs read zeros: forward new-customer revenue is 0, deterministic


@pytest.fixture()
def loaded(cohorts_db):
    with rwdb.get_rw(cohorts_db) as conn:
        seed_cohorts(conn, build_history(first=date(2025, 8, 1), last=date(2026, 10, 1)), goal=[0.2, 0.1] + [0.01] * 58)


@pytest.fixture()
def wide_bands(client):
    """Synthetic two-month files extrapolate to large multiples; these tests are about the upload mechanics, so the plan of record's bands are opened wide."""
    client.put(f"{C}/levers", json={"levers": {"upload_lifetime_min": 0.0, "upload_lifetime_max": 50.0, "upload_twelve_min": 0.0, "upload_twelve_max": 20.0}})


def csv_file(*lines: str, name: str = "cohorts.csv"):
    return {"file": (io.BytesIO(("\n".join([HEADER, *lines]) + "\n").encode()), name)}


def csv_line(month, n, first, cum, repeat="0.5", cac="80"):
    return ",".join([month, str(n), repeat, cac, "", str(first), str(first)] + [str(x) for x in cum] + ["0"] * (36 - len(cum)))


def audit_actions(pg, action):
    return pg.execute("SELECT * FROM proj_audit WHERE action = %s ORDER BY id", (action,)).fetchall()


# ---------------------------------------------------------------------------------------------------------------- module wiring

def test_module_is_built_and_listed(client):
    mods = {m["key"]: m for m in client.get(f"{P}/modules").get_json()["modules"]}
    assert mods["cohorts"]["built"] is True and mods["cohorts"]["ui"] == "cohorts.js"


def test_run_headline_carries_the_lifetime_multiple_from_the_real_module(client, loaded):
    body = client.get(f"{P}/run").get_json()
    assert body["modules"]["cohorts"] == {"built": True, "stub": False, "ms": body["modules"]["cohorts"]["ms"]}
    assert body["headline"]["lifetime_multiple"] > 0


# ---------------------------------------------------------------------------------------------------------------- reads

def test_table_payload_shape_and_totals(client, loaded):
    t = client.get(f"{C}/table").get_json()
    assert len(t["months"]) == 60 and t["months"][0] == "2025-01-01" and t["months"][-1] == "2029-12-01" and len(t["status"]) == 60
    rows = {r["key"]: r for r in t["rows"]}
    assert all(len(r["values"]) == 60 for r in t["rows"])
    years = [k for k in rows if k.startswith("repeat_20")]
    assert years == ["repeat_2025", "repeat_2026", "repeat_2027", "repeat_2028", "repeat_2029"]
    for i in range(60):
        assert sum(rows[y]["values"][i] for y in years) == pytest.approx(rows["repeat_rev"]["values"][i])
    assert rows["repeat_rev"]["kind"] == "total" and t["captions"] and "Oct 2026" in t["captions"][0]
    assert rows["repeat_actual"]["values"][0] is None                      # Jan-25: no loaded cohort has repeat that early (cohorts start Aug-25)
    assert rows["repeat_actual"]["values"][t["months"].index("2026-10-01")] > 0
    assert all(r["input"] is False for r in t["rows"])


def test_table_actual_matches_the_cohort_increments(client, loaded):
    # the Aug-25 cohort (ratios .2,.3,.2,.1,.05 x first 68, n=1080) repeats $/customer x customers in Sep-25..Jan-26
    t = client.get(f"{C}/table").get_json()
    sep25 = {r["key"]: r for r in t["rows"]}["repeat_actual"]["values"][t["months"].index("2025-09-01")]
    assert sep25 == pytest.approx(1080 * 0.2 * 68.0)


def test_curves_payload(client, loaded):
    c = client.get(f"{C}/curves").get_json()
    assert c["ages"] == list(range(1, 61)) and len(c["live"]) == len(c["goal"]) == len(c["used"]) == 60
    assert c["live"][0] == pytest.approx(0.2) and c["goal"][:2] == [0.2, 0.1] and c["used"] == c["live"]
    assert c["source"][0] == "data" and c["source"][5] == "tail" and c["n_used"][0] == 3
    assert c["summary"]["lifetime_goal"] == pytest.approx(0.3 + 0.58) and c["goal_snapshot"] == "2026-09-29"
    assert c["cumulative"]["live"][-1] == pytest.approx(c["summary"]["lifetime_live"])
    assert c["levers"]["n_cohorts"] == 3


def test_cohorts_payload_flags_the_cells_that_feed_the_live_curve(client, loaded):
    h = client.get(f"{C}/cohorts").get_json()
    by = {r["month"]: r for r in h["cohorts"]}
    assert len(h["cohorts"]) == 15 and h["ages"] == 36
    last = by["2026-09-01"]                                                # Sep-26: one month observed
    assert last["cum"][0] is not None and last["cum"][1] is None and last["feeds_curve"] == [1]
    assert by["2025-08-01"]["feeds_curve"] == []                           # old cohorts are outside the last 3 at every age
    assert by["2026-10-01"]["cum"] == [None] * 36 and by["2026-10-01"]["first_order"] > 0


def test_summary_payload_and_the_plain_freshness_line(client, loaded):
    s = client.get(f"{C}/summary").get_json()
    assert s["freshness_line"] == "Cohorts loaded through Oct 2026; Nov closes Dec 1."
    assert s["cohorts_loaded"] == 15 and s["last_cohort"] == "2026-10-01" and s["data_through"] == "2026-10-01"
    assert s["freshness"]["cadence"] == "monthly-upload" and s["freshness"]["behind"] is False
    assert s["lifetime_multiple"] == pytest.approx(s["lifetime_live"]) and s["curve"] == "live" and s["curve_label"] == "Current, last 3 cohorts"
    assert s["forward_12m_from"] == "2026-11-01" and s["forward_12m_to"] == "2027-10-01" and s["forward_12m_repeat"] > 0
    assert s["backtest"]["months"] and s["backtest"]["gap"] is not None


def test_behind_line_when_the_last_closed_month_is_not_loaded(client, cohorts_db, monkeypatch):
    with rwdb.get_rw(cohorts_db) as conn:
        seed_cohorts(conn, build_history(first=date(2025, 8, 1), last=date(2026, 9, 1)))
    s = client.get(f"{C}/summary").get_json()
    assert s["freshness_line"] == "Cohorts loaded through Sep 2026; Oct 2026 is closed and not loaded yet."
    assert s["freshness"]["behind"] is True


def test_empty_database_returns_zeros_not_errors(client):
    for path in ("table", "curves", "cohorts", "summary", "levers"):
        r = client.get(f"{C}/{path}")
        assert r.status_code == 200, (path, r.get_data(as_text=True))
    t = client.get(f"{C}/table").get_json()
    assert {r["key"]: r for r in t["rows"]}["repeat_rev"]["values"] == [0.0] * 60
    s = client.get(f"{C}/summary").get_json()
    assert s["freshness_line"] == "No cohorts are loaded." and s["lifetime_multiple"] == 0.0 and s["cohorts_loaded"] == 0
    assert client.get(f"{C}/curves").get_json()["warnings"] == []


def test_unknown_scenario_is_404(client):
    assert client.get(f"{C}/table?scenario=nope").status_code == 404
    assert client.get(f"{C}/levers?scenario=nope").status_code == 404


# ---------------------------------------------------------------------------------------------------------------- levers

def test_levers_default_and_put_is_versioned_audited_and_changes_the_numbers(client, loaded, pg):
    g = client.get(f"{C}/levers").get_json()
    assert g["levers"]["n_cohorts"] == 3 and g["own"] == {} and g["defaults"]["tail_decay"] == 0.95 and g["bounds"]["n_cohorts"]["max"] == 36
    v0 = g["version"]
    before = client.get(f"{C}/curves").get_json()["live"][0]

    r = client.put(f"{C}/levers", json={"levers": {"n_cohorts": 1, "tail_decay": 0.9}})
    assert r.status_code == 200
    out = r.get_json()
    assert out["version"] == v0 + 1 and out["levers"]["n_cohorts"] == 1 and out["own"] == {"n_cohorts": 1, "tail_decay": 0.9}
    row = audit_actions(pg, "scenario.params")[-1]
    assert row["actor"] == "JoshHoughton" and row["new"]["params"]["cohorts"]["n_cohorts"] == 1 and row["scenario_slug"] == "current"
    after = client.get(f"{C}/curves").get_json()
    assert after["levers"]["n_cohorts"] == 1 and after["n_used"][0] == 1
    assert after["live"][0] != before                                       # the last cohort alone differs from the average of three

    again = client.put(f"{C}/levers", json={"levers": {"n_cohorts": 1}}).get_json()
    assert again["version"] == v0 + 1                                       # same value: no new version


def test_levers_null_removes_a_lever_back_to_the_default(client):
    client.put(f"{C}/levers", json={"levers": {"n_cohorts": 6}})
    out = client.put(f"{C}/levers", json={"levers": {"n_cohorts": None}}).get_json()
    assert out["own"] == {} and out["levers"]["n_cohorts"] == 3


@pytest.mark.parametrize("bad,fragment", [({"n_cohorts": 0}, "between 1 and 36"), ({"n_cohorts": 2.5}, "whole number"), ({"tail_decay": 2}, "between"),
                                          ({"curve_source": "median"}, "curve_source must be"), ({"curve_source": "blend:7"}, "between 0 and 1"),
                                          ({"curve_source": "asof"}, "choose a month"), ({"asof_month": "2026-08-15"}, "first of a month"),
                                          ({"mystery": 1}, "unknown cohort lever"), ({"n_cohorts": "3"}, "must be a number")])
def test_levers_validation_leaves_the_scenario_alone(client, pg, bad, fragment):
    v0 = client.get(f"{C}/levers").get_json()["version"]
    r = client.put(f"{C}/levers", json={"levers": bad})
    assert r.status_code == 400 and fragment in r.get_json()["error"]
    assert client.get(f"{C}/levers").get_json()["version"] == v0 and not audit_actions(pg, "scenario.params")


def test_levers_combination_and_body_validation(client):
    client.put(f"{C}/levers", json={"levers": {"n_cohorts": 4}})
    r = client.put(f"{C}/levers", json={"levers": {"min_cohorts_per_age": 5}})
    assert r.status_code == 400 and "cannot exceed" in r.get_json()["error"]
    assert client.put(f"{C}/levers", json={"levers": {}}).status_code == 400
    assert client.put(f"{C}/levers", json={}).status_code == 400
    assert client.put(f"{C}/levers", json={"levers": {"n_cohorts": 2}, "expected_version": 999}).status_code == 409
    assert client.put(f"{C}/levers", json={"levers": {"n_cohorts": 2}, "scenario": "nope"}).status_code == 404


def test_lever_edit_can_be_undone_with_the_core_restore(client, loaded):
    v0 = client.get(f"{C}/levers").get_json()["version"]
    client.put(f"{C}/levers", json={"levers": {"curve_source": "goal"}})
    assert client.get(f"{C}/curves").get_json()["summary"]["curve"] == "goal"
    r = client.post(f"{P}/scenarios/current/restore", json={"version": v0})
    assert r.status_code == 200
    assert client.get(f"{C}/levers").get_json()["levers"]["curve_source"] == "live"
    assert client.get(f"{C}/curves").get_json()["summary"]["curve"] == "live"


def test_a_scenario_clone_carries_its_own_levers_and_the_base_is_untouched(client, loaded):
    client.post(f"{P}/scenarios", json={"name": "Slow decay", "clone_from": "current"})
    slug = "slow-decay"
    client.put(f"{C}/levers", json={"scenario": slug, "levers": {"tail_decay": 0.99, "curve_source": "blend:0.5"}})
    base = client.get(f"{C}/curves?scenario=current").get_json()
    mine = client.get(f"{C}/curves?scenario={slug}").get_json()
    assert base["levers"]["tail_decay"] == 0.95 and mine["levers"]["tail_decay"] == 0.99
    assert mine["summary"]["lifetime_used"] != base["summary"]["lifetime_used"]
    assert mine["used"][0] == pytest.approx(0.5 * mine["goal"][0] + 0.5 * mine["live"][0])


def test_goal_curve_choice_changes_repeat_revenue_and_a_missing_goal_warns(client, loaded, cohorts_db):
    live_total = {r["key"]: r for r in client.get(f"{C}/table").get_json()["rows"]}["repeat_rev"]["values"]
    client.put(f"{C}/levers", json={"levers": {"curve_source": "goal"}})
    goal_total = {r["key"]: r for r in client.get(f"{C}/table").get_json()["rows"]}["repeat_rev"]["values"]
    assert goal_total != live_total
    with rwdb.get_rw(cohorts_db) as conn:
        conn.execute("DELETE FROM proj_curve_goal")
    c = client.get(f"{C}/curves").get_json()
    assert c["used"] == c["live"] and c["warnings"] and "Goal curve" in c["warnings"][0]


# ---------------------------------------------------------------------------------------------------------------- upload

def _counts(pg):
    return (pg.execute("SELECT count(*) AS n FROM proj_cohorts").fetchone()["n"], pg.execute("SELECT count(*) AS n FROM proj_cohort_cum").fetchone()["n"])


def test_upload_preview_writes_nothing_and_reports_the_diff(client, pg):
    f = csv_file(csv_line("2026-08", 8417, 63.47, [76.43, 93.86]), csv_line("2026-09", 5641, 71.02, [78.91]), csv_line("2026-11", 234, 69.1, [70.3]))
    r = client.post(f"{C}/upload", data=f, content_type="multipart/form-data")
    assert r.status_code == 200
    out = r.get_json()
    assert out["committed"] is False and out["layout"] == "wide" and len(out["sha256"]) == 64 and out["through"] == "2026-10-01"
    s = out["summary"]
    assert s["cohorts_loaded"] == 2 and s["cohorts_added"] == 2 and s["cells_added"] == 3
    assert s["skipped"] == [{"month": "2026-11-01", "reason": "month not over"}]
    assert _counts(pg) == (0, 0) and not audit_actions(pg, "cohorts.upload")


def test_upload_commit_needs_the_preview_hash_and_rejects_a_changed_file(client, pg):
    f = lambda cac="80": csv_file(csv_line("2026-08", 8417, 63.47, [76.43]), csv_line("2026-09", 5641, 71.02, [78.91], cac=cac))      # noqa: E731
    sha = client.post(f"{C}/upload", data=f(), content_type="multipart/form-data").get_json()["sha256"]
    r = client.post(f"{C}/upload?commit=1", data=f(), content_type="multipart/form-data")
    assert r.status_code == 400 and "preview first" in r.get_json()["error"]
    r = client.post(f"{C}/upload?commit=1", data={**f(cac="99"), "expect_sha256": sha}, content_type="multipart/form-data")
    assert r.status_code == 409
    assert _counts(pg) == (0, 0)


def test_upload_commit_writes_cohorts_cells_and_one_audit_row(client, wide_bands, pg):
    f = lambda: csv_file(csv_line("2026-08", 8417, 63.47, [76.43, 93.86]), csv_line("2026-09", 5641, 71.02, [78.91]), name="cohorts-revenue-2023-11-02-to-2026-11-02.csv")   # noqa: E731
    sha = client.post(f"{C}/upload", data=f(), content_type="multipart/form-data").get_json()["sha256"]
    r = client.post(f"{C}/upload?commit=1", data={**f(), "expect_sha256": sha}, content_type="multipart/form-data")
    assert r.status_code == 200
    out = r.get_json()
    assert out["committed"] is True and out["loaded"] == {"cohorts": 2, "cells": 3}
    assert _counts(pg) == (2, 3)
    (a,) = audit_actions(pg, "cohorts.upload")
    assert a["actor"] == "JoshHoughton" and a["scenario_slug"] is None and a["new"]["sha256"] == sha and a["new"]["cells"] == 3
    assert a["note"].startswith("cohorts-revenue-")
    row = pg.execute("SELECT source FROM proj_cohorts WHERE cohort_month = '2026-08-01'").fetchone()
    assert sha[:12] in row["source"]

    # the same file again changes nothing
    again = client.post(f"{C}/upload", data=f(), content_type="multipart/form-data").get_json()["summary"]
    assert again["cohorts_added"] == 0 and again["cohorts_changed"] == 0 and again["cells_added"] == 0 and again["cells_changed"] == 0


def test_upload_revision_shows_up_as_changed_cells_with_old_and_new(client, wide_bands, pg):
    first = csv_file(csv_line("2026-08", 8417, 63.47, [76.43]))
    sha = client.post(f"{C}/upload", data=first, content_type="multipart/form-data").get_json()["sha256"]
    client.post(f"{C}/upload?commit=1", data={**csv_file(csv_line("2026-08", 8417, 63.47, [76.43])), "expect_sha256": sha}, content_type="multipart/form-data")
    out = client.post(f"{C}/upload", data=csv_file(csv_line("2026-08", 8417, 63.47, [80.0])), content_type="multipart/form-data").get_json()
    assert out["summary"]["cells_changed"] == 1
    ch = out["summary"]["changes"][0]
    assert ch["field"] == "M1" and ch["old"] == pytest.approx(76.43) and ch["new"] == 80.0 and ch["rel"] == pytest.approx(80.0 / 76.43 - 1)


def test_upload_with_nothing_to_load_and_bad_files(client):
    only_open = lambda: csv_file(csv_line("2026-11", 234, 69.1, [70.3]))      # noqa: E731
    sha = client.post(f"{C}/upload", data=only_open(), content_type="multipart/form-data").get_json()["sha256"]
    r = client.post(f"{C}/upload?commit=1", data={**only_open(), "expect_sha256": sha}, content_type="multipart/form-data")
    assert r.status_code == 400 and "nothing to load" in r.get_json()["error"]
    r = client.post(f"{C}/upload", data={"file": (io.BytesIO(b"a,b\n1,2\n"), "x.csv")}, content_type="multipart/form-data")
    assert r.status_code == 400 and "unrecognised layout" in r.get_json()["error"]
    assert client.post(f"{C}/upload", data={}, content_type="multipart/form-data").status_code == 400
    assert client.post(f"{C}/upload", json={"x": 1}).status_code == 400
    r = client.post(f"{C}/upload?min_customers=abc", data=only_open(), content_type="multipart/form-data")
    assert r.status_code == 400


def test_upload_through_override_and_min_customers(client):
    f = lambda: csv_file(csv_line("2026-08", 8417, 63.47, [76.43, 93.86]), csv_line("2026-09", 5, 71.02, [78.91]))      # noqa: E731
    out = client.post(f"{C}/upload?through=2026-09&min_customers=1", data=f(), content_type="multipart/form-data").get_json()
    assert out["through"] == "2026-09-01" and out["summary"]["cohorts_loaded"] == 2
    assert out["summary"]["cells_blanked_open_month"] == 2                # Aug M2 and Sep M1 both land in October, after September
    out = client.post(f"{C}/upload", data=f(), content_type="multipart/form-data").get_json()
    assert out["summary"]["cohorts_loaded"] == 1 and out["summary"]["skipped"][0]["month"] == "2026-09-01"


def test_loaded_upload_feeds_the_table(client, wide_bands):
    lines = [csv_line("2026-07", 10138, 61.39, [75.32, 94.58]), csv_line("2026-08", 8417, 63.47, [76.43, 93.86]), csv_line("2026-09", 5641, 71.02, [78.91])]
    sha = client.post(f"{C}/upload", data=csv_file(*lines), content_type="multipart/form-data").get_json()["sha256"]
    client.post(f"{C}/upload?commit=1", data={**csv_file(*lines), "expect_sha256": sha}, content_type="multipart/form-data")
    s = client.get(f"{C}/summary").get_json()
    assert s["cohorts_loaded"] == 3 and s["last_cohort"] == "2026-09-01"
    c = client.get(f"{C}/curves").get_json()
    assert c["live"][0] == pytest.approx((10138 * (75.32 - 61.39) / 61.39 + 8417 * (76.43 - 63.47) / 63.47 + 5641 * (78.91 - 71.02) / 71.02) / (10138 + 8417 + 5641))


# ---------------------------------------------------------------------------------------------------------------- goal snapshot

def test_goal_snapshot_from_live_is_dated_audited_and_keeps_the_old_one(client, loaded, pg):
    live0 = client.get(f"{C}/curves").get_json()["live"]
    r = client.post(f"{C}/goal", json={"from": "live"})
    assert r.status_code == 200 and r.get_json()["snapshot_date"] == "2026-11-03" and r.get_json()["ages"] == 60
    c = client.get(f"{C}/curves").get_json()
    assert c["goal"] == pytest.approx(live0) and c["goal_snapshot"] == "2026-11-03"
    assert pg.execute("SELECT count(DISTINCT snapshot_date) AS n FROM proj_curve_goal").fetchone()["n"] == 2
    (a,) = audit_actions(pg, "cohorts.goal.snapshot")
    assert a["old"]["snapshot_date"] == "2026-09-29" and a["new"]["snapshot_date"] == "2026-11-03" and a["actor"] == "JoshHoughton"
    assert client.post(f"{C}/goal", json={"from": "elsewhere"}).status_code == 400
    assert client.post(f"{C}/goal", json={}).status_code == 400


# ---------------------------------------------------------------------------------------------------------------- guard rails and auth

def test_endpoints_need_a_session_when_the_apps_login_is_on(anon, loaded):
    for method, path in (("get", "table"), ("get", "curves"), ("get", "cohorts"), ("get", "summary"), ("get", "levers"),
                         ("put", "levers"), ("post", "upload"), ("post", "goal")):
        r = getattr(anon, method)(f"{C}/{path}", **({"json": {}} if method != "get" else {}))
        assert r.status_code == 401, path


def test_endpoints_are_open_when_the_apps_auth_is_off(app_factory, loaded, pg):
    open_app = app_factory(AUTH_DISABLED=True)
    c = open_app.test_client()
    assert c.get(f"{C}/table").status_code == 200
    r = c.put(f"{C}/levers", json={"levers": {"n_cohorts": 2}})
    assert r.status_code == 200
    assert audit_actions(pg, "scenario.params")[-1]["actor"] == "anonymous"


def test_foreign_origin_and_non_json_writes_are_blocked_by_the_shared_guard(client):
    assert client.put(f"{C}/levers", json={"levers": {"n_cohorts": 2}}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.put(f"{C}/levers", data="levers=1", content_type="application/x-www-form-urlencoded").status_code == 415
    assert client.get(f"{C}/table").headers["X-Robots-Tag"] == "noindex, nofollow"


def test_upload_is_rate_limited_by_the_shared_guard(app_factory, loaded):
    a = app_factory(PROJ_RATE_UPLOADS_PER_HOUR=2)
    c = login(a.test_client())
    codes = [c.post(f"{C}/upload", data=csv_file(csv_line("2026-08", 100, 60, [70])), content_type="multipart/form-data").status_code for _ in range(3)]
    assert codes[:2] == [200, 200] and codes[2] == 429
