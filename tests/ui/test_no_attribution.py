"""Meta, View by "No attribution" (/no-attribution): Meta spend beside the actual new-customer revenue of each product, against the mock server
(tests/ui/mock_noattr.py: the real payload code in app/no_attribution.py over made-up daily feeds; spend runs to Nov 9 2026, new-customer revenue to Nov 7).
The default range is the last 30 days (written into the URL when the page opens), cut to the days both feeds hold, so the view reads "data through Nov 7".
"""
import re
from pathlib import Path

import pytest
from playwright.sync_api import expect

from conftest import PASSWORD, SHOTS, USERNAME
from tests._servers import start_mock
from ui_helpers import qparams, table_data

pytestmark = pytest.mark.mock_only
HERE = Path(__file__).resolve().parent
TABLE = "noattr-table"


def idle(page):
    page.wait_for_function("() => !document.querySelector('#view').hasAttribute('aria-busy')")


def goto(page, qs=""):
    page.goto("/no-attribution" + qs)
    page.wait_for_selector(f"[data-testid={TABLE}] tbody tr[data-key]")
    idle(page)


def api(page, path):
    return page.evaluate("async (u) => (await fetch(u, { credentials: 'include' })).json()", path)


def money(v):
    return "$" + format(round(v), ",")


def test_meta_products_page_links_to_the_view_and_the_range_carries_over(page):
    page.goto("/?platform=meta&window=14d")
    page.wait_for_selector("[data-testid=products-table] tbody tr[data-key]")
    idle(page)
    segs = page.locator("#view-switch .seg")
    assert [s.inner_text() for s in segs.all()] == ["Products", "Campaigns", "Countries", "By page", "No attribution"]
    segs.nth(4).click()
    page.wait_for_selector(f"[data-testid={TABLE}] tbody tr[data-key]")
    idle(page)
    assert page.evaluate("location.pathname") == "/no-attribution" and qparams(page).get("window") == "14d"
    expect(page.locator("#view-switch .seg[aria-pressed=true]")).to_have_text("No attribution")
    expect(page.locator("#platform-ctl [data-platform=meta]")).to_have_attribute("aria-pressed", "true")
    assert page.title() == "No attribution · Lonvera"
    page.locator("#view-switch .seg").nth(0).click()                                      # back to Products: Numbers from is still Meta's own
    page.wait_for_selector("[data-testid=products-table] tbody tr[data-key]")
    assert page.evaluate("location.pathname") == "/"


def test_opens_on_the_last_30_days_without_attribution_controls(page):
    page.goto("/no-attribution")
    page.wait_for_selector(f"[data-testid={TABLE}] tbody tr[data-key]")
    idle(page)
    assert qparams(page).get("window") == "30d"
    for sel in ("#source-ctl", "#basis-ctl", "#stage-ctl", "#compare-ctl", "#source-banner"):
        assert page.locator(sel).first.is_hidden(), sel
    expect(page.locator("[data-testid=noattr-note]")).to_have_text("Spend is Meta spend by product. Revenue is every new customer’s first order for that product, from all channels, not just Meta.")


def test_columns_are_spend_and_new_customer_numbers_only(page):
    goto(page, "?window=30d")
    d = table_data(page, TABLE)
    assert d["heads"] == ["name", "spend", "spend_share", "nc_revenue", "nc_revenue_share", "nc_orders", "nc_roas", "nc_cpa", "aov", "link_clicks", "cpc", "conv_rate"]
    head_text = page.locator(f"[data-testid={TABLE}] thead").inner_text()
    for attributed in ("Purchases", "Impressions", "Tracker rows", "Creatives", "Live now"):
        assert attributed not in head_text, attributed
    assert [r[0] for r in d["rows"]] == ["Vita Bull", "Beef Liver", "Boneflex", "Other products"]
    assert d["footRows"][0]["cells"][0] == "Total" and len(d["footRows"]) == 1


def test_numbers_match_the_api_and_the_total_row_adds_up(page):
    goto(page, "?window=30d")
    api_d = api(page, "/api/no-attribution/products?window=30d")
    d = table_data(page, TABLE)
    for cells, r in zip(d["rows"], api_d["rows"]):
        assert cells[1] == money(r["spend"]) and cells[3] == money(r["nc_revenue"]), (cells, r["key"])
        assert cells[6] == f"{r['nc_roas']:.2f}x"
    t, foot = api_d["total"], d["footRows"][0]["cells"]
    assert foot[1] == money(t["spend"]) and foot[3] == money(t["nc_revenue"]) and foot[6] == f"{t['nc_revenue'] / t['spend']:.2f}x"
    assert abs(t["spend"] - sum(r["spend"] for r in api_d["rows"])) < 0.01 and abs(t["nc_revenue"] - sum(r["nc_revenue"] for r in api_d["rows"])) < 0.01
    assert api_d["data_through"] == "2026-11-07"
    src = page.locator("#source").inner_text()
    assert "data through Nov 7" in src and "first Shopify orders" in src, src
    assert re.search(r"add up to \$[\d,]+ of \$[\d,]+ total new-customer revenue", page.locator("[data-testid=noattr-tie]").inner_text())


def test_since_launch_and_custom_ranges_work(page):
    goto(page, "?window=30d")
    page.goto("/no-attribution?from=2026-10-01&to=2026-10-07")
    page.wait_for_selector(f"[data-testid={TABLE}] tbody tr[data-key]")
    idle(page)
    d = api(page, "/api/no-attribution/products?from=2026-10-01&to=2026-10-07")
    assert d["window_info"]["days"] == 7 and page.locator(f"[data-testid={TABLE}] tbody tr[data-key]").count() == 4


def test_phone_width_has_no_sideways_page_scroll(browser, base_url):
    c = browser.new_context(viewport={"width": 375, "height": 812}, base_url=base_url)
    try:
        assert c.request.post(base_url + "/api/login", data={"username": USERNAME, "password": PASSWORD}).status == 204
        pg = c.new_page()
        goto(pg, "?window=30d")
        assert pg.evaluate("() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1")
        pg.screenshot(path=str(SHOTS / "no_attribution_375.png"))
    finally:
        c.close()


def test_desktop_screenshot(page):
    goto(page, "?window=30d")
    page.screenshot(path=str(SHOTS / "no_attribution_1440.png"))


@pytest.fixture(scope="module")
def empty_url():
    proc, url = start_mock(HERE / "mock_server.py", env={"MOCK_NOATTR_EMPTY": "1"}, what="mock server with empty product feeds")
    yield url
    proc.terminate()


def test_empty_feeds_show_the_empty_state(browser, empty_url):
    c = browser.new_context(viewport={"width": 1440, "height": 900}, base_url=empty_url)
    try:
        assert c.request.post(empty_url + "/api/login", data={"username": USERNAME, "password": PASSWORD}).status == 204
        pg = c.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto("/no-attribution?window=30d")
        pg.wait_for_selector("[data-testid=noattr-empty]")
        expect(pg.locator("[data-testid=noattr-empty]")).to_contain_text("have not loaded yet")
        assert pg.locator(".errorbox").count() == 0 and not errs, errs
    finally:
        c.close()
