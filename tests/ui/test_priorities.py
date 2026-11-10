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
    assert p.locator(TOOLS).is_visible()
    assert not p.evaluate("() => document.documentElement.scrollWidth > innerWidth")
    p.locator("[data-testid=pri-sel-save]").click()
    p.locator("[data-testid=pri-view-name]").fill("a fairly long view name for a phone")
    p.keyboard.press("Enter")
    p.wait_for_selector("[data-testid=pri-view-link]")
    assert not p.evaluate("() => document.documentElement.scrollWidth > innerWidth")
    p.screenshot(path=str(SHOTS / "priorities_select_375.png"))
    c.close()


# ---------------------------------------------------------------------------------------------------------------- saved views

DLG = "[data-testid=pri-name-dialog]"
NAME = "[data-testid=pri-view-name]"
VSEL = "[data-testid=pri-view-select]"
BAR = "[data-testid=pri-activebar]"


def save_view(page, name, *idx):
    """Tick rows, click Save view in the selection toolbar, name it in the dialog, Enter."""
    pick_rows(page, *idx)
    page.locator("[data-testid=pri-sel-save]").click()
    page.locator(DLG).wait_for()
    page.locator(NAME).fill(name)
    return _submit_and_link(page, lambda: page.keyboard.press("Enter"))


def _submit_and_link(page, submit):
    link = page.locator("[data-testid=pri-view-link]")
    old = link.get_attribute("href") if link.count() else None
    submit()
    page.wait_for_function("(old) => { const a = document.querySelector('[data-testid=pri-view-link]'); return a && a.getAttribute('href') !== old; }", arg=old)
    return link.get_attribute("href")


def save_filter_view(page, name):
    page.locator("[data-testid=pri-save-open]").click()
    page.locator(DLG).wait_for()
    page.locator("[data-testid=pri-kind-filters]").check()
    page.locator(NAME).fill(name)
    return _submit_and_link(page, lambda: page.locator("[data-testid=pri-dialog-save]").click())


def tok(url):
    return url.rsplit("/", 1)[1]


def open_view(page, token, extra=""):
    page.goto(f"/priorities?view={token}{extra}")
    page.wait_for_selector("[data-testid=pri-page], [data-testid=pri-empty]")
    idle(page)


def rows_text(page, col="pain"):
    return page.locator(f"[data-testid=pri-body] {ROW} .pri-c-{col}").all_text_contents()


def drop(page, keys=(), edit=None):
    r = page.request.post(page.url.split("/priorities")[0] + "/__test/priorities/drop", data={"keys": list(keys), "edit": edit or {}})
    assert r.ok


def key_of_row(page, i):
    return page.evaluate("""(i) => { const r = document.querySelectorAll('[data-testid=pri-body] [data-testid=pri-card]')[i];
      const c = (k) => r.querySelector('.pri-c-' + k).childNodes[0].textContent.trim();
      return [r.dataset.product, r.dataset.section, c('cluster'), c('icp'), c('pain')].join('|'); }""", i)


@pytest.fixture(autouse=True)
def _clean_views(page):
    yield
    try:
        base = page.url.split("/priorities")[0]
        drop(page)
        for v in page.request.get(base + "/api/priorities/views").json()["views"]:
            page.request.delete(base + "/api/priorities/views/" + v["token"])
        page.evaluate("() => { try { localStorage.clear(); sessionStorage.clear(); } catch (e) {} }")
    except Exception:
        pass


def test_save_view_opens_a_naming_dialog_with_a_focused_prefilled_selected_input_enter_saves_esc_cancels(page):
    import re
    goto(page)
    pick_rows(page, 0, 1)
    page.locator("[data-testid=pri-sel-save]").click()
    dlg = page.locator(DLG)
    dlg.wait_for()
    inp = page.locator(NAME)
    assert re.fullmatch(r"Priorities - \d{1,2}/\d{1,2}/\d{2}", inp.input_value())
    assert inp.get_attribute("maxlength") == "120" and "Madre" in inp.get_attribute("placeholder")
    assert page.evaluate("() => document.activeElement.dataset.testid") == "pri-view-name"
    assert page.evaluate("() => { const i = document.activeElement; return i.selectionStart === 0 && i.selectionEnd === i.value.length; }")
    assert dlg.locator("[data-testid=pri-dialog-save]").is_visible() and dlg.locator("[data-testid=pri-dialog-cancel]").is_visible()
    page.keyboard.press("Escape")
    assert dlg.count() == 0 and page.locator("[data-testid=pri-linkbox]").is_hidden()                   # Esc cancels, nothing saved
    page.locator("[data-testid=pri-sel-save]").click()
    page.locator(DLG).wait_for()
    page.locator("[data-testid=pri-dialog-cancel]").click()
    assert page.locator(DLG).count() == 0 and page.locator("[data-testid=pri-linkbox]").is_hidden()
    page.locator("[data-testid=pri-sel-save]").click()
    page.locator(NAME).fill("   ")
    page.keyboard.press("Enter")                                                                           # empty -> default name
    page.wait_for_selector("[data-testid=pri-saved-name]")
    assert re.search(r"Saved .Priorities - \d{1,2}/\d{1,2}/\d{2}.", page.locator("[data-testid=pri-saved-name]").text_content())
    page.locator("[data-testid=pri-sel-save]").click()
    page.locator(NAME).fill("x" * 300)
    assert len(page.locator(NAME).input_value()) == 120                                                    # 120 character cap
    page.locator(NAME).fill("  Madre - 11/10/26 Priorities  ")
    page.keyboard.press("Enter")
    page.wait_for_function("() => document.querySelector('[data-testid=pri-saved-name]').textContent.includes('Madre - 11/10/26 Priorities')")
    box = page.locator("[data-testid=pri-linkbox]")
    assert box.locator("[data-testid=pri-copy-link]").is_visible() and box.locator("[data-testid=pri-view-open]").is_visible()
    page.locator("[data-testid=pri-views]").count() == 0
    opts = page.locator(VSEL + " option").all_text_contents()
    assert opts[0] == "All concepts" and any("Madre - 11/10/26 Priorities (2 concepts)" == o for o in opts)       # the name is in the dropdown right away


