"""Step 5 of the feeds plan: the pacing, inventory demand and inventory supply refresh hooks follow the per-feed mode (sheet | shadow | direct).

sheet   the hooks behave exactly as before (the sheet tabs are read, nothing else is called).
shadow  the same as sheet for the module; the `feeds` service fills the shadow tables (the module does it too only when FEEDS_MODULE_SHADOW=1).
direct  the module's main table is filled from the feed and the sheet is NOT read for that feed; if the pull fails the sheet is read as before and the result carries a
        warning; the freshness chip uses the feed's own stale-after limit.
Real hooks, a real database with every migration, dict-row connections as the runner hands them out, fixture feed data at the pull boundary, no network."""
from __future__ import annotations

import datetime as dt

import pytest

import etl.feeds as feeds
from app.projection import freshness as core_freshness
from app.projection.inventory_demand import constants as C
from app.projection.pacing import feedspec
from etl import projection_inventory_demand as DEMAND
from etl import projection_inventory_supply as SUPPLY
from etl import projection_pacing as PACING
from etl.feeds import common
from wiring_helpers import (DEMAND_FEEDS, PACING_FEEDS, TODAY, RecordingReader, demand_tabs, install, pacing_grids, revamp_cells,
                            seed_pacing_history, serial, set_modes, wipe)

D = dt.date


# ---------------------------------------------------------------------------------------------------------------- small helpers

class Grids:
    """Stands in for etl.projection_pacing.read_grid: serves synthetic tabs and records each read; a tab in `forbid` raises."""

    def __init__(self, grids, forbid=()):
        self.grids, self.forbid, self.reads = grids, set(forbid), []

    def __call__(self, tab, snapshot_dir=None):
        self.reads.append(tab)
        if tab in self.forbid or tab not in self.grids:
            raise AssertionError(f"the sheet tab '{tab}' was read but its feed is direct")
        return self.grids[tab]


def dump(conn, table, order, cols="*"):
    """Rows of a table without the loaded_at column, ordered."""
    rows = conn.execute(f"SELECT {cols} FROM {table} ORDER BY {order}").fetchall()
    return [{k: v for k, v in r.items() if k not in ("loaded_at", "updated_at")} for r in rows]


def pacing_tables(conn):
    return {k: dump(conn, s.table, "1, 2" if k not in ("orders", "amazon", "traffic", "bids") else "1") for k, s in feedspec.SPEC_BY_KEY.items()}


def attempts(conn, feed=None):
    q = "SELECT feed, mode, source, status, rows, note FROM proj_feed_attempts" + (" WHERE feed = %s" if feed else "") + " ORDER BY id"
    return [tuple(r.values()) for r in conn.execute(q, (feed,) if feed else ()).fetchall()]


@pytest.fixture(autouse=True)
def pinned_clock(monkeypatch):
    monkeypatch.setenv("PROJECTION_TODAY", TODAY.isoformat())
    monkeypatch.delenv(feeds.MODULE_SHADOW_ENV, raising=False)


@pytest.fixture()
def pacing_ready(dconn, monkeypatch):
    """Pacing on synthetic sheet tabs, the one-time sheet seeds in place. -> (conn, Grids)."""
    seed_pacing_history(dconn)
    g = Grids(pacing_grids())
    monkeypatch.setattr(PACING, "read_grid", g)
    return dconn, g


# ---------------------------------------------------------------------------------------------------------------- registry matches what the modules aggregate

def test_every_pacing_feed_has_a_registry_entry_that_matches_the_modules_table_and_tab():
    reg = feeds.registry()
    for key, spec in feedspec.SPEC_BY_KEY.items():
        f = reg[key]
        assert (f.main, f.cols, f.sheet_tab) == (spec.table, spec.cols, spec.tab), key
        assert f.sheet_stale_after == feedspec.STALE_AFTER_DAYS, key                     # the limit the pacing chip uses while the sheet is the source


def test_the_registry_parsers_equal_the_pacing_parsers_on_the_same_tab():
    reg, grids = feeds.registry(), pacing_grids()
    for key in PACING_FEEDS:
        grid = grids[feedspec.SPEC_BY_KEY[key].tab]
        assert reg[key].parse_sheet(grid) == [tuple(r) for r in PACING._PARSERS[key](grid)], key


