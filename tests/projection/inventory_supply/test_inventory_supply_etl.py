"""Loaders: seed from the sheet snapshot (idempotent, keeps edits), the daily Revamp stock refresh (idempotent, append-only), freshness of the manual inputs."""
from __future__ import annotations

from datetime import date

import pytest

import inv_supply_support as S
from app.projection import audit as _audit                      # noqa: F401  (import check)
from app.projection import freshness as foundation
from etl import projection_inventory_supply as etl

TODAY = date(2026, 11, 3)


def grid(asof=46329, rows=((20382, 1009), (46900, 863), (40101, 426), (4162, 7))):
    names = ("Vital Bull", "Beef Liver", "Boneflex", "Vital Belle")
    return [["Current inventory (Warehance products endpoint, Revamp Utah). Available = on hand minus allocated."], ["Product", "On hand", "Allocated", "Available"],
            *[[n, oh, al, oh - al] for n, (oh, al) in zip(names, rows)], ["Snapshot date", asof]]


def stock_rows(pg, loc="revamp", prod="bull"):
    return pg.execute("SELECT as_of, on_hand, allocated FROM proj_stock_snapshot WHERE location = %s AND product = %s ORDER BY as_of", (loc, prod)).fetchall()


# ---------------------------------------------------------------------------------------------------------------- seed

def test_seed_loads_the_sheet_and_is_idempotent_and_keeps_edits(pg, seeded):
    assert seeded == {"vendors": 2, "vendor_costs": 6, "product_costs": 5, "reorder": 8, "stock": 12, "pos": 74, "transfers": 44}
    assert pg.execute("SELECT count(*) AS n FROM proj_po WHERE source = 'auto-plan'").fetchone()["n"] == 36
    assert pg.execute("SELECT min(id) AS lo, max(id) AS hi FROM proj_po").fetchone() == {"lo": 2, "hi": 78}      # ids are sheet rows
    assert sum(etl.seed(pg).values()) == 0                                                                                # nothing new inserted on the second run
    pg.execute("UPDATE proj_po SET units = 123 WHERE id = 43")
    etl.seed(pg)
    assert pg.execute("SELECT units FROM proj_po WHERE id = 43").fetchone()["units"] == 123                  # an edit survives a re-run
    again = etl.seed(pg, replace=True)
    assert again["pos"] == 74 and pg.execute("SELECT units FROM proj_po WHERE id = 43").fetchone()["units"] == 23000
    assert pg.execute("SELECT count(*) AS n FROM proj_audit WHERE action = 'inventory_supply.seed'").fetchone()["n"] == 2


def test_seed_order_dates_cost_overrides_and_status_come_from_the_sheet(pg, seeded):
    row = pg.execute("SELECT * FROM proj_po WHERE id = 13").fetchone()          # typed 'land date minus 30 days' on the sheet
    assert row["order_date"] == date(2026, 11, 1) and row["land_date"] == date(2026, 12, 1) and row["warehouse"] == "intl"
    assert pg.execute("SELECT order_date, unit_cost FROM proj_po WHERE id = 2").fetchone() == {"order_date": None, "unit_cost": None}   # derived on the sheet
    assert pg.execute("SELECT status FROM proj_po WHERE id = 3").fetchone()["status"] == "landed"
    assert pg.execute("SELECT count(*) AS n FROM proj_fba_transfer WHERE receive_date < ship_date").fetchone()["n"] == 3     # the sheet's three flagged rows


# ---------------------------------------------------------------------------------------------------------------- refresh

def test_refresh_appends_one_dated_row_per_product_and_is_idempotent(pg):
    out = etl.refresh(pg, 14, today=TODAY, reader=lambda: grid())
    assert out == {"rows": 4, "data_through": "2026-11-03"}
    assert etl.refresh(pg, 14, today=TODAY, reader=lambda: grid()) == {"rows": 0, "data_through": "2026-11-03"}
    assert stock_rows(pg) == [{"as_of": date(2026, 11, 3), "on_hand": 20382, "allocated": 1009}]
    assert pg.execute("SELECT count(*) AS n FROM proj_audit WHERE action = 'inventory_supply.stock.refresh'").fetchone()["n"] == 4


