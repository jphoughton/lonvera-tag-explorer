"""End to end: the whole nightly projection refresh (`etl.projection_daily.run_all`, every module's real hook) with every feed in direct mode and the Google Sheet
made unreadable. It must complete, fill the pacing, inventory demand and inventory supply tables from the feed data, and leave the genuinely manual inputs
(QuickBooks months, cohort upload, purchase orders, FBA transfers and counts) exactly as they were. Then the two ways it can go wrong: one feed down (the sheet
fallback is the only read, the run says so and the chip goes stale loudly when the sheet is old) and every feed down.

The pulls themselves are replaced at the Feed.pull boundary by fixture rows (their HTTP / SQL layers are covered by the feed tests); the registry, the mode switch,
the module hooks, the savepoints, the attempt log, the freshness chips and the runner are the real code. No network."""
from __future__ import annotations

import datetime as dt

import psycopg
import pytest
from psycopg.rows import dict_row

from app.projection.freshness import MODULE_KEYS
from etl import projection_daily as daily
from etl import sheets_io
from wiring_helpers import (DEMAND_FEEDS, MODULE_FEEDS, PACING_FEEDS, TODAY, FakeSheets, demand_tabs, install, pacing_grids, seed_pacing_history, set_modes)

D = dt.date


@pytest.fixture(autouse=True)
def pinned_clock(monkeypatch):
    monkeypatch.setenv("PROJECTION_TODAY", TODAY.isoformat())
    monkeypatch.delenv("FEEDS_MODULE_SHADOW", raising=False)


def runner(wired_db, capsys, **kw):
    """The nightly entry point on the all-migrations database; returns (exit code, the runner's log text)."""
    connect = lambda: psycopg.connect(wired_db, row_factory=dict_row)                       # noqa: E731  (the runner's own connection factory shape)
    capsys.readouterr()
    code = daily.run_all(list(MODULE_KEYS), connect, days=14, timeout=None, today=TODAY, **kw)
    return code, capsys.readouterr().out


def seed_manual_inputs(conn) -> dict:
    """What only a person provides: a QuickBooks month, a cohort upload, a purchase order, an FBA transfer and FBA and international stock counts."""
    conn.execute("INSERT INTO proj_actuals (line_key, month, value, source) VALUES ('pnl.amazon_rev', '2026-09-01', 123456.78, 'QuickBooks upload')")
    conn.execute("INSERT INTO proj_cohorts (cohort_month, new_customers, first_order_per_customer, source) VALUES ('2026-09-01', 9000, 55.5, 'cohort upload')")
    conn.execute("INSERT INTO proj_vendor_lead (vendor, lead_weeks, moq_units, default_warehouse, note) VALUES ('KeyStone', 12, 20000, 'revamp', 'test')")
    conn.execute("INSERT INTO proj_po (product, vendor, warehouse, units, land_date, status, note) VALUES ('bull', 'KeyStone', 'revamp', 25000, '2026-12-01', 'ordered', 'typed in the app')")
    conn.execute("INSERT INTO proj_fba_transfer (product, units, ship_date, receive_date, status, note) VALUES ('liver', 4000, '2026-11-10', '2026-11-12', 'planned', 'typed in the app')")
    conn.execute("INSERT INTO proj_stock_snapshot (location, product, as_of, on_hand, allocated, note) VALUES ('amazon', 'bull', '2026-10-30', 3000, 0, 'FBA count typed in the app')")
    conn.execute("INSERT INTO proj_stock_snapshot (location, product, as_of, on_hand, allocated, estimate, note) VALUES "
                 "('intl', 'bull', '2026-10-30', 14947, 0, true, 'international 3PL count typed in the app')")
    # the app's own Meta data (etl.run_daily wrote it before projection_daily runs)
    conn.execute("INSERT INTO meta_campaigns (campaign_id, account, name, stage) VALUES ('c1', 'Main', 'Beef Liver Testing', 'testing')")
    conn.execute("INSERT INTO meta_ads (ad_id, account, ad_name, meta_family_key, campaign_id, adset_name) VALUES ('a1', 'Main', 'x', 'x', 'c1', 'Liver adset')")
    conn.execute("INSERT INTO ad_daily (ad_id, day, meta_family_key, meta_family, campaign_id, spend, purchases, conv_value, link_clicks, source, impressions) "
                 "VALUES ('a1', '2026-11-02', 'k', 'K', 'c1', 100, 2, 200, 10, 'meta_api', 1000)")
    seed_pacing_history(conn)                                                                  # the one-time sheet seeds (commits)
    conn.commit()
    return manual_state(conn)


