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


def test_save_view_name_is_prefilled_free_text_and_becomes_title_tab_title_and_csv_filename(page):
    import re
    goto(page)
    nm = page.locator("[data-testid=pri-view-name]")
    assert re.fullmatch(r"Priorities - \d{1,2}/\d{1,2}/\d{2}", nm.input_value())
    pick_rows(page, 0, 1)
    nm.fill("  Madre - 11/10/26 Priorities  ")
    page.locator("[data-testid=pri-sel-save]").click()
    page.wait_for_selector("[data-testid=pri-view-link]")
    url = page.locator("[data-testid=pri-view-link]").get_attribute("href")
    page.goto(url)
    page.wait_for_selector("[data-testid=pri-title]")
    idle(page)
    assert page.locator("[data-testid=pri-title]").text_content() == "Creative priorities: Madre - 11/10/26 Priorities"
    assert page.title().startswith("Madre - 11/10/26 Priorities")
    with page.expect_download() as dl:
        page.locator("[data-testid=pri-csv]").click()
    assert dl.value.suggested_filename == "madre-11-10-26-priorities.csv"
    # rename from the view page: same link, new title
    page.once("dialog", lambda d: d.accept("Round 2 / <b>x</b>"))
    page.locator("[data-testid=pri-rename]").click()
    page.wait_for_function("() => document.querySelector('[data-testid=pri-title]').textContent.includes('Round 2')")
    assert page.title().startswith("Round 2 / <b>x</b>") and page.url.endswith(url)
    page.reload()
    page.wait_for_selector("[data-testid=pri-title]")
    assert page.locator("[data-testid=pri-title]").text_content() == "Creative priorities: Round 2 / <b>x</b>" and page.locator("[data-testid=pri-title] b").count() == 0


def test_empty_name_gets_the_default_and_rename_from_the_dropdown(page):
    import re
    goto(page)
    pick_rows(page, 2)
    page.locator("[data-testid=pri-view-name]").fill("   ")
    page.locator("[data-testid=pri-sel-save]").click()
    page.wait_for_selector("[data-testid=pri-view-link]")
    url = page.locator("[data-testid=pri-view-link]").get_attribute("href")
    page.locator("[data-testid=pri-views] summary").click()
    row = page.locator("[data-testid=pri-view-row]").filter(has=page.locator(f"a[href='{url}']"))
    row.wait_for()
    assert re.match(r"Priorities - \d{1,2}/\d{1,2}/\d{2}", row.locator("a").first.text_content())
    page.once("dialog", lambda d: d.accept("Renamed - 1/2/27"))
    row.locator("[data-testid=pri-view-rename]").click()
    page.wait_for_function("(u) => { const a = document.querySelector(`[data-testid=pri-view-row] a[href='${u}']`); return a && a.textContent === 'Renamed - 1/2/27'; }", arg=url)

# ---------------------------------------------------------------------------------------------------------------- redesigned detail panel

FIXTURE = json.loads((__import__("pathlib").Path(__file__).resolve().parent / "priorities_fixture.json").read_text())


def _money(v):
    return "$" + format(int(v + 0.5), ",")


def _roas(v):
    return f"{v:.2f}x"


def open_all(page):
    page.get_by_role("button", name="Expand all").first.click()


