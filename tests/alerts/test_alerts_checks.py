"""Each alert check against a real (empty, migrated) database with hand-made rows. No network."""
from __future__ import annotations

import datetime as dt

import pytest

from etl import alerts as A
from .conftest import NOW, add_run, hours_ago


def ctx(**kw):
    return A.Ctx(now=NOW, **kw)


def by_id(findings):
    return {f.id: f for f in findings}


# ---------------------------------------------------------------------------------------------------------------- etl_runs jobs
def test_healthy_jobs_are_quiet(conn):
    for j in ("run_daily", "upstack_daily", "stage_moves"):
        add_run(conn, j, finished_h_ago=6)
    assert [f for f in A.check_etl_jobs(conn, ctx())] == []


def test_failed_run_names_error_and_last_success(conn):
    add_run(conn, "run_daily", finished_h_ago=30)
    add_run(conn, "run_daily", finished_h_ago=5, status="failed", error="meta token rejected")
    for j in ("upstack_daily", "stage_moves"):
        add_run(conn, j, finished_h_ago=5)
    f = by_id(A.check_etl_jobs(conn, ctx()))["job:run_daily"]
    assert "meta token rejected" in f.title and "Last success" in f.title and f.since == hours_ago(5)


def test_failure_clears_once_a_later_run_succeeds(conn):
    add_run(conn, "run_daily", finished_h_ago=30, status="failed", error="boom")
    add_run(conn, "run_daily", finished_h_ago=5)
    for j in ("upstack_daily", "stage_moves"):
        add_run(conn, j, finished_h_ago=5)
    assert A.check_etl_jobs(conn, ctx()) == []


def test_job_older_than_cadence_is_stale_and_missing_required_is_flagged(conn):
    add_run(conn, "run_daily", finished_h_ago=A.NIGHTLY_H + 1)
    add_run(conn, "upstack_daily", finished_h_ago=A.NIGHTLY_H - 1)
    got = by_id(A.check_etl_jobs(conn, ctx()))
    assert "has not succeeded for 37 h" in got["job:run_daily"].title
    assert "job:upstack_daily" not in got
    assert "has never completed" in got["job:stage_moves"].title          # required job with no rows at all


def test_optional_jobs_are_silent_until_they_exist(conn):
    for j in ("run_daily", "upstack_daily", "stage_moves"):
        add_run(conn, j, finished_h_ago=2)
    assert A.check_etl_jobs(conn, ctx()) == []                               # no auto_tag / competitor_ads / creative_priorities / backup rows: fine
    add_run(conn, "backup", finished_h_ago=A.NIGHTLY_H + 5)
    assert "job:backup" in by_id(A.check_etl_jobs(conn, ctx()))             # the hook: once a backup row exists it is watched


def test_weekly_jobs_use_eight_days(conn):
    for j in ("run_daily", "upstack_daily", "stage_moves"):
        add_run(conn, j, finished_h_ago=2)
    add_run(conn, "competitor_ads", finished_h_ago=7 * 24 + 5)
    assert A.check_etl_jobs(conn, ctx()) == []
    add_run(conn, "creative_priorities", finished_h_ago=8 * 24 + 5)
    assert list(by_id(A.check_etl_jobs(conn, ctx()))) == ["job:creative_priorities"]


def test_killed_mid_run_is_flagged_but_a_fresh_running_row_is_not(conn):
    for j in ("upstack_daily", "stage_moves"):
        add_run(conn, j, finished_h_ago=2)
    add_run(conn, "run_daily", finished_h_ago=20)
    add_run(conn, "run_daily", finished_h_ago=None, started_h_ago=A.RUNNING_MAX_H + 1)
    f = by_id(A.check_etl_jobs(conn, ctx()))["job:run_daily"]
    assert "never finished" in f.title
    conn.execute("DELETE FROM etl_runs WHERE job='run_daily' AND finished_at IS NULL")
    add_run(conn, "run_daily", finished_h_ago=None, started_h_ago=1)
    assert A.check_etl_jobs(conn, ctx()) == []


@pytest.mark.parametrize("err,cid", [
    ("auto_tag stopped early: claude authentication failed (check CLAUDE_CODE_OAUTH_TOKEN): invalid", "claude:token"),
    ("CLAUDE_CODE_OAUTH_TOKEN is not set: auto-tagging needs the Claude subscription token", "claude:token"),
    ("auto_tag stopped early: claude usage / rate limit reached: limit reached", "claude:limit"),
    ("something else broke", "job:auto_tag")])
def test_auto_tag_token_and_limit_are_told_apart(conn, err, cid):
    for j in ("run_daily", "upstack_daily", "stage_moves"):
        add_run(conn, j, finished_h_ago=2)
    add_run(conn, "auto_tag", finished_h_ago=3, status="failed", error=err)
    got = by_id(A.check_etl_jobs(conn, ctx()))
    assert list(got) == [cid]
    if cid == "claude:token":
        assert "claude setup-token" in got[cid].fix and "Railway" in got[cid].fix and "etl" in got[cid].fix