def test_every_demand_feed_has_a_registry_entry_that_matches_the_location_and_tab():
    reg = feeds.registry()
    for loc, key in DEMAND.DIRECT_FEED.items():
        f = reg[key]
        assert (f.main, f.scope_where, f.sheet_tab) == ("proj_units_daily", f"location = '{loc}'", C.DAILY_TABS[loc]), key
        assert f.sheet_stale_after == C.STALE_AFTER_DAYS[loc], key                         # intl: 4 while the sheet is the source
    s = reg[DEMAND.STOCK_FEED]
    assert (s.main, s.sheet_tab, s.sheet_stale_after) == ("proj_demand_snapshot", DEMAND.STOCK_TAB, 2)
    assert reg["units_intl"].stale_after == 2 and reg["units_amazon"].stale_after == 3          # the limits that apply while the feed is the source


def test_the_amazon_units_feed_parses_the_product_tab_as_the_module_does():
    tabs = demand_tabs()
    parsed = DEMAND.parse_product_daily(tabs["Amazon - Product Daily"])
    assert feeds.registry()["units_amazon"].parse_sheet(tabs["Amazon - Product Daily"]) == [(r.location, r.product, r.day, r.units, r.source, r.is_estimate) for r in parsed.rows]


def test_module_windows_follow_the_feed_not_the_runners_generic_14_days():
    reg = feeds.registry()
    assert reg["amazon"].module_days(14) == 21 and reg["product_reach"].module_days(14) == 21 and reg["units_intl"].module_days(14) == 21
    assert reg["units_revamp"].module_days(14) == 14 and reg["orders"].module_days(30) == 30 and reg["orders"].module_days(None) == 14
    assert reg["bids"].module_days(14) == 3 and reg["bids"].module_days(60) == 3             # history of past days cannot be rebuilt: never widened
    assert reg["stock_revamp"].module_days(14) == 14                                          # the snapshot span ignores days; the snapshot history is untouched


# ---------------------------------------------------------------------------------------------------------------- hub plumbing the modules rely on

def test_mode_helpers_accept_dict_row_connections_and_a_database_without_the_switch(dconn):
    set_modes(dconn, "direct", ("orders",))
    assert feeds.mode_of(dconn, "orders") == "direct" and feeds.mode_of(dconn, "bids") == "sheet" and feeds.mode_of(dconn, "no_such_feed") == "sheet"
    assert common.all_modes(dconn) == {"orders": "direct"} and common.get_levers(dconn)["intl_ratio.bull"] > 0
    dconn.execute("ALTER TABLE proj_feed_config RENAME TO proj_feed_config_x")                # an old database that never ran migration 180
    assert feeds.mode_of(dconn, "orders") == "sheet" and common.all_modes(dconn) == {}
    assert feeds.direct_chip(dconn, {"feed": "orders", "label": "L", "stale_after_days": 2}, "orders")["stale_after_days"] == 2
    dconn.rollback()


def test_a_failed_pull_inside_a_module_transaction_leaves_the_callers_other_writes_alone(dconn, monkeypatch):
    install(monkeypatch, fail=("orders",))
    set_modes(dconn, "direct", ("orders",))
    dconn.execute("INSERT INTO proj_units_loads (location, source, rows) VALUES ('revamp', 'earlier write of the same module run', 7)")
    res = feeds.load("orders", dconn, 14, lambda: {"rows": 3, "data_through": "2026-11-02"}, module=True)
    assert res["fallback"] is True and res["rows"] == 3 and "orders:" in res["error"]
    assert dconn.execute("SELECT count(*) AS n FROM proj_units_loads").fetchone()["n"] == 1             # not rolled back by the failure
    assert [a[3] for a in attempts(dconn, "orders")] == ["failed", "fallback"]
    dconn.commit()                                                                                      # the module commits at the end: both log rows persist
    assert dconn.execute("SELECT count(*) AS n FROM proj_feed_attempts WHERE feed = 'orders'").fetchone()["n"] == 2


def test_a_successful_module_pull_is_not_committed_by_the_hub(dconn, monkeypatch):
    install(monkeypatch)
    set_modes(dconn, "direct", ("orders",))
    r = feeds.load("orders", dconn, 14, None, module=True)
    assert r["mode"] == "direct" and r["rows"] == 14 and r["first_day"] == "2026-10-20" and r["data_through"] == "2026-11-02"
    dconn.rollback()                                                                                     # the module's own transaction decides
    assert dconn.execute("SELECT count(*) AS n FROM proj_feed_orders").fetchone()["n"] == 0


def test_the_runners_time_cap_is_never_mistaken_for_a_failed_pull(dconn, monkeypatch):
    class RefreshTimeout(Exception):                                                                     # the name projection_daily raises when its time cap fires
        pass
    install(monkeypatch, boom={"orders": RefreshTimeout("timed out after 600s")})
    set_modes(dconn, "direct", ("orders",))
    with pytest.raises(RefreshTimeout):
        feeds.load("orders", dconn, 14, lambda: {"rows": 1}, module=True)
    assert dconn.execute("SELECT count(*) AS n FROM proj_feed_attempts WHERE status = 'fallback'").fetchone()["n"] == 0


