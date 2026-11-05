"""The pacing refresh hook for the native feed nc_hourly (docs/projection/MODULE_feeds.md): sheet or default shadow = no pull and the result is exactly what it
was before; direct = pulled into proj_feed_nc_hourly inside the module's transaction; a failed pull is a warning, never a failed refresh and never a sheet read."""
from __future__ import annotations

import dataclasses
import datetime as dt

import etl.feeds as feeds
from etl import projection_pacing as PACING
from etl.feeds.common import FeedError
from test_feeds_module_hooks import Grids, attempts, pacing_ready, pinned_clock                     # noqa: F401  (fixtures)
from wiring_helpers import TODAY, hub_ctx, install, set_modes

D = dt.date


def nc_rows(span):
    return [(d, h, 100.0 + h, 2) for d in (span.until - dt.timedelta(days=i) for i in range(3)) for h in (8, 9)]


def install_nc(monkeypatch, fail=False):
    calls = []
    real = feeds.registry

    def reg():
        out = dict(real())

        def pull(ctx, span):
            calls.append(span)
            if fail:
                raise FeedError("nc_hourly: HTTP 500 from ExpandFi")
            return nc_rows(span)
        out["nc_hourly"] = dataclasses.replace(out["nc_hourly"], pull=pull)
        return out
    monkeypatch.setattr(feeds, "registry", reg)
    monkeypatch.setattr(feeds, "make_ctx", lambda **kw: hub_ctx(**kw))
    return calls


def test_default_shadow_does_not_pull_in_the_module_and_changes_no_result(pacing_ready, monkeypatch):
    conn, _ = pacing_ready
    calls = install_nc(monkeypatch)
    out = PACING.refresh(conn, days=14)
    assert calls == [] and "nc_hourly" not in out["feeds"] and "warnings" not in out
    assert conn.execute("SELECT count(*) AS n FROM proj_feed_nc_hourly").fetchone()["n"] == 0


def test_shadow_with_the_module_env_pulls_into_the_one_table(pacing_ready, monkeypatch):
    conn, _ = pacing_ready
    monkeypatch.setenv(feeds.MODULE_SHADOW_ENV, "1")
    calls = install_nc(monkeypatch)
    out = PACING.refresh(conn, days=14)
    assert len(calls) == 1 and out["feeds"]["nc_hourly"]["shadow"]["rows"] == 6
    assert conn.execute("SELECT count(*) AS n FROM proj_feed_nc_hourly").fetchone()["n"] == 6


def test_direct_pulls_with_the_feeds_35_day_window_and_logs_the_attempt(pacing_ready, monkeypatch):
    conn, _ = pacing_ready
    calls = install_nc(monkeypatch)
    set_modes(conn, "direct", ("nc_hourly",))
    out = PACING.refresh(conn, days=14)
    assert calls[0].until == D(2026, 11, 2) and (calls[0].until - calls[0].since).days == 34
    assert out["feeds"]["nc_hourly"]["mode"] == "direct" and out["feeds"]["nc_hourly"]["source"] == "api:expandfi" and out["feeds"]["nc_hourly"]["rows"] == 6
    assert conn.execute("SELECT max(day) AS d FROM proj_feed_nc_hourly").fetchone()["d"] == D(2026, 11, 2)
    assert [a[3] for a in attempts(conn, "nc_hourly")] == ["ok"]
    conn.rollback()


def test_a_failed_direct_pull_is_a_warning_and_never_fails_the_refresh_or_reads_a_sheet(pacing_ready, monkeypatch):
    conn, grids = pacing_ready
    install_nc(monkeypatch, fail=True)
    set_modes(conn, "direct", ("nc_hourly",))
    out = PACING.refresh(conn, days=14)
    assert any(w.startswith("nc_hourly: the direct pull failed") and "HTTP 500" in w for w in out["warnings"])
    assert "nc_hourly" not in out["feeds"] or not out["feeds"]["nc_hourly"].get("rows")
    assert [a[3] for a in attempts(conn, "nc_hourly")] == ["failed"]
    assert not any("nc" in t.lower() and "hour" in t.lower() for t in grids.reads)
    conn.rollback()
