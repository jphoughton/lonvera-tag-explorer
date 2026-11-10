"""Amazon month to date, completed: the pure rule against a plain-Python oracle, the nightly snapshot (idempotent write, read-preferred, live fallback), the API,
the ETL entry point, the two cron scripts and the page."""
import datetime as dt
import os
import random
import stat
import subprocess
from pathlib import Path

import pytest

from app.projection.pacing import amazon_mtd as amz
from app.projection.pacing.feeds import Frame
from test_pacing_page import base_url, browser, open_page                   # noqa: F401  (fixtures + helper of the pacing page tests)

D = dt.date
ROOT = Path(__file__).resolve().parents[3]
P = "/api/projection/pacing"


# ---------------------------------------------------------------------------------------------------------------- the rule, against an oracle

def oracle(series, data_through, month, window=7):
    """Josh's rule written the slow way: rate = mean of the last `window` reported days (revenue > 0) up to the last reported day; the missing days between
    the last reported day and data_through (inside the month) are projected at that rate."""
    days = sorted(d for d, v in series.items() if v > 0 and d <= data_through)
    last = days[-1]
    rate = sum(series[d] for d in days[-window:]) / len(days[-window:])
    actual = sum(v for d, v in series.items() if month <= d <= data_through and v > 0)
    missing = 0
    d = max(month, last + dt.timedelta(days=1))
    while d <= data_through:
        missing += 1
        d += dt.timedelta(days=1)
    return actual, rate, missing, actual + rate * missing


def series_of(start, values):
    return {start + dt.timedelta(days=i): float(v) for i, v in enumerate(values)}


def test_josh_example_eight_days_in_five_reported_three_projected():
    s = series_of(D(2026, 10, 30), [4000, 5000, 6000, 5500, 4500, 5200, 5800])          # Oct 30 .. Nov 5
    r = amz.compute(s, D(2026, 11, 9), D(2026, 11, 8), D(2026, 11, 1))
    assert (r["days_actual"], r["days_projected"], r["last_actual_day"]) == (5, 3, "2026-11-05")
    rate = (4000 + 5000 + 6000 + 5500 + 4500 + 5200 + 5800) / 7                              # the 7 reported days at hand, two of them in October
    assert r["daily_rate"] == pytest.approx(rate, abs=0.01)
    assert [p["day"] for p in r["projected_days"]] == ["2026-11-06", "2026-11-07", "2026-11-08"]
    assert r["actual_mtd"] == 6000 + 5500 + 4500 + 5200 + 5800
    assert r["projected_mtd"] == pytest.approx(r["actual_mtd"] + 3 * rate, abs=0.02)
    assert r["status"] == "projected" and "5 days reported through Nov 5" in r["note"] and "3 days projected" in r["note"] and "trailing 7-day average" in r["note"]


def test_josh_example_exact_numbers():
    s = series_of(D(2026, 11, 1), [100, 200, 300, 400, 500])                                  # Nov 1..5 only (no October rows)
    r = amz.compute(s, D(2026, 11, 9), D(2026, 11, 8), D(2026, 11, 1))
    assert r["actual_mtd"] == 1500 and r["daily_rate"] == 300 and r["rate_days"] == 5 and "short_window" in r["flags"]
    assert r["projected_amount"] == 900 and r["projected_mtd"] == 2400


def test_actuals_overwrite_projections_when_days_arrive():
    base = series_of(D(2026, 11, 1), [100, 200, 300, 400, 500])
    r1 = amz.compute(base, D(2026, 11, 9), D(2026, 11, 8), D(2026, 11, 1))
    assert r1["days_projected"] == 3
    base[D(2026, 11, 6)] = 900.0                                                              # day 6 arrives
    r2 = amz.compute(base, D(2026, 11, 9), D(2026, 11, 8), D(2026, 11, 1))
    assert (r2["days_actual"], r2["days_projected"]) == (6, 2) and r2["actual_mtd"] == 2400
    assert r2["daily_rate"] == pytest.approx(400)                                              # (100+..+500+900)/6 : the rate recomputes
    base[D(2026, 11, 7)] = 700.0
    base[D(2026, 11, 8)] = 800.0
    r3 = amz.compute(base, D(2026, 11, 9), D(2026, 11, 8), D(2026, 11, 1))
    assert r3["days_projected"] == 0 and r3["projected_mtd"] == r3["actual_mtd"] == 3900 and r3["status"] == "complete"


