"""De-duplication, recovery, the storm cap, webhook behaviour, the CLI modes, the database-down path and /healthz. No network: the webhook is a fake."""
from __future__ import annotations

import datetime as dt

import psycopg
import pytest
from psycopg.rows import dict_row

from etl import alerts as A
from .conftest import DB_URL, NOW, add_run, hours_ago

WEB = "https://web.example"


class Fake:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    def send(self, text):
        if self.fail:
            raise A.NotifyError("Slack webhook answered HTTP 500")
        self.sent.append(text)


def F(cid="job:run_daily", title="run_daily failed: boom.", fp=""):
    return A.Finding(cid, title, "look at the logs", hours_ago(2), fp)


def rec(conn, findings, notifier, now=NOW, **kw):
    return A.reconcile(conn, findings, now, notifier, WEB, **kw)


def state(conn, cid="job:run_daily"):
    return conn.execute("SELECT * FROM alert_state WHERE check_id=%s", (cid,)).fetchone()


# ---------------------------------------------------------------------------------------------------------------- dedup + recovery
def test_alert_once_then_quiet_then_repeat_after_24h(conn):
    n = Fake()
    assert rec(conn, [F()], n)["sent"] == 1 and len(n.sent) == 1
    assert n.sent[0].startswith("ALERT: run_daily failed") and "Fix: look at the logs" in n.sent[0] and WEB in n.sent[0] and "Since" in n.sent[0]
    assert rec(conn, [F()], n, NOW + dt.timedelta(hours=5))["sent"] == 0 and len(n.sent) == 1          # still failing, inside 24 h: quiet
    r = rec(conn, [F()], n, NOW + dt.timedelta(hours=24))
    assert r["sent"] == 1 and n.sent[1].startswith("STILL FAILING (alert 2") and state(conn)["alert_count"] == 2


def test_recovery_message_and_reopen(conn):
    n = Fake()
    rec(conn, [F()], n)
    r = rec(conn, [], n, NOW + dt.timedelta(hours=3))
    assert r["recovered"] == 1 and n.sent[-1].startswith("RECOVERED: run_daily failed") and state(conn)["resolved_at"] is not None
    assert rec(conn, [], n, NOW + dt.timedelta(hours=4))["sent"] == 0                                   # nothing more once resolved
    rec(conn, [F()], n, NOW + dt.timedelta(hours=5))
    assert n.sent[-1].startswith("ALERT:") and state(conn)["resolved_at"] is None and state(conn)["alert_count"] == 1     # reopened as a new problem


def test_fingerprint_change_realerts_at_once(conn):
    n = Fake()
    rec(conn, [F(cid="job:feeds", title="feeds: a failed", fp="a")], n)
    rec(conn, [F(cid="job:feeds", title="feeds: a, b failed", fp="a|b")], n, NOW + dt.timedelta(hours=1))
    assert len(n.sent) == 2 and n.sent[1].startswith("CHANGED:")


def test_storm_cap_collapses_the_rest_into_one_summary(conn):
    n = Fake()
    findings = [F(cid=f"job:j{i}", title=f"j{i} failed") for i in range(A.MAX_MESSAGES + 4)]
    r = rec(conn, findings, n)
    assert len(n.sent) == A.MAX_MESSAGES + 1 and "4 more alert(s)" in n.sent[-1] and "job:j8" in n.sent[-1]
    assert r["pending"] == 0 and conn.execute("SELECT count(*) AS c FROM alert_state WHERE last_alerted IS NOT NULL").fetchone()["c"] == A.MAX_MESSAGES + 4
    assert rec(conn, findings, n, NOW + dt.timedelta(hours=1))["sent"] == 0                             # and none of them repeats next hour


def test_recoveries_count_towards_the_cap(conn):
    n = Fake()
    rec(conn, [F(cid=f"job:j{i}", title=f"j{i} failed") for i in range(4)], n)
    n.sent.clear()
    rec(conn, [F(cid=f"job:k{i}", title=f"k{i} failed") for i in range(4)], n, NOW + dt.timedelta(hours=1))
    assert len(n.sent) == A.MAX_MESSAGES + 1                                                             # 4 new + 4 recovered = 8 -> 5 individual + 1 summary


def test_cap_keeps_root_causes_ahead_of_data_symptoms(conn):
    n = Fake()
    findings = [F(cid="fresh:meta", title="meta stale")] + [F(cid=f"job:j{i}", title=f"j{i} failed") for i in range(A.MAX_MESSAGES)]
    rec(conn, findings, n)
    assert not any("meta stale" in m for m in n.sent[:A.MAX_MESSAGES]) and "fresh:meta" in n.sent[-1]