def manual_state(conn) -> dict:
    q = lambda sql: [dict(r) for r in conn.execute(sql).fetchall()]                              # noqa: E731
    return {"actuals": q("SELECT line_key, month, value, source FROM proj_actuals WHERE line_key = 'pnl.amazon_rev'"),
            "cohorts": q("SELECT cohort_month, new_customers, source FROM proj_cohorts"),
            "po": q("SELECT product, vendor, units, land_date, status FROM proj_po"),
            "fba_transfer": q("SELECT product, units, ship_date, status FROM proj_fba_transfer"),
            "counts": q("SELECT location, product, as_of, on_hand, note FROM proj_stock_snapshot WHERE location <> 'revamp' ORDER BY 1, 2, 3")}


def runs(conn) -> dict:
    return {r["module"]: r["status"] for r in conn.execute("SELECT DISTINCT ON (module) module, status FROM proj_refresh_runs ORDER BY module, id DESC").fetchall()}


def counts(conn) -> dict:
    t = ("proj_feed_orders", "proj_feed_amazon", "proj_feed_platform", "proj_feed_attribution", "proj_feed_traffic", "proj_feed_meta_reach", "proj_feed_product_reach",
         "proj_feed_nc_product", "proj_feed_anyclick", "proj_feed_bids", "proj_units_daily", "proj_demand_snapshot", "proj_stock_snapshot", "proj_feed_meta_day")
    return {x: conn.execute(f"SELECT count(*) AS n FROM {x}").fetchone()["n"] for x in t}


# ---------------------------------------------------------------------------------------------------------------- everything direct, no sheet

