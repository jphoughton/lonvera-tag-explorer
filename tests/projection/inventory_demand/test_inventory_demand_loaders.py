"""Loaders: tab parsing, the daily refresh hook (idempotent, trailing window, partial days held back, estimates), freshness, seed."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.projection import freshness as core_freshness
from etl import projection_inventory_demand as etl
from idemand_helpers import FakeReader, daily_tab, days, product_daily_tab, stock_tab

NOV3 = date(2026, 11, 3)
REVAMP_CAPTION = "Units shipped per day by product. 2026-11-03 is a partial day. Last synced 2026-11-03T21:23:18Z."
AMAZON_CAPTION = "Amazon units sold per day, last 90 days. Last 2 days are partial (Amazon reporting lag)."      # the fallback tab
PRODUCT_DAILY_CAPTION = "Source: ExpandFi warehouse. Today's row is partial (same-day capture, not a complete day)."
INTL_CAPTION = "Units shipped per day. Rows from 2026-10-08 (grey italics) are estimates: Warehance non-US orders x 0.89."


def rows(start: date, n: int, base=(10, 20, 30, 4)) -> dict:
    return {d: tuple(b + i for b in base) for i, d in enumerate(days(start, n))}


def tabs(**over) -> dict:
    t = {"Warehance Daily": daily_tab("revamp", rows(date(2026, 10, 1), 34), REVAMP_CAPTION),          # Oct 1 .. Nov 3
         "Amazon - Product Daily": product_daily_tab(rows(date(2026, 9, 20), 45), PRODUCT_DAILY_CAPTION),   # Sep 20 .. Nov 3
         "Amazon Daily Units": daily_tab("amazon", rows(date(2026, 9, 20), 22), AMAZON_CAPTION),        # fallback tab: Sep 20 .. Oct 11
         "Intl Daily Units": daily_tab("intl", rows(date(2026, 9, 20), 23), INTL_CAPTION),             # Sep 20 .. Oct 12
         "Warehance Fcst Inputs": stock_tab(NOV3)}
    t.update(over)
    return t


def count(pg, location=None) -> int:
    if location:
        return pg.execute("SELECT count(*) AS n FROM proj_units_daily WHERE location = %s", (location,)).fetchone()["n"]
    return pg.execute("SELECT count(*) AS n FROM proj_units_daily").fetchone()["n"]


# ------------------------------------------------------------------------------------------------------------- refresh

def test_refresh_loads_complete_days_only_and_flags_what_it_held_back(conn, pg):
    out = etl.refresh(conn, days=14, reader=FakeReader(tabs()))
    assert out["locations"]["revamp"]["held_back"] == ["2026-11-03"]
    assert out["locations"]["amazon"]["held_back"] == ["2026-11-03"] and out["locations"]["amazon"]["source"] == "Amazon - Product Daily"
    assert out["locations"]["intl"]["held_back"] == []
    assert out["locations"]["revamp"]["data_through"] == "2026-11-02"
    assert out["locations"]["amazon"]["data_through"] == "2026-11-02"
    assert out["locations"]["intl"]["data_through"] == "2026-10-12"
    assert out["data_through"] == "2026-10-12"                       # the oldest of the three feeds (the international tab)
    assert pg.execute("SELECT max(day) AS d FROM proj_units_daily WHERE location = 'revamp'").fetchone()["d"] == date(2026, 11, 2)
    assert out["rows"] == 4 * 14 * 3 and count(pg) == 4 * 14 * 3      # trailing 14 days x 4 products x 3 locations


def test_refresh_is_idempotent(conn, pg):
    reader = FakeReader(tabs())
    first = etl.refresh(conn, days=14, reader=reader)
    snap = pg.execute("SELECT location, product, day, units, source, is_estimate, loaded_at FROM proj_units_daily ORDER BY 1,2,3").fetchall()
    second = etl.refresh(conn, days=14, reader=reader)
    assert second["rows"] == first["rows"] and second["changed"] == 0 and first["changed"] == first["rows"]
    assert pg.execute("SELECT location, product, day, units, source, is_estimate, loaded_at FROM proj_units_daily ORDER BY 1,2,3").fetchall() == snap
    assert count(pg) == first["rows"]


def test_trailing_window_is_replaced_and_older_days_are_left_alone(conn, pg):
    t = tabs()
    etl.refresh(conn, days=60, reader=FakeReader(t))                  # a wide first load
    before_old = pg.execute("SELECT units FROM proj_units_daily WHERE location='revamp' AND product='bull' AND day = %s", (date(2026, 10, 3),)).fetchone()["units"]
    t2 = tabs()
    for tab, day, idx, v in (("Warehance Daily", date(2026, 10, 3), 1, 9999), ("Warehance Daily", date(2026, 11, 1), 1, 7777)):
        row = next(r for r in t2[tab] if r and r[0] == (day - date(1899, 12, 30)).days)
        row[idx] = v
    out = etl.refresh(conn, days=7, reader=FakeReader(t2))            # window Oct 27 .. Nov 2 only
    after_old = pg.execute("SELECT units FROM proj_units_daily WHERE location='revamp' AND product='bull' AND day = %s", (date(2026, 10, 3),)).fetchone()["units"]
    new = pg.execute("SELECT units FROM proj_units_daily WHERE location='revamp' AND product='bull' AND day = %s", (date(2026, 11, 1),)).fetchone()["units"]
    assert after_old == before_old and new == 7777                    # inside the window replaced, outside untouched
    assert out["locations"]["revamp"]["changed"] == 1


def test_days_the_feed_stops_reporting_leave_the_window_but_other_sources_stay(conn, pg):
    etl.refresh(conn, days=14, reader=FakeReader(tabs()))
    pg.execute("UPDATE proj_units_daily SET units = 5, source = 'upload', is_estimate = false WHERE location='intl' AND product='bull' AND day = %s", (date(2026, 10, 10),))
    t = tabs()
    t["Warehance Daily"] = [r for r in t["Warehance Daily"] if not (r and r[0] == (date(2026, 10, 30) - date(1899, 12, 30)).days)]
    t["Intl Daily Units"] = [r for r in t["Intl Daily Units"] if not (r and r[0] == (date(2026, 10, 10) - date(1899, 12, 30)).days)]
    out = etl.refresh(conn, days=14, reader=FakeReader(t))
    assert out["locations"]["revamp"]["removed"] == 4
    assert pg.execute("SELECT count(*) AS n FROM proj_units_daily WHERE location='revamp' AND day = %s", (date(2026, 10, 30),)).fetchone()["n"] == 0
    kept = pg.execute("SELECT units FROM proj_units_daily WHERE location='intl' AND product='bull' AND day = %s", (date(2026, 10, 10),)).fetchone()
    assert kept["units"] == 5                                          # the uploaded row is not the feed's to delete


def test_international_estimates_are_flagged_and_never_replace_a_real_count(conn, pg):
    pg.execute("INSERT INTO proj_units_daily (location, product, day, units, source, is_estimate) VALUES ('intl','bull',%s,777,'upload',false)", (date(2026, 10, 9),))
    out = etl.refresh(conn, days=14, reader=FakeReader(tabs()))
    assert out["locations"]["intl"]["estimate_days"] == 5                                   # Oct 8 .. Oct 12
    est = pg.execute("SELECT min(day) AS d, count(DISTINCT day) AS n FROM proj_units_daily WHERE location='intl' AND is_estimate").fetchone()
    assert est["d"] == date(2026, 10, 8) and est["n"] == 5
    real = pg.execute("SELECT units, source, is_estimate FROM proj_units_daily WHERE location='intl' AND product='bull' AND day = %s", (date(2026, 10, 9),)).fetchone()
    assert real == {"units": 777, "source": "upload", "is_estimate": False}
    assert pg.execute("SELECT DISTINCT source FROM proj_units_daily WHERE location='intl' AND day < %s", (date(2026, 10, 8),)).fetchall() == [{"source": "tracking-export"}]


def test_a_real_count_from_the_feed_replaces_an_estimate(conn, pg):
    etl.refresh(conn, days=14, reader=FakeReader(tabs()))
    t = tabs(**{"Intl Daily Units": daily_tab("intl", rows(date(2026, 9, 20), 23), "Rows from 2026-10-11 (grey italics) are estimates.")})
    etl.refresh(conn, days=14, reader=FakeReader(t))
    assert pg.execute("SELECT count(DISTINCT day) AS n FROM proj_units_daily WHERE location='intl' AND is_estimate").fetchone()["n"] == 2   # Oct 11, 12


def test_stock_snapshot_is_loaded_with_its_date(conn, pg):
    out = etl.refresh(conn, days=14, reader=FakeReader(tabs()))
    assert out["stock"] == {"rows": 4, "changed": 4, "as_of": "2026-11-03"}
    got = {r["product"]: (r["on_hand"], r["allocated"], r["as_of"]) for r in pg.execute("SELECT * FROM proj_demand_snapshot").fetchall()}
    assert got["bull"] == (100, 10, NOV3) and got["belle"] == (40, 4, NOV3)
    assert etl.refresh(conn, days=14, reader=FakeReader(tabs()))["stock"]["changed"] == 0


def test_refresh_leaves_an_audit_row_and_a_load_log(conn, pg):
    etl.refresh(conn, days=14, reader=FakeReader(tabs()))
    a = pg.execute("SELECT actor, action, new FROM proj_audit WHERE action = 'inventory_demand.refresh'").fetchall()
    assert len(a) == 1 and a[0]["actor"] == "system" and a[0]["new"]["locations"]["revamp"]["held_back"] == ["2026-11-03"]
    loads = pg.execute("SELECT location, rows, last_day, source FROM proj_units_loads ORDER BY location").fetchall()
    assert [(r["location"], r["rows"]) for r in loads] == [("amazon", 56), ("intl", 56), ("revamp", 56)]
    assert {r["location"]: r["source"] for r in loads}["amazon"] == "Amazon - Product Daily"
    assert loads[2]["last_day"] == date(2026, 11, 2)


# ------------------------------------------------------------------------------------------------------------- failures

@pytest.mark.parametrize("tab", ["Warehance Daily", "Intl Daily Units", "Warehance Fcst Inputs"])
def test_an_empty_tab_raises_a_clear_error_and_writes_nothing(conn, pg, tab):
    with pytest.raises(etl.FeedError) as e:
        etl.refresh(conn, days=14, reader=FakeReader(tabs(**{tab: []})))
    assert tab in str(e.value) or "stock" in str(e.value)
    assert count(pg) == 0 and pg.execute("SELECT count(*) AS n FROM proj_demand_snapshot").fetchone()["n"] == 0
    assert pg.execute("SELECT count(*) AS n FROM proj_audit").fetchone()["n"] == 0


def test_an_unreadable_tab_is_reported_as_a_feed_error_without_leaking_the_cause(conn, pg):
    def reader(tab):
        raise etl.FeedError(f"cannot read sheet tab '{tab}': HttpError")

    with pytest.raises(etl.FeedError, match="Warehance Daily"):
        etl.refresh(conn, days=14, reader=reader)


def test_a_failure_in_the_third_feed_rolls_back_the_first_two(conn, pg):
    bad = daily_tab("intl", rows(date(2026, 9, 20), 5), "")
    bad[3][1] = "n/a"
    with pytest.raises(etl.FeedError, match="non-numeric"):
        etl.refresh(conn, days=14, reader=FakeReader(tabs(**{"Intl Daily Units": bad})))
    assert count(pg) == 0


@pytest.mark.parametrize("values,match", [
    ([["caption"], [], ["Day", "Vital Bull"]], "no 'Date' header"),
    ([["caption"], [], ["Date", "Vital Bull", "Total"]], "expected product columns"),
    ([["caption"], [], ["Date", "Vital Bull", "Beef Liver", "Boneflex"]], "no dated rows"),
])
def test_layout_problems_are_named(values, match):
    with pytest.raises(etl.FeedError, match=match):
        etl.parse_daily_tab("revamp", values)


def test_negative_units_are_rejected():
    with pytest.raises(etl.FeedError, match="negative"):
        etl.parse_daily_tab("revamp", daily_tab("revamp", {date(2026, 10, 1): (1, -2, 3, 4), date(2026, 10, 2): (1, 1, 1, 1)}))


def test_a_tab_with_only_the_partial_tail_has_no_complete_day():
    with pytest.raises(etl.FeedError, match="no complete day"):
        etl.parse_daily_tab("revamp", daily_tab("revamp", {date(2026, 11, 3): (1, 1, 1, 1)}, "2026-11-03 is a partial day."))


@pytest.mark.parametrize("days_arg", [0, -3, True, 2.5, "7"])
def test_refresh_rejects_a_bad_window(conn, days_arg):
    with pytest.raises(ValueError):
        etl.refresh(conn, days=days_arg, reader=FakeReader(tabs()))


def test_caption_rules_fall_back_to_the_feeds_usual_partial_tail():
    p = etl.parse_daily_tab("revamp", daily_tab("revamp", rows(date(2026, 10, 1), 5), ""))
    assert [d.isoformat() for d in p.held_back] == ["2026-10-05"]
    p = etl.parse_daily_tab("amazon", daily_tab("amazon", rows(date(2026, 10, 1), 5), ""), tab="Amazon Daily Units", fallback=True)
    assert [d.isoformat() for d in p.held_back] == ["2026-10-04", "2026-10-05"]
    p = etl.parse_product_daily(product_daily_tab(rows(date(2026, 10, 1), 5), ""))
    assert [d.isoformat() for d in p.held_back] == ["2026-10-05"]
    p = etl.parse_daily_tab("intl", daily_tab("intl", rows(date(2026, 10, 1), 5), ""))
    assert p.held_back == [] and p.estimate_from is None


# ------------------------------------------------------------------------------------------------------------- freshness

def test_freshness_reports_each_location_with_its_threshold(conn):
    assert [(f["feed"], f["data_through"]) for f in etl.freshness(conn)] == [("inventory_demand.revamp", None), ("inventory_demand.amazon", None),
                                                                           ("inventory_demand.intl", None)]
    etl.refresh(conn, days=14, reader=FakeReader(tabs()))
    f = {x["feed"]: x for x in etl.freshness(conn)}
    assert f["inventory_demand.revamp"]["data_through"] == date(2026, 11, 2) and f["inventory_demand.revamp"]["stale_after_days"] == 2
    assert f["inventory_demand.amazon"]["data_through"] == date(2026, 11, 2) and f["inventory_demand.amazon"]["stale_after_days"] == 2
    assert f["inventory_demand.intl"]["data_through"] == date(2026, 10, 12) and f["inventory_demand.intl"]["stale_after_days"] == 4
    assert f["inventory_demand.revamp"]["cadence"] == "daily" and f["inventory_demand.amazon"]["cadence"] == "daily"
    assert f["inventory_demand.intl"]["cadence"] == "monthly-upload"            # hand-uploaded file: reported stale, never fatal to the daily run
    assert all(x["label"] for x in f.values())


def test_freshness_entries_are_accepted_by_the_core_runner(conn):
    etl.refresh(conn, days=14, reader=FakeReader(tabs()))
    by = {e["feed"]: core_freshness.evaluate("inventory_demand", e, date(2026, 11, 4)) for e in etl.freshness(conn)}
    assert by["inventory_demand.revamp"]["status"] == "fresh" and by["inventory_demand.revamp"]["age_days"] == 2
    assert by["inventory_demand.amazon"]["status"] == "fresh" and by["inventory_demand.intl"]["status"] == "stale"
    late = {e["feed"]: core_freshness.evaluate("inventory_demand", e, date(2026, 11, 6)) for e in etl.freshness(conn)}
    assert late["inventory_demand.amazon"]["status"] == "stale" and late["inventory_demand.revamp"]["status"] == "stale"


# ------------------------------------------------------------------------------------------------------------- seed pieces

def test_amazon_monthly_history_skips_the_month_the_pull_did_not_finish():
    values = [["Amazon units sold per month. Source: ExpandFi, pulled 2026-09-15. Sep-2026 is partial."], [],
              ["Month", "Vital Bull", "Beef Liver", "Boneflex", "Vital Belle", "Total units", "Net revenue"],
              [(date(2026, 8, 1) - date(1899, 12, 30)).days, 1, 2, 3, 0, 6, 100.0],
              [(date(2026, 9, 1) - date(1899, 12, 30)).days, 9, 9, 9, 0, 27, 100.0]]
    got = etl.parse_amazon_monthly(values)
    assert {m for _, _, m, _ in got} == {date(2026, 8, 1)} and len(got) == 4


def test_country_split_reads_dates_and_text_month_headers():
    jul26 = (date(2026, 7, 26) - date(1899, 12, 30)).days                  # the sheet parsed the header "Jul-26" as July 26
    values = [["by country"], [], ["Country", jul26, "Sep-26 (15d)", "Total"], ["United Kingdom", 100, 40, 140], ["TOTAL", 100, 40, 140], ["US share %", 0.1, 0.1, 0.1]]
    got = etl.parse_countries(values)
    assert sorted(got) == [(date(2026, 7, 1), "United Kingdom", 100.0, None), (date(2026, 9, 1), "United Kingdom", 40.0, 15)]


def test_levers_are_read_by_label_from_a_fcst_inputs_tab():
    values = [["x"], ["Assumption", "Value"], ["Baseline window (days)", 21], ["Monthly growth %", 0.03], [], ["Month", "Seasonality index"]] + [[i, 1 + i / 100] for i in range(1, 13)]
    got = etl.parse_levers("revamp", values)
    assert got["baseline_days"] == 21 and got["monthly_growth"] == 0.03 and got["seasonality"][11] == pytest.approx(1.12)


def test_seed_loads_everything_and_sets_levers_only_once(conn, pg):
    t = tabs(**{"Amazon Monthly Units": [["pulled 2026-09-15"], [], ["Month", "Vital Bull", "Beef Liver", "Boneflex", "Vital Belle"],
                                          [(date(2026, 8, 1) - date(1899, 12, 30)).days, 1, 2, 3, 0]],
                "Intl By Country": [["x"], [], ["Country", "Aug-26"], ["Canada", 5]],
                "Amazon Fcst Inputs": [["x"], ["Baseline window (days)", 45], ["Monthly growth %", 0.01]],
                "Intl Fcst Inputs": [["x"]], })
    out = etl.seed(conn, FakeReader(t))
    assert out["monthly_rows"] == 4 and out["country_rows"] == 1 and out["levers"] == "seeded from the Fcst Inputs tabs"
    params = pg.execute("SELECT params FROM proj_scenarios WHERE slug = 'current'").fetchone()["params"]["inventory_demand"]["locations"]
    assert params["amazon"]["baseline_days"] == 45 and params["amazon"]["monthly_growth"] == 0.01 and params["revamp"]["baseline_days"] == 28
    assert etl.seed(conn, FakeReader(t))["levers"] == "kept existing levers"


# ------------------------------------------------------------------------------------------------------------- Amazon: Product Daily first, Daily Units as the fallback

def test_product_daily_is_summed_per_day_and_product_and_other_products_are_ignored():
    values = product_daily_tab({date(2026, 10, 1): (5, 6, 7, 1), date(2026, 10, 2): (1, None, 2, 0), date(2026, 10, 3): (9, 9, 9, 9)})
    values.insert(2, [(date(2026, 10, 1) - date(1899, 12, 30)).days, "Vital Bull", 1.0, 1, 3])            # a second row for the same day and product
    values.insert(2, [(date(2026, 10, 1) - date(1899, 12, 30)).days, "Other", 1.0, 1, 40])
    p = etl.parse_product_daily(values)
    got = {(r.product, r.day): r.units for r in p.rows}
    assert got[("bull", date(2026, 10, 1))] == 8 and got[("liver", date(2026, 10, 1))] == 6
    assert ("liver", date(2026, 10, 2)) not in got and got[("belle", date(2026, 10, 2))] == 0          # no row = no sales; a written 0 stays
    assert p.ignored_units == 40 and [d.isoformat() for d in p.held_back] == ["2026-10-03"]
    assert all(r.source == "expandfi-product-daily" and not r.is_estimate for r in p.rows)


@pytest.mark.parametrize("values,match", [
    ([], "is empty"),
    ([["Date", "Product", "Units"]], "no dated rows"),
    ([["Day", "Product"]], "no 'Date' header"),
    ([["Date", "Product", "Revenue"], ["caption"]], "Date, Product and Units"),
])
def test_product_daily_layout_problems_are_named(values, match):
    with pytest.raises(etl.FeedError, match=match):
        etl.parse_product_daily(values)


def test_an_unusable_product_daily_falls_back_to_daily_units_and_says_so(conn, pg):
    out = etl.refresh(conn, days=14, reader=FakeReader(tabs(**{"Amazon - Product Daily": []})))
    assert out["locations"]["amazon"]["source"] == "Amazon Daily Units" and out["locations"]["amazon"]["data_through"] == "2026-10-09"
    assert out["warnings"] and "Amazon - Product Daily" in out["warnings"][0] and "Amazon Daily Units" in out["warnings"][0]
    assert pg.execute("SELECT DISTINCT source FROM proj_units_daily WHERE location = 'amazon'").fetchall() == [{"source": "expandfi"}]
    assert pg.execute("SELECT new->'warnings' AS w FROM proj_audit WHERE action = 'inventory_demand.refresh'").fetchone()["w"]


def test_when_both_amazon_tabs_fail_the_error_names_both(conn, pg):
    with pytest.raises(etl.FeedError) as e:
        etl.refresh(conn, days=14, reader=FakeReader(tabs(**{"Amazon - Product Daily": [], "Amazon Daily Units": []})))
    assert "Amazon - Product Daily" in str(e.value) and "fallback tab failed too" in str(e.value) and "Amazon Daily Units" in str(e.value)
    assert count(pg) == 0


def test_the_next_refresh_replaces_fallback_rows_with_product_daily_rows(conn, pg):
    etl.refresh(conn, days=14, reader=FakeReader(tabs(**{"Amazon - Product Daily": []})))
    etl.refresh(conn, days=14, reader=FakeReader(tabs()))
    assert pg.execute("SELECT DISTINCT source FROM proj_units_daily WHERE location = 'amazon' AND day >= '2026-10-20'").fetchall() == [{"source": "expandfi-product-daily"}]
    assert pg.execute("SELECT max(day) AS d FROM proj_units_daily WHERE location = 'amazon'").fetchone()["d"] == date(2026, 11, 2)


def test_seed_can_use_the_older_amazon_tab_the_sheets_own_forecast_reads(conn, pg):
    reader = FakeReader({**tabs(), "Amazon Monthly Units": [["pulled 2026-09-15"], [], ["Month", "Vital Bull", "Beef Liver", "Boneflex", "Vital Belle"]],
                         "Intl By Country": [["x"], [], ["Country", "Aug-26"], ["Canada", 5]]})
    out = etl.seed(conn, reader, with_levers=False, amazon_source="daily-units")
    assert out["locations"]["amazon"]["source"] == "Amazon Daily Units" and out["locations"]["amazon"]["data_through"] == "2026-10-09"
    with pytest.raises(ValueError):
        etl.seed(conn, reader, with_levers=False, amazon_source="nope")