def test_every_fact_is_still_in_every_expanded_panel(page):
    """the redesign moves and prioritises facts; nothing may be dropped: compare each panel's text with its snapshot row"""
    import re
    goto(page)
    open_all(page)
    panels = page.locator("[data-testid=pri-body] " + DETAIL)
    assert panels.count() == len(FIXTURE["rows"])
    rows = page.locator("[data-testid=pri-body] " + ROW)
    norm = lambda t: re.sub(r"\s+", " ", t.replace("×", "x"))  # noqa: E731
    for i in range(rows.count()):
        key = rows.nth(i).locator(".pri-c-pain").text_content().strip()
        txt = norm(panels.nth(i).text_content())
        r = next(x for x in FIXTURE["rows"] if x["pain_point"] == key and x["cluster"] in rows.nth(i).locator(".pri-c-cluster").text_content())
        for f in (r["cluster"], r["icp"], r["pain_point"], r["story"], r["status"], r["then_now"], r.get("diagnosis") or "", r.get("revive_play") or "", r.get("when_it_worked") or "", r.get("how_it_stopped") or "",
                  r["cluster_description"], r["icp_description"], r["pain_description"], f"Line {r['line_code']}", f"Confidence: {r['confidence']}", f"Score {r['score']:g}"):
            assert norm(f) in txt, (r["section"], r["pain_point"], f)
        for fl in r["flags"]:
            assert fl in txt
        if r["section"] in ("iterate", "faded", "stop"):
            m = r["metrics"]
            for label in ("Spend", "ROAS", "CPA", "Upstack new-customer ROAS", "ROAS, strong tags only", "Ads tested", "Hit rate", "New ads, 30 days", "Last 30 days", "Prior 30 days", "Live ads"):
                assert label.lower() in txt.lower(), (label, r["pain_point"])
            assert _money(m["spend"]) in txt and f"{m['hits']}/{m['mature']}" in txt and _money(m["spend_30d"]) in txt and _money(m["spend_prior30"]) in txt
        else:
            assert "Ads tagged" in txt or r["section"] == "proposal" or r["section"] == "competitor"
        for w in r.get("former_winners") or []:
            assert norm(w["ad"]) in txt and _money(w["spend"]) in txt and _roas(w["roas"]) in txt and w["peak_week"] in txt and w["kind"] in txt and (w["stopped"] or "") in txt
        for k in r["hooks"]:
            assert norm(k["hook"]) in txt
        if r["competitor"]:
            c = r["competitor"]
            assert str(c["competitors"]) in txt and str(c["concepts"]) in txt and f"{c['long_running']} running 60+ days" in txt and "not proven performance" in txt
        if r.get("winners_summary"):
            ws = r["winners_summary"]
            assert f"{ws['still_working']} of {ws['winners']} winning ads still working" in txt


def test_faded_summary_sentence_is_not_printed_twice(page):
    goto(page)
    page.locator("[data-testid=pri-body] [data-testid=pri-toggle]").first.click()
    txt = page.locator("[data-testid=pri-body] " + DETAIL).first.text_content()
    r = FIXTURE["rows"][[x["section"] for x in FIXTURE["rows"]].index("faded")]
    assert txt.count(r["when_it_worked"]) == 1 and txt.count(r["how_it_stopped"]) == 1


def test_detail_panel_has_identity_verdict_cta_and_grouped_stat_cards(page):
    goto(page)
    page.locator("[data-testid=pri-body] [data-testid=pri-toggle]").first.click()
    p = page.locator("[data-testid=pri-body] " + DETAIL).first
    assert p.locator(".pri-tile").count() == 4 and p.locator(".pri-verdict").is_visible()
    assert p.locator("[data-testid=pri-play]").is_visible() and p.locator(".pri-cta").count() == 1
    box = lambda sel: p.locator(sel).first.bounding_box()["y"]  # noqa: E731
    assert box(".pri-verdict") < box(".pri-cta") < box(".pri-thennow") < box(".pri-statsec")                           # verdict, then the call to action, then evidence, then the numbers
    assert p.locator(".pri-statsec h4").all_text_contents() == ["Performance", "Testing", "Live"]
    assert p.locator(".pri-tn").count() == 3 and p.locator(".pri-bar-row").count() >= 2
    assert p.locator(".pri-step").count() == 3 and p.locator(".pri-foot .pri-pill").count() >= 3


def test_long_definitions_hide_behind_a_keyboard_operable_disclosure(page):
    goto(page)
    page.locator("[data-testid=pri-body] [data-testid=pri-toggle]").first.click()
    p = page.locator("[data-testid=pri-body] " + DETAIL).first
    s = p.locator(".pri-help summary").first
    assert s.get_attribute("aria-label").startswith("What is")
    assert not p.locator(".pri-helptext").first.is_visible()
    s.focus()
    page.keyboard.press("Enter")
    assert p.locator(".pri-helptext").first.is_visible()


def test_former_winners_and_hooks_show_three_then_show_all(page):
    goto(page)
    page.locator("[data-testid=pri-body] [data-testid=pri-toggle]").first.click()
    p = page.locator("[data-testid=pri-body] " + DETAIL).first
    assert p.locator(".pri-w:visible").count() == 3 and p.locator(".pri-w").count() == 5
    btn = p.locator(".pri-winners .pri-more")
    assert btn.get_attribute("aria-expanded") == "false"
    btn.click()
    assert p.locator(".pri-w:visible").count() == 5 and btn.get_attribute("aria-expanded") == "true"
    btn.click()
    assert p.locator(".pri-w:visible").count() == 3