AMAZON_FEED_KEYS = [k for k, f in feeds.registry().items() if f.native and k.startswith("amazon_")]


def test_the_stale_after_limit_depends_on_the_mode_and_every_feed_is_listed(dconn):
    reg = feeds.registry()
    twins = tuple(k for k, f in reg.items() if not f.native)                                             # the native feed has no sheet twin: it is always shadow
    assert len(reg) == 26 and len(twins) == 14
    for mode, limit in (("sheet", lambda f: f.sheet_stale_after), ("shadow", lambda f: f.stale_after), ("direct", lambda f: f.stale_after)):
        set_modes(dconn, mode, twins)
        listed = {e["key"]: e for e in feeds.freshness(dconn, TODAY, include_sheet=True)}
        assert set(listed) == set(reg), mode                                                              # every feed, whatever its mode
        for key in twins:
            assert listed[key]["mode"] == mode and listed[key]["stale_after_days"] == limit(reg[key]), (mode, key)
            assert listed[key]["label"].endswith(f"({mode})")
        assert listed["intl_estimate"]["mode"] == "shadow" and listed["intl_estimate"]["stale_after_days"] == 2
    got = {}
    for m in ("sheet", "direct"):
        set_modes(dconn, m, ("units_intl",))
        got[m] = {e["key"]: e for e in feeds.freshness(dconn, TODAY, include_sheet=True)}["units_intl"]["stale_after_days"]
    assert got == {"sheet": 4, "direct": 2}                                                                # intl: 4 -> 2 only while the feed is direct
    set_modes(dconn, "sheet", twins)
    assert [e["key"] for e in feeds.freshness(dconn, TODAY)] == ["intl_estimate", "nc_hourly", *AMAZON_FEED_KEYS]   # the hook list is still only the non-sheet feeds (the native ones, the Amazon data feeds last)


def test_the_freshness_command_lists_every_feed_in_every_mode(wired_db, dconn, monkeypatch, capsys):
    from etl.feeds import __main__ as cli
    monkeypatch.setenv("DATABASE_URL", wired_db)
    set_modes(dconn, {"orders": "direct", "bids": "shadow"}, ("orders", "bids"))
    assert cli.main(["freshness"]) == 0
    out = capsys.readouterr().out
    for key in feeds.registry():
        assert key in out, key
    line = {ln.split()[0]: ln for ln in out.splitlines()}
    assert "direct" in line["feeds.orders"] and "stale after 2" in line["feeds.orders"] and "shadow" in line["feeds.bids"] and "sheet" in line["feeds.amazon"]
    assert "stale after 4" in line["feeds.units_intl"]


# ---------------------------------------------------------------------------------------------------------------- pacing

def run_pacing(conn, mode=None, days=14):
    """One refresh from an empty starting point (the one-time sheet seeds in place) in the given mode (None = no mode row at all, as before migration 180)."""
    wipe(conn)
    seed_pacing_history(conn)
    if mode:
        set_modes(conn, mode, PACING_FEEDS)
    out = PACING.refresh(conn, days=days)
    return out, pacing_tables(conn)


def test_pacing_sheet_and_shadow_modes_behave_exactly_as_before(pacing_ready, monkeypatch):
    conn, grids = pacing_ready
    base, base_tables = run_pacing(conn)
    assert sorted(grids.reads) == sorted(s.tab for s in feedspec.FEED_SPECS) and "warnings" not in base            # every tab read, no extra key in the result
    calls = install(monkeypatch)                                                                                    # any pull would be recorded
    for mode in ("sheet", "shadow"):
        grids.reads.clear()
        out, tables = run_pacing(conn, mode)
        assert out == base and tables == base_tables, mode
        assert sorted(grids.reads) == sorted(s.tab for s in feedspec.FEED_SPECS), mode
        assert conn.execute("SELECT count(*) AS n FROM proj_feedx_orders").fetchone()["n"] == 0                  # the feeds service owns the shadow tables
    assert calls == []


def test_pacing_in_shadow_mode_can_also_fill_the_shadow_tables_when_asked(pacing_ready, monkeypatch):
    conn, grids = pacing_ready
    install(monkeypatch)
    plain, plain_tables = run_pacing(conn, "shadow")
    monkeypatch.setenv(feeds.MODULE_SHADOW_ENV, "1")
    out, tables = run_pacing(conn, "shadow")
    assert tables == plain_tables and out["rows"] == plain["rows"]                                                 # the module's own tables: unchanged
    assert out["feeds"]["orders"]["shadow"]["rows"] == 14 and out["feeds"]["orders"]["rows"] == plain["feeds"]["orders"]["rows"]
    assert conn.execute("SELECT count(*) AS n FROM proj_feedx_orders").fetchone()["n"] == 14
    assert float(conn.execute("SELECT nc_rev FROM proj_feed_orders WHERE day = '2026-11-02'").fetchone()["nc_rev"]) == 9000.0      # main table is still the sheet's