def test_matches_the_oracle_on_random_series():
    rnd = random.Random(7)
    for _ in range(300):
        month = D(2026, rnd.randint(1, 10), 1)
        thr = month + dt.timedelta(days=rnd.randint(3, 25))
        last = thr - dt.timedelta(days=rnd.randint(0, 4))
        s = {}
        d = month - dt.timedelta(days=20)
        while d <= last:
            s[d] = round(rnd.uniform(4000, 9000), 2)
            d += dt.timedelta(days=1)
        r = amz.compute(s, thr + dt.timedelta(days=1), thr, month)
        actual, rate, missing, total = oracle(s, thr, month)
        assert r["actual_mtd"] == pytest.approx(actual, abs=0.01)
        assert r["days_projected"] == missing
        assert r["projected_mtd"] == pytest.approx(total, abs=0.02 + missing * 0.01)


def test_zero_actual_days_in_the_month_falls_back_to_the_previous_months_last_seven():
    s = series_of(D(2026, 10, 20), [1000 + 100 * i for i in range(12)])                       # Oct 20 .. Oct 31
    r = amz.compute(s, D(2026, 11, 3), D(2026, 11, 2), D(2026, 11, 1))                       # Nov 1 and 2 are missing
    assert r["days_actual"] == 0 and r["days_projected"] == 2 and r["actual_mtd"] == 0
    assert r["rate_source"] == "previous_month_last_7" and "previous month" in r["note"]
    assert r["daily_rate"] == pytest.approx(sum(1000 + 100 * i for i in range(5, 12)) / 7)
    assert r["projected_mtd"] == pytest.approx(2 * r["daily_rate"], abs=0.01)


def test_fewer_than_seven_available_days_uses_what_there_is_and_says_so():
    s = {D(2026, 11, 1): 1000.0, D(2026, 11, 2): 2000.0, D(2026, 11, 3): 3000.0}
    r = amz.compute(s, D(2026, 11, 6), D(2026, 11, 5), D(2026, 11, 1))
    assert r["rate_days"] == 3 and r["daily_rate"] == 2000 and "short_window" in r["flags"] and r["days_projected"] == 2


def test_a_zero_or_missing_day_inside_the_window_is_not_averaged_in_as_zero():
    s = series_of(D(2026, 11, 1), [1000, 0, 1000, 1000, 1000, 1000, 1000, 1000])             # Nov 2 reported as 0 (ads-only row)
    del s[D(2026, 11, 4)]                                                                      # Nov 4 has no row at all
    r = amz.compute(s, D(2026, 11, 11), D(2026, 11, 10), D(2026, 11, 1))
    assert r["daily_rate"] == 1000 and "window_has_gaps" in r["flags"] and "gap_days_inside_month" in r["flags"]
    assert r["gap_days"] == ["2026-11-02", "2026-11-04"] and r["days_projected"] == 2          # interior gaps are flagged, never projected
    assert r["actual_mtd"] == 6000


def test_a_stale_feed_is_capped_not_extrapolated_for_a_week():
    s = series_of(D(2026, 11, 1), [1000, 1000, 1000])
    r = amz.compute(s, D(2026, 11, 12), D(2026, 11, 11), D(2026, 11, 1))                     # last reported Nov 3, cutoff Nov 11: 8 days behind
    assert r["status"] == "stale" and "feed_stale" in r["flags"] and r["days_projected"] == 0 and r["projected_mtd"] == r["actual_mtd"] == 3000
    assert "feed stale" in r["note"]
    ok = amz.compute(s, D(2026, 11, 9), D(2026, 11, 8), D(2026, 11, 1))                      # exactly 5 days behind is still projected
    assert ok["status"] == "projected" and ok["days_projected"] == 5


