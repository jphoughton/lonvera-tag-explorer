"""Daily refresh: runner (etl/projection_daily.py), freshness evaluation + endpoint, and scripts/deploy/run_etl.sh."""
from __future__ import annotations

import os
import stat
import subprocess
import time
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import psycopg
import pytest
from psycopg.rows import dict_row

from app.projection import freshness
from conftest import ROOT, login
from core_helpers import P
from etl import projection_daily as D

TODAY = date(2026, 11, 4)
ALL_KEYS = list(freshness.MODULE_KEYS)


def feed(feed="ad_daily", through=TODAY - timedelta(days=1), cadence="daily", stale_after=2, label=None):
    return {"feed": feed, "label": label or feed, "cadence": cadence, "data_through": through, "stale_after_days": stale_after}


def mod(refresh=None, fresh=None, **_):
    ns = SimpleNamespace()
    if refresh is not None:
        ns.refresh = refresh
    if fresh is not None:
        ns.freshness = (lambda conn, _f=fresh: _f) if isinstance(fresh, list) else fresh
    return ns


def importer(mods: dict, broken: dict | None = None):
    def imp(name: str):
        key = name.removeprefix("etl.projection_")
        if broken and key in broken:
            raise broken[key]
        if key in mods:
            return mods[key]
        raise ModuleNotFoundError(f"No module named {name!r}", name=name)
    return imp


@pytest.fixture()
def connect(core_db):
    return lambda: psycopg.connect(core_db, row_factory=dict_row)


def runs(pg):
    return pg.execute("SELECT * FROM proj_refresh_runs ORDER BY id").fetchall()


def run(connect, mods, keys=None, broken=None, **kw):
    kw.setdefault("today", TODAY)
    kw.setdefault("timeout", 30)
    return D.run_all(keys or ALL_KEYS, connect, importer=importer(mods, broken), **kw)


# ------------------------------------------------------------------------------------------------------------ runner

def test_one_failing_module_does_not_stop_the_others_and_every_run_is_recorded(connect, pg, capsys):
    order = []

    def boom(conn, days):
        order.append("cohorts")
        raise RuntimeError("feed unreachable")

    def good(conn, days):
        order.append("drivers")
        return {"rows": 42, "data_through": "2026-11-03"}

    mods = {"cohorts": mod(boom), "drivers": mod(good), "pnl": mod(fresh=[feed("qb_export", None, "monthly-upload", 45)])}
    assert run(connect, mods) == 1
    assert order == ["cohorts", "drivers"]
    got = {r["module"]: r for r in runs(pg)}
    assert list(got) == ALL_KEYS                                                           # all seven recorded, in order
    assert (got["cohorts"]["status"], got["cohorts"]["error"]) == ("failed", "RuntimeError: feed unreachable")
    assert (got["drivers"]["status"], got["drivers"]["rows"], got["drivers"]["data_through"]) == ("ok", 42, date(2026, 11, 3))
    assert got["pnl"]["status"] == "skipped" and got["pnl"]["error"] == "no refresh hook"
    assert got["pacing"]["status"] == "skipped" and got["pacing"]["error"] == "no etl module"
    out = capsys.readouterr().out
    assert "cohorts: FAILED RuntimeError: feed unreachable" in out and "drivers: ok rows=42 data_through=2026-11-03" in out
    assert "pnl: skipped (no refresh hook)" in out and "FAILED: cohorts refresh failed" in out


def test_exit_code_0_when_everything_refreshed_and_fresh(connect, pg, capsys):
    mods = {k: mod(lambda conn, days: {"rows": 1, "data_through": "2026-11-03"}, [feed(f"{k}_feed")]) for k in ("cohorts", "drivers")}
    assert run(connect, mods) == 0
    assert [r["status"] for r in runs(pg)].count("ok") == 2
    assert "all modules refreshed and fresh" in capsys.readouterr().out


def test_stale_daily_feed_fails_the_run_with_a_precise_message(connect, capsys):
    ok = lambda conn, days: {"rows": 1}                                                  # noqa: E731
    mods = {"drivers": mod(ok, [feed("ad_daily", date(2026, 11, 1), "daily", 2)])}
    assert run(connect, mods) == 1
    out = capsys.readouterr().out
    assert "feed ad_daily last data 2026-11-01, 3 days old (stale after 2 days)" in out
    assert "FAILED: drivers: feed ad_daily last data 2026-11-01, 3 days old" in out


