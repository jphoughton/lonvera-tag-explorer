"""Inventory history (/inventory/history) in the browser, against the mock server (tests/ui/mock_inventory.py: the real payload builder over made-up rows).

Mock facts: newest day Nov 11; the US warehouse has every day Nov 3..11 (Vital Bull 1000 - 10 per day, Beef Liver 500); Amazon is an estimate every day except Nov 8
(missing); the international warehouse starts Nov 5 with a typed count (4,000 on hand, 9 allocated), later days are estimates (4,000 minus 100 per day).
"""
import re
from urllib.parse import parse_qs, urlparse

import pytest

pytestmark = pytest.mark.mock_only

SNAP = "[data-testid=ih-snapshot]"
TREND = "[data-testid=ih-trend]"


def idle(page):
    page.wait_for_function("() => !document.querySelector('#view').hasAttribute('aria-busy')")


def goto(page, qs=""):
    page.goto("/inventory/history" + qs)
    page.wait_for_selector("[data-testid=ih-page]")
    idle(page)


def text(page, testid):
    return page.locator(f"[data-testid={testid}]").first.inner_text().strip()


def test_default_shows_the_newest_day_with_a_total_column_and_estimate_marks(page):
    goto(page)
    assert page.locator("h1").inner_text() == "Inventory history"
    assert "Nov 11, 2026" in text(page, "ih-snap-title")
    assert page.locator("[data-testid=ih-date]").input_value() == "2026-11-11"
    heads = page.locator(f"{SNAP} thead th.ih-loc").all_inner_texts()
    assert heads == ["US warehouse", "Amazon (fulfilled by Amazon)", "International warehouse", "Total"]
    # Vital Bull on Nov 11 (index 8): US 920, Amazon 276 (estimate), international 3,400 (estimate); total 4,596
    assert text(page, "ih-cell-revamp-bull") == "920"
    assert text(page, "ih-cell-amazon-bull").startswith("276") and "estimate" in text(page, "ih-cell-amazon-bull")
    assert page.locator("[data-testid=ih-cell-intl-bull]").get_attribute("data-estimate") == "true"
    assert page.locator("[data-testid=ih-cell-revamp-bull]").get_attribute("data-estimate") == "false"
    assert text(page, "ih-total-bull") == "4,596"
    assert text(page, "ih-sum-total") == "5,096"                                  # plus Beef Liver 500 at the US warehouse
    assert text(page, "ih-cell-amazon-liver") == "–"
    # allocated: US 20 for Vital Bull; an estimate has no allocated figure
    assert page.locator(f"{SNAP} tbody tr[data-product=bull] td").nth(1).inner_text() == "20"
    assert page.locator("[data-testid=ih-note]").is_visible()


def test_the_day_a_count_was_typed_is_not_marked_as_an_estimate(page):
    goto(page, "?date=2026-11-05")
    assert "Nov 5, 2026" in text(page, "ih-snap-title")
    c = page.locator("[data-testid=ih-cell-intl-bull]")
    assert c.get_attribute("data-estimate") == "false" and c.inner_text().strip() == "4,000"
    assert page.locator(f"{SNAP} tbody tr[data-product=bull] td").nth(5).inner_text() == "9"      # allocated shown for a real count


def test_a_location_that_has_not_started_is_left_out_of_an_early_day(page):
    goto(page, "?date=2026-11-04")
    assert "International warehouse" not in page.locator(f"{SNAP} thead").inner_text()
    assert page.locator(f"{SNAP} thead th.ih-loc").count() == 3


def test_missing_day_is_flagged_in_the_table_and_the_summary(page):
    goto(page, "?date=2026-11-08")
    assert text(page, "ih-cell-amazon-bull") == "No snapshot"
    box = text(page, "ih-gap-box")
    assert "Missing days" in box and "Amazon" in box and "Nov 8" in box
    cell = page.locator("[data-testid=ih-t-2026-11-08-amazon]")
    assert cell.get_attribute("data-gap") == "true" and cell.inner_text().strip() == "Missing"
    assert page.locator("[data-testid=ih-t-2026-11-07-amazon]").get_attribute("data-estimate") == "true"
    # days before the international series are dashes, not gaps
    assert page.locator("[data-testid=ih-t-2026-11-04-intl]").get_attribute("data-gap") == "false"


def test_trend_table_lists_the_range_newest_first_with_totals(page):
    goto(page, "?days=7")
    days = page.locator(f"{TREND} tbody tr").evaluate_all("els => els.map(e => e.dataset.day)")
    assert days == ["2026-11-11", "2026-11-10", "2026-11-09", "2026-11-08", "2026-11-07", "2026-11-06", "2026-11-05"]
    assert page.locator("[data-testid=ih-range-7]").get_attribute("aria-pressed") == "true"
    # Nov 7 (index 4): US 960 + 500, Amazon 288, international 3,800 -> 5,548
    row = page.locator(f"{TREND} tbody tr[data-day='2026-11-07']")
    assert row.locator("td").last.inner_text().strip() == "5,548"


def test_changing_the_day_and_the_range_updates_the_url_and_the_page(page):
    goto(page)
    page.locator("[data-testid=ih-date]").fill("2026-11-06")
    page.wait_for_function("() => location.search.includes('date=2026-11-06')")
    idle(page)
    assert "Nov 6, 2026" in text(page, "ih-snap-title")
    page.locator("[data-testid=ih-range-90]").click()
    page.wait_for_function("() => location.search.includes('days=90')")
    idle(page)
    assert parse_qs(urlparse(page.url).query) == {"date": ["2026-11-06"], "days": ["90"]}
    assert page.locator(f"{TREND} tbody tr").count() == 90


@pytest.mark.allow_errors
def test_error_from_the_server_shows_the_error_box(page):
    page.route(re.compile(r"/api/inventory/history"), lambda route: route.fulfill(status=503, content_type="application/json", body='{"error":"query timed out"}'))
    page.goto("/inventory/history")
    page.wait_for_selector(".errorbox")
    assert "timed out" in page.locator(".errorbox").first.inner_text()


def test_nav_lists_the_page_under_plan(page):
    goto(page)
    assert page.locator("#topbar a.navgroup[aria-current=true]").inner_text() == "Plan"
    assert page.locator("#subnav a.navtab[aria-current=page]").inner_text() == "Inventory history"