def test_expanded_panels_at_375px_do_not_scroll_sideways_and_selection_still_works(browser, base_url):
    c = browser.new_context(viewport={"width": 375, "height": 812}, base_url=base_url)
    c.request.post(base_url + "/api/login", data={"username": "JoshHoughton", "password": "test"})
    p = c.new_page()
    p.goto("/priorities")
    p.wait_for_selector("[data-testid=pri-page]")
    idle(p)
    p.get_by_role("button", name="Expand all").first.click()
    assert not p.evaluate("() => document.documentElement.scrollWidth > innerWidth")
    wide = p.evaluate("() => [...document.querySelectorAll('.pri-panel')].filter(e => e.scrollWidth > e.clientWidth + 1).length")
    assert wide == 0
    p.locator(PICK).first.check()
    assert p.locator(TOOLS).is_visible()
    c.close()


# ---------------------------------------------------------------------------------------------------------------- manual concepts (Add concept)

FORM = "[data-testid=pri-form]"
MROW = "[data-testid=pri-body] tr.pri-row[data-source=manual]"


def add_concept(page, product="beef-liver", cluster="Ancestral Nutrition", icp="Curious Beginner", pain="General Vitality / Aging Well", notes="Lead with the farm-to-liver story.\nSecond line.", why="Competitor launched it", section="new"):
    page.locator("[data-testid=pri-add-concept]").click()
    page.wait_for_selector(FORM)
    page.select_option("[data-testid=pri-f-product]", product)
    page.select_option("[data-testid=pri-f-cluster]", cluster)
    page.select_option("[data-testid=pri-f-icp]", icp)
    page.select_option("[data-testid=pri-f-pain]", pain)
    page.select_option("[data-testid=pri-f-section]", section)
    page.fill("[data-testid=pri-f-notes]", notes)
    page.fill("[data-testid=pri-f-why]", why)
    page.locator("[data-testid=pri-f-save]").click()
    page.wait_for_selector(FORM, state="detached")
    idle(page)


def delete_all_manual(page):
    """The mock server keeps manual concepts for the whole session: remove them so other tests see the 30-row fixture."""
    page.goto("/priorities")
    page.wait_for_selector("[data-testid=pri-page]")
    idle(page)
    ids = page.evaluate("async () => (await (await fetch('/api/priorities/manual?include_done=1')).json()).rows.map(r => r.id)")
    for i in ids:
        page.evaluate("async (i) => fetch('/api/priorities/manual/' + i, { method: 'DELETE' })", i)