def test_pacing_direct_fills_the_main_tables_from_the_feeds_and_never_reads_those_tabs(pacing_ready, monkeypatch):
    conn, grids = pacing_ready
    calls = install(monkeypatch)
    set_modes(conn, "direct", PACING_FEEDS)
    grids.forbid = {s.tab for s in feedspec.FEED_SPECS}
    out = PACING.refresh(conn, days=14)
    assert grids.reads == [] and "warnings" not in out
    assert {k for k, _ in calls} == set(PACING_FEEDS)
    assert out["feeds"]["orders"]["mode"] == "direct" and out["feeds"]["orders"]["source"] == "api:upstack" and out["feeds"]["amazon"]["source"] == "api:expandfi"
    o = conn.execute("SELECT count(*) AS n, min(day) AS a, max(day) AS b, max(nc_rev) AS r FROM proj_feed_orders").fetchone()
    assert (o["n"], o["a"], o["b"], float(o["r"])) == (14, D(2026, 10, 20), D(2026, 11, 2), 1031.0)                # fixture rows (1000 + day of month), not the sheet's 9000
    a = conn.execute("SELECT count(*) AS n, min(day) AS a, max(day) AS b FROM proj_feed_amazon").fetchone()
    assert (a["n"], a["a"], a["b"]) == (21, D(2026, 10, 13), D(2026, 11, 2))                                          # Amazon keeps its own 21-day window
    assert conn.execute("SELECT count(*) AS n FROM proj_feed_bids").fetchone()["n"] == 3                              # bids: its own 3 days, never the 14 days of the runner
    assert conn.execute("SELECT max(day) AS d FROM proj_feed_traffic").fetchone()["d"] == TODAY
    assert out["data_through"] == "2026-11-02"                                                                         # the oldest feed's last day
    runs = {r["feed"]: r["source"] for r in conn.execute("SELECT feed, source FROM proj_feed_runs").fetchall()}
    assert runs["orders"] == "api:upstack" and runs["bids"] == "api:meta" and runs["nc_product"] == "api:expandfi"
    assert {a[0] for a in attempts(conn) if a[3] == "ok"} == set(PACING_FEEDS)


def test_pacing_direct_keeps_older_rows_outside_the_window(pacing_ready, monkeypatch):
    conn, grids = pacing_ready
    PACING.refresh(conn, days=20)                                                                                       # the sheet history: Oct 14 .. Nov 2
    conn.commit()
    install(monkeypatch)
    set_modes(conn, "direct", ("orders",))
    PACING.refresh(conn, days=14)
    rows = {r["day"]: float(r["nc_rev"]) for r in conn.execute("SELECT day, nc_rev FROM proj_feed_orders").fetchall()}
    assert rows[D(2026, 10, 15)] == 9000.0 and rows[D(2026, 11, 2)] == 1002.0 and len(rows) == 20                      # Oct 14-19 untouched, Oct 20 on replaced
    conn.rollback()


def test_pacing_direct_failure_falls_back_to_that_feeds_tab_only_and_says_so(pacing_ready, monkeypatch):
    conn, grids = pacing_ready
    install(monkeypatch, fail=("orders",), error="HTTP 500 from Upstack")
    set_modes(conn, "direct", PACING_FEEDS)
    grids.forbid = {s.tab for s in feedspec.FEED_SPECS if s.key != "orders"}
    out = PACING.refresh(conn, days=14)
    assert grids.reads == ["Orders - History"]                                                                         # only the failed feed's tab
    assert out["feeds"]["orders"]["fallback"] is True and out["feeds"]["orders"]["data_through"] == "2026-11-02"
    assert len(out["warnings"]) == 1 and out["warnings"][0].startswith("orders: the direct pull failed, the sheet was read instead") and "HTTP 500" in out["warnings"][0]
    assert float(conn.execute("SELECT nc_rev FROM proj_feed_orders WHERE day = '2026-11-02'").fetchone()["nc_rev"]) == 9000.0          # the sheet's value
    assert float(conn.execute("SELECT revenue FROM proj_feed_amazon WHERE day = '2026-11-02'").fetchone()["revenue"]) == 302.0         # the feed's
    assert [a[3] for a in attempts(conn, "orders")] == ["failed", "fallback"]
    assert conn.execute("SELECT source FROM proj_feed_runs WHERE feed = 'orders'").fetchone()["source"] == "sheet:Orders - History"


