"""Common plumbing: scrubbing, HTTP retry/backoff/limits, trailing-window idempotence, mode switch, levers, attempt log."""
from __future__ import annotations

import datetime as dt

import pytest
import requests

from etl.feeds import common
from etl.feeds.common import AuthError, Ctx, FeedError, Http, RateLimiter, Span, make_ctx, replace_window, scrub, trailing_span
from feeds_helpers import FakeResponse, FakeSession

D = dt.date


def ctx_with(session, sleeps=None, **env):
    sl = sleeps if sleeps is not None else []
    return make_ctx(session=session, sleep=sl.append, env=env, today=D(2026, 11, 3)), sl


def test_scrub_masks_env_values_urls_and_tokens():
    env = {"UPSTACK_API_KEY": "k" * 30, "PLAIN": "visible-value-1234", "META_ACCESS_TOKEN": "EA" + "x" * 50}
    s = scrub(f"boom {'k' * 30} postgresql://user:hunter2pw@db.example.com:5432/x Bearer abc.def-ghi access_token=zzz123 EA{'q' * 45}", env=env)
    assert "k" * 30 not in s and "hunter2pw" not in s and "abc.def-ghi" not in s and "zzz123" not in s and "q" * 45 not in s
    assert scrub("visible-value-1234", env=env) == "visible-value-1234"            # non-secret env values are left alone
    assert len(scrub("a" * 1000)) == common.NOTE_LIMIT


def test_http_retries_then_succeeds_and_backs_off():
    s = FakeSession().on(lambda c: True, [FakeResponse(503), FakeResponse(429, headers={"Retry-After": "7"}), FakeResponse(200, {"ok": 1})])
    ctx, sleeps = ctx_with(s)
    assert Http(ctx, "t", retries=5).get("https://x/y") == {"ok": 1}
    assert sleeps == [2.0, 7.0]                                                      # exponential start, then Retry-After
    assert len(s.calls) == 3


def test_http_gives_up_with_a_scrubbed_message_and_never_leaks_headers():
    s = FakeSession().on(lambda c: True, FakeResponse(500))
    ctx, sleeps = ctx_with(s)
    with pytest.raises(FeedError) as e:
        Http(ctx, "t", retries=2).get("https://x/y", headers={"x-api-key": "SECRETKEYVALUE123"})
    assert "SECRETKEYVALUE123" not in str(e.value) and "gave up after 2 retries" in str(e.value)
    assert len(s.calls) == 3 and len(sleeps) == 2


def test_http_auth_and_client_errors_are_not_retried():
    ctx, sleeps = ctx_with(FakeSession().on(lambda c: True, FakeResponse(401)))
    with pytest.raises(AuthError):
        Http(ctx, "t").get("https://x/y")
    ctx, _ = ctx_with(FakeSession().on(lambda c: True, FakeResponse(400, text="bad measure")))
    with pytest.raises(FeedError, match="HTTP 400"):
        Http(ctx, "t").get("https://x/y")
    assert sleeps == []


def test_http_network_errors_are_retried():
    s = FakeSession().on(lambda c: True, [requests.ConnectionError("down"), FakeResponse(200, {"a": 1})])
    ctx, sleeps = ctx_with(s)
    assert Http(ctx, "t").get("https://x/y") == {"a": 1}
    assert len(sleeps) == 1


def test_rate_limiter_spaces_calls():
    t = [0.0]
    slept = []

    def sleep(x):
        slept.append(x)
        t[0] += x
    rl = RateLimiter(0.5, clock=lambda: t[0], sleep=sleep)
    rl.wait(); rl.wait(); t[0] += 0.2; rl.wait()
    assert slept == pytest.approx([0.5, 0.3])


def test_trailing_span_ends_yesterday_and_can_include_today():
    assert trailing_span(D(2026, 11, 3), 14) == Span(D(2026, 10, 20), D(2026, 11, 2))
    assert trailing_span(D(2026, 11, 3), 3, include_today=True) == Span(D(2026, 11, 1), D(2026, 11, 3))
    with pytest.raises(ValueError):
        trailing_span(D(2026, 11, 3), 0)