def test_refresh_is_append_only_across_days_and_corrects_the_same_day(pg):
    etl.refresh(pg, 14, today=TODAY, reader=lambda: grid())
    etl.refresh(pg, 14, today=date(2026, 11, 4), reader=lambda: grid(asof=46330, rows=((19000, 900), (46000, 800), (40000, 400), (4100, 5))))
    assert [r["as_of"] for r in stock_rows(pg)] == [date(2026, 11, 3), date(2026, 11, 4)]                  # both days kept
    out = etl.refresh(pg, 14, today=date(2026, 11, 4), reader=lambda: grid(asof=46330, rows=((18500, 900), (46000, 800), (40000, 400), (4100, 5))))
    assert out["rows"] == 1                                                                                  # same day, one product changed
    assert [float(r["on_hand"]) for r in stock_rows(pg)] == [20382, 18500]
    assert len(stock_rows(pg, "amazon")) == 0                                                                # FBA and international counts are never touched


def test_refresh_skips_a_count_older_than_the_window_and_rejects_a_block_it_cannot_read(pg):
    out = etl.refresh(pg, 14, today=date(2026, 12, 31), reader=lambda: grid())
    assert out == {"rows": 0, "data_through": "2026-11-03"} and stock_rows(pg) == []
    with pytest.raises(RuntimeError):
        etl.refresh(pg, 14, today=TODAY, reader=lambda: [["nothing here"]])


def test_refresh_feeds_the_engine_opening_stock(pg):
    from app.projection.inventory_supply import loaders
    etl.refresh(pg, 14, today=TODAY, reader=lambda: grid())
    inp = loaders.load_inputs(pg)
    assert inp.stock[("revamp", "bull")].opening == 20382 - 1009 and inp.stock[("revamp", "belle")].on_hand == 4162


# ---------------------------------------------------------------------------------------------------------------- freshness

def feeds(pg, today=TODAY):
    return {f["feed"]: foundation.evaluate("inventory_supply", f, today) for f in etl.freshness(pg)}


def test_freshness_entries_are_valid_for_the_runner_and_report_stale_manual_counts(pg, seeded):
    pg.execute("UPDATE proj_po SET updated_at = '2026-10-20 12:00+00'")
    pg.execute("UPDATE proj_fba_transfer SET updated_at = '2026-10-20 12:00+00'")
    f = feeds(pg)
    assert set(f) == {"purchase_orders", "stock_revamp", "stock_fba", "stock_intl"}
    assert f["purchase_orders"]["cadence"] == "monthly-upload" and f["purchase_orders"]["data_through"] == "2026-10-20" and f["purchase_orders"]["status"] == "fresh"
    assert f["stock_revamp"]["cadence"] == "daily" and f["stock_revamp"]["status"] == "fresh" and f["stock_revamp"]["stale_after_days"] == 2
    assert f["stock_fba"]["status"] == "stale" and f["stock_fba"]["age_days"] == 21 and f["stock_fba"]["stale_after_days"] == 14
    assert f["stock_intl"]["status"] == "stale" and "estimate" in f["stock_intl"]["label"]
    assert foundation.evaluate("inventory_supply", etl.freshness(pg)[2], TODAY)["message"].startswith("feed stock_fba last data 2026-10-13, 21 days old")
    old = feeds(pg, date(2026, 12, 15))
    assert old["purchase_orders"]["status"] == "stale" and old["stock_revamp"]["status"] == "stale"


def test_freshness_with_nothing_loaded_is_unknown_not_an_error(pg):
    f = feeds(pg)
    assert all(v["status"] == "unknown" and v["data_through"] is None for v in f.values())


def test_the_daily_runner_picks_the_hooks_up(pg):
    hooks, problems = foundation.load_hooks(["inventory_supply"])
    assert not problems and set(hooks) == {"inventory_supply"}
    assert callable(hooks["inventory_supply"].refresh) and callable(hooks["inventory_supply"].freshness)
    feeds_, errors = foundation.collect(pg, hooks, TODAY, ["inventory_supply"])
    assert not errors and len(feeds_) == 4
