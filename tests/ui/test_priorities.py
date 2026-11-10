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


def test_print_is_a_compact_one_sheeter_not_a_dump_of_every_panel(page):
    goto(page)
    page.emulate_media(media="print")
    assert page.locator("[data-testid=pri-print-copy]").is_visible() and not page.locator("[data-testid=pri-table]").is_visible()
    assert page.locator(".pri-bar").is_hidden() and page.locator("[data-testid=pri-search]").is_hidden()
    blocks = page.locator("[data-testid=pri-print-copy] section.pri-product")
    assert blocks.count() == 3
    assert all(b.evaluate("e => getComputedStyle(e).breakBefore") == "page" for b in blocks.all())                    # every product, the first too, starts a fresh page (the intro is page 1)
    assert page.locator("[data-testid=pri-print-copy] .pri-detail").count() == 0                                       # no full detail panels in print
    tab = page.locator("[data-testid=pri-print-copy] table").first
    assert tab.evaluate("e => getComputedStyle(e).tableLayout") == "fixed"
    assert tab.locator("thead").evaluate("e => getComputedStyle(e).display") == "table-header-group"                   # the header repeats on every page
    heads = [t.strip() for t in tab.locator("thead th").all_text_contents()]
    assert heads == ["Section", "Market Cluster", "ICP", "Pain Point", "Story", "Status", "To brief", "Key number"]
    groups = page.locator("[data-testid=pri-print-row]")
    assert groups.count() > 0 and all(g.evaluate("e => getComputedStyle(e).breakInside") == "avoid" for g in groups.all())      # a row and its strip never split
    # only rows with concepts to brief are printed; the Stop list is plain lines
    assert all(c.strip() not in ("\u2013", "") for c in page.locator("[data-testid=pri-print-copy] .pri-c-concepts").all_text_contents())
    assert page.locator("[data-testid=pri-stoplist] li").count() > 0 and page.locator("[data-testid=pri-print-copy] .pri-sec-stop").count() == 0
    strips = page.locator("[data-testid=pri-print-copy] .pri-strip td").all_text_contents()
    assert strips and all(len(s) <= 700 for s in strips)
    page.screenshot(path=str(SHOTS / "priorities_print.png"), full_page=False)


def test_real_pdf_is_short_and_every_product_starts_a_page(page):
    import re
    goto(page)
    page.emulate_media(media="print")
    pdf = page.pdf(prefer_css_page_size=True, print_background=True)
    pages = len(re.findall(rb"/Type\s*/Page(?!s)", pdf))
    assert 4 <= pages <= 8, pages                                                                                       # intro + 3 products, a few pages each


def test_screen_detail_is_hidden_by_default_in_the_stylesheet_and_print_has_no_panel_dump(page):
    goto(page)
    css = page.evaluate("() => [...document.styleSheets].flatMap(s => { try { return [...s.cssRules]; } catch (e) { return []; } }).map(r => r.cssText).join('\\n')")
    assert ".pri-detail[hidden]" in css and "table-header-group" in css and "pri-print" in css


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


# ---------------------------------------------------------------------------------------------------------------- row selection

import csv  # noqa: E402
import io  # noqa: E402
import json  # noqa: E402

PICK = "[data-testid=pri-body] [data-testid=pri-pick]"
TOOLS = "[data-testid=pri-seltools]"


def pick_rows(page, *idx):
    for i in idx:
        page.locator(PICK).nth(i).check()


def csv_of(download):
    return list(csv.reader(io.StringIO(open(download.path(), encoding="utf-8").read())))


def test_selection_toolbar_counts_and_a_checkbox_click_does_not_expand_the_row(page):
    goto(page)
    assert page.locator(TOOLS).is_hidden()
    pick_rows(page, 0, 3)
    assert page.locator(TOOLS).is_visible() and page.locator("[data-testid=pri-sel-count]").text_content() == "2 selected"
    assert page.locator("[data-testid=pri-body] " + DETAIL + ":visible").count() == 0                              # ticking a box never toggles the row
    assert page.locator(PICK).nth(0).locator("xpath=ancestor::tr").evaluate("e => e.classList.contains('pri-open')") is False
    page.locator(PICK).nth(0).uncheck()
    assert page.locator("[data-testid=pri-sel-count]").text_content() == "1 selected"