def test_switching_to_a_rows_view_filters_the_same_table_in_place_and_back_to_all(page):
    goto(page)
    pains = [page.locator(PICK).nth(i).locator("xpath=ancestor::tr").locator(".pri-c-pain").text_content() for i in (1, 6, 7)]
    url = save_view(page, "Round 1 briefs", 1, 6, 7)
    assert url.startswith("/priorities/v/") and len(tok(url)) >= 22
    t = tok(url)
    goto(page)
    assert visible_rows(page) == 30 and page.locator(VSEL).input_value() == "" and page.locator(BAR).count() == 0
    page.locator(VSEL).select_option(t)
    page.wait_for_selector(BAR)
    idle(page)
    assert f"view={t}" in page.url
    assert visible_rows(page) == 3 and sorted(rows_text(page)) == sorted(pains)
    assert "Viewing: Round 1 briefs (3 concepts)" in page.locator(BAR).text_content().replace("\u00a0", " ")
    assert page.locator("[data-testid=pri-title]").text_content() == "Creative priorities: Round 1 briefs" and page.title().startswith("Round 1 briefs")
    assert page.locator(VSEL).input_value() == t
    # same table: expandable panels, checkboxes, toolbar, search, sort, exports and print all work on the filtered set
    page.locator("[data-testid=pri-toggle]").first.click()
    assert page.locator(DETAIL + ":visible").count() == 1
    pick_rows(page, 0, 1)
    assert page.locator(TOOLS).is_visible() and "2 selected" in page.locator("[data-testid=pri-sel-count]").text_content()
    with page.expect_download() as dl:
        page.locator("[data-testid=pri-sel-csv]").click()
    assert len(csv_of(dl.value)) == 3 and dl.value.suggested_filename == "round-1-briefs.csv"
    page.locator("[data-testid=pri-search]").fill(pains[0][:6])
    assert 1 <= visible_rows(page) <= 3
    page.locator("[data-testid=pri-search]").fill("")
    page.locator("[data-testid=pri-sort-pain]").click()
    assert rows_text(page) == sorted(rows_text(page), key=str.lower)
    page.emulate_media(media="print")
    assert page.locator("[data-testid=pri-print-copy] [data-testid=pri-print-row]").count() == 2         # only the 2 ticked rows print
    page.emulate_media(media="screen")
    # back to all
    page.locator("[data-testid=pri-back-all]").click()
    page.wait_for_function("() => document.querySelectorAll('[data-testid=pri-body] [data-testid=pri-card]').length === 30")
    assert "view=" not in page.url and page.locator(BAR).count() == 0 and page.locator(VSEL).input_value() == ""


@pytest.mark.allow_errors
def test_view_url_is_bookmarkable_last_view_is_remembered_only_without_a_view_param_and_unknown_token_falls_back(page):
    goto(page)
    t1 = tok(save_view(page, "Alpha view", 0))
    t2 = tok(save_view(page, "Beta view", 2, 3))
    goto(page)
    page.locator(VSEL).select_option(t2)                                                                    # choosing a view remembers it for this browser
    page.wait_for_selector(BAR)
    idle(page)
    assert visible_rows(page) == 3                                                                          # Beta = rows 0 (still ticked), 2 and 3
    goto(page)                                                                                              # no ?view: the remembered one (Beta) applies and the URL says so
    assert f"view={t2}" in page.url and visible_rows(page) == 3 and page.locator(VSEL).input_value() == t2
    open_view(page, t1)                                                                                     # explicit ?view wins over the remembered one
    assert visible_rows(page) == 1
    page.locator(VSEL).select_option("")                                                                    # All concepts is one click away and is remembered too
    page.wait_for_function("() => document.querySelectorAll('[data-testid=pri-body] [data-testid=pri-card]').length === 30")
    goto(page)
    assert "view=" not in page.url and visible_rows(page) == 30
    open_view(page, "Z" * 30)                                                                               # unknown token: friendly message, all concepts
    assert visible_rows(page) == 30 and "deleted or does not exist" in page.locator("[data-testid=pri-view-notice]").text_content()
    assert "view=" not in page.url


def test_latest_data_vs_frozen_as_saved_and_a_row_missing_from_the_current_snapshot_falls_back_to_its_frozen_copy(page):
    goto(page)
    k = [key_of_row(page, i) for i in (1, 6, 7)]
    t = tok(save_view(page, "Fallback view", 1, 6, 7))
    saved = {kk: page.locator(f"[data-testid=pri-body] {ROW}").nth(i).locator(".pri-c-concepts").text_content() for kk, i in zip(k, (1, 6, 7))}
    drop(page, keys=[k[0]], edit={k[1]: {"concepts": 77}})
    open_view(page, t)
    assert visible_rows(page) == 3                                                                          # default = Latest data
    by_key = {key_of_row(page, i): i for i in range(3)}
    row1 = page.locator(f"[data-testid=pri-body] {ROW}").nth(by_key[k[1]])
    assert "77 concepts" in row1.locator(".pri-c-concepts").text_content() and saved[k[1]] != "77 concepts"
    gone = page.locator(f"[data-testid=pri-body] {ROW}").nth(by_key[k[0]])
    assert "from 20" in gone.locator("[data-testid=pri-from]").text_content() and saved[k[0]] == gone.locator(".pri-c-concepts").text_content()
    assert "no longer in the current snapshot" in page.locator("[data-testid=pri-from-note]").text_content()
    page.locator("[data-testid=pri-data-mode]").select_option("frozen")                                     # Frozen as saved: every number as saved, no badges
    page.wait_for_function("() => location.search.includes('data=frozen')")
    idle(page)
    assert visible_rows(page) == 3 and page.locator("[data-testid=pri-from]").count() == 0
    assert [saved[kk] for kk in k] == [page.locator(f"[data-testid=pri-body] {ROW}").nth({key_of_row(page, i): i for i in range(3)}[kk]).locator(".pri-c-concepts").text_content() for kk in k]
    # the share link page stays frozen whatever the current snapshot says
    page.goto(f"/priorities/v/{t}")
    page.wait_for_selector("[data-testid=pri-view-note]")
    idle(page)
    assert page.locator("[data-testid=pri-body] tr.pri-row").count() == 3 and "frozen" in page.locator("[data-testid=pri-view-note]").text_content()
    assert "77 concepts" not in " ".join(page.locator("[data-testid=pri-body] .pri-c-concepts").all_text_contents())


