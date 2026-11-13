"""Amazon (/amazon) in the browser, against the mock server (tests/ui/mock_amazon.py: the real payload builders in app/amazon.py over made-up Amazon rows).

Mock facts: orders Oct 1 2025 .. Nov 9 2026, ads Jan 4 .. Nov 9 2026 (seven campaigns), traffic to Nov 7, search weeks Jul 13 2025 .. Nov 1 2026 (twenty terms), fees Jan .. Nov 2026.
The default range is the last 30 days (written into the URL when the page opens).
"""
import re

import pytest
from playwright.sync_api import expect

from conftest import SHOTS
from tests._servers import start_mock
from pathlib import Path
from ui_helpers import platform_btn, qparams

pytestmark = pytest.mark.mock_only
HERE = Path(__file__).resolve().parent


def idle(page):
    page.wait_for_function("() => !document.querySelector('#view').hasAttribute('aria-busy')")


def goto(page, qs="", testid="amz-body"):
    page.goto("/amazon" + qs)
    page.wait_for_selector(f"[data-testid={testid}]")
    idle(page)


def api(page, path):
    return page.evaluate("async (u) => (await fetch(u, { credentials: 'include' })).json()", path)


def no_sideways_scroll(page):
    return page.evaluate("() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1")


def rows(page, table):
    return page.locator(f"[data-testid={table}] tbody tr[data-key]")


def chart(page, cid):
    return page.evaluate("""(id) => { const c = window.Chart && Chart.getChart(id); if (!c) return null;
        return { labels: c.data.labels.slice(), ds: c.data.datasets.map((d) => ({ label: d.label, type: d.type, axis: d.yAxisID === 'y1' ? 'right' : 'left', data: d.data.slice() })) }; }""", cid)


def wait_chart(page, cid):
    page.wait_for_function("(id) => { const c = window.Chart && Chart.getChart(id); return !!c && c.data.datasets.length > 0; }", arg=cid)


# ------------------------------------------------------------------------------------------------------------------------------ picker and shell
def test_platform_picker_gains_amazon_and_goes_to_the_amazon_page(page):
    page.goto("/?window=14d")
    page.wait_for_selector("#platform-ctl")
    idle(page)
    assert [b.get_attribute("data-platform") for b in page.locator("#platform-ctl .seg").all()] == ["all", "meta", "google", "amazon"]
    platform_btn(page, "amazon").click()
    page.wait_for_selector("[data-testid=amz-body]")
    idle(page)
    assert page.evaluate("location.pathname") == "/amazon" and qparams(page).get("window") == "14d"             # the range carries over
    expect(platform_btn(page, "amazon")).to_have_attribute("aria-pressed", "true")
    expect(platform_btn(page, "all")).to_have_attribute("aria-pressed", "false")
    assert page.title() == "Amazon · Lonvera"
    expect(page.locator("#subnav a.navtab[aria-current=page]")).to_have_text("Amazon")
    # Amazon has only its own reporting: no Numbers from switch, no stage chips, no comparison, and the page says so once
    for sel in ("#source-ctl", "#basis-ctl", "#stage-ctl", "#compare-ctl", "#source-banner"):
        assert page.locator(sel).first.is_hidden(), sel
    expect(page.locator("[data-testid=amz-no-switch]")).to_contain_text("no Numbers from switch")
    assert "Source: Amazon through ExpandFi" in page.locator("#source").inner_text()
    platform_btn(page, "all").click()
    page.wait_for_selector("[data-testid=totals-strip]")
    assert page.evaluate("location.pathname") == "/" and page.locator("[data-testid=amz-body]").count() == 0
    page.goto("/")                                                                                              # the All overview is as it was: Amazon is in none of its numbers
    page.wait_for_selector("[data-testid=totals-strip]")
    idle(page)
    assert "Amazon" not in page.locator("[data-testid=totals-strip]").inner_text()


def test_the_amazon_tab_is_in_the_performance_section_and_opens_on_30_days(page):
    page.goto("/trends")
    page.wait_for_selector("[data-testid=nav-amazon]")
    assert page.locator("#subnav a.navtab").all_inner_texts().index("Amazon") == page.locator("#subnav a.navtab").all_inner_texts().index("Google") + 1
    page.locator("[data-testid=nav-amazon]").click()
    page.wait_for_selector("[data-testid=amz-body]")
    idle(page)
    assert qparams(page).get("window") == "30d"
    expect(page.locator("#range-label")).to_contain_text("30 days")