def test_an_incomplete_newest_day_is_topped_up_conservatively_and_flagged():
    s = series_of(D(2026, 11, 1), [5000, 5000, 5000, 5000, 5000, 5000, 5000, 900])          # Nov 8 looks partial (18% of the days before)
    r = amz.compute(s, D(2026, 11, 10), D(2026, 11, 9), D(2026, 11, 1))
    assert "newest_day_partial" in r["flags"] and r["daily_rate"] == 5000 and r["partial_topup"] == 4100
    assert r["days_projected"] == 1 and r["actual_mtd"] == 35900
    assert r["projected_mtd"] == pytest.approx(35900 + 4100 + 5000, abs=0.01)
    normal = series_of(D(2026, 11, 1), [5000] * 7 + [4300])                                   # a quiet Sunday (86%) is real data, never topped up
    assert amz.compute(normal, D(2026, 11, 10), D(2026, 11, 9), D(2026, 11, 1))["partial_topup"] == 0


def test_the_real_nov_8_pattern_is_not_flagged_partial():
    s = series_of(D(2026, 10, 25), [5103.89, 5493.74, 4403.32, 5737.84, 4134.09, 4451.97, 5921.14, 5518.10, 4678.57, 5061.79, 4998.68, 6742.22, 5522.31, 5928.30, 4291.71])
    r = amz.compute(s, D(2026, 11, 9), D(2026, 11, 8), D(2026, 11, 1))
    assert r["status"] == "complete" and r["days_projected"] == 0 and r["projected_mtd"] == r["actual_mtd"]


def test_month_boundaries_and_closed_months():
    s = series_of(D(2026, 10, 1), [1000] * 31)
    closed = amz.compute(s, D(2026, 11, 9), D(2026, 11, 8), D(2026, 10, 1))                    # a closed month: all 31 days, nothing to project
    assert closed["days_elapsed"] == 31 and closed["days_projected"] == 0 and closed["actual_mtd"] == 31000
    future = amz.compute(s, D(2026, 11, 9), D(2026, 11, 8), D(2026, 12, 1))
    assert future["status"] == "no_days" and future["projected_mtd"] == 0
    first = amz.compute(s, D(2026, 11, 1), D(2026, 10, 31), None)                              # Nov 1 morning: the month holding the last complete day is October
    assert first["month"] == "2026-10-01" and first["days_projected"] == 0
    empty = amz.compute({}, D(2026, 11, 9), D(2026, 11, 8), D(2026, 11, 1))
    assert empty["status"] == "no_rate" and empty["daily_rate"] is None


def test_the_pacific_clock_around_daylight_saving():
    utc = lambda *a: dt.datetime(*a, tzinfo=dt.timezone.utc)                                   # noqa: E731
    assert amz.pacific_today(utc(2026, 11, 2, 7, 30)) == D(2026, 11, 1)                        # 23:30 PST on Nov 1: still the 1st (fall back was Nov 1)
    assert amz.pacific_today(utc(2026, 11, 2, 8, 30)) == D(2026, 11, 2)
    assert amz.pacific_today(utc(2026, 3, 8, 9, 59)) == D(2026, 3, 8)                           # 01:59 PST just before spring forward
    assert amz.pacific_today(utc(2026, 3, 9, 6, 59)) == D(2026, 3, 8)                           # 23:59 PDT on Mar 8
    assert amz.pacific_today(utc(2026, 3, 9, 7, 0)) == D(2026, 3, 9)
    # the run at 13:00 UTC is 05:00-06:00 Pacific on the same date either side of the change
    assert amz.pacific_today(utc(2026, 11, 9, 13)) == D(2026, 11, 9) and amz.pacific_today(utc(2026, 7, 9, 13)) == D(2026, 7, 9)