def test_the_nightly_projection_refresh_completes_with_every_feed_direct_and_the_sheet_unreadable(wired_db, dconn, monkeypatch, capsys):
    sheet = FakeSheets(forbid=True)
    monkeypatch.setattr(sheets_io, "_service", sheet)                                          # every live sheet read of every module goes through this
    calls = install(monkeypatch)
    manual = seed_manual_inputs(dconn)
    set_modes(dconn, "direct")
    code, log = runner(wired_db, capsys)
    assert code == 0, log
    assert sheet.reads == [], sheet.reads                                                       # the Google Sheet was not read at all
    assert {k for k, _ in calls} == set(MODULE_FEEDS) and len(calls) == len(MODULE_FEEDS)      # each feed pulled once, by the one module that consumes it
    st = runs(dconn)
    assert [st[m] for m in ("inventory_demand", "inventory_supply", "pacing", "drivers")] == ["ok"] * 4, (st, log)
    assert "FAILED" not in log and "warning" not in log and "all modules refreshed and fresh" in log, log

    # pacing: all ten feed tables filled from the feeds, Meta from the app's own ad_daily
    n = counts(dconn)
    assert n["proj_feed_orders"] == 14 and n["proj_feed_amazon"] == 21 and n["proj_feed_bids"] == 3 and n["proj_feed_traffic"] == 14
    assert n["proj_feed_platform"] == 14 and n["proj_feed_attribution"] == 14 and n["proj_feed_anyclick"] == 28 and n["proj_feed_nc_product"] == 14 * 4
    assert n["proj_feed_meta_reach"] == 14 and n["proj_feed_product_reach"] == 21 * 3          # product reach keeps its own 21-day window
    src = {r["feed"]: r["source"] for r in dconn.execute("SELECT feed, source FROM proj_feed_runs").fetchall()}
    assert {src[k] for k in ("orders", "platform", "attribution", "traffic", "anyclick")} == {"api:upstack"}
    assert {src[k] for k in ("meta_reach", "product_reach", "bids")} == {"api:meta"} and {src["amazon"], src["nc_product"]} == {"api:expandfi"}
    assert src["meta_ad_daily"] == "ad_daily"
    assert dconn.execute("SELECT count(*) AS n FROM proj_feed_meta_day WHERE source = 'app' AND day = '2026-11-02'").fetchone()["n"] == 1

    # inventory demand: the three daily feeds and the US stock snapshot
    by = {r["location"]: (r["n"], r["src"]) for r in dconn.execute("SELECT location, count(*) AS n, min(source) AS src FROM proj_units_daily GROUP BY location").fetchall()}
    assert by == {"revamp": (4 * 14, "warehance"), "amazon": (4 * 21, "expandfi-product-daily"), "intl": (4 * 21, "estimate")}
    snap = dconn.execute("SELECT count(*) AS n, min(as_of) AS d, min(source) AS s FROM proj_demand_snapshot").fetchone()
    assert (snap["n"], snap["d"], snap["s"]) == (4, D(2026, 11, 3), "warehance")

    # inventory supply: the Revamp count followed the demand snapshot; nothing else of the supply tables moved
    rev = {r["product"]: float(r["on_hand"]) for r in dconn.execute("SELECT product, on_hand FROM proj_stock_snapshot WHERE location = 'revamp'").fetchall()}
    assert rev == {"bull": 100.0, "liver": 200.0, "boneflex": 300.0, "belle": 400.0}

    # the manual inputs still come from their own paths: untouched
    assert manual_state(dconn) == manual

    # every module-consumed feed logged one ok direct attempt; none fell back
    ok = {r["feed"] for r in dconn.execute("SELECT feed FROM proj_feed_attempts WHERE mode = 'direct' AND status = 'ok'").fetchall()}
    assert ok == set(MODULE_FEEDS) and dconn.execute("SELECT count(*) AS n FROM proj_feed_attempts WHERE status IN ('failed', 'fallback')").fetchone()["n"] == 0

    # a second run changes nothing and still completes (idempotent windows)
    before = counts(dconn)
    code2, log2 = runner(wired_db, capsys)
    assert code2 == 0 and counts(dconn) == before and sheet.reads == [], log2


def test_the_chips_say_which_feeds_are_direct_and_the_manual_ones_are_reported_never_fatal(wired_db, dconn, monkeypatch, capsys):
    from app.projection import freshness as core_freshness
    monkeypatch.setattr(sheets_io, "_service", FakeSheets(forbid=True))
    install(monkeypatch)
    seed_manual_inputs(dconn)
    set_modes(dconn, "direct")
    code, log = runner(wired_db, capsys)
    assert code == 0, log
    chips = {(c["module"], c["feed"]): c for c in core_freshness.report(dconn, TODAY)["feeds"]}
    for key in PACING_FEEDS:
        c = chips[("pacing", key)]
        assert c["cadence"] == "daily" and c["label"].endswith("(direct)") and c["status"] == "fresh", key
    assert chips[("pacing", "amazon")]["stale_after_days"] == 3 and chips[("pacing", "orders")]["stale_after_days"] == 2
    assert [chips[("inventory_demand", f"inventory_demand.{l}")]["stale_after_days"] for l in ("revamp", "amazon", "intl")] == [2, 3, 2]
    assert chips[("inventory_demand", "inventory_demand.intl")]["cadence"] == "daily"
    assert chips[("inventory_supply", "stock_revamp")]["label"].endswith("(direct)") and chips[("inventory_supply", "stock_revamp")]["status"] == "fresh"
    for module, feed in (("cohorts", "cohort_export"), ("pnl", "quickbooks_pnl"), ("inventory_supply", "stock_fba"), ("inventory_supply", "stock_intl"),
                         ("inventory_supply", "purchase_orders")):                                       # the manual inputs: listed, in their own cadence, never failing the run
        assert chips[(module, feed)]["cadence"] == "monthly-upload", (module, feed)


# ---------------------------------------------------------------------------------------------------------------- one feed down

