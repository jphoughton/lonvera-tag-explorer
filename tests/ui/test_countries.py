"""Countries (/countries) in the browser, against the mock server (tests/ui/mock_countries.py: the real payload builder in app/countries.py over made-up rows).

Mock facts: the Meta country breakdown covers Oct 12 .. Nov 2 2026, new customers by country Sep 1 .. Nov 2 2026 (Nov 2 = the last loaded day). Over Oct 12 - Nov 2 (22 days):
United States spends $220,000 (10,000 a day) and has $149,600 of new-customer revenue (0.68); the Total spend is $326,700 and its new-customer revenue $218,570 (0.67),
which includes Spain and Unknown, countries with new customers and no spend.
"""
from pathlib import Path

import pytest

from conftest import SHOTS
from tests._servers import start_mock

pytestmark = pytest.mark.mock_only
HERE = Path(__file__).resolve().parent
FULL = "?from=2026-10-12&to=2026-11-02"
HEADERS = ["Country", "Meta spend", "Share of spend", "New customers", "NC revenue", "NC ROAS", "NC CPA", "Average order value", "CPM", "CTR", "Cost per link click",
           "Conversion rate", "Meta-reported ROAS"]


def idle(page):
    page.wait_for_function("() => !document.querySelector('#view').hasAttribute('aria-busy')")


def goto(page, qs=FULL):
    page.goto("/countries" + qs)
    page.wait_for_selector("[data-testid=countries-table], [data-testid=countries-empty]")
    idle(page)


def body_rows(page):
    return page.evaluate("() => [...document.querySelectorAll('[data-testid=countries-table] tbody tr[data-key]')].map(tr => [...tr.children].map(td => td.innerText.trim()))")


def total_row(page):
    return page.evaluate("() => [...document.querySelector('[data-testid=countries-table] tfoot tr.total').children].map(td => td.innerText.trim())")


def test_table_has_one_row_per_country_sorted_by_spend_and_a_total(page):
    goto(page)
    assert page.evaluate("() => [...document.querySelectorAll('[data-testid=countries-table] thead th')].map(th => th.innerText.replace(/[^A-Za-z -]/g, '').trim())") == HEADERS
    rows = body_rows(page)
    assert [r[0] for r in rows] == ["United States", "United Kingdom", "Australia", "Canada", "Ireland", "Germany", "Spain", "Unknown"]
    us = rows[0]
    assert us[1] == "$220,000" and us[2] == "67.3%" and us[3] == "2,200" and us[4] == "$149,600" and us[5] == "0.68" and us[6] == "$100.00" and us[7] == "$68.00"
    assert us[8] == "$25.00" and us[9] == "1.50%" and us[10] == "$1.67" and us[11] == "1.67%" and us[12] == "0.80"
    es = rows[6]
    assert es[1] == "–" and es[2] == "–" and es[3] == "22" and es[4] == "$1,320" and es[5] == "–" and es[8] == "–" and es[12] == "–"          # no spend: blank, never zero
    de = rows[5]
    assert de[3] == "0" and de[4] == "$0" and de[5] == "0.00" and de[12] == "0.00"                                                           # spend but no new customers: a real zero
    t = total_row(page)
    assert t[0] == "Total" and t[1] == "$326,700" and t[2] == "100.0%" and t[4] == "$218,570" and t[5] == "0.67"
    assert t[3] == "3,157"


def test_caption_and_range_line_say_where_the_numbers_come_from(page):
    goto(page)
    cap = page.locator("[data-testid=countries-caption]").inner_text()
    assert cap.startswith("NC revenue = each customer’s first Shopify order by ship country (all channels, so includes Google and organic buyers)")
    assert cap.endswith("spend from Meta’s country breakdown, from Oct 12, 2026.") and "—" not in cap
    assert "Oct 12 to Nov 2, 2026 (22 days" in page.locator("[data-testid=countries-range]").inner_text()
    assert page.locator("[data-testid=countries-note]").count() == 0