def test_apply_to_frame_fills_only_projected_days_and_leaves_the_input_alone():
    from collections import defaultdict
    days = {D(2026, 11, i): defaultdict(float, {"a_revenue": 100.0, "a_orders": 3.0}) for i in range(1, 5)}
    days[D(2026, 11, 3)]["a_revenue"] = days[D(2026, 11, 4)]["a_revenue"] = 0.0
    fr = Frame(D(2026, 11, 1), D(2026, 11, 4), days)
    res = {"projected_days": [{"day": "2026-11-03", "revenue": 250.0}, {"day": "2026-11-04", "revenue": 250.0}], "partial_topup": 0, "last_reported_day": "2026-11-02"}
    new = amz.apply_to_frame(fr, res)
    assert [new.days[D(2026, 11, i)]["a_revenue"] for i in range(1, 5)] == [100, 100, 250, 250] and new.days[D(2026, 11, 4)]["a_orders"] == 3
    assert fr.days[D(2026, 11, 3)]["a_revenue"] == 0                                           # the original frame is untouched
    assert amz.apply_to_frame(fr, {"projected_days": [], "partial_topup": 0}) is fr


# ---------------------------------------------------------------------------------------------------------------- snapshot table

NOW = dt.datetime(2026, 11, 9, 21, 5, tzinfo=dt.timezone.utc)


@pytest.fixture()
def feed(pacing_db):
    """Commits a trimmed Amazon feed (rows after `last` removed, restored afterwards) and an empty snapshot table."""
    import psycopg
    made = []

    def trim(last):
        with psycopg.connect(pacing_db) as c:
            if not made:
                made.append(c.execute("SELECT * FROM proj_feed_amazon").fetchall())
                made.append([d.name for d in c.execute("SELECT * FROM proj_feed_amazon LIMIT 0").description])
            c.execute("DELETE FROM proj_feed_amazon WHERE day > %s", (last,))
            c.execute("DELETE FROM proj_amazon_mtd_snapshots")

    yield trim
    with psycopg.connect(pacing_db) as c:
        c.execute("DELETE FROM proj_amazon_mtd_snapshots")
        if made:
            c.execute("DELETE FROM proj_feed_amazon")
            cols = ", ".join(made[1])
            for r in made[0]:
                c.execute(f"INSERT INTO proj_feed_amazon ({cols}) VALUES ({', '.join(['%s'] * len(r))})", r)


def _series(pacing_db, thr):
    import psycopg
    from psycopg.rows import dict_row
    with psycopg.connect(pacing_db, row_factory=dict_row) as c:
        return amz.load_series(c, thr)


def test_snapshot_write_is_idempotent_per_day_and_keeps_history(clean_conn):
    c = clean_conn
    c.execute("DELETE FROM proj_amazon_mtd_snapshots")
    res = amz.compute(amz.load_series(c, D(2026, 11, 2)), D(2026, 11, 3), D(2026, 11, 2), D(2026, 11, 1))
    amz.save_snapshot(c, res, D(2026, 11, 3), NOW)
    amz.save_snapshot(c, res, D(2026, 11, 3), NOW + dt.timedelta(hours=1))                       # same day again: replaces, never duplicates
    assert c.execute("SELECT count(*) AS n FROM proj_amazon_mtd_snapshots").fetchone()["n"] == 1
    assert c.execute("SELECT computed_at FROM proj_amazon_mtd_snapshots").fetchone()["computed_at"] == NOW + dt.timedelta(hours=1)
    amz.save_snapshot(c, res, D(2026, 11, 4), NOW + dt.timedelta(days=1))                         # the next day adds a row: history is kept
    assert c.execute("SELECT count(*) AS n FROM proj_amazon_mtd_snapshots").fetchone()["n"] == 2
    row = c.execute("SELECT * FROM proj_amazon_mtd_snapshots WHERE snapshot_day = '2026-11-03'").fetchone()
    assert float(row["projected_mtd"]) == res["projected_mtd"] and row["method"] == amz.METHOD and row["status"] == res["status"]


def test_read_prefers_a_current_snapshot_and_says_so(clean_conn):
    c = clean_conn
    c.execute("DELETE FROM proj_amazon_mtd_snapshots")
    res = amz.compute(amz.load_series(c, D(2026, 11, 2)), D(2026, 11, 3), D(2026, 11, 2), D(2026, 11, 1))
    res["projected_mtd"] = 123456.78                                                              # a marker only the snapshot can supply
    amz.save_snapshot(c, res, D(2026, 11, 3), dt.datetime.now(dt.timezone.utc))
    got = amz.resolve(c, D(2026, 11, 1), D(2026, 11, 3), D(2026, 11, 2))
    assert got["source"] == "snapshot" and got["projected_mtd"] == 123456.78 and got["live_reason"] is None
    live = amz.resolve(c, D(2026, 11, 1), D(2026, 11, 3), D(2026, 11, 2), live=True)
    assert live["source"] == "computed live" and live["projected_mtd"] != 123456.78


