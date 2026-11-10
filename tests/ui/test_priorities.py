"""Creative priorities (/priorities) in the browser, against the mock server (tests/ui/mock_priorities.py: a trimmed real weekly snapshot, 30 concepts).

One table, one row per concept, ALL COLLAPSED on load; the chevron button (or a click on the row) opens the detail panel under it; Product / Section filters, a text search, click-to-sort headers,
Expand all / Collapse all on the filtered rows; print CSS opens every panel and puts one product per page.
"""
import pytest

from conftest import SHOTS

pytestmark = pytest.mark.mock_only

ROW = "[data-testid=pri-card]"
DETAIL = "[data-testid=pri-detail]"
SEC = ["faded", "iterate", "new", "proposal", "competitor", "stop"]


def idle(page):
    page.wait_for_function("() => !document.querySelector('#view').hasAttribute('aria-busy')")


def goto(page, qs=""):
    page.goto("/priorities" + qs)
    page.wait_for_selector("[data-testid=pri-page], [data-testid=pri-empty]")
    idle(page)


def visible_rows(page):
    return page.locator("[data-testid=pri-body] " + ROW).count()


def test_everything_is_collapsed_on_load_and_the_detail_panels_are_hidden(page):
    goto(page)
    assert page.locator("[data-testid=pri-body] tr.pri-row").count() == 30
    toggles = page.locator("[data-testid=pri-body] [data-testid=pri-toggle]")
    assert toggles.count() == 30 and all(t.get_attribute("aria-expanded") == "false" for t in toggles.all())
    assert page.locator("[data-testid=pri-body] " + DETAIL + ":visible").count() == 0
    assert page.locator("[data-testid=pri-body] " + DETAIL + "[hidden]").count() == 30
    assert page.locator("[data-testid=pri-shown]").text_content() == "30 of 30 concepts shown"
    heads = [t.strip() for t in page.locator("[data-testid=pri-table] thead th").all_text_contents()]
    assert [h.replace(" ⇅", "") for h in heads[1:]] == ["Product", "Section", "Market Cluster", "ICP", "Pain Point", "Story", "Status", "Concepts", "Key number"]
    page.screenshot(path=str(SHOTS / "priorities_collapsed.png"), full_page=False)


def test_default_order_is_section_first_then_product_then_rank_and_the_story_is_not_truncated(page):
    goto(page)
    secs = page.locator("[data-testid=pri-body] tr.pri-row").evaluate_all("rows => rows.map(r => r.dataset.section)")
    assert secs == sorted(secs, key=SEC.index) and secs[0] == "faded" and secs[-1] == "stop"
    first_story = page.locator("[data-testid=pri-body] tr.pri-row .pri-c-story").first
    clipped = first_story.evaluate("e => e.scrollWidth > e.clientWidth + 1 || getComputedStyle(e).textOverflow === 'ellipsis'")
    assert not clipped and len(first_story.text_content()) > 20


def test_legend_of_the_four_tags_stays_at_the_top(page):
    goto(page)
    assert page.locator("[data-testid=pri-legend] dt").all_text_contents() == ["Market Cluster", "ICP", "Pain Point", "Story"]


def test_chevron_button_and_row_click_open_and_close_the_panel_with_the_detail(page):
    goto(page)
    btn = page.locator("[data-testid=pri-body] [data-testid=pri-toggle]").first
    btn.click()
    assert btn.get_attribute("aria-expanded") == "true" and btn.get_attribute("aria-label").startswith("Collapse:")
    panel = page.locator("[data-testid=pri-body] " + DETAIL).first
    assert panel.is_visible()
    txt = panel.text_content()
    assert "When it worked" in txt and "Revive play" in txt and "Diagnosis (consistent with)" in txt                  # the first row is a was-working-now-isn't row
    page.screenshot(path=str(SHOTS / "priorities_expanded.png"), full_page=False)
    btn.click()
    assert btn.get_attribute("aria-expanded") == "false" and not panel.is_visible()
    page.locator("[data-testid=pri-body] " + ROW).nth(2).locator(".pri-c-story").click()                             # clicking the row itself
    assert page.locator("[data-testid=pri-body] " + DETAIL + ":visible").count() == 1
    page.keyboard.press("Tab")


def test_toggle_is_keyboard_operable_a_real_button(page):
    goto(page)
    btn = page.locator("[data-testid=pri-body] [data-testid=pri-toggle]").first
    assert btn.evaluate("e => e.tagName") == "BUTTON" and btn.get_attribute("aria-controls")
    btn.focus()
    page.keyboard.press("Enter")
    assert btn.get_attribute("aria-expanded") == "true"
    page.keyboard.press("Space")
    assert btn.get_attribute("aria-expanded") == "false"


def test_expand_all_and_collapse_all_act_on_the_filtered_rows(page):
    goto(page)
    page.locator("[data-testid=pri-f-section-stop]").click()                                                         # drop Stop
    n = visible_rows(page)
    assert 0 < n < 30
    page.locator("[data-testid=pri-all-expand]").click()
    assert page.locator("[data-testid=pri-body] " + DETAIL + ":visible").count() == n
    page.locator("[data-testid=pri-f-section-stop]").click()                                                         # bring Stop back: those rows were never expanded
    assert page.locator("[data-testid=pri-body] " + DETAIL + ":visible").count() == n and visible_rows(page) == 30
    page.locator("[data-testid=pri-all-collapse]").click()
    assert page.locator("[data-testid=pri-body] " + DETAIL + ":visible").count() == 0