def test_competitor_ads_token_error_uses_the_same_check(conn):
    for j in ("run_daily", "upstack_daily", "stage_moves"):
        add_run(conn, j, finished_h_ago=2)
    add_run(conn, "competitor_ads", finished_h_ago=3, status="failed", error="competitor_ads classification stopped early: claude authentication failed (check CLAUDE_CODE_OAUTH_TOKEN)")
    assert list(by_id(A.check_etl_jobs(conn, ctx()))) == ["claude:token"]


def test_error_text_is_scrubbed(conn, monkeypatch):
    monkeypatch.setenv("META_ACCESS_TOKEN", "EAAB-super-secret-token-value")
    for j in ("upstack_daily", "stage_moves"):
        add_run(conn, j, finished_h_ago=2)
    add_run(conn, "run_daily", finished_h_ago=2, status="failed", error="failed with access_token=abc123xyz and EAAB-super-secret-token-value")
    t = by_id(A.check_etl_jobs(conn, ctx()))["job:run_daily"].title
    assert "abc123xyz" not in t and "super-secret" not in t


# ---------------------------------------------------------------------------------------------------------------- heartbeat jobs
def test_live_heartbeat(conn):
    ins = "INSERT INTO proj_live_today_snapshots (pulled_at, day, pacific_time, meta_spend, google_spend, total_spend, nc_rev, nc_orders) VALUES (%s,'2026-11-09','12:00',1,1,2,1,1)"
    only = ctx(jobs=[j for j in A.JOBS if j.name == "live"])
    assert "has never completed" in by_id(A.check_heartbeats(conn, only))["job:live"].title
    conn.execute(ins, (hours_ago(1),)); conn.commit()
    assert A.check_heartbeats(conn, only) == []
    conn.execute("DELETE FROM proj_live_today_snapshots"); conn.execute(ins, (hours_ago(A.HOURLY_H + 1),)); conn.commit()
    assert "has not succeeded for 4 h" in by_id(A.check_heartbeats(conn, only))["job:live"].title


def test_amazon_mtd_heartbeat(conn):
    only = ctx(jobs=[j for j in A.JOBS if j.name == "amazon_mtd"])
    conn.execute("INSERT INTO proj_amazon_mtd_snapshots (snapshot_day, month, computed_at, data_through, days_actual, days_projected, actual_mtd, projected_mtd, status, method) "
                 "VALUES ('2026-11-06','2026-11-01',%s,'2026-11-05',5,0,1,1,'complete','m')", (hours_ago(A.NIGHTLY_H + 2),))
    conn.commit()
    assert "job:amazon_mtd" in by_id(A.check_heartbeats(conn, only))


def test_projection_daily_failed_module_is_named(conn):
    only = ctx(jobs=[j for j in A.JOBS if j.name == "projection_daily"])
    ins = "INSERT INTO proj_refresh_runs (module, started_at, finished_at, status, error) VALUES (%s,%s,%s,%s,%s)"
    conn.execute(ins, ("pacing", hours_ago(30), hours_ago(30), "failed", "old failure"))
    conn.execute(ins, ("pacing", hours_ago(2), hours_ago(2), "ok", None))               # pacing recovered
    conn.execute(ins, ("drivers", hours_ago(2), hours_ago(2), "failed", "sheet unreadable"))
    conn.commit()
    f = by_id(A.check_heartbeats(conn, only))["job:projection_daily"]
    assert "drivers: sheet unreadable" in f.title and "pacing" not in f.title.split("Last success")[0]


def test_feeds_service_failed_feed_and_silence(conn):
    only = ctx(jobs=[j for j in A.JOBS if j.name == "feeds"])
    ins = "INSERT INTO proj_feed_attempts (feed, mode, source, started_at, finished_at, status, note) VALUES (%s,'direct','api:upstack',%s,%s,%s,%s)"
    conn.execute(ins, ("orders", hours_ago(3), hours_ago(3), "ok", None))
    conn.execute(ins, ("amazon", hours_ago(3), hours_ago(3), "failed", "expandfi login expired"))
    conn.commit()
    f = by_id(A.check_heartbeats(conn, only))["job:feeds"]
    assert "amazon (expandfi login expired)" in f.title and "orders (" not in f.title
    conn.execute("DELETE FROM proj_feed_attempts"); conn.execute(ins, ("orders", hours_ago(A.NIGHTLY_H + 3), hours_ago(A.NIGHTLY_H + 3), "ok", None)); conn.commit()
    assert "has not succeeded" in by_id(A.check_heartbeats(conn, only))["job:feeds"].title