def test_filter_view_saves_the_current_filters_and_applies_them_live_to_new_matching_concepts(page):
    goto(page)
    page.locator("[data-testid=pri-f-product-beef-liver]").click()
    page.locator("[data-testid=pri-f-product-boneflex]").click()                                            # Vita Bull only
    page.locator("[data-testid=pri-f-section-stop]").click()                                                 # ... without Stop
    page.locator("[data-testid=pri-sort-pain]").click()
    n = visible_rows(page)
    assert 0 < n < 30
    vb = page.locator(f"[data-testid=pri-body] {ROW}").evaluate_all("els => els.map(e => [e.dataset.product, e.dataset.section])")
    assert all(p == "vita-bull" and sct != "stop" for p, sct in vb)
    first_key = key_of_row(page, 0)
    # a concept is missing at save time; it is back later and the view picks it up by itself
    drop(page, keys=[first_key])
    page.reload()
    page.wait_for_selector("[data-testid=pri-page]")
    idle(page)
    page.locator("[data-testid=pri-f-product-beef-liver]").click()
    page.locator("[data-testid=pri-f-product-boneflex]").click()
    page.locator("[data-testid=pri-f-section-stop]").click()
    page.locator("[data-testid=pri-sort-pain]").click()
    assert visible_rows(page) == n - 1
    page.locator("[data-testid=pri-save-open]").click()
    page.locator(DLG).wait_for()
    assert page.locator("[data-testid=pri-kind-rows]").is_disabled() and page.locator("[data-testid=pri-kind-filters]").is_checked()   # nothing ticked: filters is the default
    page.locator(NAME).fill("Vita Bull live")
    page.keyboard.press("Enter")
    page.wait_for_selector("[data-testid=pri-view-link]")
    t = tok(page.locator("[data-testid=pri-view-link]").get_attribute("href"))
    drop(page)                                                                                                # the dropped concept returns
    open_view(page, t)
    assert visible_rows(page) == n and "Vita Bull live" in page.locator(BAR).text_content()
    assert page.locator("[data-testid=pri-f-product-vita-bull]").get_attribute("aria-pressed") == "true" and page.locator("[data-testid=pri-f-product-beef-liver]").get_attribute("aria-pressed") == "false"
    assert page.locator("[data-testid=pri-f-section-stop]").get_attribute("aria-pressed") == "false"
    assert page.locator("[data-testid=pri-sort-pain]").locator("xpath=ancestor::th").get_attribute("aria-sort") == "ascending"
    assert "applied live" in page.locator(BAR).text_content()
    page.locator("[data-testid=pri-data-mode]").select_option("frozen")                                      # frozen = the concepts that matched when it was saved
    page.wait_for_function("() => location.search.includes('data=frozen')")
    idle(page)
    assert visible_rows(page) == n - 1


def test_update_this_view_replaces_rows_or_filters_and_rename_and_delete_work_on_the_active_view(page):
    import re
    goto(page)
    t = tok(save_view(page, "Editable", 0, 1, 2))
    open_view(page, t)
    page.locator("[data-testid=pri-view-update]").click()                                                   # a rows view needs ticked rows
    assert visible_rows(page) == 3
    pick_rows(page, 1)
    page.locator("[data-testid=pri-view-update]").click()
    page.wait_for_function("() => document.querySelectorAll('[data-testid=pri-body] [data-testid=pri-card]').length === 1")
    assert "Updated" in page.locator("[data-testid=pri-view-notice]").text_content() and "(1 concept)" in page.locator(BAR).text_content()
    assert page.request.get(page.url.split("/priorities")[0] + f"/api/priorities/views/{t}").json()["view"]["count"] == 1
    # rename: a dialog, same link, name everywhere
    page.locator("[data-testid=pri-active-rename]").click()
    page.locator(DLG).wait_for()
    assert page.locator(NAME).input_value() == "Editable"
    page.locator(NAME).fill("Renamed 11/10")
    page.keyboard.press("Enter")
    page.wait_for_function("() => document.querySelector('[data-testid=pri-title]').textContent.includes('Renamed 11/10')")
    assert f"view={t}" in page.url and page.title().startswith("Renamed 11/10")
    assert page.locator(VSEL + " option:checked").text_content().startswith("Renamed 11/10")
    # a filter view updates to the filters now on the page
    page.locator(VSEL).select_option("")
    page.wait_for_function("() => document.querySelectorAll('[data-testid=pri-body] [data-testid=pri-card]').length === 30")
    page.locator("[data-testid=pri-f-product-boneflex]").click()
    page.locator("[data-testid=pri-search]").fill("a")
    tf = tok(save_filter_view(page, "Filtered"))
    open_view(page, tf)
    page.locator("[data-testid=pri-search]").fill("")
    page.locator("[data-testid=pri-f-product-boneflex]").click()
    page.locator("[data-testid=pri-f-product-beef-liver]").click()
    page.locator("[data-testid=pri-view-update]").click()
    page.wait_for_function("() => document.querySelector('[data-testid=pri-view-notice]')")
    f = page.request.get(page.url.split("/priorities")[0] + f"/api/priorities/views/{tf}").json()["view"]["filters"]
    assert f["q"] == "" and "beef-liver" not in f["products"] and f["products"]
    # delete the active view: confirm, back to all, gone from the dropdown
    page.once("dialog", lambda d: d.accept())
    page.locator("[data-testid=pri-active-delete]").click()
    page.wait_for_function("() => !location.search.includes('view=')")
    idle(page)
    assert visible_rows(page) == 30 and "Deleted" in page.locator("[data-testid=pri-view-notice]").text_content()
    assert not any("Filtered" in o for o in page.locator(VSEL + " option").all_text_contents())
    assert page.request.get(page.url.split("/priorities")[0] + f"/api/priorities/views/{tf}").status == 404