def test_product_and_section_filters_are_multi_select_and_search_matches_the_four_tag_fields(page):
    goto(page)
    page.locator("[data-testid=pri-f-product-boneflex]").click()
    page.locator("[data-testid=pri-f-product-beef-liver]").click()
    products = set(page.locator("[data-testid=pri-body] .pri-c-product").all_text_contents())
    assert products == {"Vita Bull"}
    page.locator("[data-testid=pri-f-product-boneflex]").click()
    assert set(page.locator("[data-testid=pri-body] .pri-c-product").all_text_contents()) == {"Vita Bull", "Boneflex"}
    assert page.locator("[data-testid=pri-f-product-boneflex]").get_attribute("aria-pressed") == "true"
    word = page.locator("[data-testid=pri-body] .pri-c-icp").first.text_content().split()[0]
    page.locator("[data-testid=pri-search]").fill(word)
    rows = page.locator("[data-testid=pri-body] tr.pri-row")
    assert 0 < rows.count() <= 30
    assert all(word.lower() in " ".join(r.locator(".pri-c-cluster, .pri-c-icp, .pri-c-pain, .pri-c-story").all_text_contents()).lower() for r in rows.all())
    page.locator("[data-testid=pri-search]").fill("zzzz no such text")
    assert visible_rows(page) == 0 and page.locator("[data-testid=pri-shown]").text_content().startswith("0 of 30")


def test_click_to_sort_toggles_direction_and_sets_aria_sort(page):
    goto(page)
    th = page.locator("[data-testid=pri-sort-cluster]").locator("xpath=ancestor::th")
    page.locator("[data-testid=pri-sort-cluster]").click()
    asc = page.locator("[data-testid=pri-body] .pri-c-cluster").all_text_contents()
    assert asc == sorted(asc, key=str.lower) and th.get_attribute("aria-sort") == "ascending"
    page.locator("[data-testid=pri-sort-cluster]").click()
    desc = page.locator("[data-testid=pri-body] .pri-c-cluster").all_text_contents()
    assert desc == sorted(desc, key=str.lower, reverse=True) and th.get_attribute("aria-sort") == "descending"


def test_open_state_survives_sorting_and_nothing_is_remembered_across_reloads(page):
    goto(page)
    page.locator("[data-testid=pri-body] [data-testid=pri-toggle]").first.click()
    page.locator("[data-testid=pri-sort-pain]").click()
    assert page.locator("[data-testid=pri-body] " + DETAIL + ":visible").count() == 1
    page.reload()
    page.wait_for_selector("[data-testid=pri-page]")
    idle(page)
    assert page.locator("[data-testid=pri-body] " + DETAIL + ":visible").count() == 0


def test_print_css_shows_every_panel_one_product_per_page_with_the_section_column(page):
    goto(page)
    page.emulate_media(media="print")
    assert page.locator("[data-testid=pri-print-copy]").is_visible() and not page.locator("[data-testid=pri-table]").is_visible()
    blocks = page.locator("[data-testid=pri-print-copy] section.pri-product")
    assert blocks.count() == 3
    assert all(b.evaluate("e => getComputedStyle(e).breakBefore") in ("page", "auto") for b in blocks.all())
    assert blocks.nth(1).evaluate("e => getComputedStyle(e).breakBefore") == "page"
    details = page.locator("[data-testid=pri-print-copy] " + DETAIL)
    assert details.count() == 30 and all(d.is_visible() for d in details.all())                                          # every panel open in print
    heads = [t.strip().replace(" ⇅", "") for t in page.locator("[data-testid=pri-print-copy] table").first.locator("thead th").all_text_contents()]
    assert "Section" in heads and "Product" not in heads
    assert page.locator(".pri-bar").is_hidden()
    page.screenshot(path=str(SHOTS / "priorities_print.png"), full_page=False)


def test_screen_detail_is_hidden_by_default_in_the_stylesheet_but_shown_by_the_print_rule(page):
    goto(page)
    css = page.evaluate("() => [...document.styleSheets].flatMap(s => { try { return [...s.cssRules]; } catch (e) { return []; } }).map(r => r.cssText).join('\\n')")
    assert ".pri-detail[hidden]" in css and "display: table-row" in css and "pri-print" in css


def test_stacked_layout_at_375px_has_no_sideways_scroll(browser, base_url):
    c = browser.new_context(viewport={"width": 375, "height": 812}, base_url=base_url)
    c.request.post(base_url + "/api/login", data={"username": "JoshHoughton", "password": "test"})
    p = c.new_page()
    p.goto("/priorities")
    p.wait_for_selector("[data-testid=pri-page]")
    idle(p)
    assert not p.evaluate("() => document.documentElement.scrollWidth > innerWidth")
    p.locator("[data-testid=pri-body] [data-testid=pri-toggle]").first.click()
    assert not p.evaluate("() => document.documentElement.scrollWidth > innerWidth")
    assert p.locator("[data-testid=pri-body] tr.pri-row").first.evaluate("e => getComputedStyle(e).display") == "block"
    p.screenshot(path=str(SHOTS / "priorities_375.png"))
    c.close()