def test_one_feed_down_reads_only_that_feeds_tab_runs_ok_with_a_warning_and_goes_stale_loudly_when_the_sheet_is_old(wired_db, dconn, monkeypatch, capsys):
    """The international feed is down. Its fallback is the sheet's own tab, which ends Oct 12: the run completes (nothing else is hurt) but the international chip,
    now held to the direct feed's 2-day limit, is stale, so the run exits 1 instead of looking healthy."""
    sheet = FakeSheets({"Intl Daily Units": demand_tabs()["Intl Daily Units"]})
    monkeypatch.setattr(sheets_io, "_service", sheet)
    install(monkeypatch, fail=("units_intl",), error="HTTP 502 from Warehance")
    seed_manual_inputs(dconn)
    set_modes(dconn, "direct")
    code, log = runner(wired_db, capsys)
    assert sheet.reads == ["Intl Daily Units"], sheet.reads                                                                 # the only sheet read of the whole run
    st = runs(dconn)
    assert [st[m] for m in ("inventory_demand", "inventory_supply", "pacing")] == ["ok"] * 3
    assert "inventory_demand: ok" in log and "warning inventory_demand: units_intl: the direct pull failed, the sheet was read instead" in log and "HTTP 502" in log
    assert code == 1 and "inventory_demand.intl" in log and "stale after 2 days" in log and "last data 2026-10-12" in log
    assert dconn.execute("SELECT max(day) AS d FROM proj_units_daily WHERE location = 'revamp'").fetchone()["d"] == D(2026, 11, 2)           # the rest came from the feeds
    assert [r["status"] for r in dconn.execute("SELECT status FROM proj_feed_attempts WHERE feed = 'units_intl' ORDER BY id").fetchall()] == ["failed", "fallback"]


def test_one_feed_down_with_a_fresh_sheet_keeps_the_run_green_and_reports_the_fallback(wired_db, dconn, monkeypatch, capsys):
    tabs = {**pacing_grids(), **demand_tabs()}
    sheet = FakeSheets(tabs)
    monkeypatch.setattr(sheets_io, "_service", sheet)
    install(monkeypatch, fail=("orders",), error="HTTP 500 from Upstack")
    seed_manual_inputs(dconn)
    set_modes(dconn, "direct")
    code, log = runner(wired_db, capsys)
    assert code == 0, log
    assert sheet.reads == ["Orders - History"]
    assert "pacing: ok" in log and "warning pacing: orders: the direct pull failed, the sheet was read instead" in log
    assert dconn.execute("SELECT source FROM proj_feed_runs WHERE feed = 'orders'").fetchone()["source"] == "sheet:Orders - History"


# ---------------------------------------------------------------------------------------------------------------- every feed down

def test_every_feed_down_falls_back_to_the_sheet_everywhere_and_the_job_still_completes(wired_db, dconn, monkeypatch, capsys):
    tabs = {**pacing_grids(), **demand_tabs(), "Warehance Fcst Inputs": demand_tabs()["Warehance Fcst Inputs"]}
    sheet = FakeSheets(tabs)
    monkeypatch.setattr(sheets_io, "_service", sheet)
    install(monkeypatch, fail=MODULE_FEEDS, error="network unreachable")
    seed_manual_inputs(dconn)
    set_modes(dconn, "direct")
    code, log = runner(wired_db, capsys)
    st = runs(dconn)
    assert [st[m] for m in ("inventory_demand", "inventory_supply", "pacing")] == ["ok"] * 3, log
    assert {t.strip() for t in sheet.reads} >= {"Orders - History", "Amazon - Daily", "Warehance Daily", "Amazon - Product Daily", "Intl Daily Units"}
    for key in MODULE_FEEDS:
        assert f"warning {'inventory_demand' if key in DEMAND_FEEDS else 'pacing'}: {key}: the direct pull failed" in log, key
    assert dconn.execute("SELECT count(*) AS n FROM proj_feed_attempts WHERE status = 'fallback'").fetchone()["n"] == len(MODULE_FEEDS)
    assert dconn.execute("SELECT count(*) AS n FROM proj_feed_orders").fetchone()["n"] == 20                       # the sheet's rows, loaded in full into the empty table
    assert code in (0, 1)                                                                                         # exit code is the freshness verdict of the sheet's own data, asserted above per module