# ------------------------------------------------------------------------------------------------------------------------------ overview
def test_overview_tiles_tables_and_trend_tie_to_the_api(page):
    goto(page, "?window=30d")
    page.wait_for_selector("[data-testid=amz-by-product]")
    d = api(page, "/api/amazon/overview?window=30d")
    t = d["tiles"]
    val = lambda k: page.locator(f"[data-testid=amz-kpi-{k}-value]").inner_text()           # noqa: E731
    assert val("revenue") == "${:,}".format(round(t["revenue"])) and val("orders") == f"{t['orders']:,}" and val("ad-spend") == "${:,}".format(round(t["ad_spend"]))
    assert val("ad-return") == "{:.2f}x".format(t["ad_return"]) and val("conversion") == "{:.2f}%".format(t["conversion"] * 100) and val("sessions") == f"{t['sessions']:,}"
    assert page.locator("[data-testid=amz-tiles] .kpi").count() == 10
    assert {r.get_attribute("data-key") for r in rows(page, "amz-by-product").all()} == {"vita-bull", "beef-liver", "boneflex", "vita-belle", "none"} and rows(page, "amz-by-product").count() == 5
    assert rows(page, "amz-by-type").count() == 4
    types = page.locator("[data-testid=amz-by-type] tbody tr[data-key] td:first-child").all_inner_texts()
    assert {x.split("\n")[0] for x in types} == {"Sponsored Products", "Sponsored Brands", "Sponsored Brands video", "Sponsored Display"}
    foot = page.locator("[data-testid=amz-by-product] tfoot td").all_inner_texts()
    assert foot[0] == "Total" and foot[1] == "${:,}".format(round(t["revenue"]))
    assert "—" not in page.locator("body").inner_text()                                                          # no em dashes in page text
    wait_chart(page, "amz-trend-chart")
    c = chart(page, "amz-trend-chart")
    assert len(c["labels"]) == 30 and c["ds"][0]["type"] == "bar" and c["ds"][1]["type"] == "line" and c["ds"][0]["label"] == "Revenue" and c["ds"][1]["label"] == "Ad spend"
    page.screenshot(path=str(SHOTS / "amazon_01_overview_desktop.png"), full_page=True)


@pytest.mark.parametrize("grain,n", [("week", 6), ("month", 2)])
def test_trend_grain_switch_redraws(page, grain, n):
    goto(page, "?window=30d")
    wait_chart(page, "amz-trend-chart")
    page.locator(f"[data-testid=amz-trend] [data-grain={grain}]").click()
    page.wait_for_function("(n) => { const c = window.Chart && Chart.getChart('amz-trend-chart'); return !!c && c.data.labels.length === n; }", arg=n)


def test_ad_figures_are_blank_before_the_ads_data_starts(page):
    goto(page, "?from=2025-11-01&to=2025-11-30")
    assert page.locator("[data-testid=amz-kpi-ad-spend-value]").inner_text() == "–" and page.locator("[data-testid=amz-kpi-ad-return-value]").inner_text() == "–"
    assert page.locator("[data-testid=amz-kpi-revenue-value]").inner_text() != "–"
    page.goto("/amazon?window=launch")
    page.wait_for_selector("[data-testid=amz-tiles]")
    idle(page)
    expect(page.locator("[data-testid=amz-range-note]").first).to_contain_text("Ad figures cover Jan 4, 2026")