# ---------------------------------------------------------------------------------------------------------------- freshness
def test_freshness_flags_only_what_is_behind(conn, monkeypatch):
    monkeypatch.setattr(A, "_feed_freshness", lambda c, x, t: [])
    conn.execute("INSERT INTO app_meta (key, value) VALUES ('data_through','2026-11-08'), ('upstack_through','2026-11-05')")
    conn.commit()
    got = by_id(A.check_freshness(conn, ctx()))                 # today (PT) = 2026-11-09
    assert list(got) == ["fresh:upstack"] and "2026-11-05" in got["fresh:upstack"].title and "4 days" in got["fresh:upstack"].title
    conn.execute("UPDATE app_meta SET value='2026-11-06' WHERE key='data_through'"); conn.commit()
    assert set(by_id(A.check_freshness(conn, ctx()))) == {"fresh:meta", "fresh:upstack"}


def test_freshness_uses_the_banner_limit_and_skips_unloaded_sources(conn, monkeypatch):
    monkeypatch.setattr(A, "_feed_freshness", lambda c, x, t: [])
    assert A.check_freshness(conn, ctx()) == []                                   # nothing loaded: nothing stale
    conn.execute("INSERT INTO app_meta (key, value) VALUES ('data_through','2026-11-07')"); conn.commit()   # 2 days behind = at the limit
    assert A.check_freshness(conn, ctx()) == []
    from app import queries
    assert A.STALE_META_DAYS == queries.STALE_DATA_DAYS                            # stays in step with the stale banner


def test_projection_feed_freshness_groups_stale_daily_feeds(conn, monkeypatch):
    from app.projection import freshness as F
    feeds = [{"feed": "units", "cadence": "daily", "status": "stale", "data_through": "2026-11-01", "age_days": 8, "stale_after_days": 2},
             {"feed": "pnl", "cadence": "monthly-upload", "status": "stale", "data_through": "2026-09-30", "age_days": 40, "stale_after_days": 35},
             {"feed": "ok", "cadence": "daily", "status": "fresh", "data_through": "2026-11-08", "age_days": 1, "stale_after_days": 2}]
    monkeypatch.setattr(F, "collect", lambda c, h, t: (feeds, [{"module": "x", "error": "boom"}]))
    got = by_id(A._feed_freshness(conn, ctx(), dt.date(2026, 11, 9)))
    assert "units last data 2026-11-01" in got["fresh:feeds"].title and "pnl" not in got["fresh:feeds"].title
    assert "fresh:feed_hooks" in got


def test_projection_feed_freshness_runs_on_the_real_modules(conn):
    assert isinstance(A._feed_freshness(conn, ctx(), dt.date(2026, 11, 9)), list)      # real hooks over an empty database: no crash


# ---------------------------------------------------------------------------------------------------------------- web
class Resp:
    def __init__(self, code):
        self.status_code = code


def test_web_ok_is_quiet_and_retries_once():
    calls = []

    def get(url, timeout):
        calls.append(url)
        if len(calls) == 1:
            raise ConnectionError("https://secret.example/path")
        return Resp(200)

    slept = []
    assert A.check_web(None, A.Ctx(now=NOW, http_get=get, sleep=slept.append)) == [] and slept == [A.WEB_RETRY_WAIT_SEC] and calls[0].endswith("/healthz")


def test_web_down_twice_alerts_without_leaking_exception_text():
    def get(url, timeout):
        raise ConnectionError("https://secret.example/path?token=abc")
    f = A.check_web(None, A.Ctx(now=NOW, http_get=get, sleep=lambda s: None))[0]
    assert f.id == "web:health" and "ConnectionError" in f.title and "secret" not in f.title


def test_web_503_and_slow(monkeypatch):
    f = A.check_web(None, A.Ctx(now=NOW, http_get=lambda u, timeout: Resp(503), sleep=lambda s: None))
    assert "HTTP 503" in f[0].title
    ticks = iter([0, 7, 10, 17])
    monkeypatch.setattr(A.time, "monotonic", lambda: next(ticks))
    f = A.check_web(None, A.Ctx(now=NOW, http_get=lambda u, timeout: Resp(200), sleep=lambda s: None))
    assert "answered in 7.0 s" in f[0].title


# ---------------------------------------------------------------------------------------------------------------- runner
def test_a_crashing_check_becomes_a_crash_finding_and_others_still_run(conn):
    def boom(c, x):
        raise RuntimeError("table gone")
    findings, crashes = A.run_checks(conn, ctx(), [("bad", boom), ("good", lambda c, x: [A.Finding("x", "t", "f")])])
    assert [f.id for f in findings] == ["x"] and crashes[0].id == "alerts:check:bad" and "table gone" in crashes[0].title


def test_data_gap_is_not_reported_when_its_job_already_is(conn):
    gap = A.Finding("fresh:meta", "Meta ends 2026-11-06", "f")
    job = A.Finding("job:run_daily", "run_daily failed", "f")
    findings, _ = A.run_checks(conn, ctx(), [("a", lambda c, x: [gap, job])])
    assert [f.id for f in findings] == ["job:run_daily"]
    findings, _ = A.run_checks(conn, ctx(), [("a", lambda c, x: [gap])])
    assert [f.id for f in findings] == ["fresh:meta"]                                  # job looks fine but the data is behind: still reported