@pytest.mark.allow_errors
def test_deleted_view_share_page_says_it_was_deleted(page):
    goto(page)
    t = tok(save_view(page, "Short lived", 0))
    page.request.delete(page.url.split("/priorities")[0] + f"/api/priorities/views/{t}")
    page.goto(f"/priorities/v/{t}")
    page.wait_for_selector("[data-testid=pri-empty]")
    assert "was deleted" in page.locator("[data-testid=pri-empty]").text_content()
    assert page.locator("a", has_text="Open full priorities").count() == 1


@pytest.mark.allow_errors
def test_unknown_view_token_is_a_friendly_404_page(page):
    page.goto("/priorities/v/" + "x" * 30)
    page.wait_for_selector("[data-testid=pri-empty]")
    assert "was deleted" in page.locator("[data-testid=pri-empty]").text_content()


def test_share_page_is_frozen_names_csv_after_the_view_name_and_renames_with_the_dialog(page, browser, base_url):
    goto(page)
    pains = [page.locator(PICK).nth(i).locator("xpath=ancestor::tr").locator(".pri-c-pain").text_content() for i in (1, 6)]
    url = save_view(page, "  Madre - 11/10/26 Priorities  ", 1, 6)
    c = browser.new_context(viewport={"width": 1440, "height": 900}, base_url=base_url)                    # a fresh context: no selection, no storage
    c.request.post(base_url + "/api/login", data={"username": "JoshHoughton", "password": "test"})
    v = c.new_page()
    v.goto(url)
    v.wait_for_selector("[data-testid=pri-page]")
    idle(v)
    assert v.locator("[data-testid=pri-title]").text_content() == "Creative priorities: Madre - 11/10/26 Priorities" and v.title().startswith("Madre - 11/10/26 Priorities")
    assert sorted(v.locator("[data-testid=pri-body] .pri-c-pain").all_text_contents()) == sorted(pains)
    assert v.locator(PICK).count() == 0 and v.locator(TOOLS).count() == 0 and v.locator("[data-testid=pri-search]").count() == 0 and v.locator(VSEL).count() == 0
    with v.expect_download() as dl:
        v.locator("[data-testid=pri-csv]").click()
    assert dl.value.suggested_filename == "madre-11-10-26-priorities.csv" and len(csv_of(dl.value)) == 3
    v.locator("[data-testid=pri-rename]").click()
    v.locator(DLG).wait_for()
    v.locator(NAME).fill("Round 2 / <b>x</b>")
    v.keyboard.press("Enter")
    v.wait_for_function("() => document.querySelector('[data-testid=pri-title]').textContent.includes('Round 2')")
    assert v.title().startswith("Round 2 / <b>x</b>") and v.url.endswith(url)
    v.reload()
    v.wait_for_selector("[data-testid=pri-title]")
    assert v.locator("[data-testid=pri-title]").text_content() == "Creative priorities: Round 2 / <b>x</b>" and v.locator("[data-testid=pri-title] b").count() == 0
    v.close()
    c.close()


def test_view_bar_dialog_and_active_view_fit_375px_and_the_share_page_does_not_overflow_at_desktop_widths(browser, base_url):
    for w, h_ in ((375, 812), (1009, 800), (1280, 800)):
        c = browser.new_context(viewport={"width": w, "height": h_}, base_url=base_url)
        c.request.post(base_url + "/api/login", data={"username": "JoshHoughton", "password": "test"})
        p = c.new_page()
        p.goto("/priorities")
        p.wait_for_selector("[data-testid=pri-page]")
        idle(p)
        pick_rows(p, 0, 1)
        p.locator("[data-testid=pri-sel-save]").click()
        p.locator(DLG).wait_for()
        assert p.evaluate("() => document.documentElement.scrollWidth <= document.documentElement.clientWidth")
        box = p.locator(DLG).bounding_box()
        assert box["x"] >= 0 and box["x"] + box["width"] <= w
        p.locator(NAME).fill(f"Width {w}")
        p.keyboard.press("Enter")
        p.wait_for_selector("[data-testid=pri-view-link]")
        url = p.locator("[data-testid=pri-view-link]").get_attribute("href")
        p.goto("/priorities?view=" + tok(url))
        p.wait_for_selector(BAR)
        idle(p)
        sw, cw = p.evaluate("() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]")
        assert sw <= cw, ("active view", w, sw, cw)
        p.goto(url)
        p.wait_for_selector("[data-testid=pri-view-note]")
        idle(p)
        sw, cw = p.evaluate("() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]")
        assert sw <= cw, ("share page", w, sw, cw)
        if w == 375:
            p.screenshot(path=str(SHOTS / "priorities_view_375.png"))
        p.goto("/priorities")
        p.evaluate("() => { try { localStorage.clear(); } catch (e) {} }")
        for v in p.request.get(base_url + "/api/priorities/views").json()["views"]:
            p.request.delete(base_url + "/api/priorities/views/" + v["token"])
        c.close()


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
    page.locator(DLG).wait_for()
    page.keyboard.press("Enter")
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


