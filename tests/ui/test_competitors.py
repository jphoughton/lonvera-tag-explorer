"""Competitors page (/competitors) against the mock (tests/ui/mock_competitors.py): one sortable table of brands, a detail panel on click, a week picker, the All / Video / Image toggle kept in the
URL, graceful first-snapshot week, the optional Chatter section, no sideways scroll at 375 px, no em dashes."""
import pytest

pytestmark = pytest.mark.mock_only


def idle(page):
    page.wait_for_function("() => !document.querySelector('#view').hasAttribute('aria-busy')")


def goto(page, qs=""):
    page.goto("/competitors" + qs)
    page.wait_for_selector("[data-testid=comp-page]")
    idle(page)


def brands(page):
    return page.locator("[data-testid=comp-row]").evaluate_all("rs => rs.map(r => r.dataset.brand)")


def cell(page, brand, col):
    return page.locator(f"[data-testid=comp-row][data-brand='{brand}'] [data-col={col}]").text_content().strip()


def test_table_facts_and_no_em_dash(page):
    goto(page)
    assert page.locator("h1").text_content() == "Competitors"
    assert set(brands(page)) == {"Acme", "Bolt & Co", "Cedar"}
    assert cell(page, "Acme", "active_ads") == "3" and cell(page, "Acme", "new_ads") == "2" and cell(page, "Acme", "stopped_ads") == "2"
    assert "Stale" in page.locator("[data-testid=comp-row][data-brand='Bolt & Co'] [data-testid=comp-status]").text_content()
    facts = page.locator("[data-testid=comp-fact]").all_text_contents()
    assert facts and any("Marrow for joints" in f for f in facts) and any("could not be read" in f for f in facts)
    assert "—" not in page.locator("[data-testid=comp-page]").text_content()
    assert "carousels count as image" in page.locator("[data-testid=comp-note]").text_content()


def test_sort_by_column(page):
    goto(page)
    assert brands(page)[0] == "Acme"                                     # default: most active ads first
    page.click("[data-testid=comp-sort-brand]")
    assert brands(page) == ["Acme", "Bolt & Co", "Cedar"]
    page.click("[data-testid=comp-sort-brand]")
    assert brands(page) == ["Cedar", "Bolt & Co", "Acme"]
    page.click("[data-testid=comp-sort-max_days_running]")
    assert brands(page)[0] == "Acme"


def test_row_opens_detail_with_links(page):
    goto(page)
    page.click("[data-testid=comp-row][data-brand=Acme] [data-testid=comp-name]")
    page.wait_for_selector("[data-brand=Acme] [data-testid=comp-longest]")
    panel = page.locator("tr.comp-detailrow[data-brand=Acme]")
    assert panel.is_visible()
    hrefs = panel.locator("[data-testid=comp-adlink]").evaluate_all("as => as.map(a => a.href)")
    assert "https://www.facebook.com/ads/library/?id=a1" in hrefs
    assert panel.locator("[data-testid=comp-adlink]").first.get_attribute("rel") == "noopener noreferrer"
    assert "Marrow for joints" in panel.locator("[data-testid=comp-angles]").text_content()
    assert "acme.com/new" in panel.locator("[data-testid=comp-pages]").text_content()
    assert panel.locator("[data-testid=comp-new] tbody tr").count() == 2
    assert "q=acme" in panel.locator("[data-testid=comp-brandlink]").get_attribute("href")
    page.click("[data-testid=comp-row][data-brand=Acme] [data-testid=comp-name]")
    assert not panel.is_visible()


def test_format_toggle_filters_everything_and_lives_in_the_url(page):
    goto(page)
    page.click("[data-testid=comp-format-video]")
    page.wait_for_function("() => document.querySelector(\"[data-testid=comp-row][data-brand=Acme] [data-col=active_ads]\").textContent.trim() === '2'")
    assert "format=video" in page.url
    assert cell(page, "Bolt & Co", "active_ads") == "0" and page.locator("[data-testid=comp-format-video]").get_attribute("aria-pressed") == "true"
    page.click("[data-testid=comp-row][data-brand=Acme] [data-testid=comp-name]")
    page.wait_for_selector("[data-brand=Acme] [data-testid=comp-longest]")
    fmts = page.locator("tr.comp-detailrow[data-brand=Acme] [data-testid=comp-longest] tbody tr td:nth-child(3)").all_text_contents()
    assert fmts and set(fmts) == {"video"}
    goto(page, "?format=image")                                          # reload from the URL
    assert page.locator("[data-testid=comp-format-image]").get_attribute("aria-pressed") == "true" and cell(page, "Acme", "active_ads") == "1"
    page.click("[data-testid=comp-format-all]")
    page.wait_for_function("() => document.querySelector(\"[data-testid=comp-row][data-brand=Acme] [data-col=active_ads]\").textContent.trim() === '3'")
    assert "format=video" not in page.url and "format=image" not in page.url


def test_first_snapshot_week(page):
    goto(page)
    page.select_option("[data-testid=comp-week]", "2026-11-09")
    page.wait_for_selector("[data-testid=comp-notice]")
    assert "First snapshot" in page.locator("[data-testid=comp-notice]").text_content()
    assert cell(page, "Acme", "change") == "–" and cell(page, "Acme", "new_ads") == "–"
    page.click("[data-testid=comp-row][data-brand=Acme] [data-testid=comp-name]")
    page.wait_for_selector("[data-testid=comp-first]")
    assert "week=2026-11-09" in page.url


def test_chatter_only_when_present(page, ctx, base_url):
    ctx.request.post(base_url + "/__test/competitors/chatter?on=0")
    goto(page)
    assert page.locator("[data-testid=comp-chatter-sec]").count() == 0
    ctx.request.post(base_url + "/__test/competitors/chatter?on=1")
    try:
        goto(page)
        assert "Is Acme worth it?" in page.locator("[data-testid=comp-chatter]").text_content()
    finally:
        ctx.request.post(base_url + "/__test/competitors/chatter?on=0")


def test_phone_width_does_not_scroll_sideways(page):
    page.set_viewport_size({"width": 375, "height": 800})
    goto(page)
    page.click("[data-testid=comp-row][data-brand=Acme] [data-testid=comp-name]")
    page.wait_for_selector("[data-brand=Acme] [data-testid=comp-longest]")
    assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth")
    assert page.evaluate("() => { const s = document.querySelector('[data-testid=comp-scroll]'); return s.scrollWidth > s.clientWidth; }")      # the wide table scrolls inside its own box


def test_nav_has_competitors_tab(page):
    goto(page)
    assert page.locator("[data-testid=nav-competitors]").count() >= 1