# ------------------------------------------------------------------------------------------------------------------------------ campaigns
def test_campaigns_table_filters_search_and_campaign_trend(page):
    goto(page, "?view=campaigns&window=30d")
    page.wait_for_selector("[data-testid=amz-campaigns]")
    d = api(page, "/api/amazon/campaigns?window=30d")
    assert rows(page, "amz-campaigns").count() == len(d["rows"]) == 7
    first = rows(page, "amz-campaigns").first
    assert d["rows"][0]["name"] in first.inner_text() and "Sponsored Products" in first.inner_text()
    head = page.locator("[data-testid=amz-campaigns] thead th").all_inner_texts()
    assert [h.strip().split("\n")[0] for h in head if h.strip()][:3] == ["Campaign", "Attribution", "Ad spend"] and "Cost per order" in " ".join(head) and "Click-through rate" in " ".join(head)
    assert "14-day sales and orders" in page.locator("[data-testid=amz-campaigns]").inner_text() and "as Amazon reports" in page.locator("[data-testid=amz-campaigns]").inner_text()
    page.locator("[data-testid=amz-type-filter] [data-value=sb]").click()
    page.wait_for_function("() => new URLSearchParams(location.search).get('ad_type') === 'sb'")
    idle(page)
    expect(rows(page, "amz-campaigns")).to_have_count(1)
    page.locator("[data-testid=amz-type-filter] [data-value=all]").click()
    page.wait_for_function("() => !new URLSearchParams(location.search).has('ad_type')")
    idle(page)
    page.locator("[data-testid=amz-product-filter] [data-value=boneflex]").click()
    page.wait_for_function("() => new URLSearchParams(location.search).get('product') === 'boneflex'")
    idle(page)
    expect(rows(page, "amz-campaigns")).to_have_count(1)
    page.locator("[data-testid=amz-product-filter] [data-value=all]").click()
    idle(page)
    page.fill("[data-testid=amz-campaign-search]", "retargeting")
    expect(rows(page, "amz-campaigns")).to_have_count(1)
    page.fill("[data-testid=amz-campaign-search]", "")
    expect(rows(page, "amz-campaigns")).to_have_count(7)
    page.locator("[data-testid=amz-campaigns] tbody tr[data-key] a.rowlink").first.click()
    page.wait_for_selector("[data-testid=amz-campaign-trend]")
    wait_chart(page, "amz-campaign-chart")
    assert qparams(page).get("campaign") and chart(page, "amz-campaign-chart")["ds"][0]["label"] == "Ad spend"
    page.screenshot(path=str(SHOTS / "amazon_02_campaigns_desktop.png"), full_page=True)
    page.locator("[data-testid=amz-campaign-close]").click()
    page.wait_for_function("() => !new URLSearchParams(location.search).has('campaign')")


# ------------------------------------------------------------------------------------------------------------------------------ products
def test_products_table_and_the_brands_note(page):
    goto(page, "?view=products&window=30d")
    page.wait_for_selector("[data-testid=amz-products]")
    d = api(page, "/api/amazon/products?window=30d")
    assert rows(page, "amz-products").count() == len(d["rows"]) == 6
    assert "Sponsored Brands spend has no product grain" in page.locator("[data-testid=amz-products-note]").inner_text()
    assert page.locator("[data-testid=amz-products-gap]").count() == 1
    foot = page.locator("[data-testid=amz-products] tfoot td").all_inner_texts()
    assert foot[0] == "Total" and foot[1] == "${:,}".format(round(d["total"]["revenue"]))
    first = rows(page, "amz-products").first.inner_text()
    assert d["rows"][0]["asin"] in first
    page.screenshot(path=str(SHOTS / "amazon_03_products_desktop.png"), full_page=True)


# ------------------------------------------------------------------------------------------------------------------------------ search terms
def test_search_terms_table_note_filters_and_search(page):
    goto(page, "?view=search&window=30d")
    page.wait_for_selector("[data-testid=amz-search-terms]")
    d = api(page, "/api/amazon/search-terms?window=30d")
    assert rows(page, "amz-search-terms").count() == len(d["rows"]) == 20
    note = page.locator("[data-testid=amz-search-note]").inner_text()
    assert "no rank position" in note and "click" in note and "sponsored placements" in note and "top 100" in note and "not zero" in note
    assert "4 full weeks" in page.locator("[data-testid=amz-search-period]").inner_text()
    heads = page.locator("[data-testid=amz-search-terms] thead th").all_inner_texts()
    for want in ("Search term", "Search volume, latest week", "Our impression share", "Our click share", "Our cart-add share", "Our purchase share", "Our clicks", "Our purchases", "Weeks listed"):
        assert any(want in x for x in heads), want
    # default order: biggest search volume first
    top = rows(page, "amz-search-terms").first
    assert d["rows"][0]["term"] in top.inner_text()
    cells = top.locator("td").all_inner_texts()
    assert f"{d['rows'][0]['volume_latest']:,}" in cells[1] and "%" in cells[3] and cells[-1].strip() == "4 of 4"
    page.locator("[data-testid=amz-branded-filter] [data-value=branded]").click()
    page.wait_for_function("() => new URLSearchParams(location.search).get('branded') === 'branded'")
    idle(page)
    expect(rows(page, "amz-search-terms")).to_have_count(5)
    assert all("Branded" in t for t in page.locator("[data-testid=amz-search-terms] tbody tr[data-key] td:first-child").all_inner_texts())
    page.locator("[data-testid=amz-branded-filter] [data-value=all]").click()
    idle(page)
    page.locator("[data-testid=amz-product-filter] [data-value=boneflex]").click()
    page.wait_for_function("() => new URLSearchParams(location.search).get('product') === 'boneflex'")
    idle(page)
    expect(rows(page, "amz-search-terms")).to_have_count(5)
    page.locator("[data-testid=amz-product-filter] [data-value=all]").click()
    idle(page)
    page.fill("[data-testid=amz-search-input]", "bone")
    page.wait_for_function("() => document.querySelectorAll('[data-testid=amz-search-terms] tbody tr[data-key]').length === 2")
    assert qparams(page).get("q") == "bone"
    page.fill("[data-testid=amz-search-input]", "")
    page.wait_for_function("() => document.querySelectorAll('[data-testid=amz-search-terms] tbody tr[data-key]').length === 20")


