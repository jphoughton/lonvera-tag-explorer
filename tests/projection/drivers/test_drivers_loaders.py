"""Loaders: where each input comes from and which source wins."""
from __future__ import annotations

from datetime import date

from conftest import months, put_actuals, put_cells

from app.projection import engine, scenarios
from app.projection.drivers import MODULE, loaders
from app.projection.drivers.model import DriversResult


def M(y, m):
    return date(y, m, 1)


def load(rw, slug="current"):
    return loaders.load_inputs(rw, scenarios.get_scenario(rw, slug), date(2026, 11, 3))


def test_scenario_cells_split_into_plan_and_overrides_with_their_origin(rw):
    put_cells(rw, "drivers.spend_total", {M(2026, 12): 350000.0}, note="typed")
    put_cells(rw, "drivers.nc_rev", {M(2026, 10): 240354.88}, kind="override")
    import pytest
    from app.queries import ApiError
    with pytest.raises(ApiError, match="not a Spend plan line"):                   # the hook rejects unknown drivers.* names on every write path
        put_cells(rw, "drivers.something_else", {M(2026, 10): 1.0})
    rw.rollback()
    rw.execute("INSERT INTO proj_series (scenario_id, line_key, month, value, kind) SELECT id, 'drivers.something_else', '2026-10-01', 1, 'input' "
               "FROM proj_scenarios WHERE slug = 'current'")                       # a stray row written some other way is ignored by the loader
    rw.commit()
    inp = load(rw)
    assert inp.plan["spend_total"] == {M(2026, 12): 350000.0} and inp.overrides["nc_rev"] == {M(2026, 10): 240354.88}
    assert inp.cell_info["spend_total"][M(2026, 12)] == {"kind": "input", "note": "typed", "origin": "current"}
    assert inp.cell_info["nc_rev"][M(2026, 10)]["kind"] == "override"
    assert "something_else" not in inp.plan and "something_else" not in inp.overrides


def test_a_clone_holds_its_own_copy_and_reads_nothing_from_its_base_afterwards(rw):
    put_cells(rw, "drivers.spend_total", {M(2026, 12): 350000.0, M(2027, 1): 350000.0})
    scenarios.create(rw, "Growth", "current", actor="test")
    rw.commit()
    put_cells(rw, "drivers.spend_total", {M(2027, 1): 900000.0}, slug="growth")
    put_cells(rw, "drivers.spend_total", {M(2027, 2): 111.0}, slug="current")        # added to the base after the clone
    grown = load(rw, "growth")
    assert grown.plan["spend_total"][M(2027, 1)] == 900000.0 and grown.plan["spend_total"][M(2026, 12)] == 350000.0
    assert M(2027, 2) not in grown.plan["spend_total"] and grown.cell_info["spend_total"][M(2026, 12)]["origin"] == "growth"
    assert load(rw, "current").plan["spend_total"][M(2027, 1)] == 350000.0


def test_books_from_the_pnl_module_beat_the_seeded_keys_month_by_month(rw):
    put_actuals(rw, "spend.meta", {M(2026, 1): 100.0, M(2026, 2): 100.0})
    put_actuals(rw, "pnl.mkt_facebook_ads", {M(2026, 1): 200.0})
    put_actuals(rw, "spend.google", {M(2026, 2): 7.0})
    inp = load(rw)
    assert inp.qb_spend["meta"] == {M(2026, 1): 200.0, M(2026, 2): 100.0} and inp.qb_spend["google"] == {M(2026, 2): 7.0}
    assert inp.qb_spend["bing"] == {}


def test_revenue_actuals_prefer_the_pnl_keys(rw):
    put_actuals(rw, "amazon_rev", {M(2026, 1): 1.0, M(2026, 2): 2.0})
    put_actuals(rw, "pnl.amazon_rev", {M(2026, 1): 10.0})
    put_actuals(rw, "pnl.dtc_rev", {M(2026, 3): 5.0})
    put_actuals(rw, "tiktok_rev", {M(2026, 3): 3.0})
    inp = load(rw)
    assert inp.amazon_actual == {M(2026, 1): 10.0, M(2026, 2): 2.0} and inp.dtc_actual == {M(2026, 3): 5.0} and inp.tiktok_actual == {M(2026, 3): 3.0}


def test_meta_feed_is_its_own_series(rw):
    put_actuals(rw, "spend.meta_feed", {M(2026, 9): 123.0})
    inp = load(rw)
    assert inp.meta_feed == {M(2026, 9): 123.0} and inp.qb_spend["meta"] == {}
    assert loaders.feed_gap(inp) == {}
    put_actuals(rw, "spend.meta", {M(2026, 9): 100.0})
    assert loaders.feed_gap(load(rw)) == {M(2026, 9): (123.0, 100.0)}


def test_new_customers_come_from_the_cohort_table_when_it_has_rows(rw):
    put_actuals(rw, "nc.orders", {M(2026, 7): 999.0})
    put_actuals(rw, "nc.rev", {M(2026, 7): 999.0})
    inp = load(rw)
    assert inp.nc_orders_actual == {M(2026, 7): 999.0} and inp.nc_source == "loaded new-customer actuals"
    rw.execute("INSERT INTO proj_cohorts (cohort_month, new_customers, first_order_per_customer) VALUES ('2026-07-01', 10000, 61.5), ('2026-08-01', 8000, 63.25)")
    rw.commit()
    inp = load(rw)
    assert inp.nc_orders_actual == {M(2026, 7): 10000.0, M(2026, 8): 8000.0} and inp.nc_source == "cohort table"
    assert inp.nc_rev_actual[M(2026, 7)] == 10000 * 61.5 and inp.nc_rev_actual[M(2026, 8)] == 8000 * 63.25


def test_a_cohort_row_without_a_first_order_value_is_not_used(rw):
    rw.execute("INSERT INTO proj_cohorts (cohort_month, new_customers) VALUES ('2026-07-01', 10000)")
    rw.commit()
    assert load(rw).nc_orders_actual == {} and load(rw).nc_source == "loaded new-customer actuals"


def test_module_object_runs_through_the_engine(rw, stub_world):
    put_actuals(rw, "spend.meta", {m: 1000.0 for m in months(2026, 1, 3)})
    put_cells(rw, "drivers.spend_total", {M(2026, 12): 5000.0})
    res = engine.run("current", rw, strict=True, today=date(2026, 11, 3))
    assert res.built["drivers"] and not res.stubbed.get("drivers") and isinstance(res.drivers, DriversResult)
    assert res.actual_through == M(2026, 3) and res.drivers.spend_total[M(2026, 12)] == 5000.0
    assert MODULE.key == "drivers" and callable(MODULE.load_inputs) and callable(MODULE.compute)