def test_manual_concept_works_inside_rows_and_filter_views(page):
    goto(page)
    add_concept(page, notes="Manual notes v1")
    idx = page.locator("[data-testid=pri-body] tr.pri-row").evaluate_all("rows => rows.findIndex(r => r.dataset.source === 'manual')")
    t = tok(save_view(page, "With manual", idx, 0))
    tf = tok(save_filter_view(page, "Everything live"))
    open_view(page, t)
    assert visible_rows(page) == 2 and page.locator(MROW).count() == 1
    page.locator(MROW).locator("[data-testid=pri-toggle]").click()                                    # the manual row is editable inside the view, and the view survives the reload
    page.locator("[data-testid=pri-manual-edit]").click()
    page.wait_for_selector(FORM)
    page.fill("[data-testid=pri-f-notes]", "Manual notes v2")
    page.locator("[data-testid=pri-f-save]").click()
    page.wait_for_selector(FORM, state="detached")
    idle(page)
    assert f"view={t}" in page.url and visible_rows(page) == 2 and page.locator(BAR).count() == 1
    page.locator(MROW).locator("[data-testid=pri-toggle]").click()
    assert page.locator("[data-testid=pri-manual-notes]").text_content() == "Manual notes v2"        # Latest data
    page.locator("[data-testid=pri-data-mode]").select_option("frozen")
    page.wait_for_function("() => location.search.includes('data=frozen')")
    idle(page)
    page.locator(MROW).locator("[data-testid=pri-toggle]").click()
    assert page.locator("[data-testid=pri-manual-notes]").text_content() == "Manual notes v1"        # Frozen as saved
    open_view(page, tf)                                                                                 # a filter view includes manual rows by itself
    assert page.locator(MROW).count() == 1 and visible_rows(page) == 31
    # the manual concept is deleted later: the rows view keeps its frozen copy, marked, read-only
    page.evaluate("async () => { const rows = (await (await fetch('/api/priorities/manual?include_done=1')).json()).rows; for (const r of rows) await fetch('/api/priorities/manual/' + r.id, { method: 'DELETE' }); }")
    open_view(page, t)
    assert visible_rows(page) == 2 and "from 20" in page.locator("[data-testid=pri-from]").text_content()
    page.locator(MROW).locator("[data-testid=pri-toggle]").click()
    assert page.locator("[data-testid=pri-manual-edit]").count() == 0
    delete_all_manual(page)


# ---------------------------------------------------------------------------------------------------------------- redesigned page header

def test_header_has_title_subline_week_view_one_filled_button_and_secondary_group(page):
    goto(page)
    assert page.locator("[data-testid=pri-title]").text_content() == "Creative priorities"
    sub = page.locator("[data-testid=pri-sub]").text_content()
    assert "Week of 2026-11-08" in sub and "Data through 2026-11-08" in sub and "Competitor ads as of 2026-11-09" in sub
    assert page.locator("[data-testid=pri-week]").is_visible() and page.locator(VSEL).is_visible()
    filled = page.locator(".pri-top .pri-hd-screen .btn.primary")
    assert filled.count() == 1 and filled.first.get_attribute("data-testid") == "pri-add-concept"            # Add concept is the one filled button
    grp = page.locator(".pri-btngroup")
    assert grp.locator("[data-testid=pri-csv]").is_visible() and grp.locator("[data-testid=pri-print]").is_visible()
    seg = page.locator(".pri-tablebar .pri-seg")
    assert seg.locator("[data-testid=pri-all-expand]").is_visible() and seg.locator("[data-testid=pri-all-collapse]").is_visible()
    assert page.locator("[data-testid=pri-legend] dt").count() == 4                                             # the legend exists once (the print copy); the screen copy has its own id


def test_status_strip_has_every_count_as_a_clickable_tile_that_filters_and_clears(page):
    goto(page)
    fx = FIXTURE["stats"]["status_counts_total"]
    tiles = page.locator("[data-testid=pri-stat]")
    assert tiles.count() == len(fx)
    got = {t.get_attribute("data-status"): int(t.locator(".pri-stat-n").text_content()) for t in tiles.all()}
    assert got == fx
    page.locator("[data-testid=pri-stat][data-status='RISING']").click()
    n = visible_rows(page)
    assert 0 < n < 30 and set(page.locator("[data-testid=pri-body] [data-testid=pri-status]").all_text_contents()) == {"RISING"}
    assert page.locator("[data-testid=pri-stat][data-status='RISING']").get_attribute("aria-pressed") == "true"
    assert page.locator("[data-testid=pri-f-status]").input_value() == "RISING" and "1 filter on" in page.locator("[data-testid=pri-active-filters]").text_content()
    page.locator("[data-testid=pri-stat][data-status='RISING']").click()                                          # click again = off
    assert visible_rows(page) == 30 and page.locator("[data-testid=pri-clear-filters]").is_hidden()
    page.locator("[data-testid=pri-f-status]").select_option("FADED")                                              # the filter-bar select drives the same filter
    assert set(page.locator("[data-testid=pri-body] [data-testid=pri-status]").all_text_contents()) == {"FADED"}


def test_product_tiles_show_the_four_numbers_and_filter_by_product(page):
    goto(page)
    tiles = page.locator("[data-testid=pri-ptile]")
    assert tiles.count() == 3
    for t in tiles.all():
        slug = t.get_attribute("data-product")
        b = FIXTURE["baselines"][slug]
        txt = t.text_content()
        assert b["product_name"] in txt and _roas(b["roas"]) in txt and _roas(b["roas_30d"]) in txt and f"{round(b['hit_rate'] * 100)}%" in txt and _money(b["cpa"]) in txt, (slug, txt)
    page.locator("[data-testid=pri-ptile][data-product='beef-liver']").click()
    assert set(page.locator("[data-testid=pri-body] tr.pri-row").evaluate_all("els => els.map(e => e.dataset.product)")) == {"beef-liver"}
    assert page.locator("[data-testid=pri-f-product-beef-liver]").get_attribute("aria-pressed") == "true" and page.locator("[data-testid=pri-f-product-vita-bull]").get_attribute("aria-pressed") == "false"
    page.locator("[data-testid=pri-ptile][data-product='beef-liver']").click()
    assert visible_rows(page) == 30