def test_select_all_visible_respects_filters_and_selection_survives_filter_sort_and_expand(page):
    goto(page)
    page.locator("[data-testid=pri-f-product-boneflex]").click()
    page.locator("[data-testid=pri-f-product-beef-liver]").click()
    n = visible_rows(page)
    page.locator("[data-testid=pri-pick-all]").check()
    assert page.locator("[data-testid=pri-sel-count]").text_content() == f"{n} selected"
    page.locator("[data-testid=pri-f-product-boneflex]").click()                                                     # more rows visible: the header box is now partial
    page.locator("[data-testid=pri-f-product-beef-liver]").click()
    assert visible_rows(page) == 30 and page.locator("[data-testid=pri-sel-count]").text_content() == f"{n} selected"
    assert page.locator("[data-testid=pri-pick-all]").evaluate("e => e.indeterminate") is True
    page.locator("[data-testid=pri-sort-pain]").click()
    page.locator("[data-testid=pri-all-expand]").click()
    assert page.locator(PICK + ":checked").count() == n
    page.locator("[data-testid=pri-pick-all]").check()                                                               # all 30 visible now
    assert page.locator("[data-testid=pri-sel-count]").text_content() == "30 selected"
    page.locator("[data-testid=pri-pick-all]").uncheck()
    assert page.locator(TOOLS).is_hidden()


def test_selected_only_csv_has_the_same_columns_and_only_those_rows(page):
    goto(page)
    with page.expect_download() as dl:
        page.locator("[data-testid=pri-csv]").click()
    full = csv_of(dl.value)
    assert len(full) == 31
    pick_rows(page, 0, 4, 9)
    names = [page.locator(PICK).nth(i).locator("xpath=ancestor::tr").locator(".pri-c-pain").text_content() for i in (0, 4, 9)]
    with page.expect_download() as dl:
        page.locator("[data-testid=pri-sel-csv]").click()
    sel = csv_of(dl.value)
    assert sel[0] == full[0] and len(sel) == 4
    assert sorted(r[2] for r in sel[1:]) == sorted(names)
    assert all(r in full for r in sel[1:])
    page.locator("[data-testid=pri-sel-clear]").click()                                                             # nothing selected: the top button exports everything again
    with page.expect_download() as dl:
        page.locator("[data-testid=pri-csv]").click()
    assert len(csv_of(dl.value)) == 31


def test_print_layout_shows_only_selected_rows_and_omits_empty_products(page):
    goto(page)
    first_vb = page.locator("[data-testid=pri-body] tr.pri-row[data-product=vita-bull]").first
    idx = page.locator("[data-testid=pri-body] tr.pri-row").evaluate_all("rows => rows.findIndex(r => r.dataset.product === 'vita-bull')")
    pick_rows(page, idx)
    pain = first_vb.locator(".pri-c-pain").text_content()
    page.emulate_media(media="print")
    assert page.locator("[data-testid=pri-print-copy] section.pri-product").count() == 1                              # the other two products have no selected row
    assert page.locator("[data-testid=pri-print-copy] [data-testid=pri-print-row]").count() == 1
    assert pain in page.locator("[data-testid=pri-print-copy] [data-testid=pri-print-row]").text_content()
    assert page.locator("[data-testid=pri-print-copy] .pri-strip").count() == 1
    assert page.locator(TOOLS).is_hidden()
    page.emulate_media(media="screen")
    page.locator("[data-testid=pri-sel-clear]").click()
    page.emulate_media(media="print")
    assert page.locator("[data-testid=pri-print-copy] section.pri-product").count() == 3


def test_copy_as_text_has_one_block_per_row(page):
    goto(page)
    page.context.grant_permissions(["clipboard-read", "clipboard-write"])
    pick_rows(page, 0, 1)
    page.locator("[data-testid=pri-sel-copy]").click()
    page.wait_for_function("() => document.querySelector('[data-testid=pri-copy-note]').textContent.startsWith('Copied')")
    text = page.evaluate("() => navigator.clipboard.readText()")
    blocks = text.split("\n\n")
    assert len(blocks) == 2
    for b in blocks:
        for label in ("Product: ", "Section: ", "Market Cluster: ", "ICP: ", "Pain Point: ", "Story: ", "Status: ", "Key number: "):
            assert label in b


def test_clear_empties_the_selection_and_persistence_survives_a_reload(page):
    goto(page)
    pick_rows(page, 2, 5)
    page.reload()
    page.wait_for_selector("[data-testid=pri-page]")
    idle(page)
    assert page.locator("[data-testid=pri-sel-count]").text_content() == "2 selected"
    assert [page.locator(PICK).nth(i).is_checked() for i in (2, 5)] == [True, True]
    page.locator("[data-testid=pri-sel-clear]").click()
    assert page.locator(TOOLS).is_hidden() and page.locator(PICK + ":checked").count() == 0
    page.reload()
    page.wait_for_selector("[data-testid=pri-page]")
    idle(page)
    assert page.locator(TOOLS).is_hidden()