def test_boundary_exactly_at_the_allowed_age_is_fresh(connect):
    ok = lambda conn, days: {"rows": 1}                                                  # noqa: E731
    assert run(connect, {"drivers": mod(ok, [feed("a", date(2026, 11, 2), "daily", 2)])}) == 0
    assert run(connect, {"drivers": mod(ok, [feed("a", date(2026, 11, 1), "daily", 2)])}) == 1


def test_stale_monthly_upload_feed_never_fails_the_run(connect, capsys):
    ok = lambda conn, days: {"rows": 1}                                                  # noqa: E731
    mods = {"pnl": mod(ok, [feed("quickbooks", date(2026, 7, 1), "monthly-upload", 45), feed("ad_daily", TODAY, "daily", 2)])}
    assert run(connect, mods) == 0
    out = capsys.readouterr().out
    assert "feed quickbooks last data 2026-07-01, 126 days old (stale after 45 days)" in out and "monthly upload: not a failure" in out


def test_feed_with_no_data_yet_is_reported_but_not_fatal(connect, capsys):
    mods = {"drivers": mod(lambda conn, days: {"rows": 0}, [feed("ad_daily", None)])}
    assert run(connect, mods) == 0
    assert "feed ad_daily has no data yet" in capsys.readouterr().out


def test_freshness_hook_problems_fail_the_run(connect, capsys):
    ok = lambda conn, days: {"rows": 1}                                                  # noqa: E731

    def bad_hook(conn):
        raise RuntimeError("table missing")

    assert run(connect, {"drivers": mod(ok, bad_hook)}) == 1
    assert "drivers: freshness hook failed: RuntimeError: table missing" in capsys.readouterr().out
    assert run(connect, {"drivers": mod(ok, [{"feed": "x", "cadence": "hourly", "data_through": None, "stale_after_days": 1}])}) == 1


def test_a_failed_refresh_with_fresh_feeds_still_exits_non_zero(connect):
    def boom(conn, days):
        raise ValueError("x")

    assert run(connect, {"drivers": mod(boom, [feed()])}) == 1


def test_import_error_is_a_failed_run_for_that_module_only(connect, pg):
    ok = lambda conn, days: {"rows": 1}                                                  # noqa: E731
    assert run(connect, {"drivers": mod(ok)}, broken={"pnl": ImportError("cannot import name 'x'")}) == 1
    got = {r["module"]: r for r in runs(pg)}
    assert got["pnl"]["status"] == "failed" and "import failed: ImportError" in got["pnl"]["error"] and got["drivers"]["status"] == "ok"


def test_hooks_get_a_dict_row_connection_the_days_and_their_work_is_committed(connect, core_db, pg):
    seen = {}

    def refresh(conn, days):
        seen.update(days=days, row=conn.execute("SELECT 1 AS one").fetchone())
        conn.execute("INSERT INTO proj_actuals (line_key, month, value, source) VALUES ('t.committed', '2026-10-01', 1, 'refresh test')")
        return {"rows": 1}

    assert run(connect, {"drivers": mod(refresh)}, days=9) == 0
    assert seen == {"days": 9, "row": {"one": 1}}
    assert pg.execute("SELECT count(*) AS n FROM proj_actuals WHERE line_key = 't.committed'").fetchone()["n"] == 1


def test_a_failing_refresh_rolls_back_its_partial_writes(connect, pg):
    def refresh(conn, days):
        conn.execute("INSERT INTO proj_actuals (line_key, month, value, source) VALUES ('t.partial', '2026-10-01', 1, 'refresh test')")
        raise RuntimeError("died half way")

    assert run(connect, {"drivers": mod(refresh)}) == 1
    assert pg.execute("SELECT count(*) AS n FROM proj_actuals WHERE line_key = 't.partial'").fetchone()["n"] == 0