@pytest.mark.parametrize("case", ["missing", "old", "month_rolled", "cutoff_moved", "feed_reloaded", "other_method"])
def test_read_falls_back_to_live_and_says_why(clean_conn, case):
    c = clean_conn
    c.execute("DELETE FROM proj_amazon_mtd_snapshots")
    res = amz.compute(amz.load_series(c, D(2026, 11, 2)), D(2026, 11, 3), D(2026, 11, 2), D(2026, 11, 1))
    res["projected_mtd"] = 999.0
    today, thr, month, written = D(2026, 11, 3), D(2026, 11, 2), D(2026, 11, 1), dt.datetime.now(dt.timezone.utc)
    if case == "old":
        amz.save_snapshot(c, res, D(2026, 11, 1), written)                                         # two Pacific days old: beyond the one-day tolerance
    elif case == "month_rolled":
        amz.save_snapshot(c, res, D(2026, 11, 3), written)
        month, thr, today = D(2026, 12, 1), D(2026, 12, 2), D(2026, 12, 3)                           # asking for December: the November snapshot is no use
    elif case == "cutoff_moved":
        amz.save_snapshot(c, res, D(2026, 11, 3), written)
        thr = D(2026, 11, 1)
    elif case == "feed_reloaded":
        amz.save_snapshot(c, res, D(2026, 11, 3), written - dt.timedelta(days=30))                  # computed before the feed's last load
    elif case == "other_method":
        amz.save_snapshot(c, dict(res, method="old/v0"), D(2026, 11, 3), written)
    got = amz.resolve(c, month, today, thr)
    assert got["source"] == "computed live" and got["projected_mtd"] != 999.0
    assert got["live_reason"] and got["live_reason"] != "forced"
    if case == "missing":
        assert got["live_reason"] == "no snapshot for this month"


def test_a_snapshot_one_day_old_is_still_used_when_the_cutoff_matches(clean_conn):
    c = clean_conn
    c.execute("DELETE FROM proj_amazon_mtd_snapshots")
    res = amz.compute(amz.load_series(c, D(2026, 11, 2)), D(2026, 11, 2), D(2026, 11, 2), D(2026, 11, 1))
    amz.save_snapshot(c, res, D(2026, 11, 2), dt.datetime.now(dt.timezone.utc))
    assert amz.resolve(c, D(2026, 11, 1), D(2026, 11, 3), D(2026, 11, 2))["source"] == "snapshot"


# ---------------------------------------------------------------------------------------------------------------- ETL entry point

def test_etl_writes_one_row_per_day_and_is_idempotent(feed, pacing_db):
    import psycopg
    from psycopg.rows import dict_row
    from etl import amazon_mtd
    feed(D(2026, 10, 30))
    now = dt.datetime(2026, 11, 3, 21, 5, tzinfo=dt.timezone.utc)                                   # Nov 3, 13:05 Pacific
    for _ in range(2):
        with psycopg.connect(pacing_db, row_factory=dict_row) as c:
            assert amazon_mtd.main([], conn=c, now_utc=now) == 0
    with psycopg.connect(pacing_db, row_factory=dict_row) as c:
        rows = c.execute("SELECT * FROM proj_amazon_mtd_snapshots").fetchall()
    assert len(rows) == 1 and rows[0]["month"] == D(2026, 11, 1) and rows[0]["snapshot_day"] == D(2026, 11, 3) and rows[0]["days_projected"] == 2
    assert rows[0]["rate_source"] == "previous_month_last_7" and float(rows[0]["actual_mtd"]) == 0