def test_pacing_direct_with_the_real_upstack_pull_through_the_injected_context(pacing_ready):
    """No fixture pull here: the real orders pull runs against a fake HTTP session handed to the hook (the way the tests of the other feeds do)."""
    from feeds_helpers import upstack_session
    from wiring_helpers import ctx_for
    conn, grids = pacing_ready
    prim = [{"dt": f"2026-11-0{d}T00:00:00", "orders.new_customer_total_revenue": 111.0 * d, "orders.returning_customer_total_revenue": 50, "orders.new_customers": 3,
             "orders.total_revenue": 111.0 * d + 50, "orders.count": 5} for d in (1, 2)]
    set_modes(conn, "direct", ("orders",))
    grids.forbid = {"Orders - History"}
    out = PACING.refresh(conn, days=14, hub_ctx=ctx_for(upstack_session(query=prim, channel=[], attribution=lambda b: [])))
    assert out["feeds"]["orders"]["source"] == "api:upstack" and out["feeds"]["orders"]["rows"] == 2 and "Orders - History" not in grids.reads
    assert [(r["day"], float(r["nc_rev"])) for r in conn.execute("SELECT day, nc_rev FROM proj_feed_orders ORDER BY day").fetchall()] == [(D(2026, 11, 1), 111.0), (D(2026, 11, 2), 222.0)]


def test_pacing_direct_without_the_credentials_on_the_service_falls_back_to_the_sheet_and_names_the_missing_variable(pacing_ready):
    """The realistic first mistake: a feed flipped to direct before its variables were set on the etl service."""
    from wiring_helpers import ctx_for
    conn, grids = pacing_ready
    set_modes(conn, "direct", ("orders", "platform"))
    out = PACING.refresh(conn, days=14, hub_ctx=ctx_for(None, env={}))
    assert out["feeds"]["orders"]["fallback"] is True and out["feeds"]["platform"]["fallback"] is True
    assert "missing env var UPSTACK_API_KEY" in out["warnings"][0] and len(out["warnings"]) == 2
    assert {"Orders - History", "Platform - History"} <= set(grids.reads)
    assert float(conn.execute("SELECT nc_rev FROM proj_feed_orders WHERE day = '2026-11-02'").fetchone()["nc_rev"]) == 9000.0


def test_pacing_mixes_modes_per_feed(pacing_ready, monkeypatch):
    conn, grids = pacing_ready
    install(monkeypatch)
    set_modes(conn, {"amazon": "direct", "nc_product": "direct"}, PACING_FEEDS)                                        # the two ExpandFi feeds
    grids.forbid = {"Amazon - Daily", "NC Rev - By Product"}
    out = PACING.refresh(conn, days=14)
    assert sorted(grids.reads) == sorted(s.tab for s in feedspec.FEED_SPECS if s.key not in ("amazon", "nc_product"))
    assert out["feeds"]["amazon"]["mode"] == "direct" and out["feeds"]["nc_product"]["mode"] == "direct" and "mode" not in out["feeds"]["orders"]
    assert float(conn.execute("SELECT nc_rev FROM proj_feed_nc_product WHERE day = '2026-11-02' AND product = 'Beef Liver'").fetchone()["nc_rev"]) == 400.0


def test_a_backfill_and_a_snapshot_always_read_the_sheet(pacing_ready, monkeypatch):
    conn, grids = pacing_ready
    calls = install(monkeypatch)
    set_modes(conn, "direct", PACING_FEEDS)
    grids.grids["Meta Campaign - History"] = [["h"], [serial(D(2026, 10, 1)), "x", "c1", "Beef Liver Scaling", None, None, 1, 1, 1, 1, 1]]
    grids.grids["Meta Spend - By Product"] = [["h"], [serial(D(2026, 10, 1)), "Beef Liver", 1, None, 1]]
    PACING.refresh(conn, days=14, backfill=True)
    assert calls == [] and set(grids.reads) >= {s.tab for s in feedspec.FEED_SPECS}
    conn.rollback()
    grids.reads.clear()
    PACING.refresh(conn, days=14, snapshot_dir="/nonexistent/snapshot")                                                # a snapshot is the sheet: the hub is bypassed
    assert calls == [] and len(grids.reads) == len(feedspec.FEED_SPECS)
    conn.rollback()