# ---------------------------------------------------------------------------------------------------------------- webhook unset / failing
def test_without_a_notifier_nothing_is_marked_alerted_and_it_goes_out_later(conn):
    r = rec(conn, [F()], None)
    assert r["sent"] == 0 and r["pending"] == 1 and state(conn)["last_alerted"] is None
    n = Fake()
    rec(conn, [F()], n, NOW + dt.timedelta(hours=1))
    assert len(n.sent) == 1 and n.sent[0].startswith("ALERT:") and state(conn)["last_alerted"] is not None


def test_unalerted_problem_that_clears_is_resolved_silently(conn):
    rec(conn, [F()], None)
    n = Fake()
    rec(conn, [], n, NOW + dt.timedelta(hours=1))
    assert n.sent == [] and state(conn)["resolved_at"] is not None


def test_failed_delivery_keeps_the_alert_pending_and_is_reported(conn):
    r = rec(conn, [F()], Fake(fail=True))
    assert r["failed_delivery"] and r["pending"] == 1 and state(conn)["last_alerted"] is None
    n = Fake()
    rec(conn, [F()], n, NOW + dt.timedelta(hours=1))
    assert len(n.sent) == 1                                                                              # retried


def test_slack_notifier_posts_json_and_never_leaks_the_url():
    url = "https://hooks.slack.com/services/T000/B000/SECRETSECRET"
    seen = {}

    class R:
        status_code = 200

    def post(u, json, timeout):
        seen.update(u=u, json=json)
        return R()

    A.SlackNotifier(url, post=post).send("hello")
    assert seen == {"u": url, "json": {"text": "hello"}}

    class Bad:
        status_code = 404

    with pytest.raises(A.NotifyError) as e:
        A.SlackNotifier(url, post=lambda u, json, timeout: Bad()).send("x")
    assert "404" in str(e.value) and "SECRET" not in str(e.value)

    def boom(u, json, timeout):
        raise RuntimeError(f"cannot reach {u}")
    with pytest.raises(A.NotifyError) as e:
        A.SlackNotifier(url, post=boom).send("x")
    assert "SECRET" not in str(e.value) and "hooks.slack.com" not in str(e.value) and "RuntimeError" in str(e.value)


def test_notifier_from_env():
    assert A.notifier_from_env({}) is None and A.notifier_from_env({"SLACK_ALERT_WEBHOOK_URL": "  "}) is None
    assert isinstance(A.notifier_from_env({"SLACK_ALERT_WEBHOOK_URL": "https://hooks.slack.com/x"}), A.SlackNotifier)


# ---------------------------------------------------------------------------------------------------------------- main()
def healthy_ctx():
    class R:
        status_code = 200
    return A.Ctx(now=NOW, web_url=WEB, http_get=lambda u, timeout: R(), sleep=lambda s: None)


def connect():
    return psycopg.connect(DB_URL, row_factory=dict_row)


def seed_healthy(conn):
    for j in ("run_daily", "upstack_daily", "stage_moves"):
        add_run(conn, j, finished_h_ago=5)
    conn.execute("INSERT INTO proj_live_today_snapshots (pulled_at, day, pacific_time, meta_spend, google_spend, total_spend, nc_rev, nc_orders) VALUES (%s,'2026-11-09','12:00',1,1,2,1,1)", (hours_ago(1),))
    conn.execute("INSERT INTO proj_amazon_mtd_snapshots (snapshot_day, month, computed_at, data_through, days_actual, days_projected, actual_mtd, projected_mtd, status, method) VALUES ('2026-11-09','2026-11-01',%s,'2026-11-08',8,0,1,1,'complete','m')", (hours_ago(5),))
    conn.execute("INSERT INTO proj_refresh_runs (module, started_at, finished_at, status) VALUES ('pacing',%s,%s,'ok')", (hours_ago(5), hours_ago(5)))
    conn.execute("INSERT INTO proj_feed_attempts (feed, mode, source, started_at, finished_at, status) VALUES ('orders','direct','api:upstack',%s,%s,'ok')", (hours_ago(5), hours_ago(5)))
    conn.commit()


def test_main_end_to_end_healthy_then_failing_then_recovered(conn):
    seed_healthy(conn)
    n = Fake()
    assert A.main([], env={}, notifier=n, connect=connect, ctx=healthy_ctx()) == 0 and n.sent == []
    add_run(conn, "run_daily", finished_h_ago=1, status="failed", error="meta token rejected")
    assert A.main([], env={}, notifier=n, connect=connect, ctx=healthy_ctx()) == 0
    assert len(n.sent) == 1 and "meta token rejected" in n.sent[0]
    add_run(conn, "run_daily", finished_h_ago=0.5)
    assert A.main([], env={}, notifier=n, connect=connect, ctx=healthy_ctx()) == 0 and n.sent[-1].startswith("RECOVERED:")


