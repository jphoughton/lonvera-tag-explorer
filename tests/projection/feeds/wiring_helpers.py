"""Shared doubles for the module hook-up tests (step 5): fixture feed data at the Feed.pull boundary, synthetic sheet tabs for the three projection modules,
and a fake Google Sheets service that serves chosen tabs or refuses every read. No network; the HTTP / SQL layers of the pulls themselves are covered by the
feed tests (test_feeds_upstack, _warehance, _meta, _expandfi_feeds)."""
from __future__ import annotations

import dataclasses
import datetime as dt
import sys
from pathlib import Path
from typing import Callable

import etl.feeds as feeds
from etl.feeds import common
from etl.feeds.common import FeedError, Span, make_ctx

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tests" / "projection" / "inventory_demand"))          # idemand_helpers: sheet tab builders of the demand tests

D = dt.date
TODAY = D(2026, 11, 3)                                                              # Pacific day of every run in these tests
NOW_UTC = dt.datetime(2026, 11, 3, 20, tzinfo=dt.timezone.utc)
EPOCH = D(1899, 12, 30)

# the module-consumed feeds, by module
PACING_FEEDS = ("orders", "amazon", "platform", "attribution", "traffic", "meta_reach", "product_reach", "nc_product", "anyclick", "bids")
DEMAND_FEEDS = ("units_revamp", "units_amazon", "units_intl", "stock_revamp")
MODULE_FEEDS = PACING_FEEDS + DEMAND_FEEDS


def serial(d: dt.date) -> int:
    return (d - EPOCH).days


def span_days(span: Span):
    d = span.since
    while d <= span.until:
        yield d
        d += dt.timedelta(days=1)