def test_manual_form_dependent_dropdowns_and_other_new(page):
    goto(page)
    page.locator("[data-testid=pri-add-concept]").click()
    page.wait_for_selector(FORM)
    assert page.locator("[data-testid=pri-f-cluster]").is_disabled() and page.locator("[data-testid=pri-f-icp]").is_disabled()
    page.select_option("[data-testid=pri-f-product]", "vita-bull")
    vb = page.locator("[data-testid=pri-f-cluster] option").all_text_contents()
    page.select_option("[data-testid=pri-f-product]", "beef-liver")
    bl = page.locator("[data-testid=pri-f-cluster] option").all_text_contents()
    assert "Ancestral Nutrition" in bl and bl != vb and bl[-1] == "Other / new…"                                    # filtered by product, always ends with Other / new
    assert page.locator("[data-testid=pri-f-icp]").is_disabled()
    page.select_option("[data-testid=pri-f-cluster]", "Ancestral Nutrition")
    icps = page.locator("[data-testid=pri-f-icp] option").all_text_contents()
    assert "Curious Beginner" in icps and "Hair Thinning" not in icps
    page.select_option("[data-testid=pri-f-icp]", "Curious Beginner")
    assert "General Vitality / Aging Well" in page.locator("[data-testid=pri-f-pain] option").all_text_contents()
    page.select_option("[data-testid=pri-f-pain]", "General Vitality / Aging Well")
    stories = page.locator("[data-testid=pri-f-story] option").all_text_contents()
    assert len(stories) >= 3 and stories[0] == "No story" and stories[-1] == "Other / new…"
    # a brand-new cluster: the text box opens and the ICP list collapses to a typed value
    page.select_option("[data-testid=pri-f-cluster]", "__new__")
    assert page.locator("[data-testid=pri-f-cluster-new]").is_visible()
    assert page.locator("[data-testid=pri-f-icp]").input_value() == "" or page.locator("[data-testid=pri-f-icp] option").count() >= 2
    page.locator("[data-testid=pri-f-cluster-new]").fill("Brand New Cluster")
    assert page.locator("[data-testid=pri-f-icp-new]").is_visible() and page.locator("[data-testid=pri-f-pain-new]").is_visible() and page.locator("[data-testid=pri-f-story-new]").is_visible()
    page.locator("[data-testid=pri-f-icp-new]").fill("Someone New")
    page.locator("[data-testid=pri-f-pain-new]").fill("A New Itch")
    page.locator("[data-testid=pri-f-story-new]").fill("<b>Typed</b> story")
    page.locator("[data-testid=pri-f-save]").click()
    page.wait_for_selector(FORM, state="detached")
    idle(page)
    row = page.locator(MROW).first
    assert row.locator(".pri-c-cluster").text_content() == "Brand New Cluster" and row.locator(".pri-c-story").text_content() == "<b>Typed</b> story"          # escaped, not rendered
    row.locator("[data-testid=pri-toggle]").click()
    assert "cluster, ICP, pain point, story" in page.locator("[data-testid=pri-manual-new]").text_content()
    delete_all_manual(page)


def test_manual_form_validation_cancel_and_escape(page):
    goto(page)
    page.locator("[data-testid=pri-add-concept]").click()
    page.wait_for_selector(FORM)
    page.locator("[data-testid=pri-f-save]").click()
    assert "Fill in: Product, Market Cluster, ICP, Pain Point" in page.locator("[data-testid=pri-f-error]").text_content()
    assert page.locator(FORM).count() == 1
    page.fill("[data-testid=pri-f-notes]", "x" * 50)
    assert page.locator("[data-testid=pri-f-count]").text_content() == "50 / 2000"
    page.keyboard.press("Escape")
    page.wait_for_selector(FORM, state="detached")
    assert page.locator(MROW).count() == 0


def test_add_manual_concept_appears_in_the_table_with_badge_panel_and_note_in_the_matching_auto_row(page):
    goto(page)
    add_concept(page)
    assert page.locator("[data-testid=pri-shown]").text_content() == "31 of 31 concepts shown"
    row = page.locator(MROW)
    assert row.count() == 1
    assert row.locator("[data-testid=pri-manual-badge]").text_content() == "Manual" and row.locator("[data-testid=pri-status]").text_content() == "MANUAL"
    assert row.locator(".pri-c-cluster").text_content() == "Ancestral Nutrition" and row.locator(".pri-c-product").text_content() == "Beef Liver"
    assert row.locator(".pri-c-section .pri-sec").text_content() == "New and untested" and row.locator(".pri-c-key").text_content() == "Competitor launched it"
    assert row.evaluate("e => getComputedStyle(e.querySelector('.pri-c-tog')).boxShadow") != "none"                       # distinct left border
    page.locator("[data-testid=pri-body] [data-testid=pri-card][data-source=manual] [data-testid=pri-toggle]").click()
    panel = page.locator("[data-testid=pri-manual-panel]")
    assert panel.is_visible()
    assert panel.locator("[data-testid=pri-manual-notes]").text_content() == "Lead with the farm-to-liver story.\nSecond line."
    assert panel.locator("[data-testid=pri-manual-why]").text_content() == "Competitor launched it"
    assert "Added " in panel.locator("[data-testid=pri-manual-meta]").text_content()
    assert [b.text_content() for b in panel.locator(".pri-mactions button").all()] == ["Edit", "Mark done", "Delete"]
    # the auto row for the same product + cluster + ICP + pain point carries the note inside its own panel
    auto = page.locator("[data-testid=pri-body] tr.pri-row[data-source=auto]").filter(has_text="General Vitality / Aging Well").filter(has_text="Curious Beginner").first
    auto.locator("[data-testid=pri-toggle]").click()
    assert "Lead with the farm-to-liver story." in page.locator("[data-testid=pri-manual-note]").first.text_content()
    page.locator("[data-testid=pri-search]").fill("Ancestral")
    page.locator(MROW).scroll_into_view_if_needed()
    page.screenshot(path=str(SHOTS / "priorities_manual_row_1280.png"))
    page.locator("[data-testid=pri-search]").fill("")
    delete_all_manual(page)