def test_pacing_chips_by_mode(pacing_ready, monkeypatch):
    conn, _ = pacing_ready
    PACING.refresh(conn, days=14)
    conn.commit()
    chips = {e["feed"]: e for e in PACING.freshness(conn)}
    assert all(chips[k]["stale_after_days"] == 2 and "(direct)" not in chips[k]["label"] for k in PACING_FEEDS)
    set_modes(conn, {"amazon": "direct", "nc_product": "direct", "bids": "shadow"}, PACING_FEEDS)
    chips = {e["feed"]: e for e in PACING.freshness(conn)}
    assert chips["amazon"]["stale_after_days"] == 3 and chips["amazon"]["label"].endswith("(direct)") and chips["nc_product"]["stale_after_days"] == 2
    assert chips["orders"]["stale_after_days"] == 2 and chips["bids"]["stale_after_days"] == 2 and "(direct)" not in chips["bids"]["label"]      # shadow: unchanged
    assert chips["hourly_curve"]["stale_after_days"] == 45 and chips["meta_ad_daily"]["stale_after_days"] == 2
    assert {e["feed"] for e in PACING.freshness(conn)} == {s.key for s in feedspec.FEED_SPECS} | {"meta_ad_daily", "hourly_curve"}


# ---------------------------------------------------------------------------------------------------------------- inventory demand

def demand_state(conn):
    return {"daily": dump(conn, "proj_units_daily", "1, 2, 3"), "stock": dump(conn, "proj_demand_snapshot", "1, 2, 3")}


def run_demand(conn, mode=None):
    wipe(conn)
    if mode:
        set_modes(conn, mode, DEMAND_FEEDS)
    reader = RecordingReader(demand_tabs())
    return DEMAND.refresh(conn, days=14, reader=reader), demand_state(conn), reader.reads


def test_demand_sheet_and_shadow_modes_behave_exactly_as_before(dconn, monkeypatch):
    base, base_state, base_reads = run_demand(dconn)
    calls = install(monkeypatch)
    for mode in ("sheet", "shadow"):
        out, state, reads = run_demand(dconn, mode)
        assert out == base and state == base_state and reads == base_reads, mode
    assert calls == [] and dconn.execute("SELECT count(*) AS n FROM proj_feedx_units_daily").fetchone()["n"] == 0


def test_demand_direct_fills_every_table_from_the_feeds_and_never_reads_the_sheet(dconn, monkeypatch):
    calls = install(monkeypatch)
    set_modes(dconn, "direct", DEMAND_FEEDS)
    reader = RecordingReader(forbid=True)
    out = DEMAND.refresh(dconn, days=14, reader=reader)
    assert reader.reads == [] and out["warnings"] == []
    assert {k for k, _ in calls} == set(DEMAND_FEEDS)
    got = {r["location"]: r for r in dconn.execute("SELECT location, count(*) AS n, min(day) AS a, max(day) AS b, bool_or(is_estimate) AS est, min(source) AS src "
                                                   "FROM proj_units_daily GROUP BY location").fetchall()}
    assert (got["revamp"]["n"], got["revamp"]["a"], got["revamp"]["b"], got["revamp"]["src"]) == (4 * 14, D(2026, 10, 20), D(2026, 11, 2), "warehance")
    assert (got["amazon"]["n"], got["amazon"]["a"], got["amazon"]["src"]) == (4 * 21, D(2026, 10, 13), "expandfi-product-daily")
    assert (got["intl"]["n"], got["intl"]["a"], got["intl"]["est"]) == (4 * 21, D(2026, 10, 13), True)                # the feed's own 21-day window
    stock = dconn.execute("SELECT count(*) AS n, min(as_of) AS d, sum(on_hand) AS oh FROM proj_demand_snapshot").fetchone()
    assert (stock["n"], stock["d"], float(stock["oh"])) == (4, D(2026, 11, 3), 1000.0)
    assert out["stock"]["mode"] == "direct" and out["stock"]["as_of"] == "2026-11-03" and out["locations"]["revamp"]["mode"] == "direct"
    assert out["rows"] == 4 * (14 + 21 + 21) and out["data_through"] == "2026-11-02"
    loads = {r["location"]: r for r in dconn.execute("SELECT DISTINCT ON (location) location, source, rows, last_day, actor, note FROM proj_units_loads ORDER BY location, id DESC").fetchall()}
    assert loads["revamp"]["source"] == "api:warehance" and loads["amazon"]["source"] == "api:expandfi" and loads["intl"]["note"] == "direct feed"
    assert dconn.execute("SELECT count(*) AS n FROM proj_audit WHERE action = 'inventory_demand.refresh'").fetchone()["n"] == 1
    dconn.commit()