def test_etl_dry_run_writes_nothing_and_an_error_writes_nothing_and_exits_non_zero(feed, pacing_db, capsys):
    import psycopg
    from psycopg.rows import dict_row
    from etl import amazon_mtd
    feed(D(2026, 11, 2))
    now = dt.datetime(2026, 11, 3, 21, 5, tzinfo=dt.timezone.utc)
    with psycopg.connect(pacing_db, row_factory=dict_row) as c:
        assert amazon_mtd.main(["--dry-run"], conn=c, now_utc=now) == 0
        assert c.execute("SELECT count(*) AS n FROM proj_amazon_mtd_snapshots").fetchone()["n"] == 0
        # a feed with no revenue at all in the window is an error: nothing written, exit 1
        c.execute("UPDATE proj_feed_amazon SET revenue = 0")
        assert amazon_mtd.main([], conn=c, now_utc=now) == 1
        c.rollback()
    with psycopg.connect(pacing_db, row_factory=dict_row) as c:
        assert c.execute("SELECT count(*) AS n FROM proj_amazon_mtd_snapshots").fetchone()["n"] == 0
    assert "failed" in capsys.readouterr().err


def test_etl_unreachable_database_exits_non_zero(monkeypatch, capsys):
    from etl import amazon_mtd
    monkeypatch.setenv("DATABASE_URL", "postgresql://nobody:pw@127.0.0.1:1/none")
    assert amazon_mtd.main([]) == 1
    assert "pw@" not in capsys.readouterr().err


def test_feeds_cli_has_the_alias(monkeypatch):
    from etl import amazon_mtd
    from etl.feeds import __main__ as cli
    seen = []
    monkeypatch.setattr(amazon_mtd, "main", lambda argv=None, **kw: seen.append(argv) or 0)
    assert cli.main(["amazon-mtd", "--dry-run"]) == 0 and seen == [["--dry-run"]]


# ---------------------------------------------------------------------------------------------------------------- cron scripts

def _stub(tmp_path, script):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    log = tmp_path / "calls.log"
    exe = bin_dir / "python"
    exe.write_text('#!/bin/sh\necho "$*" >> "$STUB_LOG"\ncase "$2" in\n  etl.amazon_mtd) exit "${AMZ_RC:-0}";;\n  etl.feeds) case "$3" in run) exit "${RUN_RC:-0}";; verify) exit "${VER_RC:-0}";; esac;;\n'
                   '  etl.run_daily) exit "${DAILY_RC:-0}";;\n  etl.projection_daily) exit "${PROJ_RC:-0}";;\nesac\nexit 0\n')
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)

    def call(**env):
        e = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "STUB_LOG": str(log), **{k: str(v) for k, v in env.items()}}
        p = subprocess.run(["/bin/sh", str(ROOT / "scripts/deploy" / script)], capture_output=True, text=True, env=e, cwd=str(tmp_path))
        calls = log.read_text().splitlines() if log.exists() else []
        log.unlink(missing_ok=True)
        return p, calls
    return call


def test_run_feeds_snapshots_right_after_the_load_and_before_verify(tmp_path):
    p, calls = _stub(tmp_path, "run_feeds.sh")()
    assert p.returncode == 0 and calls == ["-m etl.migrate", "-m etl.feeds run", "-m etl.amazon_mtd", "-m etl.feeds verify"]


def test_run_feeds_exit_codes_keep_their_order_and_a_snapshot_failure_alone_is_visible(tmp_path):
    call = _stub(tmp_path, "run_feeds.sh")
    p, calls = call(AMZ_RC=4)
    assert p.returncode == 4 and "etl.amazon_mtd failed (exit 4)" in p.stderr and calls[-1] == "-m etl.feeds verify"          # the run still verified
    p, _ = call(AMZ_RC=4, VER_RC=2)
    assert p.returncode == 2                                                                                                   # earlier steps keep priority
    p, _ = call(AMZ_RC=4, RUN_RC=3)
    assert p.returncode == 3
    p, calls = call(RUN_RC=3)
    assert p.returncode == 3 and "-m etl.amazon_mtd" in calls                                                                  # a failed load still snapshots (page falls back live)