def test_manual_row_filters_search_sort_selection_csv_print_and_copy(page):
    goto(page)
    add_concept(page, notes="Unique-notes-marker for print")
    # filters / search / sort
    page.locator("[data-testid=pri-search]").fill("Ancestral")
    assert page.locator(MROW).count() == 1
    page.locator("[data-testid=pri-search]").fill("")
    page.locator("[data-testid=pri-f-section-new]").click()
    assert page.locator(MROW).count() == 0
    page.locator("[data-testid=pri-f-section-new]").click()
    page.locator("[data-testid=pri-f-product-vita-bull]").click()
    assert page.locator(MROW).count() == 1                                                                              # a Beef Liver concept is not filtered by the Vita Bull chip
    page.locator("[data-testid=pri-f-product-beef-liver]").click()
    assert page.locator(MROW).count() == 0
    page.locator("[data-testid=pri-f-product-beef-liver]").click()
    page.locator("[data-testid=pri-f-product-vita-bull]").click()
    page.locator("[data-testid=pri-sort-status]").click()
    assert page.locator("[data-testid=pri-body] tr.pri-row").evaluate_all("rows => rows.map(r => r.dataset.source)").count("manual") == 1
    # selection + CSV: the manual row is exported with source=manual in the appended columns
    idx = page.locator("[data-testid=pri-body] tr.pri-row").evaluate_all("rows => rows.findIndex(r => r.dataset.source === 'manual')")
    pick_rows(page, idx, 0 if idx else 1)
    with page.expect_download() as dl:
        page.locator("[data-testid=pri-sel-csv]").click()
    sel = csv_of(dl.value)
    head = sel[0]
    assert head[:4] == ["market_cluster", "icp", "pain_point", "story"] and head[-2:] == ["source", "notes"] and len(sel) == 3
    assert sorted(r[head.index("source")] for r in sel[1:]) == ["auto", "manual"]
    assert [r for r in sel[1:] if r[head.index("source")] == "manual"][0][head.index("notes")] == "Unique-notes-marker for print"
    # print layout: the compact strip shows the notes
    page.emulate_media(media="print")
    assert "Unique-notes-marker for print" in page.locator("[data-testid=pri-print-copy]").text_content()
    page.emulate_media(media="screen")
    # copy as text
    page.context.grant_permissions(["clipboard-read", "clipboard-write"])
    page.locator("[data-testid=pri-sel-copy]").click()
    page.wait_for_function("() => document.querySelector('[data-testid=pri-copy-note]').textContent.startsWith('Copied')")
    text = page.evaluate("() => navigator.clipboard.readText()")
    assert "Status: MANUAL" in text and "Notes: Unique-notes-marker for print" in text and "Section: New and untested" in text
    delete_all_manual(page)


def test_manual_row_in_a_saved_view_is_frozen(page):
    goto(page)
    add_concept(page, notes="Frozen notes")
    idx = page.locator("[data-testid=pri-body] tr.pri-row").evaluate_all("rows => rows.findIndex(r => r.dataset.source === 'manual')")
    pick_rows(page, idx)
    page.locator("[data-testid=pri-sel-save]").click()
    page.wait_for_selector("[data-testid=pri-view-link]")
    link = page.locator("[data-testid=pri-view-link]").get_attribute("href")
    page.locator(MROW).locator("[data-testid=pri-toggle]").click()
    page.locator("[data-testid=pri-manual-edit]").click()
    page.fill("[data-testid=pri-f-notes]", "Edited later")
    page.locator("[data-testid=pri-f-save]").click()
    page.wait_for_selector(FORM, state="detached")
    idle(page)
    page.goto(link)
    page.wait_for_selector("[data-testid=pri-page]")
    idle(page)
    assert page.locator("[data-testid=pri-body] tr.pri-row").count() == 1 and page.locator("[data-testid=pri-manual-badge]").count() == 1
    page.locator("[data-testid=pri-toggle]").click()
    assert page.locator("[data-testid=pri-manual-notes]").text_content() == "Frozen notes"                                  # frozen copy, not the edit
    assert page.locator("[data-testid=pri-manual-edit]").count() == 0 and page.locator("[data-testid=pri-add-concept]").count() == 0     # read-only
    delete_all_manual(page)