def test_refresh_without_a_days_parameter_and_non_dict_results_are_tolerated(connect, pg):
    assert run(connect, {"drivers": mod(lambda conn: None), "cohorts": mod(lambda conn, **kw: {"rows": kw["days"], "data_through": date(2026, 11, 2)})}) == 0
    got = {r["module"]: r for r in runs(pg)}
    assert (got["drivers"]["status"], got["drivers"]["rows"], got["drivers"]["data_through"]) == ("ok", None, None)
    assert (got["cohorts"]["rows"], got["cohorts"]["data_through"]) == (14, date(2026, 11, 2))


def test_time_cap_stops_a_hung_refresh_and_the_next_module_still_runs(connect, pg):
    def hang(conn, days):
        time.sleep(10)

    t0 = time.monotonic()
    assert run(connect, {"cohorts": mod(hang), "drivers": mod(lambda conn, days: {"rows": 1})}, timeout=0.4) == 1
    assert time.monotonic() - t0 < 5
    got = {r["module"]: r for r in runs(pg)}
    assert got["cohorts"]["status"] == "failed" and got["cohorts"]["error"] == "RefreshTimeout: timed out after 0.4s" and got["drivers"]["status"] == "ok"


def test_time_cap_is_off_when_not_set_and_restores_the_alarm_handler():
    import signal
    before = signal.getsignal(signal.SIGALRM)
    with D.time_cap(None):
        pass
    with D.time_cap(5):
        pass
    assert signal.getsignal(signal.SIGALRM) == before and signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)


def test_recorded_errors_are_scrubbed_and_capped(connect, pg, capsys, monkeypatch):
    monkeypatch.setenv("META_ACCESS_TOKEN", "EAAB-this-is-a-long-secret-token-value")

    def leak(conn, days):
        raise RuntimeError("connect failed postgresql://app:supersecretpw@db.internal:5432/x "
                           "Authorization: Bearer abcDEF123456 url?access_token=zzz999&x=1 token EAAB-this-is-a-long-secret-token-value " + "pad " * 200)

    assert run(connect, {"drivers": mod(leak)}) == 1
    err = runs(pg)[0 + ALL_KEYS.index("drivers")]["error"]
    out = capsys.readouterr().out
    assert len(err) <= 300 and "FAILED" in out
    for secret in ("supersecretpw", "abcDEF123456", "zzz999", "EAAB-this-is-a-long-secret-token-value"):
        assert secret not in err and secret not in out, secret
    assert "postgresql://***@db.internal" in err


def test_database_rejects_an_unscrubbed_overlong_error(pg):
    with pytest.raises(psycopg.errors.CheckViolation):
        pg.execute("INSERT INTO proj_refresh_runs (module, started_at, status, error) VALUES ('x', now(), 'failed', %s)", ("e" * 301,))
    with pytest.raises(psycopg.errors.CheckViolation):
        pg.execute("INSERT INTO proj_refresh_runs (module, started_at, status) VALUES ('x', now(), 'weird')")


def test_dry_run_refreshes_and_records_nothing_and_exits_zero(connect, pg, capsys):
    called = []
    mods = {"drivers": mod(lambda conn, days: called.append("drivers") or {"rows": 1}, [feed("ad_daily", date(2026, 10, 1), "daily", 2)]), "pnl": mod(fresh=[])}
    assert run(connect, mods, dry_run=True) == 0                                          # stale daily feed is shown, not fatal in a dry run
    assert called == [] and runs(pg) == []
    out = capsys.readouterr().out
    assert "drivers: would refresh (days=14, time cap 30s)" in out and "pnl: skip (no refresh hook)" in out and "cohorts: skip (no etl module)" in out
    assert "feed ad_daily last data 2026-10-01" in out and "dry run: nothing was refreshed or recorded" in out