def test_run_etl_snapshots_after_projection_daily_as_a_safety_net(tmp_path):
    call = _stub(tmp_path, "run_etl.sh")
    p, calls = call()
    assert p.returncode == 0 and calls[-3:] == ["-m etl.projection_daily", "-m etl.amazon_mtd", "-m etl.auto_tag"]
    p, calls = call(AMZ_RC=4)
    assert p.returncode == 4 and "etl.amazon_mtd failed (exit 4)" in p.stderr
    p, _ = call(AMZ_RC=4, PROJ_RC=1)
    assert p.returncode == 1                                                                                                   # earlier failures win
    p, _ = call(DAILY_RC=3, AMZ_RC=4)
    assert p.returncode == 3


# ---------------------------------------------------------------------------------------------------------------- API

def _expected_rate(series, last):
    days = sorted(d for d, v in series.items() if v > 0 and d <= last)[-7:]
    return sum(series[d] for d in days) / len(days)


def test_endpoint_computes_live_without_a_snapshot_and_says_so(api, feed, pacing_db):
    feed(D(2026, 10, 30))                                                                            # last reported Oct 30; as-of Nov 3 so Nov 1 and 2 are missing
    j = api.get(f"{P}/amazon-mtd?month=2026-11").get_json()
    rate = _expected_rate(_series(pacing_db, D(2026, 11, 2)), D(2026, 10, 30))
    assert j["source"] == "computed live" and j["live_reason"] == "no snapshot for this month"
    assert (j["days_actual"], j["days_projected"], j["actual_mtd"]) == (0, 2, 0) and j["projected_mtd"] == pytest.approx(2 * rate, abs=0.02)
    assert j["rate_source"] == "previous_month_last_7" and j["status"] == "projected"


def test_endpoint_reads_the_snapshot_when_current_and_goes_live_when_not(api, feed, pacing_db):
    import psycopg
    from psycopg.rows import dict_row
    feed(D(2026, 10, 30))
    from etl import amazon_mtd
    with psycopg.connect(pacing_db, row_factory=dict_row) as c:
        assert amazon_mtd.main([], conn=c, now_utc=dt.datetime.now(dt.timezone.utc)) == 0   # today = the real date: no match with the pinned Nov 3
    j = api.get(f"{P}/amazon-mtd?month=2026-11").get_json()
    assert j["source"] == "computed live"                                                             # the snapshot is dated today, the page is pinned to Nov 3
    with psycopg.connect(pacing_db, row_factory=dict_row) as c:
        res = amz.compute(amz.load_series(c, D(2026, 11, 2)), D(2026, 11, 3), D(2026, 11, 2), D(2026, 11, 1))
        res["projected_mtd"] = 4242.42
        amz.save_snapshot(c, res, D(2026, 11, 3), dt.datetime.now(dt.timezone.utc))
        c.commit()
    j = api.get(f"{P}/amazon-mtd?month=2026-11").get_json()
    assert j["source"] == "snapshot" and j["projected_mtd"] == 4242.42
    assert api.get(f"{P}/amazon-mtd?month=2026-11&live=1").get_json()["source"] == "computed live"
    assert api.get(f"{P}/amazon-mtd?month=2026-10").status_code == 400


def test_endpoint_needs_login(api_app):
    assert api_app.test_client().get(f"{P}/amazon-mtd").status_code == 401


def _row(j, key):
    return next(r for r in j["rows"] if r["key"] == key)


def test_month_table_uses_the_completed_amazon_mtd_and_actuals_only_is_one_switch_away(api, feed, pacing_db):
    feed(D(2026, 10, 30))
    full = api.get(f"{P}/month?month=2026-11").get_json()
    act = api.get(f"{P}/month?month=2026-11&amazon=actuals").get_json()
    rate = _expected_rate(_series(pacing_db, D(2026, 11, 2)), D(2026, 10, 30))
    a_full, a_act = _row(full, "amazon_rev"), _row(act, "amazon_rev")
    assert a_act["mtd"] == 0 and a_full["mtd"] == pytest.approx(2 * rate, abs=0.02)
    assert a_full["amazon_projected"] is True and a_full["actuals_only"] == 0 and "amazon_projected" not in a_act
    # downstream math follows: total revenue, the month-end projection and the pace against the goal use the completed number
    t_full, t_act = _row(full, "total_rev"), _row(act, "total_rev")
    assert t_full["mtd"] - t_act["mtd"] == pytest.approx(2 * rate, abs=0.05) and t_full["projection"] > t_act["projection"] and t_full["pacing"] > t_act["pacing"]
    assert t_full["amazon_projected"] is True and "total_rev" in full["amazon_mtd"]["rows_changed"] and "amazon_rev" in full["amazon_mtd"]["rows_changed"]
    # rows that do not touch Amazon revenue do not move
    assert _row(full, "dtc_rev")["mtd"] == _row(act, "dtc_rev")["mtd"] and "dtc_rev" not in full["amazon_mtd"]["rows_changed"]
    assert full["amazon_mtd"]["actuals_only"] is False and act["amazon_mtd"]["actuals_only"] is True and act["amazon_mtd"]["rows_changed"] == []