def test_edit_done_show_done_and_delete_with_confirm(page):
    goto(page)
    add_concept(page)
    page.locator(MROW).locator("[data-testid=pri-toggle]").click()
    page.locator("[data-testid=pri-manual-edit]").click()
    page.wait_for_selector(FORM)
    assert page.locator("[data-testid=pri-f-cluster]").input_value() == "Ancestral Nutrition" and page.locator("[data-testid=pri-f-notes]").input_value().startswith("Lead with")
    page.fill("[data-testid=pri-f-notes]", "Edited notes")
    page.select_option("[data-testid=pri-f-section]", "other")
    page.locator("[data-testid=pri-f-save]").click()
    page.wait_for_selector(FORM, state="detached")
    idle(page)
    row = page.locator(MROW)
    assert row.locator(".pri-c-section .pri-sec").text_content() == "Other"
    row.locator("[data-testid=pri-toggle]").click()
    assert page.locator("[data-testid=pri-manual-notes]").text_content() == "Edited notes"
    # mark done: hidden by default, back with Show done
    page.locator("[data-testid=pri-manual-done]").click()
    page.wait_for_function("() => document.querySelectorAll('tr[data-source=manual]').length === 0")
    assert page.locator(MROW).count() == 0 and page.locator("[data-testid=pri-shown]").text_content() == "30 of 30 concepts shown"
    page.locator("[data-testid=pri-show-done]").check()
    assert page.locator(MROW).count() == 1 and page.locator(MROW).locator("[data-testid=pri-status]").text_content() == "MANUAL (done)"
    page.locator(MROW).locator("[data-testid=pri-toggle]").click()
    assert page.locator("[data-testid=pri-manual-done]").text_content() == "Reopen"
    page.locator("[data-testid=pri-manual-done]").click()
    page.wait_for_function("() => document.querySelectorAll('tr[data-source=manual]:not(.pri-done)').length === 1")
    assert page.locator(MROW).count() == 1 and page.locator("[data-testid=pri-show-done]").count() == 0
    # delete: dismissing the confirm keeps it, accepting removes it
    page.locator(MROW).locator("[data-testid=pri-toggle]").click()
    page.once("dialog", lambda d: d.dismiss())
    page.locator("[data-testid=pri-manual-delete]").click()
    assert page.locator(MROW).count() == 1
    page.once("dialog", lambda d: d.accept())
    page.locator("[data-testid=pri-manual-delete]").click()
    page.wait_for_function("() => document.querySelectorAll('tr[data-source=manual]').length === 0")
    assert page.locator("[data-testid=pri-shown]").text_content() == "30 of 30 concepts shown"


def test_manual_form_screenshots_at_1280_and_375(browser, base_url):
    for w, h_, name in ((1280, 900, "priorities_manual_form_1280.png"), (375, 812, "priorities_manual_form_375.png")):
        c = browser.new_context(viewport={"width": w, "height": h_}, base_url=base_url)
        c.request.post(base_url + "/api/login", data={"username": "JoshHoughton", "password": "test"})
        p = c.new_page()
        p.goto("/priorities")
        p.wait_for_selector("[data-testid=pri-page]")
        idle(p)
        p.locator("[data-testid=pri-add-concept]").click()
        p.wait_for_selector(FORM)
        p.select_option("[data-testid=pri-f-product]", "beef-liver")
        p.select_option("[data-testid=pri-f-cluster]", "Ancestral Nutrition")
        p.select_option("[data-testid=pri-f-icp]", "Curious Beginner")
        p.select_option("[data-testid=pri-f-pain]", "General Vitality / Aging Well")
        p.fill("[data-testid=pri-f-notes]", "Lead with the farm-to-liver story. Show the sourcing, then the label.")
        assert not p.evaluate("() => document.documentElement.scrollWidth > innerWidth")
        assert p.locator("[data-testid=pri-f-save]").is_visible()
        p.screenshot(path=str(SHOTS / name))
        c.close()