def test_disclosures_keep_every_word_and_the_clear_filters_link_resets_everything(page):
    goto(page)
    tags, nums = page.locator("[data-testid=pri-about-tags]"), page.locator("[data-testid=pri-about-numbers]")
    assert tags.get_attribute("open") is None and nums.get_attribute("open") is None                              # collapsed by default
    tags.locator("summary").click()
    nums.locator("summary").click()
    assert [x.strip() for x in tags.locator("dt").all_text_contents()] == ["Market Cluster", "ICP", "Pain Point", "Story"]
    for leg in FIXTURE["legend"]:
        assert leg["meaning"] in tags.text_content()
    t = nums.text_content()
    for frag in ("Week of 2026-11-08. Data through 2026-11-08.", "Numbers are Meta-reported (ROAS = purchase value / spend, CPA = spend / purchases)", "Upstack new-customer ROAS beside them.",
                 "This is prioritisation: consistent with the numbers, not proof that more concepts will work.", "Every row is collapsed: open a row for the full detail.",
                 "Competitor ads: Meta Ad Library, as of 2026-11-09; refreshed weekly. Running a long time is a signal, not proven performance."):
        assert frag in t, frag
    intro = page.locator("[data-testid=pri-printintro]").text_content()
    assert "Lines by status: " in intro and "AVERAGE 36" in intro and "WORKING NOW 10" in intro
    for s_ in FIXTURE["baselines"].values():
        assert f"{s_['product_name']} average ROAS {_roas(s_['roas'])} since launch, {_roas(s_['roas_30d'])} last 30 days, CPA {_money(s_['cpa'])}, hit rate {round(s_['hit_rate'] * 100)}%" in intro.replace("×", "x")
    # filters: several at once, active count, one link clears all
    clear = page.locator("[data-testid=pri-clear-filters]")
    assert clear.is_hidden()
    page.locator("[data-testid=pri-f-product-boneflex]").click()
    page.locator("[data-testid=pri-search]").fill("a")
    page.locator("[data-testid=pri-f-status]").select_option("FADED")
    assert "3 filters on" in page.locator("[data-testid=pri-active-filters]").text_content() and clear.is_visible()
    clear.click()
    assert visible_rows(page) == 30 and clear.is_hidden() and page.locator("[data-testid=pri-search]").input_value() == "" and page.locator("[data-testid=pri-f-status]").input_value() == ""
    assert page.locator("[data-testid=pri-f-product-boneflex]").get_attribute("aria-pressed") == "true"


def test_filter_view_remembers_the_status_filter(page):
    goto(page)
    page.locator("[data-testid=pri-stat][data-status='RISING']").click()
    t = tok(save_filter_view(page, "Rising only"))
    open_view(page, t)
    assert page.locator("[data-testid=pri-f-status]").input_value() == "RISING" and page.locator("[data-testid=pri-stat][data-status='RISING']").get_attribute("aria-pressed") == "true"
    assert set(page.locator("[data-testid=pri-body] [data-testid=pri-status]").all_text_contents()) == {"RISING"}


def test_header_uses_the_width_and_print_still_has_the_full_intro_and_none_of_the_controls(browser, base_url):
    c = browser.new_context(viewport={"width": 1280, "height": 900}, base_url=base_url)
    c.request.post(base_url + "/api/login", data={"username": "JoshHoughton", "password": "test"})
    p = c.new_page()
    p.goto("/priorities")
    p.wait_for_selector("[data-testid=pri-page]")
    idle(p)
    boxes = p.locator("[data-testid=pri-ptile]").evaluate_all("els => els.map(e => { const r = e.getBoundingClientRect(); return [Math.round(r.top), Math.round(r.right)]; })")
    assert len({b[0] for b in boxes}) == 1 and max(b[1] for b in boxes) > 1100                              # three tiles in one row spanning the page, not a 380px column
    p.screenshot(path=str(SHOTS / "priorities_header_1280.png"))
    p.emulate_media(media="print")
    assert p.locator("[data-testid=pri-printintro]").is_visible() and p.locator("[data-testid=pri-legend]").is_visible() and p.locator("[data-testid=pri-counts]").is_visible()
    assert p.locator(".pri-actions").is_hidden() and p.locator("[data-testid=pri-statstrip]").is_hidden() and p.locator("[data-testid=pri-filters]").is_hidden() and p.locator(VSEL).is_hidden()
    p.close()
    c.close()
    c = browser.new_context(viewport={"width": 375, "height": 812}, base_url=base_url)
    c.request.post(base_url + "/api/login", data={"username": "JoshHoughton", "password": "test"})
    p = c.new_page()
    p.goto("/priorities")
    p.wait_for_selector("[data-testid=pri-page]")
    idle(p)
    assert p.evaluate("() => document.documentElement.scrollWidth <= document.documentElement.clientWidth")
    p.locator("[data-testid=pri-about-tags] summary").click()
    p.locator("[data-testid=pri-about-numbers] summary").click()
    assert p.evaluate("() => document.documentElement.scrollWidth <= document.documentElement.clientWidth")
    p.screenshot(path=str(SHOTS / "priorities_header_375.png"))
    c.close()


# ---------------------------------------------------------------------------------------------------------------- show selected only

def test_show_selected_only_toggles_in_place_unticking_removes_the_row_and_empty_selection_turns_it_off(page):
    goto(page)
    chip = page.locator("[data-testid=pri-f-selected]")
    assert chip.is_disabled() and "Selected (0)" in chip.text_content()                                      # nothing ticked: nothing to show
    pains = [page.locator(PICK).nth(i).locator("xpath=ancestor::tr").locator(".pri-c-pain").text_content() for i in (1, 6, 7)]
    pick_rows(page, 1, 6, 7)
    assert chip.is_enabled() and "Selected (3)" in chip.text_content()
    page.locator("[data-testid=pri-sel-only]").click()                                                         # toolbar toggle
    assert visible_rows(page) == 3 and sorted(rows_text(page)) == sorted(pains)
    assert chip.get_attribute("aria-pressed") == "true" and page.locator("[data-testid=pri-sel-only]").get_attribute("aria-pressed") == "true"
    assert "3 of 30 concepts shown" in page.locator("[data-testid=pri-shown]").text_content() and "1 filter on" in page.locator("[data-testid=pri-active-filters]").text_content()
    page.locator("[data-testid=pri-toggle]").first.click()                                                       # expand/collapse works and survives
    assert page.locator(DETAIL + ":visible").count() == 1
    page.locator(PICK).nth(0).click()                                                                          # unticking removes the row from the filtered list right away
    assert visible_rows(page) == 2 and "Selected (2)" in chip.text_content()
    page.locator("[data-testid=pri-search]").fill(pains[1][:5])                                                 # composes with search / filters / sort
    assert visible_rows(page) <= 2
    page.locator("[data-testid=pri-search]").fill("")
    page.locator("[data-testid=pri-f-product-boneflex]").click()
    assert all(p != "boneflex" for p in page.locator(f"[data-testid=pri-body] {ROW}").evaluate_all("els => els.map(e => e.dataset.product)"))
    page.locator("[data-testid=pri-f-product-boneflex]").click()
    page.locator("[data-testid=pri-sort-pain]").click()
    assert rows_text(page) == sorted(rows_text(page), key=str.lower)
    page.locator(PICK).nth(0).click()
    page.locator(PICK).nth(0).click()                                                                          # last one: empty selection turns the toggle off with a hint
    assert visible_rows(page) == 30 and chip.get_attribute("aria-pressed") == "false" and chip.is_disabled()
    assert "Nothing is selected" in page.locator("[data-testid=pri-sel-hint]").text_content() and page.locator(TOOLS).is_hidden()
    pick_rows(page, 2)
    assert page.locator("[data-testid=pri-sel-hint]").text_content() == ""                                      # the hint goes away once something is ticked again
    chip.click()
    assert visible_rows(page) == 1
    chip.click()                                                                                                 # off again from the chip
    assert visible_rows(page) == 30
    page.locator("[data-testid=pri-sel-only]").click()
    page.locator("[data-testid=pri-clear-filters]").click()                                                      # Clear filters also turns it off
    assert visible_rows(page) == 30 and chip.get_attribute("aria-pressed") == "false"