def test_main_parses_arguments_and_the_env_time_cap(core_db, pg, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(freshness, "hook_module", lambda key, imp=None: mod(lambda conn, days: calls.append((key, days)) or {"rows": 1}, [feed(f"{key}_f")])
                        if key in ("drivers", "pnl") else None)
    monkeypatch.setenv("DATABASE_URL", core_db)
    monkeypatch.setenv(D.TIMEOUT_ENV, "12")
    monkeypatch.setattr(freshness, "today_pacific", lambda: TODAY)
    assert D.main(["--only", "drivers,pnl", "--days", "3"]) == 0
    assert calls == [("drivers", 3), ("pnl", 3)] and [r["module"] for r in runs(pg)] == ["drivers", "pnl"]
    assert D.main(["--only", "nope"]) == 2 and D.main(["--days", "0"]) == 2
    assert D._timeout_from_env() == 12.0
    monkeypatch.setenv(D.TIMEOUT_ENV, "abc")
    assert D._timeout_from_env() == D.DEFAULT_TIMEOUT_SEC
    capsys.readouterr()


def test_only_the_selected_modules_feeds_decide_the_exit_code(connect):
    ok = lambda conn, days: {"rows": 1}                                                  # noqa: E731
    mods = {"drivers": mod(ok, [feed("a", TODAY)]), "pnl": mod(ok, [feed("b", date(2026, 1, 1), "daily", 2)])}
    assert run(connect, mods, keys=["drivers"]) == 0
    assert run(connect, mods, keys=["drivers", "pnl"]) == 1


def test_stale_manual_feed_is_reported_but_never_fails_the_run(connect, capsys):
    ok = lambda conn, days: {"rows": 1}                                                  # noqa: E731
    mods = {"inventory_demand": mod(ok, [feed("intl_units", date(2026, 9, 1), "manual", 35), feed("ad_daily", TODAY, "daily", 2)])}
    assert run(connect, mods) == 0
    out = capsys.readouterr().out
    assert "feed intl_units last data 2026-09-01, 64 days old (stale after 35 days)" in out and "manual feed: not a failure" in out


def test_a_stale_manual_feed_does_not_hide_a_stale_daily_one(connect, capsys):
    ok = lambda conn, days: {"rows": 1}                                                  # noqa: E731
    mods = {"drivers": mod(ok, [feed("manual_feed", date(2026, 1, 1), "manual", 35), feed("ad_daily", date(2026, 11, 1), "daily", 2)])}
    assert run(connect, mods) == 1
    assert "FAILED: drivers: feed ad_daily" in capsys.readouterr().out


def test_manual_feed_is_listed_by_the_endpoint_with_its_own_label(client, fake_hooks):
    fake_hooks["inventory_demand"] = mod(fresh=[feed("intl_units", date(2026, 9, 1), "manual", 35, "International units"), feed("ad_daily", TODAY)])
    j = client.get(f"{P}/freshness").get_json()
    by = {f["feed"]: f for f in j["feeds"]}
    assert (by["intl_units"]["status"], by["intl_units"]["cadence"], by["intl_units"]["cadence_label"], by["intl_units"]["label"]) == ("stale", "manual", "Manual", "International units")
    assert j["summary"] == {"fresh": 1, "stale": 1, "unknown": 0}


# ------------------------------------------------------------------------------------------------------------ freshness evaluation

def test_evaluate_status_age_and_messages():
    ev = lambda **kw: freshness.evaluate("drivers", feed(**kw), TODAY)                  # noqa: E731
    assert (ev(through=TODAY)["status"], ev(through=TODAY)["age_days"]) == ("fresh", 0)
    assert ev(through=TODAY - timedelta(days=2))["status"] == "fresh" and ev(through=TODAY - timedelta(days=3))["status"] == "stale"
    assert ev(through=TODAY + timedelta(days=5))["age_days"] == 0                        # a future date is not negative age
    assert ev(through="2026-11-01")["data_through"] == "2026-11-01" and ev(through=datetime(2026, 11, 1, 9, 30))["data_through"] == "2026-11-01"
    n = ev(through=None)
    assert (n["status"], n["age_days"], n["data_through"], n["message"]) == ("unknown", None, None, "feed ad_daily has no data yet")
    assert ev(through=date(2026, 11, 3), stale_after=0)["message"] == "feed ad_daily last data 2026-11-03, 1 day old (stale after 0 days)"
    assert ev(through=date(2026, 11, 3))["message"] == "feed ad_daily last data 2026-11-03, 1 day old"
    assert ev(label="Ad spend")["label"] == "Ad spend" and freshness.evaluate("m", {**feed(), "label": ""}, TODAY)["label"] == "ad_daily"
    assert freshness.CADENCES == ("daily", "monthly-upload", "manual") and ev(cadence="manual")["cadence_label"] == "Manual"
    for bad in ({"feed": ""}, {**feed(), "cadence": "weekly"}, {**feed(), "stale_after_days": -1}, {**feed(), "stale_after_days": "2"}, {**feed(), "stale_after_days": True}):
        with pytest.raises(ValueError):
            freshness.evaluate("m", bad, TODAY)
    with pytest.raises(ValueError):
        freshness.evaluate("m", {**feed(), "data_through": "not a date"}, TODAY)


def test_scrub_removes_credentials_and_caps_length():
    s = freshness.scrub("postgres://u:pw123456@h/db and Bearer tok_ABC.def and password=hunter2xx and api_key=k-123", 100)
    assert "pw123456" not in s and "tok_ABC" not in s and "hunter2xx" not in s and "k-123" not in s and len(s) <= 100
    assert freshness.scrub("x" * 1000) == "x" * 300


def test_hook_module_returns_none_only_for_a_missing_etl_module():
    imp = importer({"drivers": object()}, broken={"pnl": ModuleNotFoundError("No module named 'googleapiclient'", name="googleapiclient")})
    assert freshness.hook_module("drivers", imp) is not None and freshness.hook_module("cohorts", imp) is None
    with pytest.raises(ModuleNotFoundError):
        freshness.hook_module("pnl", imp)                                                # a missing DEPENDENCY of the module is a real error


# ------------------------------------------------------------------------------------------------------------ endpoint

@pytest.fixture()
def fake_hooks(monkeypatch):
    state = {"mods": {}}
    monkeypatch.setattr(freshness, "hook_module", lambda key, imp=None: state["mods"].get(key))
    monkeypatch.setattr(freshness, "today_pacific", lambda: TODAY)
    return state["mods"]


def seed_runs(pg):
    pg.execute("INSERT INTO proj_refresh_runs (module, started_at, finished_at, status, rows, data_through, error) VALUES "
               "('drivers', '2026-11-03 13:00+00', '2026-11-03 13:01+00', 'ok', 10, '2026-11-02', NULL),"
               "('drivers', '2026-11-04 13:00+00', '2026-11-04 13:02+00', 'failed', NULL, NULL, 'RuntimeError: nope'),"
               "('pnl', '2026-11-04 13:00+00', '2026-11-04 13:00+00', 'skipped', NULL, NULL, 'no refresh hook'),"
               "('cohorts', '2026-11-04 13:00+00', '2026-11-04 13:03+00', 'ok', 5, '2026-11-03', NULL)")


def test_freshness_endpoint_shape_statuses_and_last_run(client, pg, fake_hooks):
    seed_runs(pg)
    fake_hooks["drivers"] = mod(fresh=[feed("ad_daily", date(2026, 11, 1), "daily", 2, "Ad spend (Meta)"), feed("amazon", TODAY - timedelta(days=1))])
    fake_hooks["cohorts"] = mod(fresh=[feed("cohort_export", date(2026, 10, 1), "monthly-upload", 45, "Cohort export"), feed("never", None, "monthly-upload", 45)])
    fake_hooks["pnl"] = mod(fresh=[feed("quickbooks", date(2026, 9, 1), "monthly-upload", 45)])
    r = client.get(f"{P}/freshness")
    assert r.status_code == 200
    j = r.get_json()
    assert set(j) == {"generated_at", "feeds", "summary", "degraded"} and j["summary"] == {"fresh": 2, "stale": 2, "unknown": 1}
    assert j["degraded"] == [{"module": "drivers", "error": "RuntimeError: nope"}]                                                      # latest drivers run failed
    assert [(f["module"], f["feed"]) for f in j["feeds"]] == [("cohorts", "cohort_export"), ("cohorts", "never"), ("drivers", "ad_daily"), ("drivers", "amazon"), ("pnl", "quickbooks")]
    by = {f["feed"]: f for f in j["feeds"]}
    assert set(by["ad_daily"]) == {"module", "feed", "label", "cadence", "cadence_label", "data_through", "age_days", "stale_after_days", "status", "message", "last_run"}
    assert (by["ad_daily"]["cadence_label"], by["quickbooks"]["cadence_label"]) == ("Daily", "Monthly upload")
    assert (by["ad_daily"]["label"], by["ad_daily"]["age_days"], by["ad_daily"]["status"]) == ("Ad spend (Meta)", 3, "stale")
    assert by["ad_daily"]["message"] == "feed ad_daily last data 2026-11-01, 3 days old (stale after 2 days)"
    assert by["amazon"]["status"] == "fresh" and by["never"]["status"] == "unknown" and by["never"]["data_through"] is None
    assert by["cohort_export"]["status"] == "fresh" and by["quickbooks"]["status"] == "stale"                                           # 34 days vs 64 days, both allowed 45
    assert by["ad_daily"]["last_run"]["status"] == "failed" and by["ad_daily"]["last_run"]["error"] == "RuntimeError: nope"        # latest ok/failed run of the module
    assert set(by["ad_daily"]["last_run"]) == {"status", "finished_at", "rows", "error"}
    assert by["cohort_export"]["last_run"]["rows"] == 5
    assert by["quickbooks"]["last_run"] is None                                                                                        # skipped rows are not runs
    assert datetime.fromisoformat(j["generated_at"]).tzinfo is not None


def test_freshness_endpoint_reports_a_broken_hook_as_unknown(client, fake_hooks):
    def boom(conn):
        raise RuntimeError("bad table")

    fake_hooks["drivers"] = mod(fresh=boom)
    fake_hooks["pnl"] = mod(fresh=[feed("ok_feed", TODAY)])
    j = client.get(f"{P}/freshness").get_json()
    assert j["summary"] == {"fresh": 1, "stale": 0, "unknown": 1}
    bad = next(f for f in j["feeds"] if f["module"] == "drivers")
    assert bad["status"] == "unknown" and bad["message"] == "freshness hook failed: RuntimeError: bad table" and bad["cadence"] is None
    assert j["degraded"] == [{"module": "drivers", "error": "freshness hook failed: RuntimeError: bad table"}]


def test_freshness_endpoint_with_no_hooks_is_empty(client, fake_hooks):
    j = client.get(f"{P}/freshness").get_json()
    assert j["feeds"] == [] and j["summary"] == {"fresh": 0, "stale": 0, "unknown": 0} and j["degraded"] == []


def test_freshness_endpoint_auth_follows_the_app(anon, app_factory, fake_hooks):
    assert anon.get(f"{P}/freshness").status_code == 401
    assert app_factory(AUTH_DISABLED=True).test_client().get(f"{P}/freshness").status_code == 200
    assert login(anon).get(f"{P}/freshness").status_code == 200


# ------------------------------------------------------------------------------------------------------------ scripts/deploy/run_etl.sh

SCRIPT = ROOT / "scripts" / "deploy" / "run_etl.sh"


@pytest.fixture()
def stub_python(tmp_path):
    """A fake `python` first on PATH: logs its arguments, exits with the code set for that module in the environment."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    exe = bin_dir / "python"
    exe.write_text('#!/bin/sh\necho "$*" >> "$STUB_LOG"\ncase "$2" in\n'
                   '  etl.migrate) exit "${MIGRATE_RC:-0}";;\n  etl.run_daily) exit "${DAILY_RC:-0}";;\n  etl.upstack_daily) exit "${UP_RC:-0}";;\n  etl.stage_moves) exit "${SM_RC:-0}";;\n  etl.projection_daily) exit "${PROJ_RC:-0}";;\n'
                   '  *) exit "${OTHER_RC:-0}";;\nesac\n')
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)

    def call(*args, **env):
        e = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "STUB_LOG": str(log), **{k: str(v) for k, v in env.items()}}
        p = subprocess.run(["/bin/sh", str(SCRIPT), *args], capture_output=True, text=True, env=e, cwd=str(tmp_path))
        calls = log.read_text().splitlines() if log.exists() else []
        log.unlink(missing_ok=True)
        return p, calls

    return call


def test_run_etl_runs_migrate_then_run_daily_then_upstack_then_projection_daily(stub_python):
    p, calls = stub_python()
    assert p.returncode == 0 and calls == ["-m etl.migrate", "-m etl.run_daily", "-m etl.upstack_daily", "-m etl.stage_moves", "-m etl.projection_daily"]


def test_run_etl_runs_projection_even_when_run_daily_fails_and_exits_non_zero(stub_python):
    p, calls = stub_python(DAILY_RC=3)
    assert p.returncode == 3 and calls == ["-m etl.migrate", "-m etl.run_daily", "-m etl.upstack_daily", "-m etl.stage_moves", "-m etl.projection_daily"]
    assert "etl.run_daily failed (exit 3)" in p.stderr and "projection_daily failed" not in p.stderr and "upstack_daily failed" not in p.stderr


def test_run_etl_runs_projection_even_when_upstack_fails_and_exits_non_zero(stub_python):
    p, calls = stub_python(UP_RC=6)
    assert p.returncode == 6 and calls == ["-m etl.migrate", "-m etl.run_daily", "-m etl.upstack_daily", "-m etl.stage_moves", "-m etl.projection_daily"]
    assert "etl.upstack_daily failed (exit 6)" in p.stderr and "run_daily failed" not in p.stderr and "projection_daily failed" not in p.stderr


def test_run_etl_a_failed_stage_moves_refresh_still_runs_projection_and_fails_the_run(stub_python):
    p, calls = stub_python(SM_RC=5)
    assert p.returncode == 5 and calls[-2:] == ["-m etl.stage_moves", "-m etl.projection_daily"] and "etl.stage_moves failed (exit 5)" in p.stderr
    p, _ = stub_python(UP_RC=6, SM_RC=5)
    assert p.returncode == 6                                              # an earlier failure keeps priority


def test_run_etl_run_daily_failure_wins_over_upstack_failure(stub_python):
    p, _ = stub_python(DAILY_RC=3, UP_RC=6)
    assert p.returncode == 3 and "run_daily failed (exit 3)" in p.stderr and "upstack_daily failed (exit 6)" in p.stderr


def test_run_etl_exits_non_zero_when_only_projection_fails(stub_python):
    p, calls = stub_python(PROJ_RC=1)
    assert p.returncode == 1 and calls[-1] == "-m etl.projection_daily" and "etl.projection_daily failed (exit 1)" in p.stderr


def test_run_etl_reports_both_failures(stub_python):
    p, _ = stub_python(DAILY_RC=2, PROJ_RC=1)
    assert p.returncode == 2 and "run_daily failed (exit 2)" in p.stderr and "projection_daily failed (exit 1)" in p.stderr


def test_run_etl_stops_when_migrations_fail(stub_python):
    p, calls = stub_python(MIGRATE_RC=4)
    assert p.returncode == 4 and calls == ["-m etl.migrate"]


def test_run_etl_one_off_path_is_unchanged(stub_python):
    p, calls = stub_python("meta_pull", "--days", "185")
    assert p.returncode == 0 and calls == ["-m etl.migrate", "-m etl.meta_pull --days 185"]
    p, calls = stub_python("projection_daily", "--dry-run", PROJ_RC=7)
    assert p.returncode == 7 and calls == ["-m etl.migrate", "-m etl.projection_daily --dry-run"]
    p, calls = stub_python("meta_pull", OTHER_RC=5)
    assert p.returncode == 5


def test_the_daily_run_purges_scenarios_deleted_more_than_30_days_ago(connect, pg, capsys):
    pg.execute("INSERT INTO proj_scenarios (slug, name, base_slug, frozen, archived_at) VALUES ('old-gone', 'Old gone', 'current', true, now() - interval '45 days'),"
               "('recent', 'Recent', 'current', true, now() - interval '5 days')")
    assert run(connect, {"drivers": mod(lambda conn, days: {"rows": 1})}) == 0
    assert [r["slug"] for r in pg.execute("SELECT slug FROM proj_scenarios WHERE slug IN ('old-gone', 'recent') ORDER BY slug").fetchall()] == ["recent"]
    assert "purged 1 scenario(s) deleted more than 30 days ago" in capsys.readouterr().out