def test_replace_window_is_idempotent_and_leaves_older_rows(conn):
    cols = ("day", "new_visitors", "visitors", "sessions")
    conn.execute("INSERT INTO proj_feedx_traffic (day, new_visitors) VALUES ('2026-10-01', 5), ('2026-10-20', 9)")
    where = common.day_floor_where("day")
    rows = [(D(2026, 10, 20), 1, 2, 3), (D(2026, 10, 21), 4, 5, 6)]
    assert replace_window(conn, "proj_feedx_traffic", cols, rows, where, (D(2026, 10, 20),)) == 2
    assert replace_window(conn, "proj_feedx_traffic", cols, rows, where, (D(2026, 10, 20),)) == 2
    got = conn.execute("SELECT day, new_visitors FROM proj_feedx_traffic ORDER BY day").fetchall()
    assert got == [(D(2026, 10, 1), 5), (D(2026, 10, 20), 1), (D(2026, 10, 21), 4)]


def test_replace_window_conflict_nothing_keeps_a_real_row(conn):
    conn.execute("INSERT INTO proj_feedx_units_daily (location, product, day, units, source, is_estimate) VALUES ('intl', 'bull', '2026-10-01', 7, 'upload', false)")
    from psycopg import sql
    n = replace_window(conn, "proj_feedx_units_daily", ("location", "product", "day", "units", "source", "is_estimate"),
                       [("intl", "bull", D(2026, 10, 1), 99.0, "estimate", True), ("intl", "bull", D(2026, 10, 2), 5.0, "estimate", True)],
                       sql.SQL("location = %s AND day >= %s AND is_estimate"), ("intl", D(2026, 10, 1)), conflict_nothing=True)
    assert n == 2
    assert conn.execute("SELECT day, units FROM proj_feedx_units_daily ORDER BY day").fetchall() == [(D(2026, 10, 1), 7), (D(2026, 10, 2), 5)]


def test_mode_defaults_and_set(conn):
    assert common.get_mode(conn, "orders") == "sheet"
    assert common.get_mode(conn, "intl_estimate", "shadow") == "shadow"
    common.set_mode(conn, "orders", "shadow", "tester", "start of dual-run")
    assert common.get_mode(conn, "orders") == "shadow"
    assert common.all_modes(conn) == {"orders": "shadow"}
    with pytest.raises(ValueError):
        common.set_mode(conn, "orders", "bogus")
    with pytest.raises(Exception):
        conn.execute("INSERT INTO proj_feed_config (feed, mode) VALUES ('x', 'bogus')")
    conn.rollback()


def test_levers_are_seeded_and_editable(conn):
    lv = common.get_levers(conn)
    assert lv["intl_ratio.bull"] == pytest.approx(0.905) and lv["intl_ratio.default"] == pytest.approx(0.89)
    common.set_lever(conn, "intl_ratio.bull", 0.9, "refit", "tester")
    assert common.get_levers(conn)["intl_ratio.bull"] == pytest.approx(0.9)
    note = conn.execute("SELECT note FROM proj_feed_levers WHERE key = 'intl_ratio.bull'").fetchone()[0]
    assert note == "refit"


def test_attempt_log_scrubs_and_caps_note(conn):
    t0 = dt.datetime.now(dt.timezone.utc)
    common.log_attempt(conn, "orders", "shadow", "api:upstack", "failed", t0, note="bad Bearer abcdef123456789 " + "x" * 500)
    note = conn.execute("SELECT note FROM proj_feed_attempts").fetchone()[0]
    assert "abcdef123456789" not in note and len(note) <= 300


def test_require_names_the_missing_variable_only():
    ctx = make_ctx(env={})
    with pytest.raises(FeedError, match="UPSTACK_API_KEY"):
        ctx.require("UPSTACK_API_KEY")