def test_demand_direct_never_replaces_a_real_international_count_with_an_estimate(dconn, monkeypatch):
    install(monkeypatch)
    set_modes(dconn, "direct", ("units_intl",))
    dconn.execute("INSERT INTO proj_units_daily (location, product, day, units, source, is_estimate) VALUES ('intl', 'bull', %s, 777, 'upload', false)", (D(2026, 10, 20),))
    DEMAND.refresh(dconn, days=14, reader=RecordingReader(demand_tabs()))
    real = dconn.execute("SELECT units, source, is_estimate FROM proj_units_daily WHERE location = 'intl' AND product = 'bull' AND day = %s", (D(2026, 10, 20),)).fetchone()
    assert real == {"units": 777, "source": "upload", "is_estimate": False}
    assert dconn.execute("SELECT count(*) AS n FROM proj_units_daily WHERE location = 'intl' AND is_estimate").fetchone()["n"] == 4 * 21 - 1


def test_demand_direct_failure_reads_only_that_feeds_tab_and_the_modules_other_writes_survive(dconn, monkeypatch):
    install(monkeypatch, fail=("units_amazon",), error="expandfi warehouse: cannot connect")
    set_modes(dconn, "direct", DEMAND_FEEDS)
    reader = RecordingReader({"Amazon - Product Daily": demand_tabs()["Amazon - Product Daily"]})
    out = DEMAND.refresh(dconn, days=14, reader=reader)
    assert reader.reads == ["Amazon - Product Daily"]
    assert out["locations"]["amazon"]["fallback"] is True and out["locations"]["amazon"]["source"] == "Amazon - Product Daily"
    assert out["locations"]["revamp"]["mode"] == "direct" and "fallback" not in out["locations"]["revamp"]
    assert len(out["warnings"]) == 1 and out["warnings"][0].startswith("units_amazon: the direct pull failed, the sheet was read instead")
    srcs = {r["location"]: r["source"] for r in dconn.execute("SELECT DISTINCT ON (location) location, source FROM proj_units_daily ORDER BY location, day").fetchall()}
    assert srcs == {"revamp": "warehance", "amazon": "expandfi-product-daily", "intl": "estimate"}
    assert dconn.execute("SELECT count(*) AS n FROM proj_units_daily WHERE location = 'revamp'").fetchone()["n"] == 4 * 14                  # written before the failure, kept
    assert [a[3] for a in attempts(dconn, "units_amazon")] == ["failed", "fallback"]
    dconn.commit()


def test_a_pull_that_pulls_fine_but_cannot_be_written_is_a_failed_pull_too_and_the_transaction_stays_usable(dconn, monkeypatch):
    """The database refuses the rows (a negative unit count violates the table's CHECK): the savepoint undoes only that feed's write."""
    bad = lambda span: [("amazon", "bull", span.until, -5.0, "expandfi-product-daily", False)]                    # noqa: E731
    install(monkeypatch, rows={"units_amazon": bad})
    set_modes(dconn, "direct", DEMAND_FEEDS)
    reader = RecordingReader({"Amazon - Product Daily": demand_tabs()["Amazon - Product Daily"]})
    out = DEMAND.refresh(dconn, days=14, reader=reader)
    assert out["locations"]["amazon"]["fallback"] is True and reader.reads == ["Amazon - Product Daily"] and "units_amazon" in out["warnings"][0]
    assert dconn.execute("SELECT count(*) AS n FROM proj_units_daily WHERE location = 'revamp'").fetchone()["n"] == 4 * 14
    assert dconn.execute("SELECT count(*) AS n FROM proj_units_daily WHERE location = 'amazon' AND units < 0").fetchone()["n"] == 0
    assert [a[3] for a in attempts(dconn, "units_amazon")] == ["failed", "fallback"]
    dconn.commit()


def test_demand_stock_failure_falls_back_to_the_sheet_snapshot(dconn, monkeypatch):
    install(monkeypatch, fail=("stock_revamp",))
    set_modes(dconn, "direct", ("stock_revamp",))
    reader = RecordingReader(demand_tabs())
    out = DEMAND.refresh(dconn, days=14, reader=reader)
    assert out["stock"]["fallback"] is True and out["stock"]["as_of"] == "2026-11-03" and "Warehance Fcst Inputs" in reader.reads
    assert [w.split(":")[0] for w in out["warnings"]] == ["stock_revamp"]


