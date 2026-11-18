"""By page (/pages) in the browser, against the mock server (tests/ui/mock_pages.py: the real payload builder in app/meta_pages.py over made-up rows).

Mock facts over Oct 12 - Nov 2 2026 (22 days): Lonvera page spends 2,000 a day = $44,000, Other handle $11,000, (unknown page) $1,100; Total $56,100."""
import pytest

from tests._servers import start_mock

pytestmark = pytest.mark.mock_only
FULL = "?from=2026-10-12&to=2026-11-02"


def idle(page):
    page.wait_for_function("() => !document.querySelector('#view').hasAttribute('aria-busy')")


def goto(page, qs=FULL):
    page.goto("/pages" + qs)
    page.wait_for_selector("[data-testid=pages-table], [data-testid=pages-empty]")
    idle(page)


def rows(page, tid):
    return page.evaluate("(t) => [...document.querySelectorAll(`[data-testid=${t}] tbody tr[data-key]`)].map(tr => [...tr.children].map(td => td.innerText.trim()))", tid)


def test_summary_and_page_tables_show_lonvera_vs_other_and_a_total(page):
    goto(page)
    assert page.evaluate("() => [...document.querySelectorAll('[data-testid=pages-table] thead th')].map(th => th.innerText.replace(/[^A-Za-z -]/g, '').trim())")[:3] == ["Page", "Ads with spend", "Meta spend"]
    r = rows(page, "pages-table")
    assert [x[0] for x in r] == ["LonveraLonvera", "Other handle", "(unknown page)"]            # the Lonvera row carries a Lonvera badge after its name
    assert r[0][2] == "$44,000" and r[1][2] == "$11,000" and r[2][2] == "$1,100"
    g = rows(page, "pages-summary")
    assert [x[0] for x in g][:2] == ["Lonvera page", "All other pages"] and g[0][2] == "$44,000"
    t = page.evaluate("() => [...document.querySelector('[data-testid=pages-table] tfoot tr.total').children].map(td => td.innerText.trim())")
    assert t[0] == "Total" and t[2] == "$56,100"


def test_like_for_like_note_basis_and_view_switch_are_on_the_page(page):
    goto(page)
    note = page.locator("[data-testid=pages-likeforlike]").inner_text()
    assert "like for like" in note and "consistent with" in note and "—" not in note
    assert "Meta-reported" in page.locator("[data-testid=pages-caption]").inner_text()
    assert page.locator("#view-switch [data-platform-view=pages]").get_attribute("aria-pressed") == "true"


def test_product_filter_changes_the_table_and_no_sideways_scroll_at_375(page):
    goto(page, FULL + "&product=vita-bull")
    assert rows(page, "pages-table")[0][2] == "$22,000"                                       # the mock halves every row for a product
    page.set_viewport_size({"width": 375, "height": 812})
    goto(page)
    assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth + 1")