def test_main_exits_nonzero_when_a_check_crashes_and_says_so(conn, monkeypatch):
    seed_healthy(conn)
    monkeypatch.setattr(A, "CHECKS", [("freshness", lambda c, x: (_ for _ in ()).throw(RuntimeError("kaput")))])
    n = Fake()
    assert A.main([], env={}, notifier=n, connect=connect, ctx=healthy_ctx()) == 1
    assert "alerts check 'freshness' crashed" in n.sent[0] and "kaput" in n.sent[0]


def test_main_exits_nonzero_when_slack_is_down(conn):
    seed_healthy(conn)
    add_run(conn, "run_daily", finished_h_ago=1, status="failed", error="boom")
    assert A.main([], env={}, notifier=Fake(fail=True), connect=connect, ctx=healthy_ctx()) == 1


def test_main_without_webhook_logs_a_warning_and_exits_zero(conn, capsys):
    seed_healthy(conn)
    add_run(conn, "run_daily", finished_h_ago=1, status="failed", error="boom")
    assert A.main([], env={}, connect=connect, ctx=healthy_ctx()) == 0
    out = capsys.readouterr().out
    assert "WARNING: SLACK_ALERT_WEBHOOK_URL is not set" in out and "pending 1" in out
    assert state(conn)["last_alerted"] is None


def test_main_database_down_alerts_through_the_webhook_and_exits_nonzero():
    def dead():
        raise psycopg.OperationalError("could not connect to postgresql://user:pw@host/db")
    n = Fake()
    assert A.main([], env={"ALERTS_WEB_URL": WEB}, notifier=n, connect=dead, ctx=healthy_ctx()) == 1
    assert "cannot reach the database" in n.sent[0] and "pw@host" not in n.sent[0] and WEB in n.sent[0]


def test_main_missing_state_table_is_a_crash_alert(conn):
    seed_healthy(conn)
    conn.execute("ALTER TABLE alert_state RENAME TO alert_state_x"); conn.commit()
    try:
        n = Fake()
        assert A.main([], env={}, notifier=n, connect=connect, ctx=healthy_ctx()) == 1
        assert "the alerts job crashed" in n.sent[0] and "alert_state" in n.sent[0]
    finally:
        conn.execute("ALTER TABLE alert_state_x RENAME TO alert_state"); conn.commit()


def test_dry_run_prints_and_writes_nothing(conn, capsys):
    seed_healthy(conn)
    add_run(conn, "run_daily", finished_h_ago=1, status="failed", error="boom")
    n = Fake()
    assert A.main(["--dry-run"], env={}, notifier=n, connect=connect, ctx=healthy_ctx()) == 0
    out = capsys.readouterr().out
    assert "--- would send ---" in out and "ALERT: run_daily failed" in out and n.sent == []
    assert conn.execute("SELECT count(*) AS c FROM alert_state").fetchone()["c"] == 0


def test_dry_run_tolerates_a_database_without_the_state_table(conn, capsys):
    seed_healthy(conn)
    add_run(conn, "run_daily", finished_h_ago=1, status="failed", error="boom")
    conn.execute("ALTER TABLE alert_state RENAME TO alert_state_x"); conn.commit()
    try:
        assert A.main(["--dry-run"], env={}, connect=connect, ctx=healthy_ctx()) == 0
        assert "would send" in capsys.readouterr().out
    finally:
        conn.execute("ALTER TABLE alert_state_x RENAME TO alert_state"); conn.commit()


def test_test_flag_sends_a_labelled_message_or_explains(capsys):
    n = Fake()
    assert A.main(["--test"], env={}, notifier=n) == 0 and n.sent[0].startswith("TEST ONLY")
    assert A.main(["--test"], env={}) == 2 and "not set" in capsys.readouterr().out
    assert A.main(["--test"], env={}, notifier=Fake(fail=True)) == 1


# ---------------------------------------------------------------------------------------------------------------- /healthz
def test_healthz_endpoint_ok_and_503(dburl, monkeypatch):
    from app import create_app, db as appdb
    app = create_app({"DATABASE_URL": dburl, "APP_USERS": {}, "SECRET_KEY": "k", "DEV_INSECURE_COOKIES": True, "DB_POOL_MAX": 2, "RESPONSE_CACHE_MB": 0})
    try:
        c = app.test_client()
        r = c.get("/healthz")                                   # public: no login, no data
        assert r.status_code == 200 and r.get_json() == {"ok": True}
        monkeypatch.setattr(appdb, "ping", lambda *a, **k: False)
        r = c.get("/healthz")
        assert r.status_code == 503 and r.get_json() == {"ok": False}
    finally:
        app.extensions["pool"].close()