def test_demand_chips_by_mode_and_international_goes_from_4_to_2_days_only_while_direct(dconn, monkeypatch):
    DEMAND.refresh(dconn, days=14, reader=RecordingReader(demand_tabs()))
    dconn.commit()
    chips = {e["feed"]: e for e in DEMAND.freshness(dconn)}
    assert [(chips[f"inventory_demand.{l}"]["stale_after_days"], chips[f"inventory_demand.{l}"]["cadence"]) for l in C.LOCATIONS] == \
        [(2, "daily"), (2, "daily"), (4, "monthly-upload")]
    for mode in ("shadow",):
        set_modes(dconn, mode, DEMAND_FEEDS)
        again = {e["feed"]: e for e in DEMAND.freshness(dconn)}
        assert again == chips
    set_modes(dconn, "direct", ("units_intl", "units_amazon"))
    chips = {e["feed"]: e for e in DEMAND.freshness(dconn)}
    assert chips["inventory_demand.intl"]["stale_after_days"] == 2 and chips["inventory_demand.intl"]["cadence"] == "daily"
    assert chips["inventory_demand.intl"]["label"].endswith("(direct)") and chips["inventory_demand.amazon"]["stale_after_days"] == 3
    assert chips["inventory_demand.revamp"]["stale_after_days"] == 2 and "(direct)" not in chips["inventory_demand.revamp"]["label"]
    set_modes(dconn, "sheet", ("units_intl",))                                                                          # rolling back restores the old limit
    assert {e["feed"]: e for e in DEMAND.freshness(dconn)}["inventory_demand.intl"]["stale_after_days"] == 4


def test_a_fallback_to_a_stale_sheet_makes_the_chip_stale_instead_of_hiding_it(dconn, monkeypatch):
    """The sheet's international tab ends Oct 12: loaded by the fallback it leaves the direct chip 22 days old against a 2-day limit."""
    install(monkeypatch, fail=("units_intl",))
    set_modes(dconn, "direct", ("units_intl",))
    DEMAND.refresh(dconn, days=60, reader=RecordingReader(demand_tabs()))
    dconn.commit()
    chip = core_freshness.evaluate("inventory_demand", {e["feed"]: e for e in DEMAND.freshness(dconn)}["inventory_demand.intl"], TODAY)
    assert chip["status"] == "stale" and chip["data_through"] == "2026-10-12" and chip["stale_after_days"] == 2 and chip["cadence"] == "daily"
    assert "last data 2026-10-12" in chip["message"]


# ---------------------------------------------------------------------------------------------------------------- inventory supply

def stock_rows(conn):
    return dump(conn, "proj_stock_snapshot", "1, 2, 3", "location, product, as_of, on_hand, allocated, estimate")


def test_supply_sheet_and_shadow_modes_read_the_cells_exactly_as_before(dconn):
    base = SUPPLY.refresh(dconn, 14, today=TODAY, reader=revamp_cells)
    base_rows = stock_rows(dconn)
    assert base == {"rows": 4, "data_through": "2026-11-02"}
    dconn.rollback()
    for mode in ("sheet", "shadow"):
        set_modes(dconn, mode, ("stock_revamp",))
        assert SUPPLY.refresh(dconn, 14, today=TODAY, reader=revamp_cells) == base and stock_rows(dconn) == base_rows
        dconn.rollback()


def test_supply_direct_follows_the_count_the_demand_module_pulled_and_does_not_read_the_cells(dconn, monkeypatch):
    install(monkeypatch)
    set_modes(dconn, "direct", ("stock_revamp",))
    DEMAND.refresh(dconn, days=14, reader=RecordingReader(demand_tabs()))                                                # the daily run: demand first
    def forbidden():
        raise AssertionError("the Revamp cells were read but stock_revamp is direct")
    out = SUPPLY.refresh(dconn, 14, today=TODAY, reader=forbidden)
    assert out == {"rows": 4, "data_through": "2026-11-03", "mode": "direct"}
    assert {r["product"]: (r["as_of"], float(r["on_hand"]), float(r["allocated"])) for r in stock_rows(dconn)} == \
        {"bull": (D(2026, 11, 3), 100.0, 10.0), "liver": (D(2026, 11, 3), 200.0, 20.0), "boneflex": (D(2026, 11, 3), 300.0, 30.0), "belle": (D(2026, 11, 3), 400.0, 40.0)}
    assert SUPPLY.refresh(dconn, 14, today=TODAY, reader=forbidden)["rows"] == 0                                         # idempotent: same values, nothing written
    assert dconn.execute("SELECT count(*) AS n FROM proj_audit WHERE action = 'inventory_supply.stock.refresh'").fetchone()["n"] == 4
    assert {e["feed"]: e for e in SUPPLY.freshness(dconn, TODAY)}["stock_revamp"]["label"].endswith("(direct)")
    dconn.commit()


def test_supply_direct_with_no_count_to_follow_reads_the_cells_and_warns(dconn):
    set_modes(dconn, "direct", ("stock_revamp",))
    out = SUPPLY.refresh(dconn, 14, today=TODAY, reader=revamp_cells)
    assert out["rows"] == 4 and out["data_through"] == "2026-11-02" and out["warnings"][0].startswith("stock_revamp: no Revamp count")
    dconn.rollback()