def wipe(conn) -> None:
    """Empty every feed and module table the hooks write (the mode switch too), so one test can run the same refresh from the same starting point twice."""
    from conftest import WIRED_TRUNCATE
    present = {r["tablename"] for r in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname = current_schema()").fetchall()}
    conn.execute("TRUNCATE " + ", ".join(t for t in WIRED_TRUNCATE if t in present) + " CASCADE")
    conn.commit()


def ctx_for(session, env: dict | None = None):
    """An etl.feeds context for the real pulls: a fake HTTP session and the credentials of the shared test env (or `env`)."""
    default = {"UPSTACK_API_KEY": "key-" + "a" * 40, "UPSTACK_PIXEL_ID": "pixel-" + "b" * 30, "WAREHANCE_API_KEY": "wh-" + "c" * 40}
    return hub_ctx(session=session, env=default if env is None else env)


def hub_ctx(**kw):
    return make_ctx(today=TODAY, now_utc=NOW_UTC, sleep=lambda s: None, env=kw.pop("env", {}), **kw)


# ---------------------------------------------------------------------------------------------------------------- fixture feed data (rows in `feed.cols` order)

def _orders(span):
    return [(d, 1000.0 + d.day, 500.0, 10.0, 1500.0 + d.day, 20.0, 50.0, 5.0) for d in span_days(span)]


def _amazon(span):
    return [(d, 5.0, 8.0, 300.0 + d.day, 2.0, 80.0, 40.0, 120.0, 30.0, 900.0) for d in span_days(span)]


def _platform(span):
    return [(d, "Google Ads", 100.0 + d.day, 4.0, 300.0, 2.0, 150.0, 40.0, 60.0) for d in span_days(span)]


def _attribution(span):
    return [(d, "Meta Ads", 7.0, 700.0, 12.0, 1200.0) for d in span_days(span)]


def _traffic(span):
    return [(d, 400.0, 900.0, 1100.0) for d in span_days(span)]


def _anyclick(span):
    return [(d, stage, 5.0, 450.0) for d in span_days(span) for stage in ("Scaling", "Testing")]


def _meta_reach(span):
    return [("day", d.isoformat(), d, d, 20000.0, 9000.0, 300.0, 350.0, 280.0) for d in span_days(span)]


def _product_reach(span):
    return [("day", d.isoformat(), d, d, p, 100.0) for d in span_days(span) for p in ("VitaBull", "Beef Liver", "Boneflex")]


def _nc_product(span):
    return [(d, p, 400.0, 5.0) for d in span_days(span) for p in ("VitaBull", "Beef Liver", "Boneflex", "Other")]


def _bids(span):
    return [(d, 3000.0, 1000.0, 65000.0, 800.0, 750.0) for d in span_days(span)]


def _units(location: str, products: tuple, source: str, estimate: bool = False, base: float = 10.0):
    def rows(span):
        return [(location, p, d, base + i, source, estimate) for d in span_days(span) for i, p in enumerate(products)]
    return rows


def _stock(span):
    return [("revamp", p, span.until, 100.0 * (i + 1), 10.0 * (i + 1), "warehance") for i, p in enumerate(("bull", "liver", "boneflex", "belle"))]


ROWS: dict[str, Callable[[Span], list[tuple]]] = {
    "orders": _orders, "amazon": _amazon, "platform": _platform, "attribution": _attribution, "traffic": _traffic, "anyclick": _anyclick,
    "meta_reach": _meta_reach, "product_reach": _product_reach, "nc_product": _nc_product, "bids": _bids,
    "units_revamp": _units("revamp", ("bull", "liver", "boneflex", "belle"), "warehance", base=100.0),
    "units_amazon": _units("amazon", ("bull", "liver", "boneflex", "belle"), "expandfi-product-daily", base=20.0),
    "units_intl": _units("intl", ("bull", "liver", "boneflex", "flow"), "estimate", estimate=True, base=60.0),
    "stock_revamp": _stock,
}


class Calls(list):
    """Which feed pulls ran (key, span)."""


def install(monkeypatch, *, fail: tuple = (), error: str = "HTTP 500 from the source", boom: dict | None = None, rows: dict | None = None) -> Calls:
    """Every pull of every module-consumed feed returns fixture rows (a key in `fail` raises FeedError instead; `boom[key]` raises that exception object;
    `rows[key](span)` replaces the fixture rows of that feed); the run context is pinned to TODAY. Returns the list of pulls that ran."""
    calls = Calls()
    real = feeds.registry
    boom, rows = boom or {}, rows or {}

    def fake_registry():
        out = {}
        for key, f in real().items():
            def pull(ctx, span, key=key):
                calls.append((key, span))
                if key in boom:
                    raise boom[key]
                if key in fail:
                    raise FeedError(f"{key}: {error}")
                return (rows.get(key) or ROWS[key])(span) if key in ROWS else []
            out[key] = dataclasses.replace(f, pull=pull) if key in MODULE_FEEDS else f
        return out

    monkeypatch.setattr(feeds, "registry", fake_registry)
    monkeypatch.setattr(feeds, "make_ctx", lambda **kw: hub_ctx(**kw))
    return calls


def set_modes(conn, modes: dict[str, str] | str, keys=MODULE_FEEDS) -> None:
    for k in keys:
        common.set_mode(conn, k, modes if isinstance(modes, str) else modes.get(k, "sheet"), "test")
    conn.commit()


# ---------------------------------------------------------------------------------------------------------------- synthetic sheet tabs: pacing

def _row(*cells, n=0):
    out = list(cells)
    return out + [None] * (n - len(out))


def pacing_grids(last: dt.date = D(2026, 11, 2), n: int = 20, marker: float = 1.0) -> dict[str, list[list]]:
    """The ten pacing tabs with `n` days ending `last`. `marker` scales the revenue columns so a test can tell sheet rows from feed rows."""
    days = [last - dt.timedelta(days=i) for i in range(n - 1, -1, -1)]
    s = serial
    g: dict[str, list[list]] = {}
    g["Orders - History"] = [["Orders"]] + [[s(d), 9000.0 * marker, 400.0, 11.0, 9400.0, 22.0, 40.0, 4.0] for d in days]
    g["Amazon - Daily"] = [["Amazon"]] + [[s(d), 4.0, 6.0, 250.0 * marker, 1.0, 60.0, 30.0, 100.0, 20.0, 800.0] for d in days]
    g["Platform - History"] = [["Platform"]] + [[s(d), "Google Ads", 90.0, 3.0, 250.0, 1.0, 100.0, 30.0, 50.0] for d in days]
    g["Attribution - History"] = [["Attribution"]] + [[s(d), "last_click", "Meta Ads", 6.0, 600.0, 10.0, 1000.0] for d in days]
    g["Traffic - Daily"] = [["Traffic"]] + [[s(d), 300.0, 800.0, 1000.0] for d in days]
    g["Meta Account - Reach"] = [["Reach"]] + [["day", d.isoformat(), s(d), s(d), None, None, 15000.0, 8000.0, None, 250.0, 300.0, 240.0] for d in days]
    g["Meta Product - Reach"] = [["Product reach"]] + [["day", d.isoformat(), s(d), s(d), p, 90.0] for d in days for p in ("VitaBull", "Beef Liver", "Boneflex")]
    g["NC Rev - By Product"] = [["NC"]] + [[s(d), p, 350.0 * marker, 4.0] for d in days for p in ("VitaBull", "Beef Liver", "Boneflex", "Other")]
    g["Upstack Any-Click - By Campaign"] = [["Any click"]] + [[s(d), None, None, None, stage, 4.0, 400.0] for d in days for stage in ("Scaling", "Testing")]
    g["Meta Bids - Daily"] = [["Bids"]] + [[s(d), None, None, None, None, None, None, None, 2500.0, None, None, 900.0, 600.0, 55000.0, 500.0, 40000.0] for d in days]
    return g


def seed_pacing_history(conn) -> None:
    """The one-time sheet seeds the pacing refresh would otherwise read (campaign history before ad_daily, the typed hourly curve): already loaded in production."""
    conn.execute("INSERT INTO proj_feed_meta_day (day, campaign_id, source, campaign_name, product, stage, spend, impressions, link_clicks, purchases, purchase_value) "
                 "VALUES ('2026-06-01', 'old', 'sheet', 'Old campaign', 'Unclassified', 'Scaling', 1, 1, 1, 1, 1)")
    for h in range(24):
        conn.execute("INSERT INTO proj_pacing_hourly (hour, spend_30d, days_in_curve, note) VALUES (%s, %s, 30, 'seed')", (h, 100.0 + h))
    conn.commit()


# ---------------------------------------------------------------------------------------------------------------- synthetic sheet tabs: inventory demand

def demand_tabs(**over) -> dict[str, list[list]]:
    from idemand_helpers import daily_tab, days, product_daily_tab, stock_tab

    def rows(start: dt.date, n: int, base=(10, 20, 30, 4)) -> dict:
        return {d: tuple(b + i for b in base) for i, d in enumerate(days(start, n))}
    t = {"Warehance Daily": daily_tab("revamp", rows(D(2026, 10, 1), 34), "Units shipped per day by product. 2026-11-03 is a partial day."),
         "Amazon - Product Daily": product_daily_tab(rows(D(2026, 9, 20), 45), "Source: ExpandFi warehouse. Today's row is partial."),
         "Amazon Daily Units": daily_tab("amazon", rows(D(2026, 9, 20), 22), "Last 2 days are partial."),
         "Intl Daily Units": daily_tab("intl", rows(D(2026, 9, 20), 23), "Rows from 2026-10-08 (grey italics) are estimates: Warehance non-US orders x 0.89."),
         "Warehance Fcst Inputs": stock_tab(TODAY)}
    t.update(over)
    return t


class RecordingReader:
    """A demand-module sheet reader (tab -> values) that records every tab read; refuses a tab it does not serve."""

    def __init__(self, tabs: dict[str, list[list]] | None = None, forbid: bool = False):
        self.tabs, self.forbid, self.reads = tabs or {}, forbid, []

    def __call__(self, tab: str) -> list[list]:
        self.reads.append(tab)
        if self.forbid or tab not in self.tabs:
            raise AssertionError(f"the sheet was read ({tab}) but this feed is direct")
        return self.tabs[tab]


def revamp_cells() -> list[list]:
    """`Warehance Fcst Inputs`!A33:D39 as the supply module reads it."""
    return [["Current inventory"], ["Product", "On hand", "Allocated", "Available"], ["Vital Bull", 100, 10, 90], ["Beef Liver", 200, 20, 180],
            ["Boneflex", 300, 30, 270], ["Vital Belle", 40, 4, 36], ["Snapshot date", serial(D(2026, 11, 2))]]


# ---------------------------------------------------------------------------------------------------------------- a fake Google Sheets service

class FakeSheets:
    """Stands in for etl.sheets_io._service(): serves `tabs` (name -> values) through spreadsheets().values().get(range="'Tab'!A1:Z").execute();
    `forbid=True` raises on any read. `reads` lists the tab names read."""

    def __init__(self, tabs: dict[str, list[list]] | None = None, forbid: bool = False):
        self.tabs, self.forbid, self.reads = tabs or {}, forbid, []

    def __call__(self):
        return self

    def spreadsheets(self):
        return self

    def values(self):
        return self

    def get(self, spreadsheetId=None, range="", **kw):
        tab = range.split("!")[0].strip("'")
        self.reads.append(tab)
        if self.forbid:
            raise AssertionError(f"the sheet was read ({tab}) but no feed may read it here")
        outer = self

        class _Req:
            def execute(self_inner):
                if tab not in outer.tabs:
                    raise RuntimeError(f"no such tab {tab}")
                return {"values": outer.tabs[tab]}
        return _Req()