def test_window_that_starts_before_the_breakdown_is_cut_and_says_so(page):
    goto(page, "?from=2026-10-01&to=2026-11-02")
    note = page.locator("[data-testid=countries-note]").inner_text()
    assert "starts Oct 12, 2026" in note and "blank, not zero" in note
    assert body_rows(page)[0][1] == "$220,000"
    assert "Oct 12 to Nov 2, 2026 (22 days" in page.locator("[data-testid=countries-range]").inner_text()


def test_window_wholly_before_the_breakdown_shows_an_empty_state_not_zeros(page):
    goto(page, "?from=2026-09-01&to=2026-10-05")
    text = page.locator("[data-testid=countries-empty]").inner_text()
    assert "No country data in this window" in text and "Oct 12, 2026" in text
    assert page.locator("[data-testid=countries-table]").count() == 0


def test_default_window_is_the_last_30_days_and_the_view_switch_lists_countries(page):
    page.goto("/countries")
    page.wait_for_selector("[data-testid=countries-table]")
    idle(page)
    assert "window=30d" in page.url
    segs = page.locator("#view-switch .seg")
    assert [s.inner_text() for s in segs.all()] == ["Products", "Campaigns", "Countries"]
    assert page.locator("#view-switch .seg[data-platform-view=countries]").get_attribute("aria-pressed") == "true"
    assert page.locator("[data-testid=nav-meta]").get_attribute("aria-current") == "page"
    page.locator("#view-switch .seg[data-platform-view=campaigns]").click()
    page.wait_for_selector("[data-testid=platform-campaigns]")
    page.locator("#view-switch .seg[data-platform-view=countries]").click()
    page.wait_for_selector("[data-testid=countries-table]")
    idle(page)
    assert page.url.split("?")[0].endswith("/countries")


def test_sorting_by_nc_roas(page):
    goto(page)
    page.locator("[data-testid=countries-table] thead th[data-col=nc_roas]").click()
    ordered = [float(r[5]) for r in body_rows(page) if r[5] != "–"]
    assert ordered == sorted(ordered, reverse=True)


def test_changing_the_range_reloads_the_table(page):
    goto(page)
    page.goto("/countries?from=2026-10-12&to=2026-10-12")
    page.wait_for_selector("[data-testid=countries-table]")
    idle(page)
    assert body_rows(page)[0][1] == "$10,000"


@pytest.mark.parametrize("qs", [FULL, "?window=7d"])
def test_no_sideways_page_scroll_at_375px_and_the_table_scrolls_inside_its_box(browser, base_url, qs):
    from conftest import PASSWORD, USERNAME
    c = browser.new_context(viewport={"width": 375, "height": 812}, base_url=base_url)
    try:
        assert c.request.post(base_url + "/api/login", data={"username": USERNAME, "password": PASSWORD}).status == 204
        pg = c.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto("/countries" + qs)
        pg.wait_for_selector("[data-testid=countries-table]")
        idle(pg)
        pg.wait_for_timeout(300)
        assert pg.evaluate("() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1")
        assert pg.evaluate("() => { const w = document.querySelector('[data-testid=countries-table] .tablewrap'); return w.scrollWidth > w.clientWidth; }")
        pg.screenshot(path=str(SHOTS / "countries_375.png"), full_page=True)
        assert not errs
    finally:
        c.close()


@pytest.fixture(scope="module")
def empty_url():
    proc, url = start_mock(HERE / "mock_server.py", env={"MOCK_COUNTRIES_EMPTY": "1"}, what="mock server with no country data")
    yield url
    proc.terminate()


def test_no_country_data_yet_shows_the_empty_state(browser, empty_url):
    from conftest import PASSWORD, USERNAME
    c = browser.new_context(viewport={"width": 1440, "height": 900}, base_url=empty_url)
    try:
        assert c.request.post(empty_url + "/api/login", data={"username": USERNAME, "password": PASSWORD}).status == 204
        pg = c.new_page()
        pg.goto("/countries?window=30d")
        pg.wait_for_selector("[data-testid=countries-empty]")
        assert "Country data has not loaded yet" in pg.locator("[data-testid=countries-empty]").inner_text()
    finally:
        c.close()