def test_a_term_opens_its_weekly_history_with_gaps(page):
    goto(page, "?view=search&window=30d")
    page.wait_for_selector("[data-testid=amz-search-terms]")
    page.locator("[data-term='testosterone support']").click()
    page.wait_for_selector("[data-testid=amz-history]")
    wait_chart(page, "amz-history-chart")
    assert qparams(page).get("term") == "testosterone support"
    expect(page.locator("[data-testid=amz-history-title]")).to_contain_text("weekly history")
    c = chart(page, "amz-history-chart")
    assert len(c["labels"]) == 69 and c["ds"][0]["type"] == "bar" and c["ds"][0]["label"] == "Search volume"
    labels = [d["label"] for d in c["ds"][1:]]
    assert any(x.startswith("Vita Bull: purchase share") for x in labels) and any(x.startswith("All products together") for x in labels)
    vb = next(d for d in c["ds"] if d["label"].startswith("Vita Bull"))
    assert any(v is None for v in vb["data"]) and any(v is not None for v in vb["data"])                       # weeks the term was not listed are null (a gap), never 0
    assert 0 not in [v for v in vb["data"] if v is not None and v == 0] and all(v is None or v > 0 for v in vb["data"])
    assert "It is not zero" in page.locator("[data-testid=amz-history]").inner_text()
    page.select_option("#amz-history-metric", "click_share")
    page.wait_for_function("() => Chart.getChart('amz-history-chart').data.datasets.some((d) => /click share/.test(d.label))")
    page.screenshot(path=str(SHOTS / "amazon_04_search_terms_desktop.png"), full_page=True)
    page.locator("[data-testid=amz-history-close]").click()
    expect(page.locator("[data-testid=amz-history]")).to_have_count(0)
    assert "term" not in qparams(page)


def test_search_term_link_with_a_term_opens_the_history_on_load(page):
    goto(page, "?view=search&window=30d&term=vital+bull")
    page.wait_for_selector("[data-testid=amz-history]")
    wait_chart(page, "amz-history-chart")


def test_a_range_with_no_full_week_says_so(page):
    goto(page, "?view=search&from=2026-10-28&to=2026-11-03")
    page.wait_for_selector("[data-testid=amz-search-terms]")
    expect(page.locator("[data-testid=amz-search-period]")).to_contain_text("No full week")
    expect(rows(page, "amz-search-terms")).to_have_count(0)


# ------------------------------------------------------------------------------------------------------------------------------ fees
def test_fees_table_by_month(page):
    goto(page, "?view=fees&from=2026-08-01&to=2026-10-31")
    page.wait_for_selector("[data-testid=amz-fees]")
    d = api(page, "/api/amazon/fees?from=2026-08-01&to=2026-10-31")
    assert rows(page, "amz-fees").count() == len(d["rows"]) == 6
    heads = " ".join(page.locator("[data-testid=amz-fees] thead th").all_inner_texts())
    assert "Aug 2026" in heads and "Sep 2026" in heads and "Oct 2026" in heads and "Total" in heads
    assert "not the Ads report spend" in page.locator("[data-testid=amz-fees-note]").inner_text()
    page.screenshot(path=str(SHOTS / "amazon_05_fees_desktop.png"), full_page=True)