def test_selected_only_not_persisted_across_reload_and_save_dialog_shows_the_count_and_name(page):
    goto(page)
    pick_rows(page, 0, 1)
    page.locator("[data-testid=pri-sel-only]").click()
    assert visible_rows(page) == 2
    page.reload()
    page.wait_for_selector("[data-testid=pri-page]")
    idle(page)
    assert visible_rows(page) == 30 and "Selected (2)" in page.locator("[data-testid=pri-f-selected]").text_content()     # ticks persist (sessionStorage), the toggle does not
    page.locator("[data-testid=pri-sel-only]").click()
    page.locator(PICK).nth(1).click()                                                                          # review and untick before saving
    page.locator("[data-testid=pri-sel-save]").click()
    page.locator(DLG).wait_for()
    assert page.locator("[data-testid=pri-dialog-note]").text_content() == "1 concept selected" and page.locator(NAME).is_visible()
    page.keyboard.press("Escape")
    pick_rows(page, 0)
    page.locator("[data-testid=pri-save-open]").click()
    page.locator(DLG).wait_for()
    assert "concept" in page.locator("[data-testid=pri-dialog-note]").text_content()
    page.keyboard.press("Escape")


def test_selected_only_works_with_manual_rows(page):
    goto(page)
    add_concept(page, notes="Pick me")
    idx = page.locator("[data-testid=pri-body] tr.pri-row").evaluate_all("rows => rows.findIndex(r => r.dataset.source === 'manual')")
    pick_rows(page, idx)
    page.locator("[data-testid=pri-f-selected]").click()
    assert visible_rows(page) == 1 and page.locator(MROW).count() == 1
    page.locator(MROW).locator("[data-testid=pri-toggle]").click()
    page.locator("[data-testid=pri-manual-edit]").click()                                                        # editing reloads the page data; the toggle and the tick survive
    page.wait_for_selector(FORM)
    page.fill("[data-testid=pri-f-notes]", "Still picked")
    page.locator("[data-testid=pri-f-save]").click()
    page.wait_for_selector(FORM, state="detached")
    idle(page)
    assert visible_rows(page) == 1 and page.locator("[data-testid=pri-f-selected]").get_attribute("aria-pressed") == "true"
    delete_all_manual(page)


# ---------------------------------------------------------------------------------------------------------------- briefed checkmarks in saved views

BRIEF = "[data-testid=pri-brief-toggle]"
PROG = "[data-testid=pri-brief-progress]"


def fresh_page(browser, base_url, w=1440):
    c = browser.new_context(viewport={"width": w, "height": 900}, base_url=base_url)
    c.request.post(base_url + "/api/login", data={"username": "JoshHoughton", "password": "test"})
    p = c.new_page()
    return c, p


def open_share(p, url):
    p.goto(url)
    p.wait_for_selector("[data-testid=pri-view-note]")
    idle(p)


