"""Three-state month status (books, feeds, projected) and the boundary every page shares."""
from __future__ import annotations

from datetime import date

import pytest
from conftest import months, put_actuals, put_cells, small_plan

from app.projection.drivers.model import DriverInputs, compute_drivers

BASE = "/api/projection/drivers"
M = lambda y, m: date(y, m, 1)                # noqa: E731
TODAY = date(2026, 11, 3)


def test_the_three_states_follow_the_data():
    inp = DriverInputs(today=TODAY, qb_spend={"meta": {M(2026, 5): 1.0, M(2026, 6): 1.0}}, meta_feed={M(2026, 7): 5.0},
                       nc_orders_actual={M(2026, 6): 10.0, M(2026, 8): 10.0, M(2026, 10): 10.0, M(2026, 11): 10.0})
    d = compute_drivers({}, inp)
    assert d.month_status[M(2026, 6)] == "books" and d.month_status[M(2026, 5)] == "books"
    assert d.month_status[M(2026, 7)] == "feeds" and d.month_status[M(2026, 8)] == "feeds" and d.month_status[M(2026, 10)] == "feeds"
    assert d.month_status[M(2026, 9)] == "projected"                       # no data for September at all
    assert d.month_status[M(2026, 11)] == "projected"                      # the open month is never actual, even with a partial cohort row
    assert d.month_status[M(2027, 3)] == "projected"
    assert d.books_through == M(2026, 6) and d.feeds_through == M(2026, 10) and d.actual_through == d.books_through


def test_books_win_over_feeds_for_the_same_month():
    d = compute_drivers({}, DriverInputs(today=TODAY, qb_spend={"meta": {M(2026, 6): 1.0}}, nc_orders_actual={M(2026, 6): 5.0}, meta_feed={M(2026, 6): 2.0}))
    assert d.month_status[M(2026, 6)] == "books"


def test_no_data_means_everything_is_projected():
    d = compute_drivers({}, DriverInputs(today=TODAY))
    assert set(d.month_status.values()) == {"projected"} and d.books_through is None and d.feeds_through is None and d.actual_through is None


def test_the_page_payload_uses_books_as_the_actual_boundary_and_names_the_feeds(client, rw, stub_world):
    small_plan(rw)                                                     # books Jan-Jun 2026
    rw.execute("INSERT INTO proj_cohorts (cohort_month, new_customers, first_order_per_customer) VALUES "
               "('2026-07-01', 10000, 61.4), ('2026-08-01', 8000, 63.4), ('2026-09-01', 5600, 71.0), ('2026-10-01', 3600, 68.3)")
    rw.commit()
    t = client.get(f"{BASE}/table").get_json()
    ix = {m: i for i, m in enumerate(t["months"])}
    assert t["actual_through"] == "2026-06-01" and t["feeds_through"] == "2026-10-01"
    assert t["status"][ix["2026-06-01"]] == "actual" and t["status"][ix["2026-07-01"]] == "projected"
    assert t["month_status"][ix["2026-03-01"]] == "books" and t["month_status"][ix["2026-08-01"]] == "feeds" and t["month_status"][ix["2026-11-01"]] == "projected"
    row = next(r for r in t["rows"] if r["key"] == "month_status")
    assert row["values"][ix["2026-03-01"]] == "Books" and row["values"][ix["2026-09-01"]] == "Feeds" and row["values"][ix["2027-01-01"]] == "Projected"
    sl = t["status_line"]
    assert sl == {"books_through": "2026-06-01", "feeds_from": "2026-07-01", "feeds_to": "2026-10-01", "projected_from": "2026-11-01"}
    assert t["captions"][0].startswith("Source: Books through Jun 2026. Feeds only, no books yet: Jul 2026 to Oct 2026. Projected from Nov 2026*.")


def test_the_headline_windows_start_at_the_open_month(client, rw, stub_world):
    small_plan(rw)
    s = client.get(f"{BASE}/table").get_json()["summary"]
    assert s["next_from"] == "2026-11-01" and s["open_month"] == "2026-11-01" and s["anchor"] == "2026-10-01"