def test_selection_toolbar_at_375px_has_no_sideways_scroll(browser, base_url):
    c = browser.new_context(viewport={"width": 375, "height": 812}, base_url=base_url)
    c.request.post(base_url + "/api/login", data={"username": "JoshHoughton", "password": "test"})
    p = c.new_page()
    p.goto("/priorities")
    p.wait_for_selector("[data-testid=pri-page]")
    idle(p)
    p.locator(PICK).nth(0).check()
    p.locator("[data-testid=pri-view-name]").fill("a fairly long view name for a phone")
    assert p.locator(TOOLS).is_visible()
    assert not p.evaluate("() => document.documentElement.scrollWidth > innerWidth")
    p.locator("[data-testid=pri-sel-save]").click()
    p.wait_for_selector("[data-testid=pri-view-link]")
    assert not p.evaluate("() => document.documentElement.scrollWidth > innerWidth")
    p.screenshot(path=str(SHOTS / "priorities_select_375.png"))
    c.close()


# ---------------------------------------------------------------------------------------------------------------- saved views

def save_view(page, name, *idx):
    pick_rows(page, *idx)
    page.locator("[data-testid=pri-view-name]").fill(name)
    page.locator("[data-testid=pri-sel-save]").click()
    page.wait_for_selector("[data-testid=pri-view-link]")
    return page.locator("[data-testid=pri-view-link]").get_attribute("href")


def test_save_view_open_link_shows_only_those_rows_read_only_with_csv_and_print(page, browser, base_url):
    goto(page)
    pains = [page.locator(PICK).nth(i).locator("xpath=ancestor::tr").locator(".pri-c-pain").text_content() for i in (1, 6, 7)]
    url = save_view(page, "Round 1 briefs", 1, 6, 7)
    assert url.startswith("/priorities/v/") and len(url.rsplit("/", 1)[1]) >= 22
    assert "3 concepts" in page.locator("[data-testid=pri-linkbox]").text_content() and page.locator("[data-testid=pri-linkbox] [data-testid=pri-copy-link]").is_visible()
    c = browser.new_context(viewport={"width": 1440, "height": 900}, base_url=base_url)                                # a fresh context: no selection, no sessionStorage
    c.request.post(base_url + "/api/login", data={"username": "JoshHoughton", "password": "test"})
    v = c.new_page()
    v.goto(url)
    v.wait_for_selector("[data-testid=pri-page]")
    idle(v)
    assert "Round 1 briefs" in v.locator("[data-testid=pri-title]").text_content()
    assert "snapshot week of" in v.locator("[data-testid=pri-view-note]").text_content()
    assert v.locator("[data-testid=pri-body] tr.pri-row").count() == 3
    assert sorted(v.locator("[data-testid=pri-body] .pri-c-pain").all_text_contents()) == sorted(pains)
    assert v.locator(PICK).count() == 0 and v.locator(TOOLS).count() == 0 and v.locator("[data-testid=pri-search]").count() == 0 and v.locator("[data-testid=pri-week]").count() == 0
    assert v.locator("[data-testid=pri-open-full]").get_attribute("href") == "/priorities"
    with v.expect_download() as dl:
        v.locator("[data-testid=pri-csv]").click()
    assert len(csv_of(dl.value)) == 4
    v.emulate_media(media="print")
    assert v.locator("[data-testid=pri-print-copy] [data-testid=pri-print-row]").count() == 3
    v.close()
    c.close()


@pytest.mark.allow_errors
def test_unknown_view_token_is_a_friendly_404_page(page):
    page.goto("/priorities/v/" + "x" * 30)
    page.wait_for_selector("[data-testid=pri-empty]")
    assert "does not exist" in page.locator("[data-testid=pri-empty]").text_content()


def test_saved_views_list_reopens_and_deletes(page):
    goto(page)
    save_view(page, "Delete me", 0)
    page.locator("[data-testid=pri-views] summary").click()
    row = page.locator("[data-testid=pri-view-row]", has_text="Delete me").first
    row.wait_for()
    assert "1 concept" in row.text_content() and "snapshot" in row.text_content()
    href = row.locator("a").first.get_attribute("href")
    page.once("dialog", lambda dlg: dlg.accept())
    row.locator("[data-testid=pri-view-delete]").click()
    page.wait_for_function("(h) => ![...document.querySelectorAll('[data-testid=pri-view-row] a')].some(a => a.getAttribute('href') === h)", arg=href)
    r = page.request.get(page.url.split("/priorities")[0] + "/api/priorities/views/" + href.rsplit("/", 1)[1])
    assert r.status == 404