def test_share_page_has_product_filter_with_counts_briefed_toggle_progress_and_hide_briefed(page, browser, base_url):
    goto(page)
    rows = page.locator(f"[data-testid=pri-body] {ROW}").evaluate_all("els => els.map(e => e.dataset.product)")
    idx = [i for i in range(len(rows)) if rows[i] in ("vita-bull", "beef-liver")][:6]
    url = save_view(page, "Brief tracker", *idx)
    want = {p_: rows_ for p_, rows_ in ((p_, sum(1 for i in idx if rows[i] == p_)) for p_ in set(rows[i] for i in idx))}
    c, p = fresh_page(browser, base_url)
    open_share(p, url)
    assert p.locator(PICK).count() == 0 and p.locator(TOOLS).count() == 0 and p.locator("[data-testid=pri-search]").count() == 0       # no edit chrome beyond the briefed controls
    chips = {x.get_attribute("data-testid").replace("pri-f-product-", ""): x.text_content() for x in p.locator("[data-testid^=pri-f-product-]").all()}
    assert set(chips) == set(want) and all(f"({want[k]})" in chips[k] for k in chips)                                                # only products in the view, with counts
    assert p.locator(PROG).text_content() == f"0 of {len(idx)} briefed" and p.locator(BRIEF).count() == len(idx)
    assert "null" not in p.locator("[data-testid=pri-body]").text_content()
    p.locator(BRIEF).nth(0).click()
    p.wait_for_function("() => document.querySelector('[data-testid=pri-brief-progress]').textContent.startsWith('1 of')")
    assert p.locator("tr.pri-row.pri-briefed").count() == 1 and p.locator(BRIEF).nth(0).get_attribute("aria-pressed") == "true"
    p.locator(BRIEF).nth(1).click()
    p.wait_for_function("() => document.querySelector('[data-testid=pri-brief-progress]').textContent.startsWith('2 of')")
    # state is on the server: a reload and a second browser both see it
    p.reload()
    p.wait_for_selector("[data-testid=pri-view-note]")
    idle(p)
    assert p.locator(PROG).text_content() == f"2 of {len(idx)} briefed" and p.locator("tr.pri-row.pri-briefed").count() == 2
    c2, p2 = fresh_page(browser, base_url)
    open_share(p2, url)
    assert p2.locator(PROG).text_content() == f"2 of {len(idx)} briefed"
    # filters
    p.locator("[data-testid=pri-f-brief-open]").click()
    assert visible_rows(p) == len(idx) - 2 and p.locator("tr.pri-row.pri-briefed").count() == 0
    p.locator("[data-testid=pri-f-brief-done]").click()
    assert visible_rows(p) == 2
    p.locator("[data-testid=pri-f-brief-all]").click()
    assert visible_rows(p) == len(idx)
    p.locator("[data-testid=pri-hide-briefed]").click()                                                                               # quick toggle
    assert visible_rows(p) == len(idx) - 2 and p.locator("[data-testid=pri-hide-briefed]").get_attribute("aria-pressed") == "true"
    first = next(iter(want))
    other = [k for k in want if k != first]
    p.locator(f"[data-testid=pri-f-product-{other[0]}]").click()                                                                      # composes with the product chips
    assert all(x == first for x in p.locator(f"[data-testid=pri-body] {ROW}").evaluate_all("els => els.map(e => e.dataset.product)"))
    assert "of" in p.locator(PROG).text_content() and p.locator("[data-testid=pri-active-filters]").text_content().startswith("2 filter")
    p.locator("[data-testid=pri-clear-filters]").click()
    assert visible_rows(p) == len(idx)
    # sort by the Briefed column
    p.locator("[data-testid=pri-sort-briefed]").click()
    flags = p.locator(f"[data-testid=pri-body] {ROW}").evaluate_all("els => els.map(e => e.classList.contains('pri-briefed'))")
    assert flags == sorted(flags)
    # note
    p.locator("[data-testid=pri-brief-note]").first.click()
    p.locator(DLG).wait_for()
    p.locator(NAME).fill("sent to Madre 11/12")
    p.keyboard.press("Enter")
    p.wait_for_function("() => document.body.textContent.includes('sent to Madre 11/12')")
    # exports: CSV columns, print marker, copy as text
    with p.expect_download() as dl:
        p.locator("[data-testid=pri-csv]").click()
    rowsx = csv_of(dl.value)
    assert rowsx[0][-3:] == ["briefed", "briefed_at", "briefed_note"] and [r[-3] for r in rowsx[1:]].count("yes") == 2 and "sent to Madre 11/12" in [r[-1] for r in rowsx[1:]]
    p.emulate_media(media="print")
    assert p.locator("[data-testid=pri-print-briefed]").count() == 2
    c2.close()
    c.close()


def test_briefed_in_the_view_switcher_not_on_all_concepts_frozen_and_manual_rows(page):
    goto(page)
    assert page.locator(BRIEF).count() == 0 and page.locator(PROG).count() == 0 and page.locator("[data-testid=pri-f-brief-all]").count() == 0     # All concepts has no briefed controls
    add_concept(page, notes="Brief me too")
    idx = page.locator("[data-testid=pri-body] tr.pri-row").evaluate_all("rows => rows.findIndex(r => r.dataset.source === 'manual')")
    t = tok(save_view(page, "Tracker", idx, 0, 1))
    open_view(page, t)
    assert page.locator(PROG).text_content() == "0 of 3 briefed" and page.locator(BRIEF).count() == 3
    page.locator(MROW).locator(BRIEF).click()                                                                    # a manual row can be briefed too
    page.wait_for_function("() => document.querySelector('[data-testid=pri-brief-progress]').textContent.startsWith('1 of')")
    assert page.locator(MROW).get_attribute("class").count("pri-briefed") == 1
    page.locator("[data-testid=pri-f-product-vita-bull]").text_content()
    page.locator("[data-testid=pri-data-mode]").select_option("frozen")                                          # frozen vs latest: the checkmark belongs to the view, so it stays
    page.wait_for_function("() => location.search.includes('data=frozen')")
    idle(page)
    assert page.locator(PROG).text_content() == "1 of 3 briefed" and page.locator("tr.pri-row.pri-briefed").count() == 1
    page.locator("[data-testid=pri-f-brief-open]").click()
    assert visible_rows(page) == 2
    pick_rows(page, 0)
    page.locator("[data-testid=pri-sel-copy]").click()                                                           # copy as text carries the state
    page.locator("[data-testid=pri-f-brief-all]").click()
    page.locator("[data-testid=pri-sel-clear]").click()
    with page.expect_download() as dl:
        page.locator("[data-testid=pri-csv]").click()
    assert csv_of(dl.value)[0][-3] == "briefed"
    page.locator("[data-testid=pri-data-mode]").select_option("latest")
    page.wait_for_function("() => !location.search.includes('data=frozen')")
    idle(page)
    with page.expect_download() as dl:
        page.locator("[data-testid=pri-csv]").click()
    rowsx = csv_of(dl.value)
    assert rowsx[0][-3] == "briefed" and [r[-3] for r in rowsx[1:]].count("yes") == 1                           # latest-data export of a view carries the briefed columns too
    delete_all_manual(page)


@pytest.mark.allow_errors
def test_briefed_toggle_on_a_deleted_view_shows_an_error_and_keeps_the_row_unmarked(page):
    goto(page)
    t = tok(save_view(page, "Gone soon", 0, 1))
    open_view(page, t)
    page.request.delete(page.url.split("/priorities")[0] + f"/api/priorities/views/{t}")
    page.locator(BRIEF).first.click()
    page.wait_for_selector("[data-testid=pri-brief-error]:not(:empty)")
    assert page.locator("tr.pri-row.pri-briefed").count() == 0 and page.locator(PROG).text_content() == "0 of 2 briefed"