def test_snapshot_endpoint_follows_the_same_switch(api, feed):
    feed(D(2026, 10, 30))
    full = api.get(f"{P}/snapshot?month=2026-11").get_json()
    act = api.get(f"{P}/snapshot?month=2026-11&amazon=actuals").get_json()
    f, a = _row(full, "amazon_rev"), _row(act, "amazon_rev")
    assert a["actual"] == 0 and f["actual"] > 0 and f["amazon_projected"] is True and full["amazon_mtd"]["days_projected"] == 2


def test_a_complete_feed_changes_nothing(api, feed):
    feed(D(2026, 11, 2))                                                                            # the snapshot's own cutoff: nothing missing
    full = api.get(f"{P}/month?month=2026-11").get_json()
    act = api.get(f"{P}/month?month=2026-11&amazon=actuals").get_json()
    assert full["amazon_mtd"]["days_projected"] == 0 and full["amazon_mtd"]["rows_changed"] == []
    assert [r["mtd"] for r in full["rows"]] == [r["mtd"] for r in act["rows"]]


def test_a_closed_month_is_never_completed(api, feed):
    j = api.get(f"{P}/month?month=2026-10").get_json()
    assert j["amazon_mtd"]["applies"] is False and j["amazon_mtd"]["rows_changed"] == []


# ---------------------------------------------------------------------------------------------------------------- page

def test_page_shows_the_labelled_number_the_line_and_the_actuals_toggle(browser, base_url, feed):          # noqa: F811
    feed(D(2026, 10, 30))
    ctx, page, errors = open_page(browser, base_url)
    line = page.locator(".amz-line").inner_text()
    assert line.startswith("Amazon: no days reported yet this month") and "2 days projected at $" in line and "previous month" in line
    assert "Computed live" in page.locator(".amz-meta").inner_text()
    row = page.locator("table.pacing-table[aria-label='Month pacing against the plan'] tr:has(th:has-text('Amazon revenue'))").first
    cell = row.locator("td").nth(1)
    assert "*" in cell.inner_text() and "Actuals only —" in cell.inner_text()
    page.locator(".amz-check input").check()
    page.wait_for_function("document.querySelector('.amz-meta') && document.querySelector('.amz-meta').innerText.includes('reported days only')")
    row = page.locator("table.pacing-table[aria-label='Month pacing against the plan'] tr:has(th:has-text('Amazon revenue'))").first
    assert "Actuals only" not in row.locator("td").nth(1).inner_text()
    assert errors == []
    ctx.close()


def test_page_has_no_amazon_line_when_nothing_is_projected(browser, base_url, feed):                      # noqa: F811
    feed(D(2026, 11, 2))
    ctx, page, errors = open_page(browser, base_url)
    assert page.locator(".amz-box").count() == 0
    ctx.close()


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_amazon_line_at_375px_has_no_sideways_scroll_and_readable_text(browser, base_url, feed, scheme):  # noqa: F811
    feed(D(2026, 10, 30))
    ctx, page, errors = open_page(browser, base_url, size=(375, 800), scheme=scheme)
    assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
    sizes = page.evaluate("[...document.querySelectorAll('.amz-line, .amz-meta, .amz-check, .amz-ctx')].map(e => parseFloat(getComputedStyle(e).fontSize))")
    assert sizes and min(sizes) >= 12
    assert page.evaluate("document.querySelector('.amz-line').getBoundingClientRect().width") <= 375 - 24
    assert errors == []
    ctx.close()
