"""Vendors (/priorities/vendors) in the browser, against the mock server (tests/ui/mock_production_board.py serves GET /api/priorities/cuts)."""
import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.mock_only


def open_page(page, suffix=""):
    page.goto("/priorities/vendors" + suffix)
    page.wait_for_selector("[data-testid=vp-table]")
    page.wait_for_function("() => !document.querySelector('#view').hasAttribute('aria-busy')")


def vendors(page):
    return page.locator("[data-testid=vp-row]").evaluate_all("rows => rows.map(r => r.dataset.vendor)")


def spend(page, vendor):
    return page.locator(f"[data-testid=vp-row][data-vendor='{vendor}'] [data-metric=spend]").inner_text()


def test_page_lists_every_vendor_with_a_total_row_and_a_nav_tab(page):
    open_page(page)
    names = vendors(page)
    assert len(names) >= 2 and len(set(names)) == len(names)
    expect(page.locator("[data-testid=vp-total]")).to_contain_text("All vendors")
    expect(page.locator("[data-testid=nav-vendors]")).to_be_visible()
    expect(page.locator("[data-testid=vp-basis]")).to_contain_text("Mock basis.")
    expect(page.locator("[data-testid=vp-preset-30]")).to_have_attribute("aria-pressed", "true")


def test_sort_by_a_column_reverses_and_goes_in_the_url(page):
    open_page(page)
    page.locator("[data-testid=vp-sort-vendor]").click()
    asc = vendors(page)
    assert asc == sorted(asc, key=str.lower) and "sort=vendor" in page.url
    page.locator("[data-testid=vp-sort-vendor]").click()
    assert vendors(page) == list(reversed(asc)) and "dir=asc" not in page.url


def test_row_splits_by_product_and_the_open_row_survives_a_reload(page):
    open_page(page)
    first = vendors(page)[0]
    page.locator(f"[data-testid=vp-row][data-vendor='{first}'] [data-testid=vp-toggle]").click()
    assert page.locator("[data-testid=vp-child]").count() >= 1
    assert "open=" in page.url
    page.reload()
    page.wait_for_selector("[data-testid=vp-table]")
    assert page.locator("[data-testid=vp-child]").count() >= 1
    page.locator(f"[data-testid=vp-row][data-vendor='{first}'] [data-testid=vp-toggle]").click()
    assert page.locator("[data-testid=vp-child]").count() == 0


def test_date_preset_changes_the_window_and_the_spend(page):
    open_page(page)
    first = vendors(page)[0]
    before, range_before = spend(page, first), page.locator("[data-testid=vp-range]").inner_text()
    page.locator("[data-testid=vp-preset-7]").click()
    page.wait_for_function("(t) => document.querySelector('[data-testid=vp-range]').textContent !== t", arg=range_before)
    assert spend(page, first) != before and "preset=7" in page.url