# ------------------------------------------------------------------------------------------------------------------------------ view switch, csv
def test_view_switch_keeps_the_range_and_marks_the_current_view(page):
    goto(page, "?window=60d")
    labels = [a.inner_text() for a in page.locator("[data-testid=amz-views] a.seg").all()]
    assert labels == ["Overview", "Campaigns", "Products", "Search terms", "Fees"]
    page.locator("[data-amz-view=search]").click()
    page.wait_for_selector("[data-testid=amz-search-terms]")
    assert qparams(page).get("view") == "search" and qparams(page).get("window") == "60d"
    expect(page.locator("[data-amz-view=search]")).to_have_attribute("aria-pressed", "true")
    page.locator("[data-amz-view=overview]").click()
    page.wait_for_selector("[data-testid=amz-tiles]")
    assert "view" not in qparams(page)


def test_csv_download_of_the_search_terms(page):
    goto(page, "?view=search&window=30d")
    page.wait_for_selector("[data-testid=amz-search-terms]")
    with page.expect_download() as dl:
        page.locator("[data-testid=amz-csv]").click()
    path = dl.value.path()
    text = open(path, encoding="utf-8").read().splitlines()
    assert dl.value.suggested_filename == "amazon-search-terms.csv" and text[0].startswith("Search term,Branded,Search volume latest week") and len(text) == 21


# ------------------------------------------------------------------------------------------------------------------------------ phone
@pytest.mark.parametrize("qs", ["?window=30d", "?view=campaigns&window=30d", "?view=products&window=30d", "?view=search&window=30d&term=vital+bull", "?view=fees&window=90d"])
def test_no_sideways_page_scroll_at_375px(browser, base_url, qs):
    from conftest import PASSWORD, USERNAME
    c = browser.new_context(viewport={"width": 375, "height": 812}, base_url=base_url)
    try:
        assert c.request.post(base_url + "/api/login", data={"username": USERNAME, "password": PASSWORD}).status == 204
        pg = c.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto("/amazon" + qs)
        pg.wait_for_selector("[data-testid=amz-body] table, [data-testid=amz-body] .kpi")
        pg.wait_for_function("() => !document.querySelector('#view').hasAttribute('aria-busy')")
        pg.wait_for_timeout(400)
        assert no_sideways_scroll(pg), qs
        name = re.sub(r"[^a-z]+", "_", qs.split("view=")[-1].split("&")[0] if "view=" in qs else "overview")
        pg.screenshot(path=str(SHOTS / f"amazon_phone_{name}.png"), full_page=True)
        assert not errs
    finally:
        c.close()


# ------------------------------------------------------------------------------------------------------------------------------ empty state
@pytest.fixture(scope="module")
def empty_url():
    proc, url = start_mock(HERE / "mock_server.py", env={"MOCK_AMAZON_EMPTY": "1"}, what="mock server with no Amazon data")
    yield url
    proc.terminate()


@pytest.mark.parametrize("view", ["", "campaigns", "products", "search", "fees"])
def test_empty_amazon_tables_show_the_empty_state(browser, empty_url, view):
    from conftest import PASSWORD, USERNAME
    c = browser.new_context(viewport={"width": 1440, "height": 900}, base_url=empty_url)
    try:
        assert c.request.post(empty_url + "/api/login", data={"username": USERNAME, "password": PASSWORD}).status == 204
        pg = c.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
        pg.goto("/amazon?window=30d" + (f"&view={view}" if view else ""))
        pg.wait_for_selector("[data-testid=amz-empty]")
        expect(pg.locator("[data-testid=amz-empty]")).to_contain_text("Amazon data has not loaded yet")
        assert pg.locator(".errorbox").count() == 0 and not errs, errs
        if not view:
            assert platform_btn(pg, "amazon").is_enabled()                                                       # you can be on the page; the picker only disables it elsewhere
            pg.screenshot(path=str(SHOTS / "amazon_06_empty.png"))
    finally:
        c.close()


def test_the_picker_disables_amazon_elsewhere_until_data_loads(browser, empty_url):
    from conftest import PASSWORD, USERNAME
    c = browser.new_context(viewport={"width": 1440, "height": 900}, base_url=empty_url)
    try:
        assert c.request.post(empty_url + "/api/login", data={"username": USERNAME, "password": PASSWORD}).status == 204
        pg = c.new_page()
        pg.goto("/")
        pg.wait_for_selector("#platform-ctl")
        pg.wait_for_function("() => document.querySelector('#platform-ctl [data-platform=amazon]').title.includes('has not loaded')")
        assert platform_btn(pg, "amazon").is_disabled()
    finally:
        c.close()
